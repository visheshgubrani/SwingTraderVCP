"""Daily Fyers auth guard: verify → headless retry → Telegram escalation."""

import datetime as dt
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.config import settings
from app.services import auth_readiness as ar
from app.services import token_refresh as guard

IST = ZoneInfo("Asia/Kolkata")
SESSION_DATE = dt.date(2026, 9, 15)  # Tuesday, not a configured holiday
HOLIDAY = dt.date(2026, 9, 14)  # Monday, listed in the NSE holiday calendar


def at_ist(day, hour, minute=0):
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.expiry = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return None
        self.data[key] = str(value)
        if ex:
            self.expiry[key] = ex
        return True

    async def delete(self, *keys):
        count = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                count += 1
        return count

    async def incr(self, key):
        self.data[key] = str(int(self.data.get(key, 0)) + 1)
        return int(self.data[key])

    async def expire(self, key, seconds):
        self.expiry[key] = seconds
        return True

    async def ttl(self, key):
        return self.expiry.get(key, -1)


def _readiness(healthy=True, reason="ok"):
    return {
        "authenticated": healthy,
        "healthy": healthy,
        "reason": reason,
        "expires_at": "2026-09-16T01:00:00+00:00",
        "effective_expires_at": "2026-09-16T01:00:00+00:00",
        "session_cutoff_ist": "06:30",
        "identity": "XV12345",
        "verified": healthy,
        "last_method": None,
    }


class GuardTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.db = AsyncMock()
        self._holiday_patch = patch.object(
            settings, "nse_trading_holidays", [HOLIDAY.isoformat()]
        )
        self._holiday_patch.start()
        ar._holiday_dates.cache_clear()
        self.addCleanup(self._holiday_patch.stop)
        self.addCleanup(ar._holiday_dates.cache_clear)
        # Never let a guard test reach the real Telegram API; tests that assert
        # on a send install their own mock on top of these.
        self.stub_success = AsyncMock(return_value=False)
        self.stub_digest = AsyncMock(return_value=False)
        self.stub_alert = AsyncMock(return_value=False)
        for name, stub in (
            ("send_auth_success", self.stub_success),
            ("send_premarket_readiness", self.stub_digest),
            ("send_auth_expired_alert", self.stub_alert),
        ):
            patcher = patch.object(guard.telegram_service, name, new=stub)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _patch(self, **kwargs):
        defaults = {
            "telegram_notifications_enabled": True,
            "telegram_bot_token": "token",
            "telegram_chat_id": "chat",
            "api_public_base_url": "https://api.edurel.xyz",
            "auth_notify_success": True,
            "auth_notify_cooldown_minutes": 30,
            "auth_headless_login_enabled": False,
            "auth_guard_max_headless_attempts": 3,
        }
        defaults.update(kwargs)
        if defaults["auth_headless_login_enabled"]:
            # The guard only attempts headless login when credentials exist.
            defaults.update(
                {
                    "fyers_user_id": "XV12345",
                    "fyers_app_id": "XV12345-100",
                    "fyers_pin": "4321",
                    "fyers_totp_key": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
                }
            )
        for key, value in defaults.items():
            patcher = patch.object(settings, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class HealthySessionTests(GuardTestBase):
    async def test_healthy_session_emits_event_and_quiet_success_once(self):
        self._patch()
        send_success = AsyncMock(return_value=True)
        with (
            patch.object(guard, "evaluate_auth_readiness", new=AsyncMock(return_value=_readiness())),
            patch.object(guard.telegram_service, "send_auth_success", new=send_success),
        ):
            first = await guard._guard_tick(self.db, self.redis, now=at_ist(SESSION_DATE, 7, 0))
            second = await guard._guard_tick(self.db, self.redis, now=at_ist(SESSION_DATE, 7, 15))

        self.assertEqual(first["status"], "healthy")
        self.assertEqual(second["status"], "healthy")
        self.assertEqual(send_success.await_count, 1)
        self.db.execute.assert_awaited()

    async def test_success_notification_can_be_disabled(self):
        self._patch(auth_notify_success=False)
        send_success = AsyncMock(return_value=True)
        with (
            patch.object(guard, "evaluate_auth_readiness", new=AsyncMock(return_value=_readiness())),
            patch.object(guard.telegram_service, "send_auth_success", new=send_success),
        ):
            await guard._guard_tick(self.db, self.redis, now=at_ist(SESSION_DATE, 7, 0))
        send_success.assert_not_awaited()

    async def test_last_slot_sends_readiness_digest_once(self):
        self._patch()
        digest = AsyncMock(return_value=True)
        with (
            patch.object(guard, "evaluate_auth_readiness", new=AsyncMock(return_value=_readiness())),
            patch.object(guard.telegram_service, "send_auth_success", new=AsyncMock(return_value=True)),
            patch.object(guard.telegram_service, "send_premarket_readiness", new=digest),
        ):
            await guard._guard_tick(self.db, self.redis, now=at_ist(SESSION_DATE, 8, 45))
            await guard._guard_tick(self.db, self.redis, now=at_ist(SESSION_DATE, 8, 45))
        self.assertEqual(digest.await_count, 1)

    async def test_no_digest_on_a_holiday(self):
        self._patch()
        digest = AsyncMock(return_value=True)
        with (
            patch.object(guard, "evaluate_auth_readiness", new=AsyncMock(return_value=_readiness())),
            patch.object(guard.telegram_service, "send_auth_success", new=AsyncMock(return_value=True)),
            patch.object(guard.telegram_service, "send_premarket_readiness", new=digest),
        ):
            await guard._guard_tick(self.db, self.redis, now=at_ist(HOLIDAY, 8, 45))
        digest.assert_not_awaited()


class HeadlessRetryTests(GuardTestBase):
    async def test_headless_success_refreshes_the_session(self):
        self._patch(auth_headless_login_enabled=True)
        headless = AsyncMock(return_value={"ok": True, "expires_at": "2026-09-16T01:00:00+00:00"})
        with (
            patch.object(
                guard,
                "evaluate_auth_readiness",
                new=AsyncMock(return_value=_readiness(healthy=False, reason="expired")),
            ),
            patch.object(guard, "attempt_scheduled_headless_login", new=headless),
        ):
            result = await guard._guard_tick(self.db, self.redis, now=at_ist(SESSION_DATE, 7, 0))

        self.assertEqual(result["status"], "refreshed")
        self.assertEqual(result["method"], "headless_totp")
        headless.assert_awaited_once()
        state = await guard.load_guard_state(self.redis, SESSION_DATE)
        self.assertEqual(state["attempts"], 1)
        self.assertTrue(state["success_notified"])

    async def test_headless_failure_escalates_to_the_alert(self):
        self._patch(auth_headless_login_enabled=True)
        alert = AsyncMock(return_value=True)
        with (
            patch.object(
                guard,
                "evaluate_auth_readiness",
                new=AsyncMock(return_value=_readiness(healthy=False, reason="expired")),
            ),
            patch.object(
                guard,
                "attempt_scheduled_headless_login",
                new=AsyncMock(return_value={"ok": False, "reason": "otp_rejected", "step": "verify_otp"}),
            ),
            patch.object(guard.telegram_service, "send_auth_expired_alert", new=alert),
        ):
            result = await guard._guard_tick(self.db, self.redis, now=at_ist(SESSION_DATE, 7, 0))

        self.assertEqual(result["status"], "unauthenticated")
        self.assertEqual(result["reason"], "otp_rejected")
        alert.assert_awaited_once()
        _, kwargs = alert.await_args
        self.assertTrue(kwargs["login_url"].startswith("https://api.edurel.xyz/api/v1/auth/direct-login?t="))
        self.assertEqual(kwargs["severity"], "warning")

    async def test_attempt_cap_stops_calling_the_broker(self):
        self._patch(auth_headless_login_enabled=True, auth_guard_max_headless_attempts=1)
        headless = AsyncMock(return_value={"ok": False, "reason": "network", "step": "send_login_otp_v2"})
        with (
            patch.object(
                guard,
                "evaluate_auth_readiness",
                new=AsyncMock(return_value=_readiness(healthy=False, reason="expired")),
            ),
            patch.object(guard, "attempt_scheduled_headless_login", new=headless),
        ):
            await guard._guard_tick(self.db, self.redis, now=at_ist(SESSION_DATE, 7, 0))
            second = await guard._guard_tick(self.db, self.redis, now=at_ist(SESSION_DATE, 7, 15))

        self.assertEqual(headless.await_count, 1)
        self.assertEqual(second["headless"]["reason"], "attempt_limit_reached")

    async def test_enabled_but_unconfigured_is_reported_loudly(self):
        self._patch(auth_headless_login_enabled=True)
        with (
            patch.object(
                guard,
                "evaluate_auth_readiness",
                new=AsyncMock(return_value=_readiness(healthy=False, reason="expired")),
            ),
            patch.object(settings, "fyers_pin", ""),
            patch.object(settings, "fyers_totp_key", ""),
            patch.object(settings, "fyers_user_id", ""),
            patch.object(settings, "fyers_app_id", ""),
        ):
            result = await guard._guard_tick(self.db, self.redis, now=at_ist(SESSION_DATE, 7, 0))
        self.assertEqual(result["headless"]["reason"], guard.REASON_NOT_CONFIGURED)


class AlertEscalationTests(GuardTestBase):
    async def test_alert_respects_the_notify_cooldown(self):
        self._patch()
        alert = AsyncMock(return_value=True)
        with (
            patch.object(
                guard,
                "evaluate_auth_readiness",
                new=AsyncMock(return_value=_readiness(healthy=False, reason="expired")),
            ),
            patch.object(guard.telegram_service, "send_auth_expired_alert", new=alert),
        ):
            await guard._guard_tick(self.db, self.redis, now=at_ist(SESSION_DATE, 7, 0))
            await guard._guard_tick(self.db, self.redis, now=at_ist(SESSION_DATE, 7, 15))
        self.assertEqual(alert.await_count, 1)

    async def test_premarket_slot_escalates_to_critical_once(self):
        self._patch()
        alert = AsyncMock(return_value=True)
        with (
            patch.object(
                guard,
                "evaluate_auth_readiness",
                new=AsyncMock(return_value=_readiness(healthy=False, reason="expired")),
            ),
            patch.object(guard.telegram_service, "send_auth_expired_alert", new=alert),
        ):
            await guard._guard_tick(self.db, self.redis, now=at_ist(SESSION_DATE, 8, 45))
            await guard._guard_tick(self.db, self.redis, now=at_ist(SESSION_DATE, 8, 45))

        severities = [call.kwargs["severity"] for call in alert.await_args_list]
        self.assertEqual(severities, ["critical"])
        state = await guard.load_guard_state(self.redis, SESSION_DATE)
        self.assertTrue(state["critical_sent"])

    async def test_holiday_alert_is_informational(self):
        self._patch()
        alert = AsyncMock(return_value=True)
        with (
            patch.object(
                guard,
                "evaluate_auth_readiness",
                new=AsyncMock(return_value=_readiness(healthy=False, reason="expired")),
            ),
            patch.object(guard.telegram_service, "send_auth_expired_alert", new=alert),
        ):
            await guard._guard_tick(self.db, self.redis, now=at_ist(HOLIDAY, 8, 45))
        _, kwargs = alert.await_args
        self.assertFalse(kwargs["trading_day"])
        self.assertEqual(kwargs["severity"], "warning")

    async def test_disabled_telegram_is_recorded_as_a_skipped_notification(self):
        self._patch(telegram_notifications_enabled=False)
        with (
            patch.object(
                guard,
                "evaluate_auth_readiness",
                new=AsyncMock(return_value=_readiness(healthy=False, reason="expired")),
            ),
            patch.object(
                guard.telegram_service,
                "send_auth_expired_alert",
                new=AsyncMock(return_value=False),
            ),
        ):
            result = await guard._guard_tick(self.db, self.redis, now=at_ist(SESSION_DATE, 7, 0))
        self.assertFalse(result["notified"])
        self.db.execute.assert_awaited()


class MagicLinkTests(GuardTestBase):
    async def test_requires_a_public_base_url(self):
        self._patch(api_public_base_url="")
        self.assertIsNone(await guard.build_magic_login_url(self.redis))

    async def test_builds_a_single_use_link(self):
        self._patch(api_public_base_url="https://api.edurel.xyz/")
        first = await guard.build_magic_login_url(self.redis)
        second = await guard.build_magic_login_url(self.redis)
        self.assertTrue(first.startswith("https://api.edurel.xyz/api/v1/auth/direct-login?t="))
        self.assertNotEqual(first, second)


class JobRunTests(GuardTestBase):
    async def test_run_records_job_run_success(self):
        self._patch()
        finish = AsyncMock()
        with (
            patch.object(guard, "_guard_tick", new=AsyncMock(return_value={"status": "healthy"})),
            patch.object(guard, "_start_job_run", new=AsyncMock(return_value="run-1")),
            patch.object(guard, "_finish_job_run", new=finish),
        ):
            ctx = {"redis": self.redis, "job_id": "j1", "triggered_by": "scheduler"}
            result = await guard.run_auth_guard(ctx)
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(finish.await_args.kwargs["status"], "succeeded")

    async def test_run_records_a_crash_without_raising(self):
        self._patch()
        finish = AsyncMock()
        with (
            patch.object(guard, "_guard_tick", new=AsyncMock(side_effect=RuntimeError("boom"))),
            patch.object(guard, "_start_job_run", new=AsyncMock(return_value="run-1")),
            patch.object(guard, "_finish_job_run", new=finish),
        ):
            ctx = {"redis": self.redis, "job_id": "j1", "triggered_by": "scheduler"}
            result = await guard.run_auth_guard(ctx)
        self.assertEqual(result["status"], "crashed")
        self.assertEqual(finish.await_args.kwargs["status"], "failed")

    async def test_mark_session_authenticated_is_recorded_for_today(self):
        self._patch()
        await guard.mark_session_authenticated(self.redis, method="direct_link")
        today = dt.datetime.now(dt.timezone.utc).astimezone(IST).date()
        state = await guard.load_guard_state(self.redis, today)
        self.assertTrue(state["success_notified"])
        self.assertEqual(state["last_method"], "direct_link")


if __name__ == "__main__":
    unittest.main()
