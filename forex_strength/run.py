from __future__ import annotations

from datetime import timedelta, timezone

from .config import Settings
from .ctrader import CTraderSnapshotCollector
from .storage import connect, load_refresh_token, prices_near, save_quotes, save_refresh_token
from .strength import calculate_currency_strength, rank_pairs


LOOKBACKS = (timedelta(hours=1), timedelta(hours=4), timedelta(hours=24))


def main() -> None:
    settings = Settings.from_environment()
    connection = connect(settings.database_path)
    try:
        refresh_token = load_refresh_token(connection, "ctrader-demo", settings.refresh_token)
        quotes = CTraderSnapshotCollector(
            settings.client_id,
            settings.client_secret,
            refresh_token,
            settings.pair_symbols,
            lambda token: save_refresh_token(connection, "ctrader-demo", token),
        ).collect()
        now = max(quote.observed_at for quote in quotes).astimezone(timezone.utc)
        current = {quote.symbol: quote.price for quote in quotes}
        save_quotes(connection, quotes)
        print(f"Stored {len(quotes)} quotes at {now.isoformat()}")
        for lookback in LOOKBACKS:
            prior = prices_near(connection, settings.pair_symbols, lookback, now)
            strength = calculate_currency_strength(current, prior)
            if not strength:
                print(f"{lookback}: not enough history yet")
                continue
            ranking = ", ".join(f"{currency} {score:+.3f}%" for currency, score in sorted(strength.items(), key=lambda item: item[1], reverse=True))
            pair, score = rank_pairs(strength)[0]
            print(f"{lookback}: {ranking}; strongest synthetic pair: {pair} ({score:+.3f}%)")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
