"""Unit tests for weekday Telegram Fyers re-auth reminder (no live Bot API)."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.auth_reminder import build_auth_reminder_message, run_auth_reminder
from app.services.telegram_notifier import TelegramSendError


def _job_session():
    mock_db = AsyncMock()
    mock_scalar = MagicMock()
    run_id = uuid4()
    mock_scalar.scalar.return_value = run_id
    mock_db.execute.return_value = mock_scalar
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__.return_value = mock_db
    mock_session_ctx.__aexit__.return_value = None
    return mock_db, mock_session_ctx, run_id


class AuthReminderMessageTests(unittest.TestCase):
    def test_message_points_at_app_and_contains_no_secrets(self) -> None:
        with patch("app.services.auth_reminder.settings") as mock_settings:
            mock_settings.frontend_public_url = "https://app.edurel.xyz"
            message = build_auth_reminder_message()

        self.assertIn("06:30 IST", message)
        self.assertIn("https://app.edurel.xyz", message)
        self.assertIn("2FA", message)
        self.assertNotIn("access_token", message)
        self.assertNotIn("auth_code", message)
        self.assertNotIn("secret", message.lower())


class AuthReminderJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_skips_telegram_when_auth_is_healthy(self) -> None:
        mock_db, mock_session_ctx, run_id = _job_session()
        ctx = {"redis": AsyncMock(), "job_id": "cron_123", "triggered_by": "scheduler"}

        with (
            patch("app.services.auth_reminder.async_session", return_value=mock_session_ctx),
            patch(
                "app.services.auth_reminder.get_auth_status_from_db",
                new_callable=AsyncMock,
                return_value={"healthy": True},
            ),
            patch("app.services.auth_reminder.send_telegram_message", new_callable=AsyncMock) as mock_send,
            patch("app.services.auth_reminder._emit_system_event", new_callable=AsyncMock) as mock_emit,
        ):
            result = await run_auth_reminder(ctx)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["run_id"], str(run_id))
        mock_send.assert_not_awaited()
        mock_emit.assert_awaited()
        self.assertEqual(mock_emit.await_args.args[2], "auth_reminder_skipped")

        insert_params = mock_db.execute.call_args_list[0][0][1]
        self.assertEqual(insert_params["triggered_by"], "scheduler")
        self.assertEqual(insert_params["job_key"], "auth_reminder_cron_123")
        update_sql = str(mock_db.execute.call_args_list[-1][0][0])
        self.assertIn("succeeded", update_sql)

    async def test_sends_telegram_when_auth_is_unhealthy(self) -> None:
        mock_db, mock_session_ctx, run_id = _job_session()
        ctx = {"job_id": "cron_456", "triggered_by": "scheduler"}

        with (
            patch("app.services.auth_reminder.async_session", return_value=mock_session_ctx),
            patch(
                "app.services.auth_reminder.get_auth_status_from_db",
                new_callable=AsyncMock,
                return_value={"healthy": False, "reason": "expired"},
            ),
            patch("app.services.auth_reminder.telegram_configured", return_value=True),
            patch("app.services.auth_reminder.send_telegram_message", new_callable=AsyncMock) as mock_send,
            patch("app.services.auth_reminder._emit_system_event", new_callable=AsyncMock) as mock_emit,
            patch(
                "app.services.auth_reminder.build_auth_reminder_message",
                return_value="Fyers session expired at 06:30 IST. Open https://app.edurel.xyz and complete today's 2FA login.",
            ),
        ):
            result = await run_auth_reminder(ctx)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["run_id"], str(run_id))
        mock_send.assert_awaited_once()
        self.assertIn("06:30 IST", mock_send.await_args.args[0])
        self.assertEqual(mock_emit.await_args.args[2], "auth_reminder_sent")

    async def test_fails_closed_when_telegram_is_not_configured(self) -> None:
        mock_db, mock_session_ctx, run_id = _job_session()
        ctx = {"job_id": "cron_789"}

        with (
            patch("app.services.auth_reminder.async_session", return_value=mock_session_ctx),
            patch(
                "app.services.auth_reminder.get_auth_status_from_db",
                new_callable=AsyncMock,
                return_value={"healthy": False, "reason": "expired"},
            ),
            patch("app.services.auth_reminder.telegram_configured", return_value=False),
            patch("app.services.auth_reminder.send_telegram_message", new_callable=AsyncMock) as mock_send,
            patch("app.services.auth_reminder._emit_system_event", new_callable=AsyncMock) as mock_emit,
        ):
            result = await run_auth_reminder(ctx)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "telegram_not_configured")
        self.assertEqual(result["run_id"], str(run_id))
        mock_send.assert_not_awaited()
        self.assertEqual(mock_emit.await_args.args[1], "critical")
        self.assertEqual(mock_emit.await_args.args[2], "auth_reminder_failed")
        update_sql = str(mock_db.execute.call_args_list[-1][0][0])
        self.assertIn("failed", update_sql)

    async def test_send_failure_does_not_crash_worker(self) -> None:
        mock_db, mock_session_ctx, run_id = _job_session()
        ctx = {"job_id": "cron_fail"}

        with (
            patch("app.services.auth_reminder.async_session", return_value=mock_session_ctx),
            patch(
                "app.services.auth_reminder.get_auth_status_from_db",
                new_callable=AsyncMock,
                return_value={"healthy": False, "reason": "no_token"},
            ),
            patch("app.services.auth_reminder.telegram_configured", return_value=True),
            patch(
                "app.services.auth_reminder.send_telegram_message",
                new_callable=AsyncMock,
                side_effect=TelegramSendError("Telegram send rejected"),
            ),
            patch("app.services.auth_reminder._emit_system_event", new_callable=AsyncMock),
        ):
            result = await run_auth_reminder(ctx)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "telegram_send_failed")
        self.assertEqual(result["run_id"], str(run_id))

    async def test_unexpected_exception_is_recorded_not_reraised(self) -> None:
        mock_db, mock_session_ctx, _run_id = _job_session()
        ctx = {"job_id": "cron_crash"}

        with (
            patch("app.services.auth_reminder.async_session", return_value=mock_session_ctx),
            patch(
                "app.services.auth_reminder.get_auth_status_from_db",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Redis connection lost"),
            ),
            patch("app.services.auth_reminder._emit_system_event", new_callable=AsyncMock) as mock_emit,
        ):
            result = await run_auth_reminder(ctx)

        self.assertEqual(result["status"], "crashed")
        self.assertIn("Redis connection lost", result["error"])
        mock_emit.assert_awaited()
        self.assertEqual(mock_emit.await_args.args[2], "auth_reminder_failed")


if __name__ == "__main__":
    unittest.main()
