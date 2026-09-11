from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extensions

from .market import Quote


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


def load_refresh_token(connection: psycopg2.extensions.connection, provider: str, fallback: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT refresh_token FROM oauth_tokens WHERE provider = %s", (provider,))
        row = cursor.fetchone()
    return str(row[0]) if row else fallback


def save_refresh_token(connection: psycopg2.extensions.connection, provider: str, refresh_token: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO oauth_tokens (provider, refresh_token, updated_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (provider) DO UPDATE SET refresh_token = EXCLUDED.refresh_token, updated_at = EXCLUDED.updated_at",
            (provider, refresh_token, datetime.now(timezone.utc)),
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
