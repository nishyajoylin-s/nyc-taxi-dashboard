# NYC Taxi Real-Time Analytics Dashboard

Local **and** cloud real-time analytics dashboard using NYC Yellow Taxi data (Jan–Jun 2023, ~19M trips).  
No paid services. Fully open-source.

---

## Architecture

```
TLC Parquet (6 months)
        ↓ download_data.py
   data/*.parquet
        ↓ load_data.py
   db/taxi.db  (DuckDB)
        ↓ SimulatorThread (runs inside FastAPI / Streamlit Cloud)
   active_trips + metrics_log tables
        ↓ FastAPI (port 8001)  ← local mode
   Streamlit dashboard (port 8502)

        ↓ standalone.py       ← cloud mode (no FastAPI)
   Streamlit Community Cloud
        ↓ iframe embed
   nishyajoylin-s.github.io
```

| Component | Tool | Why |
|-----------|------|-----|
| Storage | DuckDB | Columnar, parquet-native, zero-server |
| API | FastAPI + uvicorn | Async, minimal (local only) |
| Dashboard | Streamlit | Python-native, built-in charts |
| Charts | Plotly | Dark theme, interactive |
| Simulator | Python daemon thread | No file-locking issues vs separate process |

---

## Setup (one-time, local)

```bash
cd ~/nyc-taxi-dashboard

# 1. Create venv + install deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Download data (~300MB, ~2 min)
python scripts/download_data.py

# 3. Load into DuckDB (~2 min)
python scripts/load_data.py
```

---

## Run (local)

```bash
./start.sh
```

| Service | URL |
|---------|-----|
| Dashboard | http://localhost:8502 |
| API | http://localhost:8001 |
| API docs | http://localhost:8001/docs |

Stop: `Ctrl+C` in the terminal running `start.sh`

---

## Run (cloud standalone — no FastAPI needed)

```bash
CLOUD=1 streamlit run dashboard/standalone.py
```

Downloads Jan–Mar 2023 data on first run (~150 MB, ~2 min), then starts normally.

---

## Simulator

Replays historical trips on an accelerated clock.

- **Speed:** 60x by default (1 real-second = 1 sim-minute)
- **Restart:** loops back to Jan 1 2023 when all trips are replayed
- **Override speed:** `SPEED=120 ./start.sh`

---

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /metrics/realtime` | Active trips, trips/min, revenue/min, avg duration |
| `GET /metrics/historical?minutes=10` | Per-minute time series (last N minutes) |
| `GET /zones/top?limit=10&borough=Manhattan` | Top pickup zones (borough-filterable) |
| `GET /analysis/hourly?borough=all` | 24h demand profile (weekday vs weekend) |
| `GET /analysis/borough` | Trips, revenue, avg fare/mile by borough |
| `GET /analysis/routes?limit=10` | Top origin→destination zone pairs |
| `GET /analysis/fare-buckets?borough=all&month=all` | Fare distribution histogram |
| `GET /analysis/opportunity-zones?borough=all` | Pickup vs dropoff imbalance by zone |
| `GET /health` | Service health + simulator status |

---

## Dashboard

### Tab 1 — Live Operations
- **KPI cards:** Active Trips · Trips/min · Revenue/min · Avg Duration
- **Line chart:** trips started vs completed (configurable time window)
- **Bar chart:** top pickup zones (borough-filtered)
- **Area chart:** revenue/min sparkline
- **Sidebar:** borough filter · time window slider · diagnostics

### Tab 2 — Historical Intelligence
Each section answers a business question:

| Section | Question |
|---------|---------|
| 24h demand chart | When is demand highest? (weekday vs weekend) |
| Borough breakdown | Which borough drives revenue + fare efficiency? |
| Fare distribution | What does a typical fare look like? |
| Top routes | What are the busiest origin→destination pairs? |
| Opportunity zones | Where should drivers reposition? |

Sidebar filters: Borough · Month · (fare range on fare chart)

---

## File Structure

```
nyc-taxi-dashboard/
├── CLAUDE.md                ← AI session context (start here for future sessions)
├── DATA_PIPELINE.md         ← pipeline deep-dive + data source alternatives
├── api/
│   └── main.py              ← FastAPI app + simulator lifespan + all endpoints
├── dashboard/
│   ├── app.py               ← Streamlit (local mode, fetches from FastAPI)
│   ├── db.py                ← shared DuckDB query layer (used by both modes)
│   └── standalone.py        ← Streamlit Cloud entry point (no FastAPI)
├── simulator/
│   └── stream_sim.py        ← SimulatorThread (60x replay, RLock-protected)
├── scripts/
│   ├── download_data.py     ← streams parquet from TLC CDN
│   └── load_data.py         ← builds DuckDB tables from parquet
├── .streamlit/
│   └── config.toml          ← dark theme, wide layout
├── packages.txt             ← Streamlit Cloud system deps
├── data/                    ← parquet files (gitignored, ~300MB)
├── db/                      ← taxi.db (gitignored)
├── logs/                    ← simulator.log
├── requirements.txt
└── start.sh
```

---

## DuckDB Tables

| Table | Description |
|-------|-------------|
| `trips` | All 18.9M cleaned trips (static) |
| `active_trips` | Currently in-progress trips (live, updated every second) |
| `metrics_log` | Per-tick aggregates — started/completed/revenue (rolling 2h) |
| `zones` | NYC TLC zone lookup (265 zones) |

---

## Deploy to Streamlit Community Cloud

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**
3. Set **Main file path:** `dashboard/standalone.py`
4. Add secret/env var: `CLOUD = 1`
5. Deploy — first cold start downloads data and builds the DB (~2 min)

Then embed the deployed URL in your GitHub Pages site via `<iframe>`.

---

## What I'd Build Next

- **Driver dispatch recommendations** — use opportunity-zone imbalance as a real-time repositioning signal
- **Surge pricing predictor** — time-of-day × borough demand model
- **MTA subway integration** — find zones where transit gaps create taxi demand
