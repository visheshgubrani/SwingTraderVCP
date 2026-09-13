"""Unit tests for Fyers token refresh with 4-digit PIN (validate-refresh-token)."""

import hashlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import datetime

from app.config import settings
from app.services.auth_service import (
    _try_refresh_token,
    get_auth_status_from_db,
    refresh_and_save,
)


class FyersTokenRefreshPinTests(unittest.IsolatedAsyncioTestCase):
    async def test_try_refresh_token_sends_pin_and_sha256_hash(self):
        """Verify _try_refresh_token sends grant_type, appIdHash, refresh_token, and pin."""
        expected_hash = hashlib.sha256(b"TEST_APP_ID:TEST_SECRET_KEY").hexdigest()
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "s": "ok",
            "code": 200,
            "message": "Successfully generated access token",
            "access_token": "new_access_token_xyz",
            "refresh_token": "new_refresh_token_abc",
            "expires_in": 86400,
        }

        with (
            patch.object(settings, "fyers_app_id", "TEST_APP_ID"),
            patch.object(settings, "fyers_secret_key", "TEST_SECRET_KEY"),
            patch.object(settings, "fyers_pin", "4321"),
            patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post,
        ):
            mock_post.return_value = fake_response
            result = await _try_refresh_token("existing_refresh_token_123")

        self.assertIsNotNone(result)
        self.assertEqual(result["access_token"], "new_access_token_xyz")
        self.assertEqual(result["refresh_token"], "new_refresh_token_abc")
        self.assertEqual(result["expires_in"], 86400)

        mock_post.assert_awaited_once()
        url = mock_post.await_args.args[0]
        json_payload = mock_post.await_args.kwargs.get("json", {})

        self.assertEqual(url, "https://api-t1.fyers.in/api/v3/validate-refresh-token")
        self.assertEqual(json_payload.get("grant_type"), "refresh_token")
        self.assertEqual(json_payload.get("refresh_token"), "existing_refresh_token_123")
        self.assertEqual(json_payload.get("appIdHash"), expected_hash)
        self.assertEqual(json_payload.get("pin"), "4321")

    async def test_try_refresh_token_fails_when_pin_missing(self):
        """Verify _try_refresh_token fails immediately without network call if FYERS_PIN is missing."""
        with (
            patch.object(settings, "fyers_app_id", "TEST_APP_ID"),
            patch.object(settings, "fyers_secret_key", "TEST_SECRET_KEY"),
            patch.object(settings, "fyers_pin", ""),
            patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post,
        ):
            result = await _try_refresh_token("existing_refresh_token_123")

        self.assertIsNone(result)
        mock_post.assert_not_called()

    async def test_try_refresh_token_handles_fyers_error_response(self):
        """Verify _try_refresh_token handles Fyers rejection (s != 'ok') gracefully."""
        fake_response = MagicMock()
        fake_response.status_code = 400
        fake_response.json.return_value = {
            "s": "error",
            "code": -371,
            "message": "Invalid PIN or refresh token expired",
        }

        with (
            patch.object(settings, "fyers_app_id", "TEST_APP_ID"),
            patch.object(settings, "fyers_secret_key", "TEST_SECRET_KEY"),
            patch.object(settings, "fyers_pin", "0000"),
            patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post,
        ):
            mock_post.return_value = fake_response
            result = await _try_refresh_token("expired_refresh_token")

        self.assertIsNone(result)

    async def test_refresh_and_save_records_missing_pin_event(self):
        """Verify refresh_and_save emits specific missing_fyers_pin system event when PIN unset."""
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        with (
            patch.object(settings, "fyers_pin", ""),
            patch(
                "app.services.auth_service.get_fyers_token",
                new_callable=AsyncMock,
                return_value={"refresh_token": "sample_rt"},
            ),
            patch("app.services.auth_service._emit_system_event", new_callable=AsyncMock) as mock_emit,
            patch("app.services.auth_service._set_auth_health", new_callable=AsyncMock) as mock_set_health,
        ):
            new_token = await refresh_and_save(mock_db, mock_redis)

        self.assertIsNone(new_token)
        mock_emit.assert_awaited_once_with(
            mock_db,
            "critical",
            "auth_refresh_failed",
            {"reason": "missing_fyers_pin"},
            redis=mock_redis,
        )
        mock_set_health.assert_awaited_once_with(mock_redis, False)
        mock_db.commit.assert_awaited_once()

    async def test_refresh_and_save_success_flow(self):
        """Verify full refresh_and_save updates DB, cache, and marks auth healthy on success."""
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        with (
            patch.object(settings, "fyers_pin", "1234"),
            patch(
                "app.services.auth_service.get_fyers_token",
                new_callable=AsyncMock,
                return_value={"refresh_token": "valid_rt"},
            ),
            patch(
                "app.services.auth_service._try_refresh_token",
                new_callable=AsyncMock,
                return_value={
                    "access_token": "brand_new_token",
                    "refresh_token": "reused_or_rotated_rt",
                    "expires_in": 86400,
                },
            ),
            patch("app.services.auth_service.persist_and_cache_fyers_token", new_callable=AsyncMock) as mock_persist,
            patch("app.services.auth_service._emit_system_event", new_callable=AsyncMock) as mock_emit,
        ):
            new_token = await refresh_and_save(mock_db, mock_redis)

        self.assertEqual(new_token, "brand_new_token")
        mock_persist.assert_awaited_once()
        self.assertEqual(mock_persist.await_args.kwargs["access_token"], "brand_new_token")
        self.assertEqual(mock_persist.await_args.kwargs["refresh_token"], "reused_or_rotated_rt")

        mock_emit.assert_awaited_once()
        self.assertEqual(mock_emit.await_args.args[1], "info")
        self.assertEqual(mock_emit.await_args.args[2], "auth_refresh_succeeded")

    async def test_get_auth_status_reports_pin_and_refresh_token(self):
        """Verify get_auth_status_from_db exposes readiness indicators for the UI."""
        from app.services import auth_readiness as ar

        mock_db = AsyncMock()
        issued = datetime.datetime.now(datetime.timezone.utc)
        stored = {
            "expires_at": issued + datetime.timedelta(hours=24),
            "refreshed_at": issued,
            "updated_at": issued,
            "has_refresh_token": True,
        }

        async def _readiness(stored_row):
            with patch.object(ar, "read_stored_token", new=AsyncMock(return_value=stored_row)):
                return await get_auth_status_from_db(mock_db)

        with patch.object(settings, "fyers_pin", "1234"):
            status = await _readiness(None)
            self.assertFalse(status["authenticated"])
            self.assertFalse(status["has_refresh_token"])
            self.assertTrue(status["has_pin"])

            status = await _readiness(stored)
            self.assertTrue(status["authenticated"])
            self.assertTrue(status["healthy"])
            self.assertTrue(status["has_refresh_token"])
            self.assertTrue(status["has_pin"])
            self.assertEqual(status["session_cutoff_ist"], settings.fyers_session_cutoff_ist)
            # The stored 24h lifetime is reported clamped to the daily cutoff.
            self.assertLess(
                datetime.datetime.fromisoformat(status["expires_at"]),
                issued + datetime.timedelta(hours=24),
            )

    async def test_refresh_attempts_are_rate_limited(self):
        """A dead session must not hammer the broker or spam system events."""
        from app.services import auth_service

        mock_db = AsyncMock()
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=None)  # cooldown key already present

        with patch.object(settings, "fyers_pin", "1234"):
            with patch(
                "app.services.auth_service.get_fyers_token", new_callable=AsyncMock
            ) as get_token:
                result = await auth_service.refresh_and_save(mock_db, mock_redis)

        self.assertIsNone(result)
        get_token.assert_not_awaited()

    async def test_persisted_expiry_is_clamped_to_the_session_cutoff(self):
        """A 24h broker lifetime must never be stored past the 06:30 IST cutoff."""
        from app.services import auth_readiness as ar
        from app.services import auth_service

        fixed = datetime.datetime(2026, 9, 15, 7, 20, tzinfo=datetime.timezone.utc)
        expected = ar.clamp_token_expiry(fixed, 86400)
        self.assertLess(
            expected, fixed + datetime.timedelta(hours=24)
        )  # sanity: the clamp actually bites

        saved = {}

        async def _save(db, access_token, refresh_token, expires_at):
            saved["expires_at"] = expires_at

        mock_redis = AsyncMock()
        with (
            patch("app.services.auth_service.save_fyers_token", new=_save),
            patch("app.services.auth_service.clamp_token_expiry", return_value=expected),
        ):
            await auth_service.persist_and_cache_fyers_token(
                AsyncMock(),
                mock_redis,
                access_token="AT",
                refresh_token=None,
                expires_at=fixed + datetime.timedelta(hours=24),
                expires_in=86400,
            )

        self.assertEqual(saved["expires_at"], expected)


if __name__ == "__main__":
    unittest.main()
