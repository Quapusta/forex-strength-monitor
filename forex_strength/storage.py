from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .market import Quote


SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
  symbol TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  price REAL NOT NULL CHECK (price > 0),
  PRIMARY KEY (symbol, observed_at)
);
CREATE INDEX IF NOT EXISTS quotes_symbol_time ON quotes(symbol, observed_at);
CREATE TABLE IF NOT EXISTS oauth_tokens (
  provider TEXT PRIMARY KEY,
  refresh_token TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.executescript(SCHEMA)
    return connection


def save_quotes(connection: sqlite3.Connection, quotes: list[Quote]) -> None:
    connection.executemany(
        "INSERT OR IGNORE INTO quotes(symbol, observed_at, price) VALUES (?, ?, ?)",
        [(quote.symbol, quote.observed_at.isoformat(), quote.price) for quote in quotes],
    )
    connection.commit()


def load_refresh_token(connection: sqlite3.Connection, provider: str, fallback: str) -> str:
    row = connection.execute("SELECT refresh_token FROM oauth_tokens WHERE provider = ?", (provider,)).fetchone()
    return str(row[0]) if row else fallback


def save_refresh_token(connection: sqlite3.Connection, provider: str, refresh_token: str) -> None:
    connection.execute(
        "INSERT INTO oauth_tokens(provider, refresh_token, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(provider) DO UPDATE SET refresh_token = excluded.refresh_token, updated_at = excluded.updated_at",
        (provider, refresh_token, datetime.now(timezone.utc).isoformat()),
    )
    connection.commit()


def prices_near(connection: sqlite3.Connection, symbols: tuple[str, ...], lookback: timedelta, now: datetime) -> dict[str, float]:
    """Return the latest observation at or before the requested lookback time."""
    target = (now - lookback).astimezone(timezone.utc).isoformat()
    result: dict[str, float] = {}
    for symbol in symbols:
        row = connection.execute(
            "SELECT price FROM quotes WHERE symbol = ? AND observed_at <= ? ORDER BY observed_at DESC LIMIT 1",
            (symbol, target),
        ).fetchone()
        if row:
            result[symbol] = float(row[0])
    return result
