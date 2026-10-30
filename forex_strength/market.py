from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Quote:
    """A midpoint FX quote collected from an authorized market-data source."""

    symbol: str
    price: float
    observed_at: datetime


@dataclass(frozen=True)
class Bar:
    """A five-minute OHLC bar for an index or metal used in a synthetic cross."""

    symbol: str
    opened_at: datetime
    open: float
    high: float
    low: float
    close: float
