from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping
from urllib.parse import urlencode
from urllib.request import urlopen

from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoErrorRes
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOAAccountsTokenInvalidatedEvent,
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAErrorRes,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOAGetAccountListByAccessTokenRes,
    ProtoOAGetTrendbarsReq,
    ProtoOAGetTrendbarsRes,
    ProtoOASpotEvent,
    ProtoOASubscribeSpotsReq,
    ProtoOASymbolsListReq,
    ProtoOASymbolsListRes,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbarPeriod
from twisted.internet import reactor

from .market import Bar, Quote


TOKEN_URL = "https://openapi.ctrader.com/apps/token"
PRICE_SCALE = 100_000.0
# Access tokens live ~30 days. Refresh only when less than this is left, so the
# risky refresh call happens about once a month instead of on every run.
REFRESH_BEFORE_EXPIRY = timedelta(days=7)
TOKEN_REJECTION_CODES = frozenset({"CH_ACCESS_TOKEN_INVALID", "OA_AUTH_TOKEN_EXPIRED", "ACCOUNT_NOT_AUTHORIZED"})
BAR_PERIOD = ProtoOATrendbarPeriod.Value("M5")
# cTrader caps how far back one trendbar request may reach; three days of M5 bars stays inside it.
BAR_BACKFILL = timedelta(days=3)


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str
    expires_at: datetime | None = None

    def valid_at(self, moment: datetime) -> bool:
        return bool(self.access_token) and self.expires_at is not None and self.expires_at > moment


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> OAuthTokens:
    """Exchange a stored OAuth refresh token for a short-lived access token."""
    query = urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    )
    with urlopen(f"{TOKEN_URL}?{query}", timeout=20) as response:  # nosec B310: fixed HTTPS URL
        payload = json.load(response)
    access_token = payload.get("accessToken")
    rotated_refresh_token = payload.get("refreshToken")
    if not access_token or not rotated_refresh_token:
        # Never print the raw payload: it may contain a token, and the logs are public.
        reason = payload.get("description") or payload.get("errorCode") or "response has no tokens"
        raise RuntimeError(f"cTrader token refresh failed: {reason}")
    expires_in = payload.get("expiresIn")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in)) if expires_in else None
    return OAuthTokens(str(access_token), str(rotated_refresh_token), expires_at)


def _normalise_symbol(name: str) -> str:
    # Keep digits: index names such as "GERMANY 40" and "US 500" depend on them.
    return "".join(character for character in name.upper() if character.isalnum())


def _bar_from_trendbar(symbol: str, trendbar: object) -> Bar:
    """cTrader sends the low as an integer price and the other prices as deltas from it."""
    low = trendbar.low
    return Bar(
        symbol,
        datetime.fromtimestamp(trendbar.utcTimestampInMinutes * 60, timezone.utc),
        (low + trendbar.deltaOpen) / PRICE_SCALE,
        (low + trendbar.deltaHigh) / PRICE_SCALE,
        low / PRICE_SCALE,
        (low + trendbar.deltaClose) / PRICE_SCALE,
    )


