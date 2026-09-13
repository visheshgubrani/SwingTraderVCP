"""Headless Fyers TOTP login chain (mocked broker responses)."""

import datetime as dt
import json
import logging
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.config import settings
from app.domain.totp import generate_totp
from app.services import fyers_totp

IST = ZoneInfo("Asia/Kolkata")
TOTP_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
USER_ID = "XV12345"
PIN = "4321"
NOW = dt.datetime(2026, 9, 15, 7, 15, tzinfo=IST)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeClient:
    """Records calls and replays queued responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if not self._responses:
            raise AssertionError(f"unexpected POST to {url}")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _settings_patch(enabled=True, totp=TOTP_SECRET, pin=PIN):
    return (
        patch.object(settings, "auth_headless_login_enabled", enabled),
        patch.object(settings, "fyers_pin", pin),
        patch.object(settings, "fyers_totp_key", totp),
        patch.object(settings, "fyers_user_id", USER_ID),
        patch.object(settings, "fyers_app_id", f"{USER_ID}-100"),
        patch.object(settings, "fyers_secret_key", "SECRET"),
        patch.object(settings, "fyers_redirect_uri", "https://app.edurel.xyz/callback"),
    )


_HAPPY_FLOW = [
    FakeResponse({"request_key": "rk-1"}),
    FakeResponse({"request_key": "rk-2"}),
    FakeResponse({"s": "ok", "data": {"access_token": "identity-token"}}),
    FakeResponse({"Url": "https://app.edurel.xyz/callback?auth_code=CODE123&state=abc"}),
]


class HeadlessLoginConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_flag_short_circuits_without_network(self):
        client = FakeClient([])
        with patch.object(settings, "auth_headless_login_enabled", False):
            result = await fyers_totp.attempt_headless_login(client=client)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, fyers_totp.REASON_DISABLED)
        self.assertEqual(client.calls, [])

    async def test_missing_credentials_are_reported(self):
        client = FakeClient([])
        for enabled, totp, pin, expected_missing in (
            (True, "", PIN, "FYERS_TOTP_KEY"),
            (True, TOTP_SECRET, "", "FYERS_PIN"),
        ):
            with self.subTest(missing=expected_missing):
                with (
                    patch.object(settings, "auth_headless_login_enabled", enabled),
                    patch.object(settings, "fyers_pin", pin),
                    patch.object(settings, "fyers_totp_key", totp),
                    patch.object(settings, "fyers_user_id", USER_ID),
                    patch.object(settings, "fyers_app_id", f"{USER_ID}-100"),
                ):
                    result = await fyers_totp.attempt_headless_login(client=client)
                self.assertFalse(result.ok)
                self.assertEqual(result.reason, fyers_totp.REASON_NOT_CONFIGURED)
                self.assertIn(expected_missing, result.detail or "")
        self.assertEqual(client.calls, [])


class HeadlessLoginFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path_sends_expected_payloads(self):
        client = FakeClient(_HAPPY_FLOW)
        patches = _settings_patch()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patch.object(
                fyers_totp,
                "exchange_authorization_code",
                new=AsyncMock(return_value={"ok": True, "access_token": "access-1", "expires_in": 86400}),
            ) as exchange,
        ):
            result = await fyers_totp.attempt_headless_login(client=client, now=NOW)

        self.assertTrue(result.ok)
        self.assertEqual(result.access_token, "access-1")
        self.assertEqual(result.expires_in, 86400)
        exchange.assert_awaited_once_with("CODE123")

        urls = [call["url"] for call in client.calls]
        self.assertEqual(
            urls,
            [
                f"{fyers_totp.VAGATOR_BASE_URL}/send_login_otp_v2",
                f"{fyers_totp.VAGATOR_BASE_URL}/verify_otp",
                f"{fyers_totp.VAGATOR_BASE_URL}/verify_pin_v2",
                fyers_totp.TOKEN_REQUEST_URL,
            ],
        )

        import base64

        expected_user = base64.b64encode(USER_ID.encode()).decode()
        expected_pin = base64.b64encode(PIN.encode()).decode()
        self.assertEqual(client.calls[0]["json"]["fy_id"], expected_user)
        self.assertEqual(client.calls[0]["json"]["app_id"], "2")
        self.assertEqual(client.calls[1]["json"]["request_key"], "rk-1")
        self.assertEqual(client.calls[1]["json"]["otp"], generate_totp(TOTP_SECRET, at=NOW))
        self.assertEqual(client.calls[2]["json"]["identity_type"], "pin")
        self.assertEqual(client.calls[2]["json"]["identifier"], expected_pin)
        self.assertEqual(client.calls[3]["headers"]["Authorization"], "Bearer identity-token")
        self.assertEqual(client.calls[3]["json"]["app_id"], USER_ID)
        self.assertEqual(
            client.calls[3]["json"]["redirect_uri"], "https://app.edurel.xyz/callback"
        )

    async def test_waits_for_a_fresh_totp_step(self):
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        client = FakeClient(_HAPPY_FLOW)
        patches = _settings_patch()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patch.object(fyers_totp, "seconds_remaining", return_value=1.0),
            patch.object(
                fyers_totp,
                "exchange_authorization_code",
                new=AsyncMock(return_value={"ok": True, "access_token": "a", "expires_in": 100}),
            ),
        ):
            result = await fyers_totp.attempt_headless_login(
                client=client, now=NOW, sleep=fake_sleep
            )
        self.assertTrue(result.ok)
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 1.5)

    async def test_otp_rejection_is_typed(self):
        client = FakeClient(
            [
                FakeResponse({"request_key": "rk-1"}),
                FakeResponse({"s": "error", "code": -2, "message": "something went wrong"}),
            ]
        )
        patches = _settings_patch()
        with (patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]):
            result = await fyers_totp.attempt_headless_login(client=client, now=NOW)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, fyers_totp.REASON_OTP_REJECTED)
        self.assertEqual(result.step, fyers_totp.STEP_VERIFY_OTP)

    async def test_pin_rejection_is_typed(self):
        client = FakeClient(
            [
                FakeResponse({"request_key": "rk-1"}),
                FakeResponse({"request_key": "rk-2"}),
                FakeResponse({"s": "error", "code": -1018, "message": "something went wrong"}),
            ]
        )
        patches = _settings_patch()
        with (patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]):
            result = await fyers_totp.attempt_headless_login(client=client, now=NOW)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, fyers_totp.REASON_IDENTITY_REJECTED)
        self.assertEqual(result.step, fyers_totp.STEP_VERIFY_PIN)

    async def test_missing_identity_token_is_identity_rejected(self):
        client = FakeClient(
            [
                FakeResponse({"request_key": "rk-1"}),
                FakeResponse({"request_key": "rk-2"}),
                FakeResponse({"s": "ok", "data": {}}),
            ]
        )
        patches = _settings_patch()
        with (patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]):
            result = await fyers_totp.attempt_headless_login(client=client, now=NOW)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, fyers_totp.REASON_IDENTITY_REJECTED)

    async def test_missing_auth_code_is_typed(self):
        client = FakeClient(_HAPPY_FLOW[:3] + [FakeResponse({"Url": "https://x/cb?state=none"})])
        patches = _settings_patch()
        with (patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]):
            result = await fyers_totp.attempt_headless_login(client=client, now=NOW)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, fyers_totp.REASON_AUTH_CODE_MISSING)
        self.assertEqual(result.step, fyers_totp.STEP_TOKEN)

    async def test_exchange_rejection_is_typed(self):
        client = FakeClient(_HAPPY_FLOW)
        patches = _settings_patch()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patch.object(
                fyers_totp,
                "exchange_authorization_code",
                new=AsyncMock(return_value={"ok": False, "message": "invalid code"}),
            ),
        ):
            result = await fyers_totp.attempt_headless_login(client=client, now=NOW)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, fyers_totp.REASON_EXCHANGE_REJECTED)

    async def test_network_failure_is_typed(self):
        import httpx

        client = FakeClient([httpx.ConnectError("boom")])
        patches = _settings_patch()
        with (patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]):
            result = await fyers_totp.attempt_headless_login(client=client, now=NOW)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, fyers_totp.REASON_NETWORK)

    async def test_invalid_totp_secret_is_typed(self):
        client = FakeClient([])
        patches = _settings_patch(totp="!!!!not-base32!!!!")
        with (patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]):
            result = await fyers_totp.attempt_headless_login(client=client, now=NOW)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, fyers_totp.REASON_TOTP_GENERATION)
        self.assertEqual(client.calls, [])

    async def test_secrets_never_reach_the_logs(self):
        client = FakeClient(
            [
                FakeResponse({"request_key": "rk-1"}),
                FakeResponse({"s": "error", "code": -2, "message": "nope"}),
            ]
        )
        patches = _settings_patch()
        with self.assertLogs(level=logging.DEBUG) as captured:
            with (patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]):
                await fyers_totp.attempt_headless_login(client=client, now=NOW)
        blob = "\n".join(captured.output)
        self.assertNotIn(PIN, blob)
        self.assertNotIn(generate_totp(TOTP_SECRET, at=NOW), blob)
        self.assertNotIn("identity-token", blob)


class AuthCodeExtractionTests(unittest.TestCase):
    def test_extracts_auth_code(self):
        self.assertEqual(
            fyers_totp.extract_auth_code("https://x/cb?auth_code=ABC123&state=s"), "ABC123"
        )

    def test_falls_back_to_code_parameter(self):
        self.assertEqual(fyers_totp.extract_auth_code("https://x/cb?code=ZZ"), "ZZ")

    def test_missing_values_return_none(self):
        self.assertIsNone(fyers_totp.extract_auth_code(None))
        self.assertIsNone(fyers_totp.extract_auth_code("not-a-url"))
        self.assertIsNone(fyers_totp.extract_auth_code("https://x/cb?other=1"))


class ExchangeWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_exchange_reports_sdk_failure_without_raising(self):
        with patch("fyers_apiv3.fyersModel.SessionModel") as model:
            model.return_value.generate_token.side_effect = RuntimeError("sdk blew up")
            result = await fyers_totp.exchange_authorization_code("CODE")
        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "exchange_error")

    async def test_exchange_maps_success(self):
        with patch("fyers_apiv3.fyersModel.SessionModel") as model:
            model.return_value.generate_token.return_value = {
                "s": "ok",
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 86400,
            }
            result = await fyers_totp.exchange_authorization_code("CODE")
        self.assertTrue(result["ok"])
        self.assertEqual(result["access_token"], "AT")
        self.assertEqual(result["refresh_token"], "RT")


if __name__ == "__main__":
    unittest.main()
