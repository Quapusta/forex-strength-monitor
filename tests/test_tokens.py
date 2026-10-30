import io
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from forex_strength import ctrader
from forex_strength.ctrader import CTraderSnapshotCollector, OAuthTokens


NOW = datetime.now(timezone.utc)
FRESH = OAuthTokens("new-access", "new-refresh", NOW + timedelta(days=30))


def make_collector(stored: OAuthTokens) -> tuple[CTraderSnapshotCollector, list]:
    saved: list = []
    collector = CTraderSnapshotCollector("id", "secret", stored, ("EURUSD",), saved.append, lambda: saved.append("forgot"))
    collector.client = SimpleNamespace(stopService=lambda: None)
    return collector, saved


def test_stored_access_token_is_reused_without_refresh() -> None:
    collector, saved = make_collector(OAuthTokens("stored-access", "stored-refresh", NOW + timedelta(days=20)))
    with patch.object(ctrader, "refresh_access_token", side_effect=AssertionError("must not refresh")):
        assert collector._choose_access_token() == "stored-access"
    assert collector.using_stored_access_token
    assert saved == []


def test_token_close_to_expiry_is_refreshed_and_saved() -> None:
    collector, saved = make_collector(OAuthTokens("stored-access", "stored-refresh", NOW + timedelta(days=3)))
    with patch.object(ctrader, "refresh_access_token", return_value=FRESH) as refresh:
        assert collector._choose_access_token() == "new-access"
    refresh.assert_called_once_with("id", "secret", "stored-refresh")
    assert saved == [FRESH]
    assert not collector.using_stored_access_token


def test_missing_access_token_is_refreshed() -> None:
    collector, saved = make_collector(OAuthTokens("", "stored-refresh", None))
    with patch.object(ctrader, "refresh_access_token", return_value=FRESH):
        assert collector._choose_access_token() == "new-access"
    assert saved == [FRESH]


def test_failed_refresh_falls_back_to_still_valid_stored_token() -> None:
    collector, saved = make_collector(OAuthTokens("stored-access", "dead-refresh", NOW + timedelta(days=3)))
    with patch.object(ctrader, "refresh_access_token", side_effect=RuntimeError("Access denied")):
        assert collector._choose_access_token() == "stored-access"
    assert collector.refresh_warning and "Access denied" in collector.refresh_warning
    assert saved == []


def test_failed_refresh_without_valid_stored_token_raises() -> None:
    collector, _ = make_collector(OAuthTokens("stored-access", "dead-refresh", NOW - timedelta(hours=1)))
    with patch.object(ctrader, "refresh_access_token", side_effect=RuntimeError("Access denied")):
        try:
            collector._choose_access_token()
        except RuntimeError as error:
            assert "Access denied" in str(error)
        else:
            raise AssertionError("expected RuntimeError")


def test_token_rejection_marks_stored_token_for_removal() -> None:
    collector, _ = make_collector(OAuthTokens("stored-access", "stored-refresh", NOW + timedelta(days=20)))
    collector._handle_error(SimpleNamespace(errorCode="CH_ACCESS_TOKEN_INVALID", description="Invalid token"))
    assert collector.access_token_rejected
    assert collector.finished
    assert "CH_ACCESS_TOKEN_INVALID" in str(collector.error)


def test_other_errors_do_not_drop_the_token() -> None:
    collector, _ = make_collector(OAuthTokens("stored-access", "stored-refresh", NOW + timedelta(days=20)))
    collector._handle_error(SimpleNamespace(errorCode="MARKET_CLOSED", description=""))
    assert not collector.access_token_rejected
    assert not collector.finished


def test_refresh_reads_expiry_and_never_echoes_tokens() -> None:
    def respond(payload: dict):
        return patch.object(ctrader, "urlopen", return_value=io.BytesIO(json.dumps(payload).encode()))

    with respond({"accessToken": "a", "refreshToken": "r", "expiresIn": 2628000}):
        tokens = ctrader.refresh_access_token("id", "secret", "old")
    assert tokens.access_token == "a" and tokens.refresh_token == "r"
    assert timedelta(days=30) < tokens.expires_at - NOW < timedelta(days=31)

    with respond({"accessToken": "leaky-access-token", "refreshToken": None}):
        try:
            ctrader.refresh_access_token("id", "secret", "old")
        except RuntimeError as error:
            assert "leaky-access-token" not in str(error)
        else:
            raise AssertionError("expected RuntimeError")
