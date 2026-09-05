"""
database.py — Local SQLite database module for CAA-2.

Provides:
    init_db(db_path)                          → create DB file + ohlcv_5m table
    upsert_candles(conn, candles, exchange,
                   symbol, timeframe)         → insert or update candle records
    get_latest_timestamp(conn, exchange,
                         symbol, timeframe)   → latest timestamp in DB for a market
"""

import sqlite3
import os

# ------------------------------------------------------------------
# DDL (Data Definition Language — ngôn ngữ định nghĩa cấu trúc DB)
# ------------------------------------------------------------------
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ohlcv_5m (
    id        INTEGER  PRIMARY KEY AUTOINCREMENT,
    exchange  TEXT     NOT NULL,
    symbol    TEXT     NOT NULL,
    timeframe TEXT     NOT NULL,
    timestamp INTEGER  NOT NULL,
    open      REAL     NOT NULL,
    high      REAL     NOT NULL,
    low       REAL     NOT NULL,
    close     REAL     NOT NULL,
    volume    REAL     NOT NULL,
    is_closed INTEGER  NOT NULL DEFAULT 0,

    UNIQUE (exchange, symbol, timeframe, timestamp)
);
"""

# UPSERT logic:
#   - INSERT new record if (exchange, symbol, timeframe, timestamp) not seen before.
#   - DO UPDATE only when existing record is still OPEN (is_closed = 0).
#     This covers both:
#       OPEN → OPEN   : update latest OHLCV during candle formation
#       OPEN → CLOSED : mark candle as closed with final OHLCV
#   - If existing is already CLOSED (is_closed = 1), skip — historical data is immutable.
_UPSERT_SQL = """
INSERT INTO ohlcv_5m
    (exchange, symbol, timeframe, timestamp,
     open, high, low, close, volume, is_closed)
VALUES
    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (exchange, symbol, timeframe, timestamp)
DO UPDATE SET
    high      = excluded.high,
    low       = excluded.low,
    close     = excluded.close,
    volume    = excluded.volume,
    is_closed = excluded.is_closed
WHERE ohlcv_5m.is_closed = 0;
"""


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def init_db(db_path: str) -> sqlite3.Connection:
    """
    Create the SQLite database file and ohlcv_5m table if they don't exist.

    Args:
        db_path: Path to the .db file (e.g. 'db/local.db').
                 Parent directory is created automatically if missing.

    Returns:
        An open sqlite3.Connection with row_factory set to Row for
        dict-like column access.

    Notes:
        - Safe to call multiple times; CREATE TABLE IF NOT EXISTS is idempotent.
        - Existing data is never deleted.
    """
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row          # row_factory: truy cập column bằng tên
    conn.execute("PRAGMA journal_mode=WAL") # WAL mode: cải thiện concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")

    conn.execute(_CREATE_TABLE_SQL)
    conn.commit()
    return conn


def upsert_candles(
    conn: sqlite3.Connection,
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
        is_closed  (int)   : 0 = candle still forming, 1 = candle confirmed closed

    UPSERT rules:
        - New timestamp → INSERT
        - Existing timestamp, is_closed = 0 → UPDATE (open candle refresh or close)
        - Existing timestamp, is_closed = 1 → SKIP  (closed candle is immutable)

    Args:
        conn      : open sqlite3.Connection from init_db()
        candles   : list of candle dicts
        exchange  : exchange identifier, e.g. 'binance'
        symbol    : unified symbol, e.g. 'BTC/USDT'
        timeframe : timeframe string, e.g. '5m'

    Returns:
        Number of rows that were inserted or modified.
    """
    if not candles:
        return 0

    rows = [
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
            int(c["is_closed"]),  # ensure 0 or 1
        )
        for c in candles
    ]

    conn.executemany(_UPSERT_SQL, rows)
    conn.commit()
    return conn.total_changes


def get_latest_timestamp(
    conn: sqlite3.Connection,
    exchange: str,
    symbol: str,
    timeframe: str,
) -> int | None:
    """
    Return the largest timestamp stored in ohlcv_5m for a given market.

    Used by the collector for incremental ingestion (thu thập dữ liệu tăng dần):
    fetch only candles newer than this timestamp instead of the full history.

    Args:
        conn      : open sqlite3.Connection
        exchange  : e.g. 'binance'
        symbol    : e.g. 'BTC/USDT'
        timeframe : e.g. '5m'

    Returns:
        Latest timestamp as int (ms), or None if table is empty for this market.
    """
    cur = conn.execute(
        """
        SELECT MAX(timestamp)
        FROM   ohlcv_5m
        WHERE  exchange  = ?
          AND  symbol    = ?
          AND  timeframe = ?
        """,
        (exchange, symbol, timeframe),
    )
    row = cur.fetchone()
    return row[0] if row and row[0] is not None else None
