from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Quote:
    """A midpoint FX quote collected from an authorized market-data source."""

    symbol: str
    price: float
    observed_at: datetime
