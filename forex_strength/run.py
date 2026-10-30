from __future__ import annotations

from datetime import timedelta, timezone

from .config import Settings
from .ctrader import CTraderSnapshotCollector, OAuthTokens
from .storage import connect, forget_access_token, load_tokens, prices_near, save_quotes, save_tokens
from .strength import calculate_currency_strength, rank_pairs


LOOKBACKS = (timedelta(hours=1), timedelta(hours=4), timedelta(hours=24))
TOKEN_PROVIDER = "ctrader-demo"


def main() -> None:
    settings = Settings.from_environment()
    connection = connect(settings.database_url)
    try:
        refresh_token, access_token, access_expires_at = load_tokens(connection, TOKEN_PROVIDER, settings.refresh_token)
        collector = CTraderSnapshotCollector(
            settings.client_id,
            settings.client_secret,
            OAuthTokens(access_token, refresh_token, access_expires_at),
            settings.pair_symbols,
            lambda tokens: save_tokens(
                connection, TOKEN_PROVIDER, tokens.refresh_token, tokens.access_token, tokens.expires_at
            ),
            lambda: forget_access_token(connection, TOKEN_PROVIDER),
        )
        quotes = collector.collect()
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
        if collector.refresh_warning:
            # Quotes are saved, but fail the run so the problem shows up while there is time to fix it.
            print(f"::error title=cTrader refresh token::{collector.refresh_warning}")
            raise SystemExit(1)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
