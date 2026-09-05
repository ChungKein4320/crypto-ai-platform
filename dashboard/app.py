"""
dashboard/app.py — Local Streamlit dashboard for CAA-2.

Reads from db/local.db in strict READ-ONLY mode.
Displays BTC/USDT 5m candlestick chart, volume, latest observed
price, candle state, and data freshness status.

Does NOT:
  - Call the Binance API
  - INSERT / UPDATE / DELETE anything in the database
  - Modify any file on disk
"""

import sqlite3
from datetime import datetime, timezone

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

# Paths and market identity (fixed for CAA-2)
DB_PATH       = "db/local.db"
EXCHANGE      = "binance"
SYMBOL        = "BTC/USDT"
TIMEFRAME     = "5m"

# Data freshness threshold.
# DATA FRESHNESS STATUS only — not a full collector health monitoring system.
# LIVE  : age of latest candle ≤ this many minutes
# OFFLINE: age > this threshold
LIVE_THRESHOLD_MINUTES = 15


# ------------------------------------------------------------------
# Database — READ-ONLY helpers
# ------------------------------------------------------------------

def _get_connection() -> sqlite3.Connection:
    """
    Open db/local.db using the SQLite URI with mode=ro (read-only).
    Any write attempt (INSERT/UPDATE/DELETE/CREATE) raises OperationalError.
    """
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def read_latest_candle() -> dict | None:
    """
    Return the single most-recent candle for the configured market,
    or None if the database contains no rows yet.
    """
    conn = _get_connection()
    try:
        row = conn.execute(
            """
            SELECT timestamp, open, high, low, close, volume, is_closed
            FROM   ohlcv_5m
            WHERE  exchange  = ?
              AND  symbol    = ?
              AND  timeframe = ?
            ORDER  BY timestamp DESC
            LIMIT  1
            """,
            (EXCHANGE, SYMBOL, TIMEFRAME),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def read_ohlcv_history() -> list[dict]:
    """
    Return all candles for the configured market ordered oldest → newest (for charting).
    Passing datetime objects directly to Plotly avoids string parsing overhead.
    """
    conn = _get_connection()
    try:
        rows = conn.execute(
            """
            SELECT timestamp, open, high, low, close, volume, is_closed
            FROM   ohlcv_5m
            WHERE  exchange  = ?
              AND  symbol    = ?
              AND  timeframe = ?
            ORDER  BY timestamp ASC
            """,
            (EXCHANGE, SYMBOL, TIMEFRAME),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def ts_to_dt(ts_ms: int) -> datetime:
    """Convert Unix milliseconds (integer) to a UTC-aware datetime object."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def compute_freshness(latest_ts_ms: int) -> tuple[str, float]:
    """
    Compute DATA FRESHNESS STATUS from the age of the latest stored candle.

    This is NOT collector health monitoring — it only measures how old
    the most recently stored candle is relative to right now.

    Returns:
        (label, age_minutes)
    """
    now_ms      = datetime.now(tz=timezone.utc).timestamp() * 1000
    age_minutes = (now_ms - latest_ts_ms) / 60_000
    label = "🟢 LIVE" if age_minutes <= LIVE_THRESHOLD_MINUTES else "🔴 OFFLINE"
    return label, age_minutes


def build_chart(rows: list[dict]) -> go.Figure:
    """
    Build a two-row Plotly figure:
      Row 1 — Candlestick  (70 % height)
      Row 2 — Volume bars  (30 % height)

    Shared X-axis. Datetime objects are passed to Plotly directly
    (no pre-conversion to string).
    """
    if not rows:
        return go.Figure()

    # Convert timestamps once; reuse for both traces
    dt_x   = [ts_to_dt(r["timestamp"]) for r in rows]
    opens  = [r["open"]   for r in rows]
    highs  = [r["high"]   for r in rows]
    lows   = [r["low"]    for r in rows]
    closes = [r["close"]  for r in rows]
    vols   = [r["volume"] for r in rows]

    # Volume bar colour matches candle direction
    vol_colors = [
        "#26a69a" if closes[i] >= opens[i] else "#ef5350"
        for i in range(len(rows))
    ]

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.70, 0.30],
        vertical_spacing=0.03,
    )

    # Candlestick (row 1)
    fig.add_trace(
        go.Candlestick(
            x=dt_x,
            open=opens, high=highs, low=lows, close=closes,
            name="OHLC",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        ),
        row=1, col=1,
    )

    # Volume (row 2)
    fig.add_trace(
        go.Bar(
            x=dt_x, y=vols,
            name="Volume",
            marker_color=vol_colors,
            showlegend=False,
        ),
        row=2, col=1,
    )

    fig.update_layout(
        xaxis_rangeslider_visible=False,
        height=580,
        margin=dict(l=10, r=10, t=10, b=10),
        template="plotly_dark",
        legend=dict(orientation="h", y=1.02, x=0),
    )
    fig.update_yaxes(title_text="Price (USDT)", row=1, col=1)
    fig.update_yaxes(title_text="Volume (BTC)",  row=2, col=1)

    return fig


# ------------------------------------------------------------------
# Auto-refreshing content fragment
# ------------------------------------------------------------------

@st.fragment(run_every=30)
def dashboard_content() -> None:
    """
    All live content lives inside this @st.fragment so Streamlit
    re-runs only this function every 30 seconds — without blocking
    the main thread and without time.sleep().
    """
    latest  = read_latest_candle()

    if latest is None:
        st.warning(
            "⚠️ No candles found in the database. "
            "Run `python src/collector.py` first.",
            icon=None,
        )
        return

    history = read_ohlcv_history()

    # ── Freshness / status ──────────────────────────────────────────
    # DATA FRESHNESS STATUS only — not full collector health monitoring.
    freshness_label, age_min = compute_freshness(latest["timestamp"])
    latest_dt    = ts_to_dt(latest["timestamp"])
    candle_state = "OPEN 🕐" if latest["is_closed"] == 0 else "CLOSED ✓"

    # ── Metrics row ─────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(
            label="Latest Observed Price (USDT)",
            value=f"${latest['close']:,.2f}",
        )
    with col2:
        st.metric(label="Latest Candle", value=candle_state)
    with col3:
        st.metric(
            label="Data Freshness",
            value=freshness_label,
            help=(
                f"LIVE = latest data is ≤ {LIVE_THRESHOLD_MINUTES} min old. "
                "DATA FRESHNESS STATUS only — not full collector health monitoring."
            ),
        )

    # ── Timestamp / age caption ──────────────────────────────────────
    st.caption(
        f"📅 Latest candle: **{latest_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}**"
        f"  ·  Age: **{age_min:.1f} min**"
        f"  ·  Candles in chart: **{len(history)}**"
        f"  ·  Auto-refreshes every 30 s"
    )

    # ── Open-candle notice ───────────────────────────────────────────
    if latest["is_closed"] == 0:
        st.info(
            "ℹ️ The latest candle is **OPEN** — still forming. "
            "The price shown is the **last observed price** within this "
            "5-minute period, not an official closing price.",
        )

    # ── Chart ────────────────────────────────────────────────────────
    st.plotly_chart(build_chart(history), use_container_width=True)


# ------------------------------------------------------------------
# Page layout (runs once on load; fragment handles periodic refresh)
# ------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="Crypto Dashboard — BTC/USDT",
        page_icon="📈",
        layout="wide",
    )

    st.title("📈 Crypto Market Dashboard")
    st.markdown(
        "**Exchange:** Binance  ·  "
        "**Market:** BTC/USDT  ·  "
        "**Timeframe:** 5m  ·  "
        "**Type:** Spot"
    )
    st.divider()

    dashboard_content()


if __name__ == "__main__":
    main()
