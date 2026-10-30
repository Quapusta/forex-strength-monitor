from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoMessage
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAErrorRes,
    ProtoOAGetTrendbarsReq,
    ProtoOAGetTrendbarsRes,
    ProtoOASubscribeSpotsReq,
)
from twisted.internet import defer
from twisted.python.failure import Failure

from forex_strength.ctrader import (
    BAR_BACKFILL,
    BAR_PERIOD,
    CTraderSnapshotCollector,
    OAuthTokens,
    _bar_from_trendbar,
    _normalise_symbol,
)


NOW = datetime.now(timezone.utc)
MINUTE = 1700000000 // 60


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[object, defer.Deferred]] = []

    def send(self, request: object) -> defer.Deferred:
        deferred = defer.Deferred()
        self.sent.append((request, deferred))
        return deferred

    def stopService(self) -> None:
        pass

    def bar_requests(self) -> list[tuple[ProtoOAGetTrendbarsReq, defer.Deferred]]:
        return [(request, deferred) for request, deferred in self.sent if isinstance(request, ProtoOAGetTrendbarsReq)]


def make_collector(instruments: dict[str, str], last_bar_times: dict | None = None) -> CTraderSnapshotCollector:
    collector = CTraderSnapshotCollector(
        "id",
        "secret",
        OAuthTokens("access", "refresh", NOW + timedelta(days=20)),
        ("EURUSD",),
        lambda tokens: None,
        lambda: None,
        instruments=instruments,
        last_bar_times=last_bar_times,
    )
    collector.client = FakeClient()
    collector.account_id = 1
    return collector


def symbol_list(*names: str) -> SimpleNamespace:
    return SimpleNamespace(symbol=[SimpleNamespace(symbolName=name, symbolId=index) for index, name in enumerate(names, start=1)])


def trendbars_message(*lows: int) -> ProtoMessage:
    response = ProtoOAGetTrendbarsRes(ctidTraderAccountId=1, period=BAR_PERIOD, timestamp=0)
    for offset, low in enumerate(lows):
        response.trendbar.add(volume=1, low=low, deltaOpen=10, deltaHigh=30, deltaClose=20, utcTimestampInMinutes=MINUTE + 5 * offset)
    return ProtoMessage(payloadType=response.payloadType, payload=response.SerializeToString())


def error_message(code: str) -> ProtoMessage:
    response = ProtoOAErrorRes(errorCode=code)
    return ProtoMessage(payloadType=response.payloadType, payload=response.SerializeToString())


def send_eurusd_quote(collector: CTraderSnapshotCollector) -> None:
    collector._record_spot(SimpleNamespace(symbolId=1, bid=110_000, ask=110_002))


def test_index_symbol_names_keep_their_digits() -> None:
    assert _normalise_symbol("GERMANY 40") == "GERMANY40"
    assert _normalise_symbol("US 500") != _normalise_symbol("US TECH 100")
    assert _normalise_symbol("EUR/USD") == "EURUSD"


def test_trendbar_prices_are_low_plus_deltas() -> None:
    bar = _bar_from_trendbar("GER40", SimpleNamespace(low=2_400_000_000, deltaOpen=1_000, deltaHigh=5_000, deltaClose=2_000, utcTimestampInMinutes=MINUTE))
    assert (bar.open, bar.high, bar.low, bar.close) == (24_000.01, 24_000.05, 24_000.0, 24_000.02)
    assert bar.opened_at == datetime.fromtimestamp(MINUTE * 60, timezone.utc)


def test_bars_are_requested_one_at_a_time_and_errors_do_not_stop_fx() -> None:
    collector = make_collector({"GER40": "GERMANY 40", "XAUUSD": "XAUUSD"})
    collector._subscribe_requested_pairs(symbol_list("EURUSD", "GERMANY 40", "XAUUSD"))
    assert isinstance(collector.client.sent[0][0], ProtoOASubscribeSpotsReq)
    requests = collector.client.bar_requests()
    assert [request.symbolId for request, _ in requests] == [2]

    requests[0][1].callback(trendbars_message(2_400_000_000, 2_400_100_000))
    assert [bar.symbol for bar in collector.bars] == ["GER40", "GER40"]
    requests = collector.client.bar_requests()
    assert [request.symbolId for request, _ in requests] == [2, 3]

    requests[1][1].callback(error_message("INVALID_REQUEST"))
    assert collector.bars_done and not collector.finished
    assert any(problem.startswith("XAUUSD: INVALID_REQUEST") for problem in collector.bar_errors)

    send_eurusd_quote(collector)
    assert collector.finished and collector.error is None


def test_fx_quote_waits_for_bars_before_finishing() -> None:
    collector = make_collector({"GER40": "GERMANY 40"})
    collector._subscribe_requested_pairs(symbol_list("EURUSD", "GERMANY 40"))
    send_eurusd_quote(collector)
    assert not collector.finished
    collector.client.bar_requests()[0][1].callback(trendbars_message(2_400_000_000))
    assert collector.finished and collector.error is None


def test_missing_instrument_is_reported_but_fx_still_completes() -> None:
    collector = make_collector({"EU50": "EUROPE 50"})
    collector._subscribe_requested_pairs(symbol_list("EURUSD"))
    assert collector.client.bar_requests() == []
    assert "EU50" in collector.bar_errors[0]
    send_eurusd_quote(collector)
    assert collector.finished and collector.error is None


def test_timeout_keeps_quotes_when_only_bars_are_late() -> None:
    collector = make_collector({"GER40": "GERMANY 40"})
    collector._subscribe_requested_pairs(symbol_list("EURUSD", "GERMANY 40"))
    send_eurusd_quote(collector)
    collector._timeout()
    assert collector.finished and collector.error is None
    assert "GER40" in collector.bar_errors[-1]


def test_timeout_without_fx_quotes_is_still_an_error() -> None:
    collector = make_collector({"GER40": "GERMANY 40"})
    collector._subscribe_requested_pairs(symbol_list("EURUSD", "GERMANY 40"))
    collector._timeout()
    assert collector.finished and isinstance(collector.error, RuntimeError)


def test_failed_bar_request_moves_on_to_the_next_instrument() -> None:
    collector = make_collector({"GER40": "GERMANY 40", "EU50": "EUROPE 50"})
    collector._subscribe_requested_pairs(symbol_list("EURUSD", "GERMANY 40", "EUROPE 50"))
    collector.client.bar_requests()[0][1].errback(Failure(TimeoutError("no answer")))
    assert "GER40: no bars response" in collector.bar_errors[0]
    assert [request.symbolId for request, _ in collector.client.bar_requests()] == [2, 3]


def test_bar_request_starts_at_last_stored_bar_but_not_beyond_backfill() -> None:
    recent = NOW - timedelta(hours=1)
    collector = make_collector(
        {"GER40": "GERMANY 40", "EU50": "EUROPE 50"},
        {"GER40": recent, "EU50": NOW - timedelta(days=30)},
    )
    collector._subscribe_requested_pairs(symbol_list("EURUSD", "GERMANY 40", "EUROPE 50"))
    collector.client.bar_requests()[0][1].callback(trendbars_message())
    first, second = (request for request, _ in collector.client.bar_requests())
    assert first.fromTimestamp == int(recent.timestamp() * 1000)
    oldest_allowed = (NOW - BAR_BACKFILL).timestamp() * 1000
    assert abs(second.fromTimestamp - oldest_allowed) < 60_000
    assert second.period == BAR_PERIOD
