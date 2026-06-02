"""
Streamlit dashboard — polls FastAPI every 5 seconds.

Tab 1 — Live Operations:
  KPI cards · trips/min line · top zones bar · revenue sparkline
  Sidebar filters: borough · time window

Tab 2 — Historical Intelligence:
  24h demand · borough breakdown · fare distribution · top routes · opportunity zones
  Sidebar filters: borough · month · hour range · fare range
"""
import time
import requests
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

API_BASE   = "http://localhost:8001"
REFRESH_MS = 5000

BOROUGHS = ["All", "Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "EWR"]
MONTHS   = {
    "All": "All", "January": "1", "February": "2", "March": "3",
    "April": "4", "May": "5", "June": "6",
}
DARK = dict(plot_bgcolor="#0e1117", paper_bgcolor="#0e1117", font_color="#fafafa")

st.set_page_config(
    page_title="NYC Taxi Analytics",
    page_icon="🚕",
    layout="wide",
)

# ── auto-refresh (Tab 1 only, via key) ────────────────────────────────────────
st_autorefresh(interval=REFRESH_MS, key="refresh")


# ── fetch helpers (Live tab) ──────────────────────────────────────────────────

@st.cache_data(ttl=4)
def fetch_realtime():
    try:
        t0 = time.perf_counter()
        r = requests.get(f"{API_BASE}/metrics/realtime", timeout=4)
        latency = round((time.perf_counter() - t0) * 1000)
        r.raise_for_status()
        return r.json(), latency, None
    except Exception as e:
        return None, None, str(e)


@st.cache_data(ttl=4)
def fetch_historical(minutes: int = 10):
    try:
        r = requests.get(f"{API_BASE}/metrics/historical",
                         params={"minutes": minutes}, timeout=4)
        r.raise_for_status()
        return pd.DataFrame(r.json()), None
    except Exception as e:
        return pd.DataFrame(), str(e)


@st.cache_data(ttl=4)
def fetch_zones(limit: int = 10, borough: str = "All"):
    try:
        params = {"limit": limit}
        if borough != "All":
            params["borough"] = borough
        r = requests.get(f"{API_BASE}/zones/top", params=params, timeout=4)
        r.raise_for_status()
        return pd.DataFrame(r.json()), None
    except Exception as e:
        return pd.DataFrame(), str(e)


# ── fetch helpers (Historical tab, cached aggressively) ───────────────────────

@st.cache_data(ttl=3600)
def fetch_hourly(borough: str = "All"):
    try:
        r = requests.get(f"{API_BASE}/analysis/hourly",
                         params={"borough": borough}, timeout=30)
        r.raise_for_status()
        return pd.DataFrame(r.json()), None
    except Exception as e:
        return pd.DataFrame(), str(e)


@st.cache_data(ttl=3600)
def fetch_borough():
    try:
        r = requests.get(f"{API_BASE}/analysis/borough", timeout=30)
        r.raise_for_status()
        return pd.DataFrame(r.json()), None
    except Exception as e:
        return pd.DataFrame(), str(e)


@st.cache_data(ttl=3600)
def fetch_routes(limit: int = 10):
    try:
        r = requests.get(f"{API_BASE}/analysis/routes",
                         params={"limit": limit}, timeout=30)
        r.raise_for_status()
        return pd.DataFrame(r.json()), None
    except Exception as e:
        return pd.DataFrame(), str(e)


@st.cache_data(ttl=3600)
def fetch_fare_buckets(borough: str = "All", month: str = "All"):
    try:
        r = requests.get(f"{API_BASE}/analysis/fare-buckets",
                         params={"borough": borough, "month": month}, timeout=30)
        r.raise_for_status()
        return pd.DataFrame(r.json()), None
    except Exception as e:
        return pd.DataFrame(), str(e)


@st.cache_data(ttl=3600)
def fetch_opportunity(borough: str = "All"):
    try:
        r = requests.get(f"{API_BASE}/analysis/opportunity-zones",
                         params={"borough": borough}, timeout=30)
        r.raise_for_status()
        return pd.DataFrame(r.json()), None
    except Exception as e:
        return pd.DataFrame(), str(e)


# ── helper: dark plotly layout ────────────────────────────────────────────────

def dark_layout(**kwargs):
    return dict(**DARK, margin=dict(l=0, r=0, t=30, b=0), **kwargs)


# ── tabs ──────────────────────────────────────────────────────────────────────

tab1, tab2 = st.tabs(["Live Operations", "Historical Intelligence"])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — LIVE OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════

with tab1:
    # Sidebar filters (scoped to live tab)
    with st.sidebar:
        st.header("Live Filters")
        live_borough = st.selectbox("Borough", BOROUGHS, key="live_borough")
        time_window  = st.select_slider(
            "History window",
            options=[5, 10, 15, 30],
            value=10,
            format_func=lambda x: f"{x} min",
            key="time_window",
        )
        st.divider()
        st.header("Diagnostics")

    # ── fetch ──────────────────────────────────────────────────────────────────
    realtime, api_latency, rt_err = fetch_realtime()
    hist_df,  hist_err            = fetch_historical(minutes=time_window)
    zone_df,  zone_err            = fetch_zones(limit=10, borough=live_borough)

    st.title("🚕 NYC Taxi — Live Operations")
    st.caption(
        f"Simulating 2023 NYC Yellow Taxi data at 60× speed · "
        f"Auto-refreshes every {REFRESH_MS // 1000}s · DuckDB + FastAPI"
    )

    # ── KPI cards ──────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    if realtime:
        c1.metric("Active Trips",     f"{realtime['active_trips']:,}")
        c2.metric("Trips / min",       f"{realtime['trips_per_min']:,}")
        c3.metric("Revenue / min",     f"${realtime['revenue_per_min']:,.2f}")
        c4.metric("Avg Duration",      f"{realtime['avg_duration_min']:.1f} min")
    else:
        for col, label in zip([c1, c2, c3, c4],
                              ["Active Trips", "Trips/min", "Revenue/min", "Avg Duration"]):
            col.metric(label, "—")
        st.warning(f"API unavailable: {rt_err}")

    st.divider()

    # ── Row 2: line + bar ──────────────────────────────────────────────────────
    left, right = st.columns([3, 2])

    with left:
        label = live_borough if live_borough != "All" else "All Boroughs"
        st.subheader(f"Trips/min — Last {time_window} minutes")
        if not hist_df.empty and "bucket" in hist_df.columns:
            hist_df["bucket"] = pd.to_datetime(hist_df["bucket"])
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=hist_df["bucket"], y=hist_df["trips_started"],
                mode="lines+markers", name="Started",
                line=dict(color="#f5c518", width=2),
            ))
            fig.add_trace(go.Scatter(
                x=hist_df["bucket"], y=hist_df["trips_completed"],
                mode="lines+markers", name="Completed",
                line=dict(color="#17becf", width=2, dash="dot"),
            ))
            fig.update_layout(
                **dark_layout(height=300),
                legend=dict(orientation="h", y=1.1),
                yaxis_title="trips",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Waiting for data..." if not hist_err else f"Error: {hist_err}")

    with right:
        st.subheader(f"Top Pickup Zones — {label}")
        if not zone_df.empty:
            fig2 = go.Figure(go.Bar(
                x=zone_df["trip_count"],
                y=zone_df["zone_name"],
                orientation="h",
                marker_color="#f5c518",
            ))
            fig2.update_layout(
                **dark_layout(height=300),
                xaxis_title="active trips",
                yaxis=dict(autorange="reversed"),
            )
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("Waiting for zone data..." if not zone_err else f"Error: {zone_err}")

    # ── Row 3: revenue sparkline ───────────────────────────────────────────────
    if not hist_df.empty and "revenue" in hist_df.columns:
        st.subheader(f"Revenue / min — Last {time_window} minutes")
        fig3 = go.Figure(go.Scatter(
            x=hist_df["bucket"], y=hist_df["revenue"],
            fill="tozeroy", mode="lines",
            line=dict(color="#2ecc71", width=2),
        ))
        fig3.update_layout(**dark_layout(height=150), yaxis_title="$")
        st.plotly_chart(fig3, use_container_width=True)

    # ── Sidebar diagnostics ────────────────────────────────────────────────────
    with st.sidebar:
        if realtime:
            st.metric("API latency",   f"{api_latency} ms")
            st.metric("DuckDB query",  f"{realtime.get('query_ms', '—')} ms")
            st.caption(f"Last tick: {realtime.get('last_tick', '—')}")
        if zone_df is not None and not zone_df.empty:
            st.caption(
                f"Top zone: {zone_df.iloc[0]['zone_name']} "
                f"({zone_df.iloc[0]['trip_count']} trips)"
            )
        st.divider()
        st.caption("Stack: DuckDB · FastAPI · Streamlit")
        st.caption("Data: NYC TLC Yellow Taxi 2023 (Jan–Jun)")
        st.caption(f"Refresh: every {REFRESH_MS // 1000}s")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — HISTORICAL INTELLIGENCE
# ═══════════════════════════════════════════════════════════════════════════════

with tab2:
    st.title("NYC Taxi — Historical Intelligence")
    st.caption("18.9M trips · Jan–Jun 2023 · Queries run directly on DuckDB")

    # ── sidebar filters ────────────────────────────────────────────────────────
    with st.sidebar:
        st.header("Analysis Filters")
        h_borough = st.selectbox("Borough", BOROUGHS, key="h_borough")
        h_month   = st.selectbox("Month", list(MONTHS.keys()), key="h_month")
        st.divider()
        st.caption("Charts cache on first load (~5s). Changing filters re-queries.")

    month_val = MONTHS[h_month]

    # ── 1. When is demand highest? ─────────────────────────────────────────────
    st.subheader("When is demand highest?")
    hourly_df, hourly_err = fetch_hourly(borough=h_borough)
    if not hourly_df.empty:
        fig_h = go.Figure()
        fig_h.add_trace(go.Scatter(
            x=hourly_df["hour"], y=hourly_df["weekday_trips"],
            mode="lines+markers", name="Weekday",
            line=dict(color="#f5c518", width=2),
        ))
        fig_h.add_trace(go.Scatter(
            x=hourly_df["hour"], y=hourly_df["weekend_trips"],
            mode="lines+markers", name="Weekend",
            line=dict(color="#e74c3c", width=2, dash="dot"),
        ))
        fig_h.update_layout(
            **dark_layout(height=300),
            xaxis=dict(
                title="Hour of day",
                tickmode="array",
                tickvals=list(range(0, 24, 2)),
                ticktext=[f"{h:02d}:00" for h in range(0, 24, 2)],
            ),
            yaxis_title="total trips (Jan–Jun 2023)",
            legend=dict(orientation="h", y=1.1),
        )
        st.plotly_chart(fig_h, use_container_width=True)
        st.caption(
            "Weekday demand peaks sharply in the morning (8–9am) and evening (5–7pm) "
            "rush hours. Weekend demand builds later and stays elevated through midnight."
        )
    else:
        st.info(f"Loading... {hourly_err or ''}")

    st.divider()

    # ── 2. Which borough drives revenue? ──────────────────────────────────────
    st.subheader("Which borough drives revenue?")
    borough_df, borough_err = fetch_borough()
    if not borough_df.empty:
        # Filter to selected borough if not "All"
        display_df = borough_df if h_borough == "All" else borough_df[borough_df["borough"] == h_borough]
        col_a, col_b = st.columns(2)

        with col_a:
            fig_b1 = go.Figure(go.Bar(
                x=display_df["borough"],
                y=display_df["trips"],
                marker_color="#f5c518",
                name="Trips",
            ))
            fig_b1.update_layout(**dark_layout(height=280), yaxis_title="total trips",
                                 title="Total Trips by Borough")
            st.plotly_chart(fig_b1, use_container_width=True)

        with col_b:
            fig_b2 = go.Figure()
            fig_b2.add_trace(go.Bar(
                x=display_df["borough"], y=display_df["revenue"],
                marker_color="#2ecc71", name="Revenue ($)",
            ))
            fig_b2.add_trace(go.Scatter(
                x=display_df["borough"], y=display_df["avg_fare_per_mile"],
                mode="lines+markers", name="Avg $/mile",
                line=dict(color="#e74c3c", width=2),
                yaxis="y2",
            ))
            fig_b2.update_layout(
                **dark_layout(height=280),
                title="Revenue & Fare Efficiency",
                yaxis=dict(title="revenue ($)"),
                yaxis2=dict(title="avg $/mile", overlaying="y", side="right"),
                legend=dict(orientation="h", y=1.1),
            )
            st.plotly_chart(fig_b2, use_container_width=True)
    else:
        st.info(f"Loading... {borough_err or ''}")

    st.divider()

    # ── 3. What does a typical fare look like? ────────────────────────────────
    st.subheader("What does a typical fare look like?")
    fare_df, fare_err = fetch_fare_buckets(borough=h_borough, month=month_val)
    if not fare_df.empty:
        fig_f = go.Figure(go.Bar(
            x=fare_df["bucket"],
            y=fare_df["count"],
            marker_color="#9b59b6",
        ))
        fig_f.update_layout(
            **dark_layout(height=280),
            xaxis_title="fare range",
            yaxis_title="number of trips",
        )
        st.plotly_chart(fig_f, use_container_width=True)
        peak_row = fare_df.loc[fare_df["count"].idxmax()]
        st.caption(
            f"Most trips fall in the **{peak_row['bucket']}** range "
            f"({peak_row['count']:,} trips). "
            "Short city rides dominate — long airport runs skew the average upward."
        )
    else:
        st.info(f"Loading... {fare_err or ''}")

    st.divider()

    # ── 4. What are the busiest routes? ───────────────────────────────────────
    st.subheader("What are the busiest routes?")
    routes_df, routes_err = fetch_routes(limit=15)
    if not routes_df.empty:
        display_routes = (
            routes_df if h_borough == "All"
            else routes_df[routes_df["pickup_borough"] == h_borough]
        ).head(10)
        display_routes = display_routes[["pickup_zone", "dropoff_zone", "trip_count"]].copy()
        display_routes.columns = ["Pickup Zone", "Dropoff Zone", "Trip Count"]
        display_routes["Trip Count"] = display_routes["Trip Count"].apply(lambda x: f"{x:,}")
        st.dataframe(display_routes, use_container_width=True, hide_index=True)
        st.caption(
            "Airport routes (JFK, LaGuardia) and Midtown corridors dominate. "
            "High volume + fixed geography = predictable demand — ideal for pre-positioning."
        )
    else:
        st.info(f"Loading... {routes_err or ''}")

    st.divider()

    # ── 5. Where should drivers reposition? ──────────────────────────────────
    st.subheader("Where should drivers reposition?")
    st.caption(
        "Zones with many **dropoffs but few pickups** are underserved — "
        "drivers completing trips there face a long wait for the next fare."
    )
    opp_df, opp_err = fetch_opportunity(borough=h_borough)
    if not opp_df.empty:
        fig_o = go.Figure(go.Scatter(
            x=opp_df["pickup_count"],
            y=opp_df["dropoff_count"],
            mode="markers",
            text=opp_df["zone_name"],
            marker=dict(
                size=10,
                color=opp_df["imbalance"],
                colorscale="RdYlGn",
                showscale=True,
                colorbar=dict(title="Imbalance<br>(dropoff−pickup)", thickness=12),
            ),
            hovertemplate=(
                "<b>%{text}</b><br>"
                "Pickups: %{x:,}<br>"
                "Dropoffs: %{y:,}<br>"
                "<extra></extra>"
            ),
        ))
        # diagonal reference line
        max_val = max(opp_df["pickup_count"].max(), opp_df["dropoff_count"].max())
        fig_o.add_trace(go.Scatter(
            x=[0, max_val], y=[0, max_val],
            mode="lines",
            line=dict(color="#555", dash="dash", width=1),
            showlegend=False,
        ))
        fig_o.update_layout(
            **dark_layout(height=400),
            xaxis_title="Pickups (supply of passengers)",
            yaxis_title="Dropoffs (incoming passengers)",
            annotations=[dict(
                x=0.02, y=0.98, xref="paper", yref="paper",
                text="↑ Above line = more dropoffs than pickups (opportunity zone)",
                showarrow=False, font=dict(color="#aaa", size=11),
            )],
        )
        st.plotly_chart(fig_o, use_container_width=True)
        top_opp = opp_df.iloc[0]
        st.caption(
            f"**Top opportunity zone:** {top_opp['zone_name']} — "
            f"{top_opp['dropoff_count']:,} dropoffs vs {top_opp['pickup_count']:,} pickups "
            f"({top_opp['imbalance']:,} unmatched passengers)."
        )
    else:
        st.info(f"Loading... {opp_err or ''}")

    st.divider()
    st.caption(
        "**What I'd build next:** A real-time driver dispatch recommendation engine using "
        "opportunity-zone imbalance as the signal · Surge pricing predictor (time-of-day "
        "× borough demand) · MTA subway integration to find transit-gap zones."
    )
