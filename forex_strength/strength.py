from __future__ import annotations

import math
from collections import defaultdict


def calculate_currency_strength(current: dict[str, float], prior: dict[str, float]) -> dict[str, float]:
    """Average signed log returns for currencies in valid six-letter pair symbols."""
    contributions: dict[str, list[float]] = defaultdict(list)
    for pair, current_price in current.items():
        previous_price = prior.get(pair)
        if previous_price is None or len(pair) != 6 or current_price <= 0 or previous_price <= 0:
            continue
        move = math.log(current_price / previous_price) * 100
        base, quote = pair[:3], pair[3:]
        contributions[base].append(move)
        contributions[quote].append(-move)
    return {currency: sum(values) / len(values) for currency, values in contributions.items()}


def rank_pairs(currency_strength: dict[str, float]) -> list[tuple[str, float]]:
    currencies = sorted(currency_strength)
    pairs = [
        (base + quote, currency_strength[base] - currency_strength[quote])
        for base in currencies for quote in currencies if base != quote
    ]
    return sorted(pairs, key=lambda item: item[1], reverse=True)

