"""
collector.py — Binance 5m BTC/USDT candle collector for CAA-2.

Flow:
    1. Open (or create) local SQLite database via init_db()
    2. Read latest stored timestamp via get_latest_timestamp()
    3. Fetch candles from Binance Public REST API (incremental)
    4. Determine is_closed for each candle
    5. Persist via upsert_candles()
    6. Print run summary to terminal

Run:
    python src/collector.py

No API key required — uses Binance public endpoint only.
"""

import sys
import os
import time
from datetime import datetime, timezone

import requests

# Allow running as a script from any working directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.database import init_db, upsert_candles, get_latest_timestamp

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

# Market identity (cố định cho CAA-2)
EXCHANGE  = "binance"
SYMBOL    = "BTC/USDT"      # Unified symbol (CCXT format)
API_SYMBOL = "BTCUSDT"      # Binance API symbol (không có dấu /)
TIMEFRAME  = "5m"
INTERVAL_MS = 5 * 60 * 1000  # 5 phút = 300,000 ms

# Binance REST endpoint (public, không cần API key)
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

# Số candle fetch trong lần đầu tiên (database còn trống)
INITIAL_LIMIT = 200

# Số candle fetch trong các lần chạy incremental (database đã có data).
# Fetch đủ rộng để cover:
#   - candle hiện tại đang OPEN (cần update)
#   - một vài candle vừa CLOSED sau lần chạy trước
# 10 candles = 50 phút buffer — đủ cho prototype chạy mỗi 5m.
INCREMENTAL_LIMIT = 10

# Database location (bên trong db/, đã được .gitignore)
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "db", "local.db"
)

# Binance API request timeout (giây)
REQUEST_TIMEOUT = 10


# ------------------------------------------------------------------
# Core logic
# ------------------------------------------------------------------

def fetch_klines(
    symbol: str,
    interval: str,
    limit: int,
    start_time_ms: int | None = None,
) -> list[list]:
    """
    Fetch raw klines (candlestick data) from Binance Public REST API.

    Args:
        symbol       : Binance API symbol, e.g. 'BTCUSDT'
        interval     : Binance interval string, e.g. '5m'
        limit        : Number of candles to fetch (max 1000 per Binance docs)
        start_time_ms: If provided, fetch candles with open_time >= start_time_ms.
                       Binance startTime parameter is inclusive.

    Returns:
        List of raw kline arrays. Each element:
            [0]  open_time      (int ms)
            [1]  open           (str)
            [2]  high           (str)
            [3]  low            (str)
            [4]  close          (str)
            [5]  volume         (str)
            [6]  close_time     (int ms)
            [7]  quote_volume, [8] trade_count, ... (unused)

    Raises:
        requests.RequestException on network / HTTP error.
        ValueError on unexpected response format.
    """
    params: dict = {
        "symbol":   symbol,
        "interval": interval,
        "limit":    limit,
    }
    if start_time_ms is not None:
        params["startTime"] = start_time_ms

    resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()  # HTTP error → raises requests.HTTPError

    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Unexpected Binance response type: {type(data)}")

    return data


def parse_candles(raw_klines: list[list], now_ms: int) -> list[dict]:
    """
    Convert raw Binance klines into candle dicts ready for upsert_candles().

    is_closed logic:
        Binance provides close_time at index [6], which is the LAST
        millisecond of the candle period.  A candle is CLOSED when
        the current time has passed that close_time.

            is_closed = 1  if  now_ms > close_time
            is_closed = 0  otherwise  (candle still forming)

    open price is never updated — it is the price at the very start of
    the period and cannot change by definition.  This matches the UPSERT
    logic in database.py which preserves 'open' on conflict.

    Args:
        raw_klines : list of raw kline arrays from Binance
        now_ms     : current UTC time in milliseconds

    Returns:
        List of candle dicts with keys:
            timestamp, open, high, low, close, volume, is_closed
    """
    candles = []
    for k in raw_klines:
        open_time  = int(k[0])
        close_time = int(k[6])   # last ms of candle period

        candles.append({
            "timestamp": open_time,
            "open":      float(k[1]),
            "high":      float(k[2]),
            "low":       float(k[3]),
            "close":     float(k[4]),
            "volume":    float(k[5]),
            "is_closed": 1 if now_ms > close_time else 0,
        })
    return candles


