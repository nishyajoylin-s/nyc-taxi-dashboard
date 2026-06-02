"""
FastAPI layer — serves real-time and historical metrics from DuckDB.
Simulator runs as a background daemon thread; shares a single DuckDB
connection protected by a threading.RLock.
"""
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, List, Optional

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# Add project root to path so simulator + dashboard modules are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from simulator.stream_sim import SimulatorThread
import dashboard.db as db

# ── config ───────────────────────────────────────────────────────────────────
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "db", "taxi.db"))
LOG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs", "simulator.log"))
SPEED = int(os.environ.get("SPEED", 60))

# ── logging ──────────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("api")

# ── shared state ─────────────────────────────────────────────────────────────
_con:  Optional[duckdb.DuckDBPyConnection] = None
_lock: threading.RLock = threading.RLock()
_sim:  Optional[SimulatorThread] = None


def get_con() -> duckdb.DuckDBPyConnection:
    global _con
    if _con is None:
        if not os.path.exists(DB_PATH):
            raise RuntimeError(f"Database not found: {DB_PATH}. Run load_data.py first.")
        _con = duckdb.connect(DB_PATH)
        _con.execute("PRAGMA memory_limit='4GB'")
        log.info(f"DuckDB connected: {DB_PATH}")
    return _con


def query(sql: str, params: Optional[List] = None) -> List[dict]:
    con = get_con()
    with _lock:
        cursor = con.execute(sql, params or [])
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _con_lock():
    return get_con(), _lock


# ── lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _sim
    get_con()
    _sim = SimulatorThread(get_con(), _lock, speed=SPEED)
    _sim.start()
    log.info("Simulator thread started.")
    yield
    if _sim:
        _sim.stop()
        _sim.join(timeout=5)
    if _con:
        _con.close()
    log.info("Shutdown complete.")


# ── app ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="NYC Taxi Dashboard API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── /metrics/realtime ─────────────────────────────────────────────────────────
@app.get("/metrics/realtime")
def metrics_realtime():
    try:
        return db.query_realtime(*_con_lock())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── /metrics/historical ───────────────────────────────────────────────────────
@app.get("/metrics/historical")
def metrics_historical(minutes: int = Query(default=10, ge=1, le=120)):
    try:
        return db.query_historical(*_con_lock(), minutes=minutes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── /zones/top ────────────────────────────────────────────────────────────────
@app.get("/zones/top")
def zones_top(
    limit: int = Query(default=10, ge=1, le=50),
    borough: Optional[str] = Query(default=None),
):
    try:
        return db.query_zones_top(*_con_lock(), limit=limit, borough=borough)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── /analysis/hourly ─────────────────────────────────────────────────────────
@app.get("/analysis/hourly")
def analysis_hourly(borough: str = Query(default="All")):
    try:
        return db.query_hourly(*_con_lock(), borough=borough)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── /analysis/borough ────────────────────────────────────────────────────────
@app.get("/analysis/borough")
def analysis_borough():
    try:
        return db.query_borough_breakdown(*_con_lock())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── /analysis/routes ─────────────────────────────────────────────────────────
@app.get("/analysis/routes")
def analysis_routes(limit: int = Query(default=10, ge=1, le=50)):
    try:
        return db.query_top_routes(*_con_lock(), limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── /analysis/fare-buckets ────────────────────────────────────────────────────
@app.get("/analysis/fare-buckets")
def analysis_fare_buckets(
    borough: str = Query(default="All"),
    month: str = Query(default="All"),
):
    try:
        return db.query_fare_buckets(*_con_lock(), borough=borough, month=month)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── /analysis/opportunity-zones ───────────────────────────────────────────────
@app.get("/analysis/opportunity-zones")
def analysis_opportunity_zones(borough: str = Query(default="All")):
    try:
        return db.query_opportunity_zones(*_con_lock(), borough=borough)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── /health ───────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    try:
        sim_alive = _sim is not None and _sim.is_alive()
        return db.query_health(*_con_lock(), sim_alive=sim_alive)
    except Exception as e:
        return {"status": "error", "detail": str(e)}
