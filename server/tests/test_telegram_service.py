"""Telegram notifier: message shape, escaping, and never-fatal failure handling."""

import json
import unittest
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.services import telegram_service as tg

BOT_TOKEN = "123456:TEST-BOT-TOKEN"
CHAT_ID = "987654321"


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"ok": True}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def post(self, url, json=None):
        self.calls.append({"url": url, "json": json})
        if self.error:
            raise self.error
        return self.response


def _enabled(totp_ok=True):
    return [
        patch.object(settings, "telegram_notifications_enabled", True),
        patch.object(settings, "telegram_bot_token", BOT_TOKEN if totp_ok else ""),
        patch.object(settings, "telegram_chat_id", CHAT_ID if totp_ok else ""),
        patch.object(settings, "telegram_api_base_url", "https://api.telegram.org"),
    ]


class TelegramSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_never_calls_telegram(self):
        client = FakeClient(FakeResponse())
        with patch.object(settings, "telegram_notifications_enabled", False):
            sent = await tg.send_message(AsyncMock(), "hello", client=client)
        self.assertFalse(sent)
        self.assertEqual(client.calls, [])

    async def test_enabled_but_unconfigured_records_event(self):
        client = FakeClient(FakeResponse())
        db = AsyncMock()
        patches = _enabled(totp_ok=False)
        with (patches[0], patches[1], patches[2], patches[3]):
            sent = await tg.send_message(db, "hello", client=client)
        self.assertFalse(sent)
        self.assertEqual(client.calls, [])
        db.execute.assert_awaited()

    async def test_message_payload_shape_with_button(self):
        client = FakeClient(FakeResponse())
        patches = _enabled()
        with (patches[0], patches[1], patches[2], patches[3]):
            sent = await tg.send_auth_expired_alert(
                None,
                login_url="https://api.edurel.xyz/api/v1/auth/direct-login?t=abc",
                session_date="2026-09-15",
                reason="otp_rejected",
                minutes_to_open=25,
                client=client,
            )
        self.assertTrue(sent)
        call = client.calls[0]
        self.assertTrue(call["url"].startswith("https://api.telegram.org/bot"))
        self.assertIn(BOT_TOKEN, call["url"])
        body = call["json"]
        self.assertEqual(body["chat_id"], CHAT_ID)
        self.assertEqual(body["parse_mode"], "HTML")
        self.assertTrue(body["link_preview_options"]["is_disabled"])
        button = body["reply_markup"]["inline_keyboard"][0][0]
        self.assertIn("Log in to Fyers", button["text"])
        self.assertTrue(button["url"].startswith("https://api.edurel.xyz/"))
        # The bot token belongs in the URL path only, never in the message body.
        self.assertNotIn(BOT_TOKEN, json.dumps(body))

    async def test_success_message_is_quiet(self):
        client = FakeClient(FakeResponse())
        patches = _enabled()
        with (patches[0], patches[1], patches[2], patches[3]):
            sent = await tg.send_auth_success(
                None,
                method="headless_totp",
                session_date="2026-09-15",
                expires_at_ist="16 Sep 06:30",
                client=client,
            )
        self.assertTrue(sent)
        self.assertTrue(client.calls[0]["json"]["disable_notification"])
        self.assertIn("Fyers session active", client.calls[0]["json"]["text"])

    async def test_critical_alert_without_link_still_sends(self):
        client = FakeClient(FakeResponse())
        patches = _enabled()
        with (patches[0], patches[1], patches[2], patches[3]):
            sent = await tg.send_auth_expired_alert(
                None,
                login_url=None,
                session_date="2026-09-15",
                severity="critical",
                trading_day=True,
                client=client,
            )
        self.assertTrue(sent)
        body = client.calls[0]["json"]
        self.assertNotIn("reply_markup", body)
        self.assertIn("CRITICAL", body["text"])

    async def test_holiday_message_is_informational(self):
        client = FakeClient(FakeResponse())
        patches = _enabled()
        with (patches[0], patches[1], patches[2], patches[3]):
            await tg.send_auth_expired_alert(
                None,
                login_url="https://api.edurel.xyz/x",
                session_date="2026-09-14",
                trading_day=False,
                client=client,
            )
        text = client.calls[0]["json"]["text"]
        self.assertIn("Non-trading day", text)
        self.assertNotIn("CRITICAL", text)

    async def test_http_error_returns_false_and_records_event(self):
        client = FakeClient(FakeResponse(status_code=401, payload={"ok": False}))
        db = AsyncMock()
        patches = _enabled()
        with (patches[0], patches[1], patches[2], patches[3]):
            sent = await tg.send_message(db, "hello", client=client)
        self.assertFalse(sent)
        db.execute.assert_awaited()

    async def test_network_error_returns_false(self):
        import httpx

        client = FakeClient(error=httpx.ConnectError("no route"))
        db = AsyncMock()
        patches = _enabled()
        with (patches[0], patches[1], patches[2], patches[3]):
            sent = await tg.send_message(db, "hello", client=client)
        self.assertFalse(sent)
        db.execute.assert_awaited()

    async def test_long_messages_are_truncated(self):
        client = FakeClient(FakeResponse())
        patches = _enabled()
        with (patches[0], patches[1], patches[2], patches[3]):
            await tg.send_message(None, "x" * 10_000, client=client)
        self.assertLessEqual(len(client.calls[0]["json"]["text"]), tg.MAX_MESSAGE_CHARS)


class TelegramContentTests(unittest.TestCase):
    def test_dynamic_text_is_html_escaped(self):
        text = tg.build_auth_alert_text(
            session_date="2026-09-15",
            reason="<script>alert(1)</script>",
            minutes_to_open=10,
        )
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)

    def test_readiness_digest_fields(self):
        text = tg.build_readiness_text(
            {
                "session_date": "2026-09-15",
                "expires_at_ist": "16 Sep 06:30",
                "headless_login_enabled": True,
            }
        )
        self.assertIn("Pre-market readiness", text)
        self.assertIn("2026-09-15", text)
        self.assertIn("16 Sep 06:30", text)
        self.assertIn("Automated TOTP login: enabled", text)

    def test_success_text_labels_the_method(self):
        text = tg.build_success_text(
            method="direct_link", session_date="2026-09-15", expires_at_ist="16 Sep 06:30"
        )
        self.assertIn("one-tap login link", text)
        self.assertIn("16 Sep 06:30", text)

    def test_no_secret_material_in_any_builder(self):
        texts = [
            tg.build_auth_alert_text(session_date="2026-09-15", reason="expired"),
            tg.build_success_text(method="headless_totp", session_date="2026-09-15"),
            tg.build_readiness_text({"session_date": "2026-09-15"}),
        ]
        for text in texts:
            with self.subTest(text=text[:40]):
                self.assertNotIn(BOT_TOKEN, text)
                self.assertNotIn(CHAT_ID, text)


if __name__ == "__main__":
    unittest.main()
