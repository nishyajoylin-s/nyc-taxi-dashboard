"""
Simulator thread — replays NYC taxi trips on an accelerated clock.
Runs inside FastAPI's lifespan as a daemon thread.
Shares a single DuckDB connection via a threading.RLock.

SPEED: sim-seconds per real-second (default 60 → 1 real-sec = 1 sim-min)
"""
import logging
import threading
import time
from datetime import datetime, timedelta

import duckdb
import pandas as pd

log = logging.getLogger("sim")

SPEED     = 60        # sim-sec per real-sec (overridden by env in api/main.py)
TICK      = 1.0       # real-sec per iteration
SIM_START = datetime(2023, 1, 1, 0, 0, 0)


class SimulatorThread(threading.Thread):
    def __init__(self, con: duckdb.DuckDBPyConnection, lock: threading.RLock, speed: int = SPEED):
        super().__init__(daemon=True, name="simulator")
        self.con   = con
        self.lock  = lock
        self.speed = speed
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def _load_chunk(self, from_ts: datetime, to_ts: datetime) -> pd.DataFrame:
        with self.lock:
            return self.con.execute("""
                SELECT trip_id, pickup_ts, dropoff_ts, pickup_zone, fare_amount
                FROM trips
                WHERE pickup_ts >= ? AND pickup_ts < ?
                ORDER BY pickup_ts
            """, [from_ts, to_ts]).df()

    def run(self):
        log.info(f"Simulator starting (SPEED={self.speed}x)")

        with self.lock:
            self.con.execute("DELETE FROM active_trips")
            self.con.execute("DELETE FROM metrics_log")

        sim_clock  = SIM_START
        chunk_end  = sim_clock + timedelta(hours=1)
        chunk_df   = self._load_chunk(sim_clock, chunk_end)
        chunk_idx  = 0
        log.info(f"First chunk: {len(chunk_df):,} trips")

        while not self._stop.is_set():
            tick_start  = time.monotonic()
            next_clock  = sim_clock + timedelta(seconds=self.speed * TICK)

            # Collect trips starting this tick
            started_rows = []
            while chunk_idx < len(chunk_df):
                row = chunk_df.iloc[chunk_idx]
                if row.pickup_ts <= next_clock:
                    started_rows.append(row)
                    chunk_idx += 1
                else:
                    break

            # Reload chunk if exhausted
            if chunk_idx >= len(chunk_df):
                chunk_end = next_clock + timedelta(hours=1)
                chunk_df  = self._load_chunk(next_clock, chunk_end)
                chunk_idx = 0
                if len(chunk_df) == 0:
                    log.info("All trips replayed — restarting from 2023-01-01")
                    with self.lock:
                        self.con.execute("DELETE FROM active_trips")
                    sim_clock = SIM_START
                    chunk_end = sim_clock + timedelta(hours=1)
                    chunk_df  = self._load_chunk(sim_clock, chunk_end)
                    chunk_idx = 0
                    continue

            with self.lock:
                # Batch-insert new trips
                started_count = len(started_rows)
                if started_rows:
                    new_df = pd.DataFrame(started_rows)[
                        ["trip_id", "pickup_ts", "dropoff_ts", "pickup_zone", "fare_amount"]
                    ]
                    self.con.execute(
                        "INSERT OR IGNORE INTO active_trips SELECT * FROM new_df"
                    )

                # Count + delete completed trips
                row_c = self.con.execute("""
                    SELECT COUNT(*), COALESCE(SUM(fare_amount), 0)
                    FROM active_trips WHERE dropoff_ts <= ?
                """, [next_clock]).fetchone()
                completed_count   = int(row_c[0] or 0)
                completed_revenue = float(row_c[1] or 0.0)

                if completed_count > 0:
                    self.con.execute(
                        "DELETE FROM active_trips WHERE dropoff_ts <= ?", [next_clock]
                    )

                # Log metrics tick (use local time to match DuckDB's now())
                self.con.execute(
                    "INSERT INTO metrics_log VALUES (?, ?, ?, ?)",
                    [datetime.now(), started_count, completed_count, completed_revenue],
                )
                # Prune metrics_log > 2 hours old
                self.con.execute(
                    "DELETE FROM metrics_log WHERE ts < now() - INTERVAL '2 hours'"
                )

                active = self.con.execute("SELECT COUNT(*) FROM active_trips").fetchone()[0]

            sim_clock = next_clock
            log.info(
                f"sim={sim_clock.strftime('%m-%d %H:%M')} "
                f"started={started_count} completed={completed_count} "
                f"active={active} rev=${completed_revenue:.2f}"
            )

            elapsed    = time.monotonic() - tick_start
            sleep_time = max(0.0, TICK - elapsed)
            time.sleep(sleep_time)

        log.info("Simulator stopped.")
