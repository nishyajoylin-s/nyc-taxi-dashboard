"""
Streamlit Cloud entry point — no FastAPI required.

Initialises DuckDB + simulator directly inside the Streamlit process.
On first run: downloads Jan–Mar 2023 parquet (~150 MB) and builds taxi.db.
Subsequent runs: skips download, starts simulator immediately.

Run locally:  CLOUD=1 streamlit run dashboard/standalone.py
Deploy:       point Streamlit Community Cloud at dashboard/standalone.py
"""
import os
import sys
import threading
import time

import duckdb
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ── path setup ────────────────────────────────────────────────────────────────
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import dashboard.db as db
from simulator.stream_sim import SimulatorThread

# ── paths ─────────────────────────────────────────────────────────────────────
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH  = os.path.join(ROOT, "db", "taxi.db")
# Cloud mode: 3 months only (Jan–Mar 2023) to keep startup fast
CLOUD_MONTHS = [1, 2, 3]
TLC_BASE     = "https://d37ci6vzurychx.cloudfront.net/trip-data"
ZONE_URL     = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv"

BOROUGHS = ["All", "Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "EWR"]
MONTHS   = {
    "All": "All", "January": "1", "February": "2", "March": "3",
}
DARK = dict(plot_bgcolor="#0e1117", paper_bgcolor="#0e1117", font_color="#fafafa")

REFRESH_MS = 5000


# ── one-time bootstrap (download + load) ──────────────────────────────────────

def _download_file(url: str, dest: str, label: str, progress_bar) -> None:
    if os.path.exists(dest):
        return
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total and progress_bar:
                    progress_bar.progress(downloaded / total, text=label)


def _build_db(progress_text) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    # Download parquets
    bar = st.progress(0, text="Preparing data…")
    for m in CLOUD_MONTHS:
        fname = f"yellow_tripdata_2023-{m:02d}.parquet"
        dest  = os.path.join(DATA_DIR, fname)
        _download_file(f"{TLC_BASE}/{fname}", dest, f"Downloading {fname}…", bar)

    bar.progress(0.9, text="Downloading zone lookup…")
    _download_file(ZONE_URL, os.path.join(DATA_DIR, "taxi_zone_lookup.csv"), "Zones…", bar)

    bar.progress(0.95, text="Building database (takes ~60s)…")
    import glob
    parquet_glob = os.path.join(DATA_DIR, "yellow_tripdata_2023-0[123].parquet")
    zone_csv     = os.path.join(DATA_DIR, "taxi_zone_lookup.csv")

    con = duckdb.connect(DB_PATH)
    con.execute("PRAGMA memory_limit='2GB'")
    con.execute("PRAGMA threads=2")

    con.execute("DROP TABLE IF EXISTS trips")
    con.execute(f"""
        CREATE TABLE trips AS
        SELECT
            row_number() OVER (ORDER BY tpep_pickup_datetime) AS trip_id,
            tpep_pickup_datetime  AS pickup_ts,
            tpep_dropoff_datetime AS dropoff_ts,
            PULocationID          AS pickup_zone,
            DOLocationID          AS dropoff_zone,
            CAST(fare_amount    AS DOUBLE) AS fare_amount,
            CAST(trip_distance  AS DOUBLE) AS trip_distance,
            (epoch_ms(tpep_dropoff_datetime) - epoch_ms(tpep_pickup_datetime)) AS duration_ms
        FROM read_parquet('{parquet_glob}')
        WHERE fare_amount > 0
          AND trip_distance > 0
          AND tpep_pickup_datetime >= '2023-01-01'
          AND tpep_pickup_datetime <  '2023-04-01'
          AND tpep_dropoff_datetime > tpep_pickup_datetime
          AND (epoch_ms(tpep_dropoff_datetime) - epoch_ms(tpep_pickup_datetime))
              BETWEEN 60000 AND 7200000
    """)
    con.execute("DROP TABLE IF EXISTS active_trips")
    con.execute("""
        CREATE TABLE active_trips (
            trip_id BIGINT PRIMARY KEY, pickup_ts TIMESTAMP,
            dropoff_ts TIMESTAMP, pickup_zone INTEGER, fare_amount DOUBLE
        )
    """)
    con.execute("DROP TABLE IF EXISTS metrics_log")
    con.execute("""
        CREATE TABLE metrics_log (
            ts TIMESTAMP, started INTEGER, completed INTEGER, revenue DOUBLE
        )
    """)
    if os.path.exists(zone_csv):
        con.execute("DROP TABLE IF EXISTS zones")
        con.execute(f"""
            CREATE TABLE zones AS
            SELECT LocationID AS zone_id, Borough AS borough,
                   Zone AS zone_name, service_zone
            FROM read_csv_auto('{zone_csv}')
        """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_trips_pickup ON trips(pickup_ts)")
    con.close()
    bar.progress(1.0, text="Database ready!")
    time.sleep(0.5)
    bar.empty()


# ── shared DuckDB connection + simulator (one per server process) ──────────────

@st.cache_resource
def _init_engine():
    """Download data if needed, build DB, start simulator. Runs once per deploy."""
    if not os.path.exists(DB_PATH):
        _build_db("Building database…")

    lock = threading.RLock()
    con  = duckdb.connect(DB_PATH)
    con.execute("PRAGMA memory_limit='2GB'")

    sim = SimulatorThread(con, lock, speed=60)
    sim.start()
    return con, lock, sim


# ── dark layout helper ────────────────────────────────────────────────────────

def dark_layout(**kwargs):
    return dict(**DARK, margin=dict(l=0, r=0, t=30, b=0), **kwargs)


# ── page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="NYC Taxi Analytics",
    page_icon="🚕",
    layout="wide",
)

# Initialise engine (shows spinner on first cold start)
with st.spinner("Initialising… first run downloads ~150 MB and builds the database (~60s). Grab a coffee!"):
    con, lock, sim = _init_engine()

st_autorefresh(interval=REFRESH_MS, key="refresh")

# ── tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2 = st.tabs(["Live Operations", "Historical Intelligence"])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — LIVE OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════

with tab1:
    with st.sidebar:
        st.header("Live Filters")
        live_borough = st.selectbox("Borough", BOROUGHS, key="live_borough")
        time_window  = st.select_slider(
            "History window", options=[5, 10, 15, 30], value=10,
            format_func=lambda x: f"{x} min", key="time_window",
        )
        st.divider()
        st.header("Diagnostics")

    st.title("🚕 NYC Taxi — Live Operations")
    st.caption(
        "Simulating 2023 NYC Yellow Taxi data at 60× speed · "
        f"Auto-refreshes every {REFRESH_MS // 1000}s · DuckDB + Streamlit (cloud)"
    )

    # ── fetch live data ────────────────────────────────────────────────────────
    t0       = time.perf_counter()
    realtime = db.query_realtime(con, lock)
    realtime["query_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    api_latency = realtime["query_ms"]

    hist_rows = db.query_historical(con, lock, minutes=time_window)
    hist_df   = pd.DataFrame(hist_rows)

    zone_rows = db.query_zones_top(con, lock, limit=10,
                                   borough=live_borough if live_borough != "All" else None)
    zone_df   = pd.DataFrame(zone_rows)

    # ── KPI cards ──────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active Trips",   f"{realtime['active_trips']:,}")
    c2.metric("Trips / min",    f"{realtime['trips_per_min']:,}")
    c3.metric("Revenue / min",  f"${realtime['revenue_per_min']:,.2f}")
    c4.metric("Avg Duration",   f"{realtime['avg_duration_min']:.1f} min")
    st.divider()

    # ── charts ─────────────────────────────────────────────────────────────────
    left, right = st.columns([3, 2])
    with left:
        st.subheader(f"Trips/min — Last {time_window} minutes")
        if not hist_df.empty:
            hist_df["bucket"] = pd.to_datetime(hist_df["bucket"])
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=hist_df["bucket"], y=hist_df["trips_started"],
                mode="lines+markers", name="Started", line=dict(color="#f5c518", width=2)))
            fig.add_trace(go.Scatter(x=hist_df["bucket"], y=hist_df["trips_completed"],
                mode="lines+markers", name="Completed",
                line=dict(color="#17becf", width=2, dash="dot")))
            fig.update_layout(**dark_layout(height=300),
                              legend=dict(orientation="h", y=1.1), yaxis_title="trips")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Waiting for simulation data…")

    with right:
        label = live_borough if live_borough != "All" else "All Boroughs"
        st.subheader(f"Top Pickup Zones — {label}")
        if not zone_df.empty:
            fig2 = go.Figure(go.Bar(x=zone_df["trip_count"], y=zone_df["zone_name"],
                orientation="h", marker_color="#f5c518"))
            fig2.update_layout(**dark_layout(height=300), xaxis_title="active trips",
                               yaxis=dict(autorange="reversed"))
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("No active trips yet…")

    if not hist_df.empty and "revenue" in hist_df.columns:
        st.subheader(f"Revenue / min — Last {time_window} minutes")
        fig3 = go.Figure(go.Scatter(x=hist_df["bucket"], y=hist_df["revenue"],
            fill="tozeroy", mode="lines", line=dict(color="#2ecc71", width=2)))
        fig3.update_layout(**dark_layout(height=150), yaxis_title="$")
        st.plotly_chart(fig3, use_container_width=True)

    with st.sidebar:
        st.metric("DuckDB query", f"{api_latency:.1f} ms")
        st.caption(f"Last tick: {realtime.get('last_tick', '—')}")
        if not zone_df.empty:
            st.caption(f"Top zone: {zone_df.iloc[0]['zone_name']} ({zone_df.iloc[0]['trip_count']} trips)")
        st.divider()
        st.caption("Stack: DuckDB · Streamlit Cloud")
        st.caption("Data: NYC TLC Yellow Taxi 2023 (Jan–Mar)")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — HISTORICAL INTELLIGENCE
