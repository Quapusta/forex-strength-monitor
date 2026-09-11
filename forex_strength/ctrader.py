from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlencode
from urllib.request import urlopen

from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOAGetAccountListByAccessTokenRes,
    ProtoOASpotEvent,
    ProtoOASubscribeSpotsReq,
    ProtoOASymbolsListReq,
    ProtoOASymbolsListRes,
)
from twisted.internet import reactor

from .market import Quote


TOKEN_URL = "https://openapi.ctrader.com/apps/token"
PRICE_SCALE = 100_000.0


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str


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
        raise RuntimeError(f"cTrader token refresh failed: {payload.get('description', payload)}")
    return OAuthTokens(str(access_token), str(rotated_refresh_token))


def _normalise_symbol(name: str) -> str:
    return "".join(character for character in name.upper() if character.isalpha())


class CTraderSnapshotCollector:
    """Collect one midpoint snapshot from the cTrader Demo Open API endpoint."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        pairs: tuple[str, ...],
        on_refresh_token: Callable[[str], None],
        timeout_seconds: int = 25,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.on_refresh_token = on_refresh_token
        self.access_token = ""
        self.pairs = pairs
        self.timeout_seconds = timeout_seconds
        self.account_id: int | None = None
        self.symbol_by_id: dict[int, str] = {}
        self.quotes: dict[str, Quote] = {}
        self.error: Exception | None = None
        self.finished = False
        self.client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)

    def collect(self) -> list[Quote]:
        tokens = refresh_access_token(self.client_id, self.client_secret, self.refresh_token)
        # cTrader invalidates the old refresh token as soon as it rotates it.
        # Persist the replacement before making any further API request.
        self.access_token = tokens.access_token
        self.on_refresh_token(tokens.refresh_token)
        self.client.setConnectedCallback(self._connected)
        self.client.setDisconnectedCallback(self._disconnected)
        self.client.setMessageReceivedCallback(self._received)
        self.client.startService()
        reactor.callLater(self.timeout_seconds, self._timeout)
        reactor.run()
        if self.error:
            raise self.error
        missing = set(self.pairs) - set(self.quotes)
        if missing:
            raise RuntimeError(f"cTrader did not return a quote for: {', '.join(sorted(missing))}")
        return [self.quotes[pair] for pair in self.pairs]

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

    def _subscribe_requested_pairs(self, response: object) -> None:
        matches: dict[str, int] = {}
        for symbol in response.symbol:
            normalised = _normalise_symbol(symbol.symbolName)
            for pair in self.pairs:
                if pair not in matches and normalised == pair:
                    matches[pair] = int(symbol.symbolId)
        missing = set(self.pairs) - set(matches)
        if missing:
            self._finish(RuntimeError(f"These requested pairs are unavailable on the cTrader Demo account: {', '.join(sorted(missing))}"))
            return
        self.symbol_by_id = {symbol_id: pair for pair, symbol_id in matches.items()}
        request = ProtoOASubscribeSpotsReq()
        request.ctidTraderAccountId = self._account_id()
        request.symbolId.extend(self.symbol_by_id)
        request.subscribeToSpotTimestamp = True
        self._send(request)

    def _record_spot(self, event: object) -> None:
        pair = self.symbol_by_id.get(int(event.symbolId))
        if not pair or not event.bid or not event.ask:
            return
        midpoint = (event.bid + event.ask) / (2 * PRICE_SCALE)
        self.quotes[pair] = Quote(pair, midpoint, datetime.now(timezone.utc))
        if len(self.quotes) == len(self.pairs):
            self._finish()

    def _account_id(self) -> int:
        if self.account_id is None:
            raise RuntimeError("cTrader account was not selected.")
        return self.account_id

    def _timeout(self) -> None:
        if not self.finished:
            self._finish(RuntimeError(f"Timed out after {self.timeout_seconds} seconds waiting for cTrader quotes."))

    def _finish(self, error: Exception | None = None) -> None:
        if self.finished:
            return
        self.finished = True
        self.error = error
        self.client.stopService()
        if reactor.running:
            reactor.stop()
