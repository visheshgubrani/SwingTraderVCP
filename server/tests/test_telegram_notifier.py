"""Unit tests for outbound Telegram Bot API helper. Network is mocked."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.telegram_notifier import (
    TelegramConfigError,
    TelegramSendError,
    send_telegram_message,
    telegram_configured,
)


class TelegramConfiguredTests(unittest.TestCase):
    def test_false_when_token_or_chat_missing(self) -> None:
        with patch("app.services.telegram_notifier.settings") as mock_settings:
            mock_settings.telegram_bot_token = ""
            mock_settings.telegram_chat_id = "123"
            self.assertFalse(telegram_configured())

            mock_settings.telegram_bot_token = "bot-token"
            mock_settings.telegram_chat_id = "  "
            self.assertFalse(telegram_configured())

            mock_settings.telegram_bot_token = "bot-token"
            mock_settings.telegram_chat_id = "12345"
            self.assertTrue(telegram_configured())


class SendTelegramMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_raises_without_network_when_unconfigured(self) -> None:
        with (
            patch("app.services.telegram_notifier.settings") as mock_settings,
            patch("httpx.AsyncClient") as mock_client,
        ):
            mock_settings.telegram_bot_token = ""
            mock_settings.telegram_chat_id = ""
            with self.assertRaises(TelegramConfigError):
                await send_telegram_message("hello")
        mock_client.assert_not_called()

    async def test_posts_send_message_payload(self) -> None:
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ok": True, "result": {"message_id": 1}}
        client = AsyncMock()
        client.post.return_value = response
        client_cm = AsyncMock()
        client_cm.__aenter__.return_value = client
        client_cm.__aexit__.return_value = None

        with (
            patch("app.services.telegram_notifier.settings") as mock_settings,
            patch("httpx.AsyncClient", return_value=client_cm) as mock_client_cls,
        ):
            mock_settings.telegram_bot_token = "bot-secret"
            mock_settings.telegram_chat_id = "4242"
            await send_telegram_message("Fyers session expired at 06:30 IST.")

        mock_client_cls.assert_called_once()
        url = client.post.await_args.args[0]
        payload = client.post.await_args.kwargs["json"]
        self.assertEqual(url, "https://api.telegram.org/botbot-secret/sendMessage")
        self.assertEqual(payload["chat_id"], "4242")
        self.assertEqual(payload["text"], "Fyers session expired at 06:30 IST.")
        self.assertTrue(payload["disable_web_page_preview"])

    async def test_rejected_response_raises_send_error(self) -> None:
        response = MagicMock()
        response.status_code = 400
        response.json.return_value = {"ok": False, "description": "Bad Request"}
        client = AsyncMock()
        client.post.return_value = response
        client_cm = AsyncMock()
        client_cm.__aenter__.return_value = client
        client_cm.__aexit__.return_value = None

        with (
            patch("app.services.telegram_notifier.settings") as mock_settings,
            patch("httpx.AsyncClient", return_value=client_cm),
        ):
            mock_settings.telegram_bot_token = "bot-secret"
            mock_settings.telegram_chat_id = "4242"
            with self.assertRaises(TelegramSendError):
                await send_telegram_message("hello")


if __name__ == "__main__":
    unittest.main()
