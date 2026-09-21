"""Outbound Telegram Bot API helper for owner-only alerts.

Uses existing httpx. Not a long-running service, webhook, or new package.
Never send access tokens, auth codes, or secrets in chat.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramConfigError(Exception):
    """Bot token or chat id is missing."""


class TelegramSendError(Exception):
    """Telegram Bot API rejected or failed the send."""


def telegram_configured() -> bool:
    return bool(
        (settings.telegram_bot_token or "").strip()
        and (settings.telegram_chat_id or "").strip()
    )


async def send_telegram_message(text: str) -> None:
    token = (settings.telegram_bot_token or "").strip()
    chat_id = (settings.telegram_chat_id or "").strip()
    if not token or not chat_id:
        raise TelegramConfigError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set"
        )

    url = f"{_TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        logger.error("Telegram send HTTP error: %s", exc)
        raise TelegramSendError("Telegram send failed") from exc

    try:
        data = resp.json()
    except ValueError as exc:
        logger.error("Telegram returned non-JSON (status %s)", resp.status_code)
        raise TelegramSendError("Telegram returned a non-JSON response") from exc

    if resp.status_code >= 400 or not data.get("ok"):
        logger.error(
            "Telegram send rejected (status %s, description=%s)",
            resp.status_code,
            data.get("description"),
        )
        raise TelegramSendError("Telegram send rejected")