def run_collector() -> dict:
    """
    Main collector entry point. Executes one full ingestion cycle.

    Returns:
        Summary dict with run statistics.
    """
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)

    # --- Step 1: Initialise database ---
    conn = init_db(DB_PATH)

    # --- Step 2: Check latest stored timestamp ---
    latest_ts = get_latest_timestamp(conn, EXCHANGE, SYMBOL, TIMEFRAME)

    # --- Step 3: Decide fetch parameters ---
    if latest_ts is None:
        # First run: database is empty — fetch INITIAL_LIMIT candles
        # No startTime → Binance returns the most recent `limit` candles
        limit      = INITIAL_LIMIT
        start_time = None
        mode       = "initial"
    else:
        # Incremental run: fetch from latest_ts onward.
        #
        # Why re-fetch from latest_ts (not latest_ts + INTERVAL_MS)?
        #   The candle at latest_ts may have been OPEN during the previous
        #   run (is_closed=0). We must re-fetch it so the UPSERT can
        #   transition it to is_closed=1 if it has since closed.
        #   Using startTime=latest_ts ensures that candle is included.
        limit      = INCREMENTAL_LIMIT
        start_time = latest_ts
        mode       = "incremental"

    # --- Step 4: Fetch from Binance ---
    raw_klines = fetch_klines(
        symbol        = API_SYMBOL,
        interval      = TIMEFRAME,
        limit         = limit,
        start_time_ms = start_time,
    )

    if not raw_klines:
        conn.close()
        return {"mode": mode, "fetched": 0, "upserted": 0,
                "latest_ts": latest_ts, "db_path": DB_PATH}

    # --- Step 5: Parse candles + determine is_closed ---
    candles = parse_candles(raw_klines, now_ms)

    # --- Step 6: Persist to database ---
    before_changes = conn.total_changes
    upsert_candles(conn, candles, EXCHANGE, SYMBOL, TIMEFRAME)
    upserted = conn.total_changes - before_changes

    # --- Step 7: Read updated state ---
    new_latest_ts = get_latest_timestamp(conn, EXCHANGE, SYMBOL, TIMEFRAME)

    # Determine latest candle status for summary
    latest_candle   = candles[-1]
    latest_status   = "CLOSED" if latest_candle["is_closed"] == 1 else "OPEN"
    latest_datetime = datetime.fromtimestamp(
        latest_candle["timestamp"] / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")

    conn.close()

    return {
        "mode":           mode,
        "fetched":        len(candles),
        "upserted":       upserted,
        "latest_ts":      new_latest_ts,
        "latest_datetime": latest_datetime,
        "latest_status":  latest_status,
        "db_path":        DB_PATH,
    }


def print_summary(result: dict) -> None:
    """Print a human-readable run summary to the terminal."""
    print("\n" + "=" * 55)
    print("  Binance Collector — Run Summary")
    print("=" * 55)
    print(f"  Exchange    : {EXCHANGE}")
    print(f"  Symbol      : {SYMBOL}")
    print(f"  Timeframe   : {TIMEFRAME}")
    print(f"  Mode        : {result['mode']}")
    print(f"  Fetched     : {result['fetched']} candles")
    print(f"  Upserted    : {result['upserted']} rows changed")
    print(f"  Latest ts   : {result['latest_ts']}")
    print(f"  Latest dt   : {result.get('latest_datetime', 'N/A')}")
    print(f"  Latest candle: {result.get('latest_status', 'N/A')}")
    print(f"  DB path     : {result['db_path']}")
    print("=" * 55 + "\n")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    try:
        result = run_collector()
        print_summary(result)
    except requests.exceptions.ConnectionError as e:
        print(f"\n[ERROR] Network error — could not reach Binance: {e}")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"\n[ERROR] Binance API HTTP error: {e}")
        sys.exit(1)
    except requests.exceptions.Timeout:
        print(f"\n[ERROR] Request to Binance timed out ({REQUEST_TIMEOUT}s)")
        sys.exit(1)
    except ValueError as e:
        print(f"\n[ERROR] Unexpected API response: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        sys.exit(1)
