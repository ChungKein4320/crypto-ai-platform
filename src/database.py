"""
database.py — Supabase PostgreSQL database module for CAA-3.

Connects to Supabase PostgreSQL via DATABASE_URL environment variable.
Table public.ohlcv_5m must already exist on the cloud database.

Provides:
    get_connection()                          → open psycopg Connection
    upsert_candles(conn, candles, exchange,
                   symbol, timeframe)         → insert or update candle records
    get_latest_timestamp(conn, exchange,
                         symbol, timeframe)   → latest timestamp in DB for a market
"""

import os

import psycopg

from dotenv import load_dotenv

# Load .env file from project root (contains DATABASE_URL).
# .env is gitignored — secrets are never committed.
load_dotenv()

# ------------------------------------------------------------------
# UPSERT SQL for PostgreSQL
# ------------------------------------------------------------------
#
# UPSERT logic (unchanged from CAA-2):
#   - INSERT new record if (exchange, symbol, timeframe, timestamp) not seen before.
#   - DO UPDATE only when existing record is still OPEN (is_closed = FALSE).
#     This covers both:
#       OPEN → OPEN   : update latest OHLCV during candle formation
#       OPEN → CLOSED : mark candle as closed with final OHLCV
#   - If existing is already CLOSED (is_closed = TRUE), skip —
#     historical data is immutable.
#
# PostgreSQL differences from SQLite:
#   - Placeholders: %s instead of ?
#   - is_closed: BOOLEAN (TRUE/FALSE) instead of INTEGER (0/1)
#   - EXCLUDED keyword (PostgreSQL convention, case-insensitive)
#
_UPSERT_SQL = """
INSERT INTO ohlcv_5m
    (exchange, symbol, timeframe, timestamp,
     open, high, low, close, volume, is_closed)
VALUES
    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (exchange, symbol, timeframe, timestamp)
DO UPDATE SET
    high      = EXCLUDED.high,
    low       = EXCLUDED.low,
    close     = EXCLUDED.close,
    volume    = EXCLUDED.volume,
    is_closed = EXCLUDED.is_closed
WHERE ohlcv_5m.is_closed = FALSE;
"""


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def get_connection() -> psycopg.Connection:
    """
    Open a connection to Supabase PostgreSQL using DATABASE_URL.

    The DATABASE_URL environment variable must be set (typically via .env).
    It contains the full PostgreSQL connection string including SSL mode.
    The password is never printed or logged.

    Returns:
        An open psycopg.Connection ready for queries.

    Raises:
        RuntimeError if DATABASE_URL is not set.
        psycopg.OperationalError on connection failure.

    Notes:
        - Table ohlcv_5m must already exist on Supabase (created via SQL Editor).
        - Safe to call multiple times; each call opens a new connection.
        - Caller is responsible for closing the connection.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Create a .env file with DATABASE_URL=postgresql://... "
            "or set the variable in your shell."
        )

    conn = psycopg.connect(dsn)
    return conn


def upsert_candles(
    conn: psycopg.Connection,
    candles: list[dict],
    exchange: str,
    symbol: str,
    timeframe: str,
) -> int:
    """
    Insert or update a batch of candles for a specific market.

    Each candle dict must contain:
        timestamp  (int)   : Unix milliseconds UTC — candle open time
        open       (float) : opening price
        high       (float) : highest price in period
        low        (float) : lowest price in period
        close      (float) : closing price (may change while candle is open)
        volume     (float) : traded volume
        is_closed  (int|bool) : 0/False = candle still forming,
                                1/True  = candle confirmed closed

    UPSERT rules:
        - New timestamp → INSERT
        - Existing timestamp, is_closed = FALSE → UPDATE (open candle refresh or close)
        - Existing timestamp, is_closed = TRUE  → SKIP  (closed candle is immutable)

    Args:
        conn      : open psycopg.Connection from get_connection()
        candles   : list of candle dicts
        exchange  : exchange identifier, e.g. 'binance'
        symbol    : unified symbol, e.g. 'BTC/USDT'
        timeframe : timeframe string, e.g. '5m'

    Returns:
        Total number of rows that were inserted or modified.
    """
    if not candles:
        return 0

    total_affected = 0

    with conn.cursor() as cur:
        for c in candles:
            cur.execute(
                _UPSERT_SQL,
                (
                    exchange,
                    symbol,
                    timeframe,
                    c["timestamp"],
                    c["open"],
                    c["high"],
                    c["low"],
                    c["close"],
                    c["volume"],
                    bool(c["is_closed"]),  # int 0/1 → Python bool → PG BOOLEAN
                ),
            )
            total_affected += cur.rowcount

    conn.commit()
    return total_affected


def get_latest_timestamp(
    conn: psycopg.Connection,
    exchange: str,
    symbol: str,
    timeframe: str,
) -> int | None:
    """
    Return the largest timestamp stored in ohlcv_5m for a given market.

    Used by the collector for incremental ingestion (thu thập dữ liệu tăng dần):
    fetch only candles newer than this timestamp instead of the full history.

    Args:
        conn      : open psycopg.Connection
        exchange  : e.g. 'binance'
        symbol    : e.g. 'BTC/USDT'
        timeframe : e.g. '5m'

    Returns:
        Latest timestamp as int (ms), or None if table is empty for this market.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT MAX(timestamp)
            FROM   ohlcv_5m
            WHERE  exchange  = %s
              AND  symbol    = %s
              AND  timeframe = %s
            """,
            (exchange, symbol, timeframe),
        )
        row = cur.fetchone()

    return row[0] if row and row[0] is not None else None
