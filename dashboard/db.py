"""
Shared DuckDB query layer.

Used by:
  - dashboard/standalone.py  (Streamlit Cloud — no FastAPI, DuckDB direct)
  - api/main.py              (local — same functions, different caller)

All analysis queries target the static `trips` table and are cached on
first call (trips never changes after load_data.py).
"""
import threading
import time
from typing import Any, Dict, List, Optional

import duckdb

# ── module-level cache for static analysis results ────────────────────────────
_analysis_cache: Dict[str, Any] = {}
_cache_lock = threading.Lock()


def _cached(key: str, fn):
    """Run fn() once and cache; return cached value on subsequent calls."""
    with _cache_lock:
        if key not in _analysis_cache:
            _analysis_cache[key] = fn()
        return _analysis_cache[key]


def invalidate_cache():
    """Call this if trips table is ever rebuilt (e.g. re-run load_data.py)."""
    with _cache_lock:
        _analysis_cache.clear()


# ── low-level query helper ────────────────────────────────────────────────────

def _rows(con: duckdb.DuckDBPyConnection, lock: threading.RLock,
          sql: str, params: Optional[List] = None) -> List[dict]:
    with lock:
        cursor = con.execute(sql, params or [])
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]


# ── live queries (hit active_trips / metrics_log) ─────────────────────────────

def query_realtime(con: duckdb.DuckDBPyConnection, lock: threading.RLock) -> dict:
    t0 = time.perf_counter()
    active = _rows(con, lock, """
        SELECT
            COUNT(*)                                               AS active_trips,
            AVG(epoch_ms(dropoff_ts) - epoch_ms(pickup_ts)) / 60000.0 AS avg_duration_min
        FROM active_trips
    """)[0]
    rates = _rows(con, lock, """
        SELECT
            COALESCE(SUM(started),   0) AS trips_per_min,
            COALESCE(SUM(revenue),   0) AS revenue_per_min,
            COALESCE(SUM(completed), 0) AS completed_per_min
        FROM metrics_log
        WHERE ts >= now() - INTERVAL '60 seconds'
    """)[0]
    sim = _rows(con, lock, "SELECT MAX(ts) AS last_tick FROM metrics_log")[0]
    return {
        "active_trips":      int(active["active_trips"] or 0),
        "avg_duration_min":  round(float(active["avg_duration_min"] or 0), 2),
        "trips_per_min":     int(rates["trips_per_min"] or 0),
        "revenue_per_min":   round(float(rates["revenue_per_min"] or 0), 2),
        "completed_per_min": int(rates["completed_per_min"] or 0),
        "last_tick":         str(sim["last_tick"]),
        "query_ms":          round((time.perf_counter() - t0) * 1000, 2),
    }


def query_historical(con: duckdb.DuckDBPyConnection, lock: threading.RLock,
                     minutes: int = 10) -> List[dict]:
    rows = _rows(con, lock, """
        SELECT
            date_trunc('minute', ts)     AS bucket,
            SUM(started)                 AS trips_started,
            SUM(completed)               AS trips_completed,
            SUM(revenue)                 AS revenue
        FROM metrics_log
        WHERE ts >= now() - (? * INTERVAL '1 minute')
        GROUP BY bucket
        ORDER BY bucket
    """, [minutes])
    for r in rows:
        r["bucket"]          = str(r["bucket"])
        r["trips_started"]   = int(r["trips_started"] or 0)
        r["trips_completed"] = int(r["trips_completed"] or 0)
        r["revenue"]         = round(float(r["revenue"] or 0), 2)
    return rows


