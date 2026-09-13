"""Live-probe script guards: precise config reporting and no accidental broker calls.

The probe is what the owner runs on the VPS before trusting the guard, so it
must never fire a broker request while configuration is incomplete (a stray
``send_login_otp_v2`` would text a real OTP) and must never print secrets.
"""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, patch

from app.config import settings
from scripts import fyers_auth_probe as probe


class ProbeConfigTests(unittest.TestCase):
    def test_missing_list_names_only_what_is_absent(self):
        with (
            patch.object(settings, "fyers_app_id", "XV12345-100"),
            patch.object(settings, "fyers_secret_key", "SECRET"),
            patch.object(settings, "fyers_user_id", ""),
            patch.object(settings, "fyers_pin", "4321"),
            patch.object(settings, "fyers_totp_key", ""),
        ):
            missing = probe._headless_missing()
        self.assertEqual(missing, ["FYERS_TOTP_KEY"])

    def test_missing_list_is_empty_when_configured(self):
        with (
            patch.object(settings, "fyers_app_id", "XV12345-100"),
            patch.object(settings, "fyers_secret_key", "SECRET"),
            patch.object(settings, "fyers_user_id", ""),
            patch.object(settings, "fyers_pin", "4321"),
            patch.object(settings, "fyers_totp_key", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"),
        ):
            self.assertEqual(probe._headless_missing(), [])

    def test_mask_never_reveals_the_secret(self):
        secret = "SUPER-SECRET-VALUE"
        masked = probe._mask(secret)
        self.assertNotIn(secret, masked)
        self.assertIn("set", masked)
        self.assertEqual(probe._mask(""), "unset")
        self.assertEqual(probe._mask(None), "unset")

    def test_config_report_contains_no_secret_material(self):
        pin, totp, token, chat, secret = (
            "4321",
            "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
            "123456:BOT-TOKEN",
            "987654321",
            "APP-SECRET",
        )
        buffer = io.StringIO()
        with (
            patch.object(settings, "fyers_app_id", "XV12345-100"),
            patch.object(settings, "fyers_secret_key", secret),
            patch.object(settings, "fyers_pin", pin),
            patch.object(settings, "fyers_totp_key", totp),
            patch.object(settings, "telegram_bot_token", token),
            patch.object(settings, "telegram_chat_id", chat),
            redirect_stdout(buffer),
        ):
            probe._config_report()
        output = buffer.getvalue()
        for value in (pin, totp, token, chat, secret):
            with self.subTest(value=value[:8]):
                self.assertNotIn(value, output)
        self.assertIn("FYERS_PIN", output)
        self.assertIn("session cutoff (IST)", output)


class ProbeSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_makes_no_broker_call_when_unconfigured(self):
        post = AsyncMock()
        buffer = io.StringIO()
        with (
            patch.object(settings, "fyers_app_id", "XV12345-100"),
            patch.object(settings, "fyers_secret_key", "SECRET"),
            patch.object(settings, "fyers_pin", "4321"),
            patch.object(settings, "fyers_totp_key", ""),
            patch.object(probe, "_post_json", new=post),
            redirect_stdout(buffer),
        ):
            code = await probe._dry_run()
        self.assertEqual(code, 2)
        post.assert_not_awaited()
        self.assertIn("No broker request was made", buffer.getvalue())

    async def test_verify_makes_no_broker_call_when_unconfigured(self):
        with (
            patch.object(settings, "fyers_app_id", "XV12345-100"),
            patch.object(settings, "fyers_secret_key", "SECRET"),
            patch.object(settings, "fyers_pin", "4321"),
            patch.object(settings, "fyers_totp_key", ""),
            patch(
                "app.services.fyers_totp.attempt_headless_login", new=AsyncMock()
            ) as attempt,
            redirect_stdout(io.StringIO()),
        ):
            code = await probe._verify()
        self.assertEqual(code, 2)
        attempt.assert_not_awaited()

    async def test_telegram_test_reports_disabled_without_sending(self):
        buffer = io.StringIO()
        with (
            patch.object(settings, "telegram_notifications_enabled", False),
            patch(
                "app.services.telegram_service.send_message", new=AsyncMock()
            ) as send,
            redirect_stdout(buffer),
        ):
            code = await probe._telegram_test()
        self.assertEqual(code, 2)
        send.assert_not_awaited()
        self.assertIn("nothing sent", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
