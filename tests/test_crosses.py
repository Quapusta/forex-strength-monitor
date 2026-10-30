from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from dashboard import cross_window


START = datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)


def make_bars(prices: dict[str, list[float]]) -> pd.DataFrame:
    rows = [
        (symbol, START + timedelta(minutes=5 * index), price)
        for symbol, series in prices.items()
        for index, price in enumerate(series)
        if price is not None
    ]
    frame = pd.DataFrame(rows, columns=["symbol", "opened_at", "close"])
    frame["opened_at"] = pd.to_datetime(frame["opened_at"], utc=True)
    return frame


def test_strength_is_ratio_change_since_period_start() -> None:
    gold = [2000 + index for index in range(25)]
    silver = [25.0] * 25
    window = cross_window(make_bars({"XAU": gold, "XAG": silver}), "XAU", "XAG", timedelta(hours=1))
    assert len(window) == 13  # the bar at the start plus 12 five-minute bars
    assert window["strength"].iloc[0] == 0
    assert np.isclose(window["strength"].iloc[-1], np.log(2024 / 2012) * 100)
    assert np.isclose(window["XAG"].iloc[-1], 0)
    assert window.index[-1] == pd.Timestamp(START + timedelta(minutes=5 * 25))


def test_falling_ratio_means_the_denominator_is_stronger() -> None:
    window = cross_window(make_bars({"A": [100, 100, 100], "B": [10, 10.5, 11]}), "A", "B", timedelta(minutes=10))
    assert window["strength"].iloc[-1] < 0


def test_missing_bar_keeps_the_previous_close() -> None:
    window = cross_window(make_bars({"A": [100, 101, 102], "B": [10, None, 10]}), "A", "B", timedelta(minutes=10))
    assert len(window) == 3
    assert np.isclose(window["B"].iloc[1], 0)


def test_not_enough_history_returns_nothing() -> None:
    assert cross_window(make_bars({"A": [100, 101], "B": [10, 10]}), "A", "B", timedelta(hours=4)).empty
    assert cross_window(make_bars({"A": [100, 101]}), "A", "B", timedelta(minutes=5)).empty
