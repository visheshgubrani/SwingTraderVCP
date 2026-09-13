"""Outbound Telegram notifications for broker-auth readiness.

Send-only by design: the guard pushes an alert with a one-tap login link, and
the user's tap comes back through the public OAuth callback — no inbound bot
listener, no long-poll loop, no extra process.

Every failure here is non-fatal: notification problems must never block or
crash the auth guard or the money path.
"""

from __future__ import annotations

import html
import logging
from typing import Sequence

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Telegram's hard cap is 4096 characters; leave headroom for entities.
MAX_MESSAGE_CHARS = 3800
REQUEST_TIMEOUT_SECONDS = 15.0


def _escape(value: object) -> str:
    return html.escape(str(value), quote=False)


def build_auth_alert_text(
    *,
    session_date: str,
    severity: str = "warning",
    reason: str | None = None,
    minutes_to_open: int | None = None,
    trading_day: bool = True,
) -> str:
    """Message body asking the owner to complete the daily Fyers 2FA."""
    if not trading_day:
        header = "ℹ️ <b>Non-trading day</b>"
        body = (
            "No NSE session today. Logging in is optional and only keeps the "
            "evening EOD data sync working."
        )
    elif severity == "critical":
        header = "🚨 <b>CRITICAL — Fyers session missing</b>"
        body = "Trading will be blocked until you authenticate."
    else:
        header = "⚠️ <b>Fyers session expired</b>"

    if trading_day and severity != "critical":
        if minutes_to_open is not None and minutes_to_open > 0:
            body = (
                f"Automated login did not complete. Please authenticate before "
                f"market open (about {minutes_to_open} min)."
            )
        else:
            body = "Automated login did not complete. Please authenticate."

    lines = [header, "", f"Session date: <b>{_escape(session_date)}</b>"]
    if reason:
        lines.append(f"Reason: <code>{_escape(reason)}</code>")
    lines.extend(["", body])
    return "\n".join(lines)


def build_success_text(
    *,
    method: str,
    session_date: str,
    expires_at_ist: str | None = None,
) -> str:
    """Quiet confirmation that the trading session is armed."""
    method_label = {
        "headless_totp": "via automated TOTP login",
        "direct_link": "via the one-tap login link",
        "browser": "via the dashboard",
        "refresh_token": "via refresh token",
    }.get(method, f"via {method}")
    lines = [
        "🟢 <b>Fyers session active</b>",
        "",
        f"<b>{_escape(session_date)}</b> {_escape(method_label)}",
    ]
    if expires_at_ist:
        lines.append(f"Session ends: <b>{_escape(expires_at_ist)}</b> IST")
    return "\n".join(lines)


def build_readiness_text(status: dict) -> str:
    """Pre-market readiness digest (green path)."""
    lines = [
        "🟢 <b>Pre-market readiness</b>",
        "",
        "Fyers session: <b>connected</b>",
        f"Session date: <b>{_escape(status.get('session_date', '-'))}</b>",
    ]
    expires = status.get("expires_at_ist")
    if expires:
        lines.append(f"Session ends: <b>{_escape(expires)}</b> IST")
    if status.get("headless_login_enabled"):
        lines.append("Automated TOTP login: enabled")
    return "\n".join(lines)


def _reply_markup(buttons: Sequence[tuple[str, str]] | None) -> dict | None:
    if not buttons:
        return None
    return {
        "inline_keyboard": [
            [{"text": label, "url": url}] for label, url in buttons
        ]
    }


async def send_message(
    db,
    text: str,
    *,
    buttons: Sequence[tuple[str, str]] | None = None,
    quiet: bool = False,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Send one Telegram message. Returns False (never raises) on any failure.

    ``db`` is an optional SQLAlchemy session; when supplied, send failures are
    recorded as ``auth_notification_failed`` system events.
    """
    if not settings.telegram_notifications_enabled:
        logger.debug("Telegram notifications disabled; skipping message")
        return False
    if not settings.telegram_configured:
        logger.warning("Telegram notifications enabled but not configured")
        await _record_failure(db, "not_configured")
        return False

    url = (
        f"{settings.telegram_api_base_url.rstrip('/')}"
        f"/bot{settings.telegram_bot_token}/sendMessage"
    )
    payload: dict = {
        "chat_id": settings.telegram_chat_id,
        "text": text[:MAX_MESSAGE_CHARS],
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    if quiet:
        payload["disable_notification"] = True
    markup = _reply_markup(buttons)
    if markup:
        payload["reply_markup"] = markup

    try:
        if client is not None:
            response = await client.post(url, json=payload)
        else:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as owned:
                response = await owned.post(url, json=payload)
    except httpx.HTTPError as exc:
        logger.warning("Telegram send failed (network): %s", exc)
        await _record_failure(db, "network")
        return False

    if response.status_code >= 400:
        logger.warning(
            "Telegram send rejected: status=%s body=%s",
            response.status_code,
            response.text[:200],
        )
        await _record_failure(db, f"http_{response.status_code}")
        return False

    return True


async def send_auth_expired_alert(
    db,
    *,
    login_url: str | None,
    session_date: str,
    reason: str | None = None,
    severity: str = "warning",
    minutes_to_open: int | None = None,
    trading_day: bool = True,
    client: httpx.AsyncClient | None = None,
) -> bool:
    buttons = None
    if login_url:
        buttons = [("🔐 Log in to Fyers (1 tap)", login_url)]
    return await send_message(
        db,
        build_auth_alert_text(
            session_date=session_date,
            severity=severity,
            reason=reason,
            minutes_to_open=minutes_to_open,
            trading_day=trading_day,
        ),
        buttons=buttons,
        client=client,
    )


async def send_auth_success(
    db,
    *,
    method: str,
    session_date: str,
    expires_at_ist: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> bool:
    return await send_message(
        db,
        build_success_text(
            method=method, session_date=session_date, expires_at_ist=expires_at_ist
        ),
        quiet=True,
        client=client,
    )


async def send_premarket_readiness(
    db,
    status: dict,
    *,
    client: httpx.AsyncClient | None = None,
) -> bool:
    return await send_message(
        db, build_readiness_text(status), quiet=True, client=client
    )


async def _record_failure(db, reason: str) -> None:
    if db is None:
        return
    try:
        from app.services.auth_service import _emit_system_event

        await _emit_system_event(
            db,
            "warning",
            "auth_notification_failed",
            {"reason": reason},
            cooldown_seconds=settings.auth_event_cooldown_seconds,
        )
        await db.commit()
    except Exception as exc:  # pragma: no cover - defensive only
        logger.warning("Could not record Telegram failure: %s", exc)