# ═══════════════════════════════════════════════════════════════════════════════

with tab2:
    st.title("NYC Taxi — Historical Intelligence")
    st.caption("~9M trips · Jan–Mar 2023 · Queries run directly on DuckDB")

    with st.sidebar:
        st.header("Analysis Filters")
        h_borough = st.selectbox("Borough", BOROUGHS, key="h_borough")
        h_month   = st.selectbox("Month", list(MONTHS.keys()), key="h_month")
        st.divider()
        st.caption("Charts cache on first load. Changing filters re-queries.")

    month_val = MONTHS[h_month]

    # ── 1. When is demand highest? ─────────────────────────────────────────────
    st.subheader("When is demand highest?")
    hourly_rows = db.query_hourly(con, lock, borough=h_borough)
    hourly_df   = pd.DataFrame(hourly_rows)
    if not hourly_df.empty:
        fig_h = go.Figure()
        fig_h.add_trace(go.Scatter(x=hourly_df["hour"], y=hourly_df["weekday_trips"],
            mode="lines+markers", name="Weekday", line=dict(color="#f5c518", width=2)))
        fig_h.add_trace(go.Scatter(x=hourly_df["hour"], y=hourly_df["weekend_trips"],
            mode="lines+markers", name="Weekend",
            line=dict(color="#e74c3c", width=2, dash="dot")))
        fig_h.update_layout(**dark_layout(height=300),
            xaxis=dict(title="Hour of day", tickmode="array",
                       tickvals=list(range(0, 24, 2)),
                       ticktext=[f"{h:02d}:00" for h in range(0, 24, 2)]),
            yaxis_title="total trips", legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig_h, use_container_width=True)
        st.caption("Weekday peaks: 8–9am + 5–7pm. Weekend demand builds late and stays elevated through midnight.")

    st.divider()

    # ── 2. Which borough drives revenue? ──────────────────────────────────────
    st.subheader("Which borough drives revenue?")
    borough_rows = db.query_borough_breakdown(con, lock)
    borough_df   = pd.DataFrame(borough_rows)
    if not borough_df.empty:
        display_df = borough_df if h_borough == "All" else borough_df[borough_df["borough"] == h_borough]
        col_a, col_b = st.columns(2)
        with col_a:
            fig_b1 = go.Figure(go.Bar(x=display_df["borough"], y=display_df["trips"],
                marker_color="#f5c518"))
            fig_b1.update_layout(**dark_layout(height=280), yaxis_title="total trips",
                                 title="Total Trips by Borough")
            st.plotly_chart(fig_b1, use_container_width=True)
        with col_b:
            fig_b2 = go.Figure()
            fig_b2.add_trace(go.Bar(x=display_df["borough"], y=display_df["revenue"],
                marker_color="#2ecc71", name="Revenue ($)"))
            fig_b2.add_trace(go.Scatter(x=display_df["borough"], y=display_df["avg_fare_per_mile"],
                mode="lines+markers", name="Avg $/mile",
                line=dict(color="#e74c3c", width=2), yaxis="y2"))
            fig_b2.update_layout(**dark_layout(height=280), title="Revenue & Fare Efficiency",
                yaxis=dict(title="revenue ($)"),
                yaxis2=dict(title="avg $/mile", overlaying="y", side="right"),
                legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig_b2, use_container_width=True)

    st.divider()

    # ── 3. What does a typical fare look like? ────────────────────────────────
    st.subheader("What does a typical fare look like?")
    fare_rows = db.query_fare_buckets(con, lock, borough=h_borough, month=month_val)
    fare_df   = pd.DataFrame(fare_rows)
    if not fare_df.empty:
        fig_f = go.Figure(go.Bar(x=fare_df["bucket"], y=fare_df["count"],
            marker_color="#9b59b6"))
        fig_f.update_layout(**dark_layout(height=280),
                            xaxis_title="fare range", yaxis_title="number of trips")
        st.plotly_chart(fig_f, use_container_width=True)
        peak_row = fare_df.loc[fare_df["count"].idxmax()]
        st.caption(f"Most trips: **{peak_row['bucket']}** ({peak_row['count']:,} trips). "
                   "Short city rides dominate; airport runs skew the average up.")

    st.divider()

    # ── 4. What are the busiest routes? ───────────────────────────────────────
    st.subheader("What are the busiest routes?")
    routes_rows = db.query_top_routes(con, lock, limit=15)
    routes_df   = pd.DataFrame(routes_rows)
    if not routes_df.empty:
        display_routes = (
            routes_df if h_borough == "All"
            else routes_df[routes_df["pickup_borough"] == h_borough]
        ).head(10)[["pickup_zone", "dropoff_zone", "trip_count"]].copy()
        display_routes.columns = ["Pickup Zone", "Dropoff Zone", "Trip Count"]
        display_routes["Trip Count"] = display_routes["Trip Count"].apply(lambda x: f"{x:,}")
        st.dataframe(display_routes, use_container_width=True, hide_index=True)
        st.caption("Airport + Midtown corridors dominate. Fixed geography = predictable demand.")

    st.divider()

    # ── 5. Where should drivers reposition? ──────────────────────────────────
    st.subheader("Where should drivers reposition?")
    st.caption("Zones above the diagonal: more dropoffs than pickups → underserved, high opportunity.")
    opp_rows = db.query_opportunity_zones(con, lock, borough=h_borough)
    opp_df   = pd.DataFrame(opp_rows)
    if not opp_df.empty:
        max_val = max(opp_df["pickup_count"].max(), opp_df["dropoff_count"].max())
        fig_o = go.Figure()
        fig_o.add_trace(go.Scatter(
            x=opp_df["pickup_count"], y=opp_df["dropoff_count"],
            mode="markers", text=opp_df["zone_name"],
            marker=dict(size=10, color=opp_df["imbalance"], colorscale="RdYlGn",
                        showscale=True, colorbar=dict(title="Imbalance<br>(drop−pick)", thickness=12)),
            hovertemplate="<b>%{text}</b><br>Pickups: %{x:,}<br>Dropoffs: %{y:,}<extra></extra>",
        ))
        fig_o.add_trace(go.Scatter(x=[0, max_val], y=[0, max_val], mode="lines",
            line=dict(color="#555", dash="dash", width=1), showlegend=False))
        fig_o.update_layout(**dark_layout(height=400),
            xaxis_title="Pickups (supply)", yaxis_title="Dropoffs (demand incoming)")
        st.plotly_chart(fig_o, use_container_width=True)
        top_opp = opp_df.iloc[0]
        st.caption(
            f"**Top opportunity:** {top_opp['zone_name']} — "
            f"{top_opp['dropoff_count']:,} dropoffs vs {top_opp['pickup_count']:,} pickups "
            f"({top_opp['imbalance']:,} unmatched)."
        )

    st.divider()
    st.caption(
        "**What I'd build next:** Driver dispatch recommendation engine using opportunity-zone "
        "imbalance as signal · Surge pricing predictor (time × borough) · MTA subway integration."
    )
