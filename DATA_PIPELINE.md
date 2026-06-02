# NYC Taxi Data Pipeline

## Overview

The pipeline has three stages: **download → load → simulate**. No cloud account, API key, or database server is required. All data is public and freely available.

---

## 1. Data Source

**NYC TLC (Taxi & Limousine Commission) Trip Record Data**
- Published by the City of New York under an open data license
- Served as Parquet files from an AWS CloudFront CDN (`d37ci6vzurychx.cloudfront.net`)
- No authentication required — plain HTTP GET

Files pulled:
```
yellow_tripdata_2023-01.parquet  through  yellow_tripdata_2023-06.parquet   (~300 MB total)
taxi_zone_lookup.csv                                                          (zone names/boroughs)
```

**Why this dataset was chosen:**
| Reason | Detail |
|--------|--------|
| Free & public | No signup, no API key, no quota |
| Real-world scale | ~19M rows across 6 months — enough to stress-test queries |
| Rich schema | Timestamps, fares, distances, zone IDs — covers most analytics use cases |
| Stable format | TLC has used Parquet since 2022; schema rarely changes |
| Well-known benchmark | Used in countless analytics tutorials — easy to validate results |

---

## 2. Stage 1 — Download (`scripts/download_data.py`)

**Access needed:** outbound HTTPS to `d37ci6vzurychx.cloudfront.net` (public CDN, no auth).

What it does:
- Loops over months 1–6 of 2023
- Streams each Parquet file to `data/` with a 1 MB chunk size and a progress percentage
- Skips files that already exist (idempotent)
- Also downloads the zone lookup CSV

```
TLC CloudFront CDN
      │  HTTPS GET (streaming, ~300 MB)
      ▼
data/yellow_tripdata_2023-{01..06}.parquet
data/taxi_zone_lookup.csv
```

---

## 3. Stage 2 — Load into DuckDB (`scripts/load_data.py`)

**Access needed:** read access to `data/`, write access to `db/taxi.db`. Local only.

DuckDB is configured with:
- `memory_limit = 4 GB`
- `threads = 4`

### Tables created

| Table | Source | Rows | Notes |
|-------|--------|------|-------|
| `trips` | Parquet glob | ~18.9M | Cleaned, typed, indexed |
| `active_trips` | Empty at load | 0 (populated at runtime) | Simulator writes here |
| `metrics_log` | Empty at load | 0 (populated at runtime) | Per-second aggregates |
| `zones` | `taxi_zone_lookup.csv` | 265 | Borough + zone name lookup |

### `trips` table — cleaning rules applied

```sql
WHERE fare_amount > 0
  AND trip_distance > 0
  AND tpep_pickup_datetime >= '2023-01-01'
  AND tpep_pickup_datetime <  '2023-07-01'
  AND tpep_dropoff_datetime > tpep_pickup_datetime
  AND duration BETWEEN 1 min AND 2 hours
```

Columns renamed for clarity: `tpep_pickup_datetime → pickup_ts`, `PULocationID → pickup_zone`, etc.

An index is created on `pickup_ts` to support the simulator's time-windowed chunk queries.

```
data/*.parquet  ──read_parquet()──►  trips        (static, ~18.9M rows)
data/taxi_zone_lookup.csv ─────────►  zones        (265 rows)
                                       active_trips  (empty shell)
                                       metrics_log   (empty shell)
                                            │
                                       db/taxi.db
```

---

## 4. Stage 3 — Simulation (`simulator/stream_sim.py`)

**Access needed:** read/write on the shared DuckDB connection (managed via `threading.RLock`).

The `SimulatorThread` runs as a daemon thread inside FastAPI's lifespan. It replays historical trips on an accelerated clock (default **60x** — 1 real-second = 1 sim-minute).

### Per-tick loop (every 1 real-second)

1. Advance the sim clock by `speed × 1s` (default: 60 sim-seconds)
2. **Query** `trips` for all pickups in the new time window (loaded in 1-hour chunks to avoid scanning the full table every tick)
3. **INSERT** new trips into `active_trips`
4. **COUNT + DELETE** trips whose `dropoff_ts ≤ sim_clock` (completed trips)
5. **INSERT** one row into `metrics_log` (started, completed, revenue for this tick)
6. **DELETE** `metrics_log` rows older than 2 hours (rolling window)

```
trips (static)
    │  chunk query (1-hour window)
    ▼
SimulatorThread (daemon thread, 1s tick)
    │
    ├─ INSERT → active_trips   (in-progress trips)
    ├─ DELETE ← active_trips   (completed trips)
    └─ INSERT → metrics_log    (aggregates, 2h rolling)
```

FastAPI reads `active_trips` and `metrics_log` on every API call, protected by the same `RLock`.

---

## 5. Access Summary

| Stage | Who needs access | What they need |
|-------|-----------------|----------------|
| Download | `scripts/download_data.py` | Outbound HTTPS (port 443) to TLC CDN |
| Load | `scripts/load_data.py` | Read `data/`, write `db/` |
| Simulate | `SimulatorThread` | Shared DuckDB connection (in-process) |
| API reads | FastAPI handlers | Same shared DuckDB connection |

No credentials, environment variables, or secrets are required at any stage.

---

## 6. Alternative Data Sources

If you wanted to swap out the TLC dataset, these are the closest equivalents:

| Source | Data | Access | Pros | Cons |
|--------|------|--------|------|------|
| **NYC OpenData (Socrata API)** | Same TLC trips via REST/SQL | Free, public API key | Queryable without downloading | Row limits per call; slower than Parquet |
| **Chicago Taxi Trips** | ~100M trips 2013–present | Free, Socrata | Larger history | Different schema; Chicago-specific zones |
| **Uber/Lyft FHVHV data** | For-hire vehicle trips (TLC) | Same CDN as yellow taxi | Same pipeline, same format | Fare data less detailed |
| **Google BigQuery Public Datasets** | TLC yellow + green taxi | Free tier (10 GB/mo queries) | No download needed; SQL interface | Requires Google account; cloud dependency |
| **Kaggle NYC Taxi datasets** | Various years | Free, Kaggle account | Pre-cleaned versions available | Older data; account required |
| **Streaming alternative: MTA Bus/Subway** | Real-time GTFS feeds | Free API key from MTA | Truly real-time (no simulation needed) | Transit data, not taxi — different schema |

The TLC Parquet-from-CDN approach was chosen because it requires zero accounts, works offline after download, and loads natively into DuckDB with a single `read_parquet()` glob — no ETL framework needed.
