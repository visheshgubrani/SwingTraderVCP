"""Unit tests for Fyers daily 06:30 IST expiry cap and no-refresh auth path."""

import datetime
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.services.auth_service import (
    AuthUnavailableError,
    fyers_access_token_expires_at,
    get_auth_status_from_db,
    get_valid_access_token,
)

_IST = ZoneInfo("Asia/Kolkata")


class FyersExpiryCapTests(unittest.TestCase):
    def test_login_after_0630_caps_at_next_morning(self) -> None:
        now = datetime.datetime(2026, 9, 21, 7, 0, tzinfo=_IST)
        expiry = fyers_access_token_expires_at(now=now, expires_in=86400)
        expected = datetime.datetime(2026, 9, 22, 6, 30, tzinfo=_IST).astimezone(
            datetime.timezone.utc
        )
        self.assertEqual(expiry, expected)

    def test_login_before_0630_uses_same_day_cutoff(self) -> None:
        now = datetime.datetime(2026, 9, 21, 6, 0, tzinfo=_IST)
        expiry = fyers_access_token_expires_at(now=now, expires_in=86400)
        expected = datetime.datetime(2026, 9, 21, 6, 30, tzinfo=_IST).astimezone(
            datetime.timezone.utc
        )
        self.assertEqual(expiry, expected)

    def test_login_exactly_at_0630_rolls_to_next_day(self) -> None:
        now = datetime.datetime(2026, 9, 21, 6, 30, tzinfo=_IST)
        expiry = fyers_access_token_expires_at(now=now, expires_in=86400)
        expected = datetime.datetime(2026, 9, 22, 6, 30, tzinfo=_IST).astimezone(
            datetime.timezone.utc
        )
        self.assertEqual(expiry, expected)

    def test_shorter_api_ttl_wins(self) -> None:
        now = datetime.datetime(2026, 9, 21, 7, 0, tzinfo=_IST)
        expiry = fyers_access_token_expires_at(now=now, expires_in=60)
        expected = now.astimezone(datetime.timezone.utc) + datetime.timedelta(seconds=60)
        self.assertEqual(expiry, expected)

    def test_missing_api_ttl_uses_daily_cutoff(self) -> None:
        now = datetime.datetime(2026, 9, 21, 10, 0, tzinfo=_IST)
        expiry = fyers_access_token_expires_at(now=now, expires_in=None)
        expected = datetime.datetime(2026, 9, 22, 6, 30, tzinfo=_IST).astimezone(
            datetime.timezone.utc
        )
        self.assertEqual(expiry, expected)


class AuthStatusNoRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_omits_refresh_indicators(self) -> None:
        mock_db = AsyncMock()
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
        with patch(
            "app.services.auth_service.get_fyers_token",
            new_callable=AsyncMock,
            return_value={"access_token": "at", "refresh_token": "stale-rt", "expires_at": future},
        ):
            status = await get_auth_status_from_db(mock_db)

        self.assertTrue(status["healthy"])
        self.assertNotIn("has_refresh_token", status)
        self.assertNotIn("has_pin", status)

    async def test_expired_status_does_not_suggest_refresh(self) -> None:
        mock_db = AsyncMock()
        past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
        with patch(
            "app.services.auth_service.get_fyers_token",
            new_callable=AsyncMock,
            return_value={"access_token": "at", "refresh_token": "stale-rt", "expires_at": past},
        ):
            status = await get_auth_status_from_db(mock_db)

        self.assertFalse(status["healthy"])
        self.assertEqual(status["reason"], "expired")
        self.assertNotIn("has_refresh_token", status)


class GetValidAccessTokenNoRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_token_fails_closed_without_refresh(self) -> None:
        mock_db = AsyncMock()
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_db
        mock_session_ctx.__aexit__.return_value = None
        redis = AsyncMock()
        redis.get.return_value = None
        past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)

        with (
            patch("app.services.auth_service.async_session", return_value=mock_session_ctx),
            patch(
                "app.services.auth_service.get_fyers_token",
                new_callable=AsyncMock,
                return_value={"access_token": "stale", "expires_at": past},
            ),
            patch("app.services.auth_service._emit_system_event", new_callable=AsyncMock) as mock_emit,
            patch("httpx.AsyncClient") as mock_client,
        ):
            with self.assertRaises(AuthUnavailableError) as raised:
                await get_valid_access_token(redis)

        self.assertIn("Daily 2FA", str(raised.exception))
        mock_client.assert_not_called()
        mock_emit.assert_awaited()
        event_type = mock_emit.await_args.args[2]
        self.assertEqual(event_type, "auth_unavailable")

    async def test_cached_token_is_returned_without_db(self) -> None:
        redis = AsyncMock()
        redis.get.return_value = b"cached-access-token"
        with patch("app.services.auth_service.get_fyers_token", new_callable=AsyncMock) as mock_get:
            token = await get_valid_access_token(redis)
        self.assertEqual(token, "cached-access-token")
        mock_get.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
