"""Read-only Streamlit dashboard for the Forex relative-strength monitor.

Run locally (from the project root, same folder as forex_strength/ and .env):

    streamlit run dashboard.py

This reads the same DATABASE_URL used by the collector. It never writes to
the database and has no dependency on cTrader credentials or trading logic —
it only queries the `quotes` table that the collector already fills.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.express as px
import psycopg2
import streamlit as st

from forex_strength.config import load_dotenv

PAIRS = ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDJPY", "USDCHF", "USDCAD")

# All timestamps are stored and calculated in UTC (see storage.py / this
# file's own queries). This is only for the x-axis labels shown on screen —
# it converts to the user's local time (with automatic DST handling) so the
# chart doesn't look "delayed" when it's actually just showing UTC.
DISPLAY_TZ = ZoneInfo("Europe/Warsaw")

LOOKBACKS = {
    "1 година": timedelta(hours=1),
    "4 години": timedelta(hours=4),
    "24 години": timedelta(hours=24),
}

DISPLAY_RANGE = timedelta(hours=24)

RANK_LOOKBACKS = {
    "1 година": timedelta(hours=1),
    "4 години": timedelta(hours=4),
    "24 години": timedelta(hours=24),
    "3 дні": timedelta(days=3),
    "7 днів": timedelta(days=7),
}


def currency_of(symbol: str) -> tuple[str, str]:
    return symbol[:3], symbol[3:]


@st.cache_data(ttl=60)
def load_quotes(database_url: str, since: datetime) -> pd.DataFrame:
    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT symbol, observed_at, price FROM quotes "
                "WHERE symbol = ANY(%s) AND observed_at >= %s "
                "ORDER BY observed_at",
                (list(PAIRS), since),
            )
            rows = cursor.fetchall()
    frame = pd.DataFrame(rows, columns=["symbol", "observed_at", "price"])
    if not frame.empty:
        frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True)
    return frame


def strength_timeseries(
    frame: pd.DataFrame, lookback: timedelta, display_start: datetime
) -> pd.DataFrame:
    """Compute rolling per-currency strength for every observation timestamp
    within the display window, using the log-return formula from README.md:
    base currency gets the pair's log return, quote currency gets the
    inverse, and each currency's strength is the average across its pairs.
    """
    results: list[pd.DataFrame] = []
    for symbol in PAIRS:
        pair_frame = frame[frame["symbol"] == symbol].sort_values("observed_at")
        if pair_frame.empty:
            continue

        shifted = pair_frame.copy()
        shifted["target_time"] = shifted["observed_at"] - lookback

        past = pd.merge_asof(
            shifted.sort_values("target_time"),
            pair_frame[["observed_at", "price"]]
            .rename(columns={"observed_at": "past_time", "price": "past_price"})
            .sort_values("past_time"),
            left_on="target_time",
            right_on="past_time",
            direction="backward",
        )
        past = past.dropna(subset=["past_price"])
        past = past[past["observed_at"] >= display_start]
        if past.empty:
            continue

        past["log_return"] = np.log(past["price"] / past["past_price"])
        base, quote = currency_of(symbol)

        base_rows = past[["observed_at", "log_return"]].copy()
        base_rows["currency"] = base

        quote_rows = past[["observed_at", "log_return"]].copy()
        quote_rows["log_return"] = -quote_rows["log_return"]
        quote_rows["currency"] = quote

        results.append(base_rows)
        results.append(quote_rows)

    if not results:
        return pd.DataFrame(columns=["observed_at", "currency", "strength"])

    combined = pd.concat(results, ignore_index=True)
    strength = (
        combined.groupby(["observed_at", "currency"])["log_return"]
        .mean()
        .reset_index()
        .rename(columns={"log_return": "strength"})
    )
    return strength


def current_strength(frame: pd.DataFrame, lookback: timedelta, now: datetime) -> pd.Series:
    """Strength of each currency right now, for a single lookback window —
    the same base/quote/average logic as strength_timeseries, but evaluated
    only at `now` instead of at every historical timestamp."""
    rows: list[tuple[str, float]] = []
    for symbol in PAIRS:
        pair_frame = frame[frame["symbol"] == symbol].sort_values("observed_at")
        if pair_frame.empty:
            continue

        current_rows = pair_frame[pair_frame["observed_at"] <= now]
        past_rows = pair_frame[pair_frame["observed_at"] <= now - lookback]
        if current_rows.empty or past_rows.empty:
            continue

        last_price = current_rows.iloc[-1]["price"]
        past_price = past_rows.iloc[-1]["price"]
        log_return = float(np.log(last_price / past_price))

        base, quote = currency_of(symbol)
        rows.append((base, log_return))
        rows.append((quote, -log_return))

    if not rows:
        return pd.Series(dtype=float)

    strength = (
        pd.DataFrame(rows, columns=["currency", "log_return"])
        .groupby("currency")["log_return"]
        .mean()
    )
    return strength * 100  # percent


def build_rank_table(
    frame: pd.DataFrame, lookbacks: dict[str, timedelta], now: datetime
) -> pd.DataFrame:
    columns = {label: current_strength(frame, lookback, now) for label, lookback in lookbacks.items()}
    table = pd.DataFrame(columns)
    return table.dropna(how="all")


def main() -> None:
    st.set_page_config(page_title="Forex relative strength", layout="wide")
    st.title("Forex relative-strength monitor")

    load_dotenv()
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        st.error("DATABASE_URL не знайдено. Перевірте .env у корені проєкту.")
        return

    lookback_label = st.selectbox("Вікно розрахунку сили", list(LOOKBACKS.keys()), index=0)
    lookback = LOOKBACKS[lookback_label]

    now = datetime.now(timezone.utc)
    display_start = now - DISPLAY_RANGE
    query_since = display_start - lookback  # extra history needed near the window start

    frame = load_quotes(database_url, query_since)
    if frame.empty:
        st.warning("Даних ще недостатньо. Зачекайте, поки збереться історія.")
        return

    strength = strength_timeseries(frame, lookback, display_start)
    if strength.empty:
        st.warning(
            f"Недостатньо історії для вікна «{lookback_label}». "
            "Зачекайте, поки накопичиться більше знімків."
        )
        return

    display_frame = frame[frame["observed_at"] >= display_start]
    price_range = (
        display_frame.groupby("symbol")["price"]
        .agg(min="min", max="max", last="last")
        .reindex(PAIRS)
    )
    price_range["range_pips"] = price_range["max"] - price_range["min"]

    strength["strength_pct"] = strength["strength"] * 100
    strength_plot = strength.sort_values("observed_at").copy()
    strength_plot["observed_at"] = strength_plot["observed_at"].dt.tz_convert(DISPLAY_TZ)
    fig = px.line(
        strength_plot,
        x="observed_at",
        y="strength_pct",
        color="currency",
        labels={"observed_at": "Час (Варшава)", "strength_pct": "Сила, %", "currency": "Валюта"},
        title=f"Відносна сила валют — вікно {lookback_label}, останні 24 год",
    )
    fig.update_layout(hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Opportunity score по парі")
    pair_choice = st.selectbox("Валютна пара", PAIRS, key="opportunity_pair")
    base, quote = currency_of(pair_choice)

    opp_pivot = strength.pivot_table(index="observed_at", columns="currency", values="strength_pct")
    if base not in opp_pivot.columns or quote not in opp_pivot.columns:
        st.warning(f"Недостатньо даних для пари {pair_choice} у цьому вікні.")
    else:
        opportunity = (opp_pivot[base] - opp_pivot[quote]).dropna().sort_index()
        if opportunity.empty:
            st.warning(f"Недостатньо даних для пари {pair_choice} у цьому вікні.")
        else:
            st.metric(
                f"{pair_choice} — opportunity score ({lookback_label})",
                f"{opportunity.iloc[-1]:+.3f}%",
            )
            opportunity_plot = opportunity.copy()
            opportunity_plot.index = opportunity_plot.index.tz_convert(DISPLAY_TZ)
            opp_fig = px.line(
                opportunity_plot.reset_index(name="opportunity_pct"),
                x="observed_at",
                y="opportunity_pct",
                labels={"observed_at": "Час (Варшава)", "opportunity_pct": "Opportunity, %"},
                title=f"{pair_choice}: сила {base} − сила {quote}, вікно {lookback_label}",
            )
            opp_fig.add_hline(y=0, line_dash="dot", line_color="gray")
            st.plotly_chart(opp_fig, use_container_width=True)
            st.caption(
                f"Додатне значення означає, що {base} сильніша за {quote} за обране вікно; "
                "від'ємне — навпаки. Аналітичний показник, не торговий сигнал."
            )

    st.subheader("Поточний ранг валют")
    rank_periods = st.multiselect(
        "Періоди для таблиці рангу",
        list(RANK_LOOKBACKS.keys()),
        default=["1 година", "4 години", "24 години", "3 дні"],
    )
    if not rank_periods:
        st.info("Виберіть хоча б один період, щоб побачити таблицю рангу.")
    else:
        sort_period = st.selectbox("Сортувати за", rank_periods, index=0)

        max_lookback = max(RANK_LOOKBACKS[period] for period in rank_periods)
        rank_since = now - max_lookback - timedelta(hours=6)  # buffer for asof lookup
        rank_frame = frame if rank_since >= query_since else load_quotes(database_url, rank_since)

        rank_table = build_rank_table(
            rank_frame, {period: RANK_LOOKBACKS[period] for period in rank_periods}, now
        )
        if rank_table.empty or sort_period not in rank_table.columns:
            st.warning(
                "Недостатньо історії для обраних періодів. Спробуйте коротші вікна "
                "або зачекайте, поки накопичиться більше даних."
            )
        else:
            rank_table = rank_table.sort_values(sort_period, ascending=False)
            rank_table.insert(0, "Ранг", range(1, len(rank_table) + 1))
            percent_columns = [col for col in rank_table.columns if col != "Ранг"]
            st.dataframe(
                rank_table.style.format({col: "{:+.3f}%" for col in percent_columns}),
                use_container_width=True,
            )
        st.caption(
            "Ранг рахується на поточний момент (не за весь обраний період на графіку "
            "вище) — для кожного вибраного вікна: найсильніша валюта вгорі."
        )

    st.subheader("Сирі ціни за останні 24 год (діагностика)")
    st.caption(
        "Якщо min і max співпадають (range_pips = 0) для всіх пар — ціна за "
        "обране вікно не рухалась взагалі (типово для вихідних, коли ринок "
        "Forex закритий), а не помилка розрахунку сили."
    )
    st.dataframe(
        price_range.style.format({"min": "{:.5f}", "max": "{:.5f}", "last": "{:.5f}", "range_pips": "{:.5f}"}),
        use_container_width=True,
    )

    st.caption(
        "Аналітичний інструмент. Не є торговою рекомендацією та не виконує "
        "жодних торгових операцій."
    )


if __name__ == "__main__":
    main()
