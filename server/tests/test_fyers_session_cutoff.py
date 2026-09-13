"""Daily Fyers session-cutoff math and readiness evaluation.

Fyers retires every access token at the daily IST cutoff regardless of the
``expires_in`` it reports, so these rules decide whether the system believes a
session is alive.
"""

import datetime as dt
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.config import settings
from app.services import auth_readiness as ar

IST = ZoneInfo("Asia/Kolkata")


def ist(year, month, day, hour, minute=0):
    return dt.datetime(year, month, day, hour, minute, tzinfo=IST)


def utc(year, month, day, hour, minute=0):
    return dt.datetime(year, month, day, hour, minute, tzinfo=dt.timezone.utc)


class CutoffParsingTests(unittest.TestCase):
    def test_default_cutoff_is_0630_ist(self):
        self.assertEqual(ar.parse_session_cutoff("06:30"), dt.time(6, 30))

    def test_invalid_values_are_rejected(self):
        for value in ("", "6", "06:30:00", "25:00", "06:70", "aa:bb"):
            with self.subTest(value=value):
                with self.assertRaises(ar.SessionCutoffError):
                    ar.parse_session_cutoff(value)


class CutoffBoundaryTests(unittest.TestCase):
    def test_token_minted_after_cutoff_dies_next_morning(self):
        issued = ist(2026, 9, 15, 7, 20)
        deadline = ar.session_deadline_for_issuance(issued).astimezone(IST)
        self.assertEqual(deadline, ist(2026, 9, 16, 6, 30))

    def test_token_minted_before_cutoff_dies_same_morning(self):
        issued = ist(2026, 9, 15, 5, 0)
        deadline = ar.session_deadline_for_issuance(issued).astimezone(IST)
        self.assertEqual(deadline, ist(2026, 9, 15, 6, 30))

    def test_minted_exactly_at_cutoff_rolls_to_next_day(self):
        issued = ist(2026, 9, 15, 6, 30)
        deadline = ar.session_deadline_for_issuance(issued).astimezone(IST)
        self.assertEqual(deadline, ist(2026, 9, 16, 6, 30))

    def test_clamp_shortens_a_24h_broker_lifetime(self):
        now = ist(2026, 9, 15, 7, 20)
        clamped = ar.clamp_token_expiry(now, 86400).astimezone(IST)
        self.assertEqual(clamped, ist(2026, 9, 16, 6, 30))

    def test_clamp_keeps_a_shorter_broker_lifetime(self):
        now = ist(2026, 9, 15, 5, 0)
        clamped = ar.clamp_token_expiry(now, 1200).astimezone(IST)
        self.assertEqual(clamped, ist(2026, 9, 15, 5, 20))

    def test_next_cutoff_is_utc_aware_and_in_the_future(self):
        now = utc(2026, 9, 15, 2, 0)  # 07:30 IST
        cutoff = ar.next_session_cutoff_ist(now)
        self.assertGreater(cutoff, now)
        self.assertEqual(cutoff.astimezone(IST).hour, 6)
        self.assertEqual(cutoff.astimezone(IST).minute, 30)

    def test_seconds_until_cutoff_is_positive(self):
        self.assertGreater(ar.seconds_until_session_cutoff(ist(2026, 9, 15, 7, 0)), 0)


class LegacyTokenRowTests(unittest.TestCase):
    """Rows written before the cutoff rule must not look alive for 24 hours."""

    def test_legacy_24h_expiry_is_considered_expired_next_morning(self):
        issued = utc(2026, 8, 14, 12, 51)
        stored_expiry = issued + dt.timedelta(hours=24)
        self.assertFalse(
            ar.token_expiry_is_current(
                stored_expiry,
                issued_at=issued,
                now=ist(2026, 8, 17, 7, 0),
            )
        )

    def test_same_row_is_current_within_the_session_day(self):
        issued = utc(2026, 8, 14, 12, 51)  # 18:21 IST
        stored_expiry = issued + dt.timedelta(hours=24)
        self.assertTrue(
            ar.token_expiry_is_current(
                stored_expiry,
                issued_at=issued,
                now=ist(2026, 8, 14, 20, 0),
            )
        )
        self.assertFalse(
            ar.token_expiry_is_current(
                stored_expiry,
                issued_at=issued,
                now=ist(2026, 8, 15, 7, 0),
            )
        )

    def test_effective_expiry_without_issuance_falls_back_to_stored_value(self):
        stored = utc(2026, 9, 15, 12, 0)
        self.assertEqual(ar.effective_token_expiry(stored), stored)


class TradingSessionTests(unittest.TestCase):
    def test_weekends_are_not_sessions(self):
        self.assertFalse(ar.is_nse_session(dt.date(2026, 9, 12)))  # Saturday
        self.assertFalse(ar.is_nse_session(dt.date(2026, 9, 13)))  # Sunday

    def test_holidays_are_not_sessions(self):
        holidays = frozenset({dt.date(2026, 9, 14)})
        self.assertFalse(ar.is_nse_session(dt.date(2026, 9, 14), holidays=holidays))
        self.assertTrue(ar.is_nse_session(dt.date(2026, 9, 15), holidays=holidays))

    def test_configured_holidays_load_from_settings(self):
        with patch.object(settings, "nse_trading_holidays", ["2026-09-14"]):
            ar._holiday_dates.cache_clear()
            try:
                self.assertFalse(ar.is_nse_session(dt.date(2026, 9, 14)))
                self.assertTrue(ar.is_nse_session(dt.date(2026, 9, 15)))
            finally:
                ar._holiday_dates.cache_clear()


class VerifySessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_token_is_not_ok(self):
        result = await ar.verify_fyers_session("")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "missing_token")

    async def test_ok_response_reports_identity(self):
        client = AsyncMock()
        client.get.return_value = _FakeResponse(
            {"s": "ok", "code": 200, "data": {"fy_id": "XV12345"}}
        )
        with patch.object(settings, "fyers_app_id", "XV12345-100"):
            result = await ar.verify_fyers_session("token", client=client)
        self.assertTrue(result.ok)
        self.assertEqual(result.identity, "XV12345")

    async def test_rejected_response_is_reported_without_leaking_token(self):
        client = AsyncMock()
        client.get.return_value = _FakeResponse(
            {"s": "error", "code": -99, "message": "Invalid token"},
            status_code=401,
        )
        result = await ar.verify_fyers_session("secret-token", client=client)
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "rejected")
        self.assertEqual(result.code, -99)
        _, kwargs = client.get.call_args
        self.assertNotIn("secret-token", str(kwargs.get("params", {})))


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_token_reports_no_token(self):
        with patch.object(ar, "read_stored_token", new=AsyncMock(return_value=None)):
            status = await ar.evaluate_auth_readiness(AsyncMock(), AsyncMock(), verify=False)
        self.assertFalse(status["authenticated"])
        self.assertEqual(status["reason"], "no_token")
        self.assertEqual(status["session_cutoff_ist"], settings.fyers_session_cutoff_ist)

    async def test_expired_row_reports_expired(self):
        issued = utc(2026, 8, 14, 12, 51)
        stored = {
            "expires_at": issued + dt.timedelta(hours=24),
            "refreshed_at": issued,
            "updated_at": issued,
            "has_refresh_token": True,
        }
        with patch.object(ar, "read_stored_token", new=AsyncMock(return_value=stored)):
            status = await ar.evaluate_auth_readiness(
                AsyncMock(), AsyncMock(), verify=False, now=ist(2026, 8, 17, 7, 0)
            )
        self.assertFalse(status["authenticated"])
        self.assertEqual(status["reason"], "expired")

    async def test_live_row_is_current_without_network_when_verify_off(self):
        issued = utc(2026, 9, 15, 1, 50)  # 07:20 IST
        stored = {
            "expires_at": issued + dt.timedelta(hours=24),
            "refreshed_at": issued,
            "updated_at": issued,
            "has_refresh_token": False,
        }
        with patch.object(ar, "read_stored_token", new=AsyncMock(return_value=stored)):
            status = await ar.evaluate_auth_readiness(
                AsyncMock(), AsyncMock(), verify=False, now=ist(2026, 9, 15, 9, 0)
            )
        self.assertTrue(status["authenticated"])
        self.assertTrue(status["healthy"])
        self.assertFalse(status["verified"])

    async def test_unavailable_token_provider_marks_unhealthy(self):
        issued = utc(2026, 9, 15, 1, 50)
        stored = {
            "expires_at": issued + dt.timedelta(hours=24),
            "refreshed_at": issued,
            "updated_at": issued,
            "has_refresh_token": False,
        }
        provider = AsyncMock(side_effect=RuntimeError("no token"))
        with patch.object(ar, "read_stored_token", new=AsyncMock(return_value=stored)):
            status = await ar.evaluate_auth_readiness(
                AsyncMock(),
                AsyncMock(),
                verify=True,
                token_provider=provider,
                now=ist(2026, 9, 15, 9, 0),
            )
        self.assertFalse(status["healthy"])
        self.assertEqual(status["reason"], "unavailable")


class EnsureSessionReadyTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_token_raises_auth_unavailable(self):
        from app.services.auth_service import AuthUnavailableError

        with patch(
            "app.services.auth_service.get_valid_access_token",
            new=AsyncMock(side_effect=AuthUnavailableError("no token")),
        ):
            with self.assertRaises(AuthUnavailableError):
                await ar.ensure_session_ready(AsyncMock())

    async def test_failed_live_verification_raises(self):
        from app.services.auth_service import AuthUnavailableError

        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)
        with (
            patch(
                "app.services.auth_service.get_valid_access_token",
                new=AsyncMock(return_value="token"),
            ),
            patch.object(
                ar,
                "verify_fyers_session",
                new=AsyncMock(return_value=ar.VerifyResult(ok=False, error="rejected")),
            ),
        ):
            with self.assertRaises(AuthUnavailableError):
                await ar.ensure_session_ready(redis)
        redis.set.assert_awaited()  # health flag is cleared for the UI banner

    async def test_verified_session_passes(self):
        with (
            patch(
                "app.services.auth_service.get_valid_access_token",
                new=AsyncMock(return_value="token"),
            ),
            patch.object(
                ar,
                "verify_fyers_session",
                new=AsyncMock(return_value=ar.VerifyResult(ok=True, identity="XV1")),
            ),
        ):
            await ar.ensure_session_ready(AsyncMock())


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


if __name__ == "__main__":
    unittest.main()