def query_zones_top(con: duckdb.DuckDBPyConnection, lock: threading.RLock,
                    limit: int = 10, borough: Optional[str] = None) -> List[dict]:
    borough_filter = "AND z.borough = ?" if borough and borough != "All" else ""
    params = [limit] if not (borough and borough != "All") else [borough, limit]
    sql = f"""
        SELECT
            a.pickup_zone                       AS zone_id,
            COUNT(*)                            AS trip_count,
            COALESCE(z.zone_name, 'Unknown')    AS zone_name,
            COALESCE(z.borough,   'Unknown')    AS borough
        FROM active_trips a
        LEFT JOIN zones z ON z.zone_id = a.pickup_zone
        WHERE 1=1 {borough_filter}
        GROUP BY a.pickup_zone, z.zone_name, z.borough
        ORDER BY trip_count DESC
        LIMIT ?
    """
    # fix param order: borough before limit
    if borough and borough != "All":
        params = [borough, limit]
    else:
        params = [limit]
    rows = _rows(con, lock, sql, params)
    for r in rows:
        r["trip_count"] = int(r["trip_count"])
    return rows


def query_health(con: duckdb.DuckDBPyConnection, lock: threading.RLock,
                 sim_alive: bool) -> dict:
    rows = _rows(con, lock, "SELECT COUNT(*) AS n FROM metrics_log")
    return {"status": "ok", "metrics_log_rows": rows[0]["n"], "sim_alive": sim_alive}


# ── analysis queries (static trips table, cached) ─────────────────────────────

def query_hourly(con: duckdb.DuckDBPyConnection, lock: threading.RLock,
                 borough: str = "All") -> List[dict]:
    """24-hour demand profile split by weekday vs weekend."""
    cache_key = f"hourly:{borough}"

    def _run():
        borough_filter = "AND z.borough = ?" if borough != "All" else ""
        params = [borough] if borough != "All" else []
        rows = _rows(con, lock, f"""
            SELECT
                EXTRACT(hour FROM t.pickup_ts)::INTEGER        AS hour,
                SUM(CASE WHEN DAYOFWEEK(t.pickup_ts) IN (1, 7) THEN 1 ELSE 0 END) AS weekend_trips,
                SUM(CASE WHEN DAYOFWEEK(t.pickup_ts) NOT IN (1, 7) THEN 1 ELSE 0 END) AS weekday_trips
            FROM trips t
            LEFT JOIN zones z ON z.zone_id = t.pickup_zone
            WHERE 1=1 {borough_filter}
            GROUP BY hour
            ORDER BY hour
        """, params)
        for r in rows:
            r["hour"]          = int(r["hour"])
            r["weekend_trips"] = int(r["weekend_trips"] or 0)
            r["weekday_trips"] = int(r["weekday_trips"] or 0)
        return rows

    return _cached(cache_key, _run)


def query_borough_breakdown(con: duckdb.DuckDBPyConnection,
                            lock: threading.RLock) -> List[dict]:
    """Revenue, trip count, and avg fare/mile by borough."""
    def _run():
        rows = _rows(con, lock, """
            SELECT
                COALESCE(z.borough, 'Unknown')      AS borough,
                COUNT(*)                             AS trips,
                ROUND(SUM(t.fare_amount), 2)         AS revenue,
                ROUND(SUM(t.fare_amount) / NULLIF(SUM(t.trip_distance), 0), 2) AS avg_fare_per_mile
            FROM trips t
            LEFT JOIN zones z ON z.zone_id = t.pickup_zone
            GROUP BY z.borough
            ORDER BY trips DESC
        """)
        for r in rows:
            r["trips"]            = int(r["trips"])
            r["revenue"]          = float(r["revenue"] or 0)
            r["avg_fare_per_mile"] = float(r["avg_fare_per_mile"] or 0)
        return rows

    return _cached("borough_breakdown", _run)


