"""Load NYC Yellow Taxi parquet files into DuckDB and create all tables."""
import os
import duckdb

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "taxi.db")


def main() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    parquet_glob = os.path.join(DATA_DIR, "yellow_tripdata_2023-*.parquet")
    zone_csv = os.path.join(DATA_DIR, "taxi_zone_lookup.csv")

    print(f"Connecting to {DB_PATH} ...")
    con = duckdb.connect(DB_PATH)
    con.execute("PRAGMA memory_limit='4GB'")
    con.execute("PRAGMA threads=4")

    # ── trips ──────────────────────────────────────────────────────────────
    print("Creating trips table ...")
    con.execute("DROP TABLE IF EXISTS trips")
    con.execute(f"""
        CREATE TABLE trips AS
        SELECT
            row_number() OVER (ORDER BY tpep_pickup_datetime) AS trip_id,
            tpep_pickup_datetime  AS pickup_ts,
            tpep_dropoff_datetime AS dropoff_ts,
            PULocationID          AS pickup_zone,
            DOLocationID          AS dropoff_zone,
            CAST(fare_amount AS DOUBLE)    AS fare_amount,
            CAST(trip_distance AS DOUBLE)  AS trip_distance,
            (epoch_ms(tpep_dropoff_datetime) - epoch_ms(tpep_pickup_datetime))
                AS duration_ms
        FROM read_parquet('{parquet_glob}')
        WHERE fare_amount > 0
          AND trip_distance > 0
          AND tpep_pickup_datetime >= '2023-01-01'
          AND tpep_pickup_datetime <  '2023-07-01'
          AND tpep_dropoff_datetime > tpep_pickup_datetime
          AND (epoch_ms(tpep_dropoff_datetime) - epoch_ms(tpep_pickup_datetime))
              BETWEEN 60000 AND 7200000  -- 1 min to 2 hours
    """)
    count = con.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
    print(f"  trips loaded: {count:,}")

    # ── active_trips ────────────────────────────────────────────────────────
    print("Creating active_trips table ...")
    con.execute("DROP TABLE IF EXISTS active_trips")
    con.execute("""
        CREATE TABLE active_trips (
            trip_id     BIGINT PRIMARY KEY,
            pickup_ts   TIMESTAMP,
            dropoff_ts  TIMESTAMP,
            pickup_zone INTEGER,
            fare_amount DOUBLE
        )
    """)

    # ── metrics_log ─────────────────────────────────────────────────────────
    print("Creating metrics_log table ...")
    con.execute("DROP TABLE IF EXISTS metrics_log")
    con.execute("""
        CREATE TABLE metrics_log (
            ts        TIMESTAMP,
            started   INTEGER,
            completed INTEGER,
            revenue   DOUBLE
        )
    """)

    # ── zones ───────────────────────────────────────────────────────────────
    if os.path.exists(zone_csv):
        print("Creating zones table ...")
        con.execute("DROP TABLE IF EXISTS zones")
        con.execute(f"""
            CREATE TABLE zones AS
            SELECT
                LocationID   AS zone_id,
                Borough      AS borough,
                Zone         AS zone_name,
                service_zone
            FROM read_csv_auto('{zone_csv}')
        """)
        zcount = con.execute("SELECT COUNT(*) FROM zones").fetchone()[0]
        print(f"  zones loaded: {zcount}")
    else:
        print("  zone CSV not found — skipping zones table")

    # ── indexes ─────────────────────────────────────────────────────────────
    print("Creating indexes ...")
    con.execute("CREATE INDEX IF NOT EXISTS idx_trips_pickup ON trips(pickup_ts)")

    con.close()
    print("\nDone. Database ready at:", os.path.abspath(DB_PATH))


if __name__ == "__main__":
    main()