class CTraderSnapshotCollector:
    """Collect one midpoint snapshot from the cTrader Demo Open API endpoint,
    plus recent M5 bars for the synthetic-cross instruments."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        stored_tokens: OAuthTokens,
        pairs: tuple[str, ...],
        on_tokens: Callable[[OAuthTokens], None],
        on_access_token_rejected: Callable[[], None],
        timeout_seconds: int = 25,
        instruments: Mapping[str, str] | None = None,
        last_bar_times: Mapping[str, datetime] | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.stored_tokens = stored_tokens
        self.on_tokens = on_tokens
        self.on_access_token_rejected = on_access_token_rejected
        self.access_token = ""
        self.using_stored_access_token = False
        self.access_token_rejected = False
        self.refresh_warning: str | None = None
        self.pairs = pairs
        self.timeout_seconds = timeout_seconds
        self.account_id: int | None = None
        self.symbol_by_id: dict[int, str] = {}
        self.quotes: dict[str, Quote] = {}
        # Bars are optional: their problems go to bar_errors and never cost us the FX quotes.
        self.instruments = dict(instruments or {})
        self.last_bar_times = dict(last_bar_times or {})
        self.bar_symbol_ids: dict[str, int] = {}
        self.pending_bars: list[str] = []
        self.bars: list[Bar] = []
        self.bar_errors: list[str] = []
        self.bars_done = not self.instruments
        self.error: Exception | None = None
        self.finished = False
        self.client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)

    def collect(self) -> list[Quote]:
        self.access_token = self._choose_access_token()
        self.client.setConnectedCallback(self._connected)
        self.client.setDisconnectedCallback(self._disconnected)
        self.client.setMessageReceivedCallback(self._received)
        self.client.startService()
        reactor.callLater(self.timeout_seconds, self._timeout)
        reactor.run()
        if self.access_token_rejected and self.using_stored_access_token:
            # Forget it so the next run refreshes instead of retrying a dead token.
            self.on_access_token_rejected()
        if self.error:
            raise self.error
        missing = set(self.pairs) - set(self.quotes)
        if missing:
            raise RuntimeError(f"cTrader did not return a quote for: {', '.join(sorted(missing))}")
        return [self.quotes[pair] for pair in self.pairs]

    def _choose_access_token(self) -> str:
        now = datetime.now(timezone.utc)
        stored = self.stored_tokens
        if stored.valid_at(now + REFRESH_BEFORE_EXPIRY):
            self.using_stored_access_token = True
            return stored.access_token
        try:
            tokens = refresh_access_token(self.client_id, self.client_secret, stored.refresh_token)
        except Exception as error:
            if not stored.valid_at(now):
                raise
            # The stored access token still works: keep collecting, but flag that a new
            # refresh token is needed before the access token expires.
            self.using_stored_access_token = True
            self.refresh_warning = (
                f"cTrader token refresh failed ({error}). Quotes are still collected with the stored "
                f"access token until {stored.expires_at:%Y-%m-%d %H:%M} UTC; get a new refresh token before then."
            )
            return stored.access_token
        # cTrader invalidates the old refresh token as soon as it rotates it.
        # Persist the replacement before making any further API request.
        self.on_tokens(tokens)
        return tokens.access_token

    def _send(self, request: object) -> None:
        deferred = self.client.send(request)
        deferred.addErrback(self._request_failed)

    def _connected(self, client: Client) -> None:
        request = ProtoOAApplicationAuthReq()
        request.clientId = self.client_id
        request.clientSecret = self.client_secret
        self._send(request)

    def _disconnected(self, client: Client, reason: object) -> None:
        if not self.finished:
            self._finish(RuntimeError(f"cTrader connection closed unexpectedly: {reason}"))

    def _request_failed(self, failure: object) -> None:
        self._finish(RuntimeError(f"cTrader API request failed: {failure}"))

    def _received(self, client: Client, message: object) -> None:
        payload_type = message.payloadType
        if payload_type == ProtoOAApplicationAuthRes().payloadType:
            request = ProtoOAGetAccountListByAccessTokenReq()
            request.accessToken = self.access_token
            self._send(request)
        elif payload_type == ProtoOAGetAccountListByAccessTokenRes().payloadType:
            response = Protobuf.extract(message)
            demo_accounts = [account for account in response.ctidTraderAccount if not account.isLive]
            if not demo_accounts:
                self._finish(RuntimeError("No cTrader Demo accounts are authorized for this OAuth token."))
                return
            self.account_id = int(demo_accounts[0].ctidTraderAccountId)
            request = ProtoOAAccountAuthReq()
            request.ctidTraderAccountId = self.account_id
            request.accessToken = self.access_token
            self._send(request)
        elif payload_type == ProtoOAAccountAuthRes().payloadType:
            request = ProtoOASymbolsListReq()
            request.ctidTraderAccountId = self._account_id()
            self._send(request)
        elif payload_type == ProtoOASymbolsListRes().payloadType:
            self._subscribe_requested_pairs(Protobuf.extract(message))
        elif payload_type == ProtoOASpotEvent().payloadType:
            self._record_spot(Protobuf.extract(message))
        elif payload_type in (ProtoOAErrorRes().payloadType, ProtoErrorRes().payloadType):
            self._handle_error(Protobuf.extract(message))
        elif payload_type == ProtoOAAccountsTokenInvalidatedEvent().payloadType:
            self.access_token_rejected = True
            self._finish(RuntimeError("cTrader invalidated the access token."))

    def _handle_error(self, response: object) -> None:
        description = f"{response.errorCode}: {response.description}" if response.description else response.errorCode
        if response.errorCode in TOKEN_REJECTION_CODES:
            self.access_token_rejected = True
            self._finish(RuntimeError(f"cTrader rejected the access token: {description}"))
            return
        # Other errors used to be ignored; log them and let the quote timeout decide.
        print(f"cTrader error response: {description}")

    def _subscribe_requested_pairs(self, response: object) -> None:
        symbol_ids: dict[str, int] = {}
        for symbol in response.symbol:
            symbol_ids.setdefault(_normalise_symbol(symbol.symbolName), int(symbol.symbolId))
        missing = set(self.pairs) - set(symbol_ids)
        if missing:
            self._finish(RuntimeError(f"These requested pairs are unavailable on the cTrader Demo account: {', '.join(sorted(missing))}"))
            return
        self.symbol_by_id = {symbol_ids[pair]: pair for pair in self.pairs}
        request = ProtoOASubscribeSpotsReq()
        request.ctidTraderAccountId = self._account_id()
        request.symbolId.extend(self.symbol_by_id)
        request.subscribeToSpotTimestamp = True
        self._send(request)
        try:
            self._start_bars(symbol_ids)
        except Exception as error:
            self.bar_errors.append(f"could not request bars ({error})")
            self.pending_bars.clear()
            self.bars_done = True

    def _start_bars(self, symbol_ids: dict[str, int]) -> None:
        for name, broker_symbol in self.instruments.items():
            symbol_id = symbol_ids.get(_normalise_symbol(broker_symbol))
            if symbol_id is None:
                self.bar_errors.append(f"{name}: symbol '{broker_symbol}' is unavailable on the cTrader Demo account")
                continue
            self.bar_symbol_ids[name] = symbol_id
            self.pending_bars.append(name)
        self._request_next_bars()

    def _request_next_bars(self) -> None:
        # One request at a time keeps us well inside the historical-data rate limit.
        if self.finished:
            return
        if not self.pending_bars:
            self.bars_done = True
            self._finish_if_complete()
            return
        name = self.pending_bars[0]
        now = datetime.now(timezone.utc)
        since = max(self.last_bar_times.get(name, now - BAR_BACKFILL), now - BAR_BACKFILL)
        request = ProtoOAGetTrendbarsReq()
        request.ctidTraderAccountId = self._account_id()
        request.symbolId = self.bar_symbol_ids[name]
        request.period = BAR_PERIOD
        # Start at the last stored bar, not after it: that bar may still have been forming.
        request.fromTimestamp = int(since.timestamp() * 1000)
        request.toTimestamp = int(now.timestamp() * 1000)
        deferred = self.client.send(request)
        deferred.addCallbacks(self._bars_answered, self._bars_failed, callbackArgs=(name,), errbackArgs=(name,))

    def _bars_answered(self, message: object, name: str) -> None:
        try:
            if message.payloadType == ProtoOAGetTrendbarsRes().payloadType:
                response = Protobuf.extract(message)
                self.bars.extend(_bar_from_trendbar(name, trendbar) for trendbar in response.trendbar)
            else:
                error = Protobuf.extract(message)
                self.bar_errors.append(f"{name}: {getattr(error, 'errorCode', 'unexpected response')} {getattr(error, 'description', '')}".strip())
        except Exception as error:
            self.bar_errors.append(f"{name}: could not read bars ({error})")
        self._next_bars(name)

    def _bars_failed(self, failure: object, name: str) -> None:
        self.bar_errors.append(f"{name}: no bars response ({failure.getErrorMessage()})")
        self._next_bars(name)

    def _next_bars(self, name: str) -> None:
        if self.pending_bars and self.pending_bars[0] == name:
            self.pending_bars.pop(0)
        self._request_next_bars()

    def _record_spot(self, event: object) -> None:
        pair = self.symbol_by_id.get(int(event.symbolId))
        if not pair or not event.bid or not event.ask:
            return
        midpoint = (event.bid + event.ask) / (2 * PRICE_SCALE)
        self.quotes[pair] = Quote(pair, midpoint, datetime.now(timezone.utc))
        self._finish_if_complete()

    def _finish_if_complete(self) -> None:
        if len(self.quotes) == len(self.pairs) and self.bars_done:
            self._finish()

    def _account_id(self) -> int:
        if self.account_id is None:
            raise RuntimeError("cTrader account was not selected.")
        return self.account_id

    def _timeout(self) -> None:
        if self.finished:
            return
        if len(self.quotes) == len(self.pairs):
            # Every FX quote arrived and only bars are late: keep the quotes.
            self.bar_errors.append(f"timed out waiting for bars: {', '.join(self.pending_bars) or 'symbol list'}")
            self._finish()
            return
        self._finish(RuntimeError(f"Timed out after {self.timeout_seconds} seconds waiting for cTrader quotes."))

    def _finish(self, error: Exception | None = None) -> None:
        if self.finished:
            return
        self.finished = True
        self.error = error
        self.client.stopService()
        if reactor.running:
            reactor.stop()