def query_top_routes(con: duckdb.DuckDBPyConnection, lock: threading.RLock,
                     limit: int = 10) -> List[dict]:
    """Top origin→destination zone pairs by trip count."""
    cache_key = f"top_routes:{limit}"

    def _run():
        rows = _rows(con, lock, """
            SELECT
                COALESCE(pz.zone_name, 'Zone ' || t.pickup_zone::VARCHAR)  AS pickup_zone,
                COALESCE(dz.zone_name, 'Zone ' || t.dropoff_zone::VARCHAR) AS dropoff_zone,
                COALESCE(pz.borough,   'Unknown')                          AS pickup_borough,
                COUNT(*)                                                   AS trip_count
            FROM trips t
            LEFT JOIN zones pz ON pz.zone_id = t.pickup_zone
            LEFT JOIN zones dz ON dz.zone_id = t.dropoff_zone
            GROUP BY t.pickup_zone, t.dropoff_zone, pz.zone_name, dz.zone_name, pz.borough
            ORDER BY trip_count DESC
            LIMIT ?
        """, [limit])
        for r in rows:
            r["trip_count"] = int(r["trip_count"])
        return rows

    return _cached(cache_key, _run)


def query_fare_buckets(con: duckdb.DuckDBPyConnection, lock: threading.RLock,
                       borough: str = "All", month: str = "All") -> List[dict]:
    """Fare distribution in $5 buckets, optionally filtered."""
    cache_key = f"fare_buckets:{borough}:{month}"

    def _run():
        filters = []
        params = []
        if borough != "All":
            filters.append("z.borough = ?")
            params.append(borough)
        if month != "All":
            filters.append("EXTRACT(month FROM t.pickup_ts)::INTEGER = ?")
            params.append(int(month))
        where = ("WHERE " + " AND ".join(filters)) if filters else ""

        rows = _rows(con, lock, f"""
            SELECT
                CONCAT('$', (FLOOR(t.fare_amount / 5) * 5)::INTEGER, '–$',
                       (FLOOR(t.fare_amount / 5) * 5 + 5)::INTEGER)  AS bucket,
                FLOOR(t.fare_amount / 5)::INTEGER                    AS bucket_order,
                COUNT(*)                                              AS count
            FROM trips t
            LEFT JOIN zones z ON z.zone_id = t.pickup_zone
            {where}
            GROUP BY bucket, bucket_order
            ORDER BY bucket_order
            LIMIT 20
        """, params)
        for r in rows:
            r["count"] = int(r["count"])
            del r["bucket_order"]
        return rows

    return _cached(cache_key, _run)


def query_opportunity_zones(con: duckdb.DuckDBPyConnection,
                            lock: threading.RLock,
                            borough: str = "All") -> List[dict]:
    """Pickup vs dropoff imbalance by zone — high dropoff + low pickup = opportunity."""
    cache_key = f"opportunity_zones:{borough}"

    def _run():
        borough_filter = "AND z.borough = ?" if borough != "All" else ""
        params = [borough] if borough != "All" else []
        rows = _rows(con, lock, f"""
            SELECT
                COALESCE(z.zone_name, 'Zone ' || pu.zone_id::VARCHAR) AS zone_name,
                COALESCE(z.borough,   'Unknown')                       AS borough,
                pu.pickup_count,
                do_.dropoff_count,
                (do_.dropoff_count - pu.pickup_count)                  AS imbalance
            FROM (
                SELECT pickup_zone AS zone_id, COUNT(*) AS pickup_count
                FROM trips GROUP BY pickup_zone
            ) pu
            JOIN (
                SELECT dropoff_zone AS zone_id, COUNT(*) AS dropoff_count
                FROM trips GROUP BY dropoff_zone
            ) do_ ON do_.zone_id = pu.zone_id
            LEFT JOIN zones z ON z.zone_id = pu.zone_id
            WHERE 1=1 {borough_filter}
            ORDER BY imbalance DESC
            LIMIT 40
        """, params)
        for r in rows:
            r["pickup_count"]  = int(r["pickup_count"])
            r["dropoff_count"] = int(r["dropoff_count"])
            r["imbalance"]     = int(r["imbalance"])
        return rows

    return _cached(cache_key, _run)
