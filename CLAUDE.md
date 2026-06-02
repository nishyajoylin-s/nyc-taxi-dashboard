# CLAUDE.md — Project Context

> Start here. Do NOT re-read all project files from scratch — this file captures everything you need to understand the codebase and continue work.

---

## What this project is

A **local real-time analytics dashboard** for NYC Yellow Taxi data (Jan–Jun 2023, ~18.9M trips).  
No cloud. No paid services. Fully open-source.

**Portfolio goal:** Hosted at [nishyajoylin-s.github.io](https://nishyajoylin-s.github.io/) via Streamlit Community Cloud embed. Targets all audiences — engineers (pipeline depth), analysts (historical insights), business stakeholders (actionable narrative).

---

## Architecture

```
TLC Parquet CDN (public HTTPS, no auth)
        ↓ scripts/download_data.py
   data/*.parquet  +  data/taxi_zone_lookup.csv
        ↓ scripts/load_data.py
   db/taxi.db  (DuckDB — trips, zones tables)
        ↓ SimulatorThread (daemon thread inside FastAPI)
   active_trips + metrics_log  (live, updated every second)
        ↓ FastAPI (port 8001)  [local mode]
   dashboard/app.py  →  Streamlit (port 8502)
        ↓ [cloud mode, no FastAPI]
   dashboard/standalone.py  →  Streamlit Cloud
```

---

## Stack

| Layer | Tool | Why chosen |
|-------|------|-----------|
| Storage | DuckDB | Columnar, parquet-native, zero-server, fast analytical queries |
| Pipeline | Python + requests | No ETL framework needed; parquet reads with one glob |
| API | FastAPI + uvicorn | Async, minimal, local dev only |
| Dashboard | Streamlit | Python-native, built-in charts, rapid iteration |
| Charts | Plotly | Dark theme, interactive, no extra deps |
| Simulator | Python daemon thread | No file-locking issues vs separate process |

---

## Database Tables

### `trips` — static, ~18.9M rows (local) / ~9M rows (cloud, 3 months)

| Column | Type | Notes |
|--------|------|-------|
| `trip_id` | BIGINT | row_number() PK |
| `pickup_ts` | TIMESTAMP | indexed (`idx_trips_pickup`) |
| `dropoff_ts` | TIMESTAMP | |
| `pickup_zone` | INTEGER | PULocationID → matches zones.zone_id |
| `dropoff_zone` | INTEGER | DOLocationID |
| `fare_amount` | DOUBLE | > 0, cleaned |
| `trip_distance` | DOUBLE | > 0, cleaned |
| `duration_ms` | BIGINT | 60,000–7,200,000 ms (1 min–2 hours) |

Cleaning filters applied at load: positive fare/distance, valid date range, sane duration.

### `active_trips` — live, simulator-managed

| Column | Type |
|--------|------|
| `trip_id` | BIGINT PK |
| `pickup_ts` | TIMESTAMP |
| `dropoff_ts` | TIMESTAMP |
| `pickup_zone` | INTEGER |
| `fare_amount` | DOUBLE |

### `metrics_log` — 2-hour rolling window, simulator-managed

| Column | Type | Notes |
|--------|------|-------|
| `ts` | TIMESTAMP | wall-clock time of tick |
| `started` | INTEGER | trips started this tick |
| `completed` | INTEGER | trips completed this tick |
| `revenue` | DOUBLE | revenue from completed trips |

### `zones` — 265 NYC TLC zones

| Column | Type |
|--------|------|
| `zone_id` | INTEGER |
| `borough` | VARCHAR |
| `zone_name` | VARCHAR |
| `service_zone` | VARCHAR |

---

## API Endpoints (local FastAPI, port 8001)

| Endpoint | Description | Key response fields |
|----------|-------------|---------------------|
| `GET /metrics/realtime` | Live KPIs | `active_trips`, `trips_per_min`, `revenue_per_min`, `avg_duration_min`, `query_ms` |
| `GET /metrics/historical?minutes=10` | Per-minute time series | `[{bucket, trips_started, trips_completed, revenue}]` |
| `GET /zones/top?limit=10` | Top pickup zones (active) | `[{zone_id, trip_count, zone_name, borough}]` |
| `GET /zones/top?limit=10&borough=Manhattan` | Borough-filtered zones | same shape |
| `GET /metrics/historical?minutes=30` | Extended history | same shape |
| `GET /analysis/hourly?borough=all` | 24h demand profile | `[{hour, weekday_trips, weekend_trips}]` |
| `GET /analysis/borough` | Borough breakdown | `[{borough, trips, revenue, avg_fare_per_mile}]` |
| `GET /analysis/routes?limit=10` | Top OD pairs | `[{pickup_zone, dropoff_zone, count}]` |
| `GET /analysis/fare-buckets?borough=all&month=all` | Fare histogram | `[{bucket, count}]` |
| `GET /health` | Simulator alive + metrics_log row count | `{status, sim_alive, metrics_log_rows}` |

---

## Key Files

```
nyc-taxi-dashboard/
├── CLAUDE.md                ← you are here
├── DATA_PIPELINE.md         ← deep dive: data source, pipeline, alternatives
├── README.md                ← setup + run instructions
├── api/
│   └── main.py              ← FastAPI app + simulator lifespan + all endpoints
├── dashboard/
│   ├── app.py               ← Streamlit dashboard (local, fetches from FastAPI)
│   ├── db.py                ← shared query functions (DuckDB direct, used by standalone)
│   └── standalone.py        ← Streamlit Cloud entry point (no FastAPI needed)
├── simulator/
│   └── stream_sim.py        ← SimulatorThread class (60x replay, RLock-protected)
├── scripts/
│   ├── download_data.py     ← streams parquet from TLC CDN
│   └── load_data.py         ← builds DuckDB tables from parquet
├── data/                    ← parquet + CSV (gitignored, ~300MB)
├── db/                      ← taxi.db (gitignored)
├── .streamlit/
│   └── config.toml          ← dark theme, wide layout for Streamlit Cloud
├── packages.txt             ← Streamlit Cloud system deps
└── start.sh                 ← launches FastAPI + Streamlit together (local)
```

---

## Simulator Behaviour

- **Speed:** 60x (1 real-second = 1 sim-minute). Override: `SPEED=120 ./start.sh`
- **Tick:** 1 real-second per iteration
- **Chunk loading:** reads `trips` in 1-hour sim-windows to avoid full-table scans per tick
- **Loop:** restarts from 2023-01-01 when all trips exhausted
- **Thread safety:** single `threading.RLock` wraps all DuckDB writes; FastAPI reads also acquire the same lock

---

## Dashboard Layout (current + extended)

### Tab 1 — Live Operations
- **KPI cards** (row): Active Trips · Trips/min · Revenue/min · Avg Duration
- **Line chart**: Trips started vs completed (last N min, configurable)
- **Bar chart**: Top pickup zones (borough-filtered)
- **Area chart**: Revenue/min sparkline
- **Sidebar filters**: Borough multiselect · Time window slider (5/10/15/30 min)
- **Sidebar diagnostics**: API latency · DuckDB query ms · Last tick · Top zone

### Tab 2 — Historical Intelligence
Each section frames a business question:
1. **"When is demand highest?"** — 24h line chart (weekday vs weekend)
2. **"Which borough drives revenue?"** — grouped bar (trips + revenue + avg fare/mile)
3. **"What does a typical fare look like?"** — histogram
4. **"What are the busiest routes?"** — top OD pairs table
5. **"Where should drivers reposition?"** — scatter (pickup count vs dropoff count by zone)

Sidebar filters: Borough · Month · Hour of day range · Fare range

---

## Cloud vs Local Mode

| | Local | Cloud (Streamlit Community Cloud) |
|--|-------|-----------------------------------|
| Entry point | `dashboard/app.py` | `dashboard/standalone.py` |
| Data | 6 months, ~18.9M trips | 3 months (Jan–Mar), ~9M trips |
| API | FastAPI on port 8001 | No FastAPI — DuckDB direct via `db.py` |
| DB init | Pre-built by `load_data.py` | Auto-downloaded + built on first run |
| Simulator | Runs in FastAPI lifespan | Runs in `@st.cache_resource` thread |

Detect cloud mode: `os.environ.get("CLOUD") == "1"` or check `IS_CLOUD` flag.

---

## Design Decisions & Rationale

- **DuckDB over Postgres/SQLite:** Columnar engine handles the 18.9M row analytical queries (hourly aggregations, fare histograms) in milliseconds without indexing every column.
- **Single shared connection + RLock:** DuckDB supports multiple readers but only one writer. The simulator writes constantly, so a single connection with a reentrant lock is the simplest correct approach.
- **`@lru_cache` on analysis endpoints:** The `trips` table never changes after load — caching the first result means no repeated full-table scans on the 18.9M row historical queries.
- **Simulator inside FastAPI lifespan (not a separate process):** Avoids DuckDB file-locking conflicts that occur with two processes sharing a `.db` file.
- **TLC Parquet via CDN:** Free, no auth, and DuckDB reads it natively with `read_parquet()` — zero ETL framework overhead.

---

## How to Run (local)

```bash
# One-time setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/download_data.py   # ~300MB, ~2 min
python scripts/load_data.py       # ~2 min

# Run
./start.sh
# Dashboard: http://localhost:8502
# API:       http://localhost:8001
# API docs:  http://localhost:8001/docs
```

## How to Run (cloud standalone)

```bash
CLOUD=1 streamlit run dashboard/standalone.py
# Downloads Jan–Mar 2023 on first run (~150MB), then starts normally
```
