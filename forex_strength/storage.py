from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extensions
import psycopg2.extras

from .market import Bar, Quote


SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
  symbol TEXT NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL,
  price DOUBLE PRECISION NOT NULL CHECK (price > 0),
  PRIMARY KEY (symbol, observed_at)
);
CREATE INDEX IF NOT EXISTS quotes_symbol_time ON quotes (symbol, observed_at);

CREATE TABLE IF NOT EXISTS oauth_tokens (
  provider TEXT PRIMARY KEY,
  refresh_token TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE oauth_tokens ADD COLUMN IF NOT EXISTS access_token TEXT;
ALTER TABLE oauth_tokens ADD COLUMN IF NOT EXISTS access_expires_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS bars_m5 (
  symbol TEXT NOT NULL,
  opened_at TIMESTAMPTZ NOT NULL,
  open DOUBLE PRECISION NOT NULL,
  high DOUBLE PRECISION NOT NULL,
  low DOUBLE PRECISION NOT NULL,
  close DOUBLE PRECISION NOT NULL CHECK (close > 0),
  PRIMARY KEY (symbol, opened_at)
);
"""


def connect(database_url: str) -> psycopg2.extensions.connection:
    """Open a PostgreSQL connection and ensure the schema exists.

    `database_url` accepts any libpq-style connection string, including a
    Neon connection string with SSL, e.g.:
      "postgresql://user:password@ep-xxx.region.aws.neon.tech/forex_strength?sslmode=require"
    """
    connection = psycopg2.connect(database_url)
    connection.autocommit = False
    with connection.cursor() as cursor:
        cursor.execute(SCHEMA)
    connection.commit()
    return connection


def save_quotes(connection: psycopg2.extensions.connection, quotes: list[Quote]) -> None:
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO quotes (symbol, observed_at, price) VALUES (%s, %s, %s) "
            "ON CONFLICT (symbol, observed_at) DO NOTHING",
            [(quote.symbol, quote.observed_at, quote.price) for quote in quotes],
        )
    connection.commit()


def save_bars(connection: psycopg2.extensions.connection, bars: list[Bar]) -> None:
    """Insert bars, overwriting any stored bar that was still forming when it was fetched."""
    with connection.cursor() as cursor:
        psycopg2.extras.execute_values(
            cursor,
            "INSERT INTO bars_m5 (symbol, opened_at, open, high, low, close) VALUES %s "
            "ON CONFLICT (symbol, opened_at) DO UPDATE SET open = EXCLUDED.open, high = EXCLUDED.high, "
            "low = EXCLUDED.low, close = EXCLUDED.close",
            [(bar.symbol, bar.opened_at, bar.open, bar.high, bar.low, bar.close) for bar in bars],
            page_size=1000,
        )
    connection.commit()


def latest_bar_times(connection: psycopg2.extensions.connection, symbols: tuple[str, ...]) -> dict[str, datetime]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT symbol, MAX(opened_at) FROM bars_m5 WHERE symbol = ANY(%s) GROUP BY symbol",
            (list(symbols),),
        )
        return {str(symbol): opened_at for symbol, opened_at in cursor.fetchall()}


def load_tokens(
    connection: psycopg2.extensions.connection, provider: str, fallback_refresh_token: str
) -> tuple[str, str, datetime | None]:
    """Return (refresh token, access token or "", access token expiry or None)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT refresh_token, access_token, access_expires_at FROM oauth_tokens WHERE provider = %s",
            (provider,),
        )
        row = cursor.fetchone()
    if not row:
        return fallback_refresh_token, "", None
    return str(row[0]), str(row[1] or ""), row[2]


def save_tokens(
    connection: psycopg2.extensions.connection,
    provider: str,
    refresh_token: str,
    access_token: str,
    access_expires_at: datetime | None,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO oauth_tokens (provider, refresh_token, access_token, access_expires_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (provider) DO UPDATE SET refresh_token = EXCLUDED.refresh_token, "
            "access_token = EXCLUDED.access_token, access_expires_at = EXCLUDED.access_expires_at, "
            "updated_at = EXCLUDED.updated_at",
            (provider, refresh_token, access_token, access_expires_at, datetime.now(timezone.utc)),
        )
    connection.commit()


def forget_access_token(connection: psycopg2.extensions.connection, provider: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE oauth_tokens SET access_token = NULL, access_expires_at = NULL WHERE provider = %s",
            (provider,),
        )
    connection.commit()


def prices_near(
    connection: psycopg2.extensions.connection,
    symbols: tuple[str, ...],
    lookback: timedelta,
    now: datetime,
) -> dict[str, float]:
    """Return the latest observation at or before the requested lookback time."""
    target = (now - lookback).astimezone(timezone.utc)
    result: dict[str, float] = {}
    with connection.cursor() as cursor:
        for symbol in symbols:
            cursor.execute(
                "SELECT price FROM quotes WHERE symbol = %s AND observed_at <= %s "
                "ORDER BY observed_at DESC LIMIT 1",
                (symbol, target),
            )
            row = cursor.fetchone()
            if row:
                result[symbol] = float(row[0])
    return result
