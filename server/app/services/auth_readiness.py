"""Fyers session readiness: cutoff-aware expiry, live verification, health gates.

Two facts drive this module:

1. Fyers expires every API access token at ``06:30`` IST daily *regardless* of
   the ``expires_in`` the token endpoint reports. Stored expiry must therefore
   be clamped to that boundary, otherwise every cached-expiry check in the
   system is optimistic and the token silently dies mid-morning.
2. A stored token is not proof of a live session: the broker can invalidate a
   session server-side. Readiness that matters (before the entry window,
   before executing an approved entry) is confirmed with one cheap
   authenticated call.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

IST_TZ = ZoneInfo("Asia/Kolkata")

# Redis keys (health flag / hot token / expiry cache live in auth_service).
REDIS_HEALTH_KEY = "auth:fyers:healthy"


class SessionCutoffError(ValueError):
    """Raised when the configured session cutoff cannot be parsed."""


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of one live authenticated call against Fyers."""

    ok: bool
    identity: str | None = None
    error: str | None = None
    code: int | None = None


def parse_session_cutoff(value: str | None = None) -> dt.time:
    """Parse ``FYERS_SESSION_CUTOFF_IST`` (``HH:MM``) into an IST wall-clock time."""
    raw = (value if value is not None else settings.fyers_session_cutoff_ist) or ""
    parts = raw.strip().split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise SessionCutoffError(f"Invalid Fyers session cutoff: {raw!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise SessionCutoffError(f"Invalid Fyers session cutoff: {raw!r}")
    return dt.time(hour=hour, minute=minute)


def as_ist(moment: dt.datetime) -> dt.datetime:
    """Coerce an aware or naive datetime to IST (naive input is treated as UTC)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(IST_TZ)


def next_session_cutoff_ist(now: dt.datetime | None = None) -> dt.datetime:
    """Next daily session death as an aware UTC datetime."""
    return session_deadline_for_issuance(_as_utc(now))


def session_deadline_for_issuance(issued_at: dt.datetime) -> dt.datetime:
    """First cutoff boundary strictly after a token was issued.

    A token minted at 07:20 IST dies at 06:30 the next morning; one minted at
    05:00 IST dies at 06:30 the same morning. Deriving the deadline from
    issuance (not from the current time) also repairs tokens that were stored
    before this rule existed.
    """
    issued_utc = _as_utc(issued_at)
    issued_ist = issued_utc.astimezone(IST_TZ)
    cutoff = parse_session_cutoff()
    candidate = dt.datetime.combine(issued_ist.date(), cutoff, tzinfo=IST_TZ)
    while candidate <= issued_ist:
        candidate += dt.timedelta(days=1)
    return candidate.astimezone(dt.timezone.utc)


def clamp_token_expiry(now: dt.datetime | None, expires_in: int | None) -> dt.datetime:
    """Clamp a broker-reported lifetime to the next daily session cutoff."""
    now_utc = _as_utc(now)
    claimed = now_utc + dt.timedelta(seconds=max(int(expires_in or 0), 0))
    return min(claimed, session_deadline_for_issuance(now_utc))


def effective_token_expiry(
    expires_at: dt.datetime | None,
    *,
    issued_at: dt.datetime | None = None,
) -> dt.datetime | None:
    """Stored expiry, clamped to the cutoff boundary of its issuance day."""
    if not expires_at:
        return None
    stored = _as_utc(expires_at)
    if issued_at is None:
        return stored
    return min(stored, session_deadline_for_issuance(issued_at))


def seconds_until_session_cutoff(now: dt.datetime | None = None) -> float:
    return max(
        (next_session_cutoff_ist(now) - _as_utc(now)).total_seconds(),
        0.0,
    )


def token_expiry_is_current(
    expires_at: dt.datetime | None,
    *,
    issued_at: dt.datetime | None = None,
    now: dt.datetime | None = None,
) -> bool:
    """True when the effective token expiry is still in the future."""
    effective = effective_token_expiry(expires_at, issued_at=issued_at)
    if not effective:
        return False
    return effective > _as_utc(now)


@lru_cache(maxsize=8)
def _holiday_dates(values: tuple[str, ...]) -> frozenset[dt.date]:
    parsed: set[dt.date] = set()
    for value in values:
        try:
            parsed.add(dt.date.fromisoformat(value))
        except ValueError:
            logger.warning("Ignoring malformed NSE holiday entry: %r", value)
    return frozenset(parsed)


def is_nse_session(day: dt.date, *, holidays: frozenset[dt.date] | None = None) -> bool:
    """Weekday and not a configured NSE holiday."""
    if day.weekday() >= 5:
        return False
    holiday_set = (
        holidays
        if holidays is not None
        else _holiday_dates(tuple(settings.nse_trading_holidays))
    )
    return day not in holiday_set


def _profile_identity(payload: dict[str, Any] | None) -> str | None:
    """Best-effort Fyers account identity from a /profile response."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    for key in ("fy_id", "fyId", "client_id", "clientId", "id"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def verify_fyers_session(
    access_token: str,
    *,
    client: httpx.AsyncClient | None = None,
    now: dt.datetime | None = None,
) -> VerifyResult:
    """Confirm the session is live with one cheap authenticated Fyers call.

    Fyers authenticates REST calls with ``Authorization: <app_id>:<token>``
    (the form the pinned SDK uses), not a Bearer token.
    """
    if not access_token:
        return VerifyResult(ok=False, error="missing_token")
    if not settings.fyers_app_id:
        return VerifyResult(ok=False, error="missing_app_id")

    url = f"{settings.fyers_api_base_url.rstrip('/')}/profile"
    headers = {"Authorization": f"{settings.fyers_app_id}:{access_token}"}
    timeout = settings.auth_live_verify_timeout_seconds

    try:
        if client is not None:
            response = await client.get(url, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=timeout) as owned:
                response = await owned.get(url, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("Fyers session verification failed (network): %s", exc)
        return VerifyResult(ok=False, error="network")

    try:
        payload = response.json()
    except (ValueError, TypeError):
        return VerifyResult(ok=False, error="non_json", code=response.status_code)

    if not isinstance(payload, dict) or payload.get("s") != "ok":
        message = ""
        code = None
        if isinstance(payload, dict):
            message = str(payload.get("message") or "")
            raw_code = payload.get("code")
            code = int(raw_code) if isinstance(raw_code, int) else None
        logger.warning(
            "Fyers session verification rejected: status=%s code=%s message=%s",
            response.status_code,
            code,
            message[:120],
        )
        return VerifyResult(
            ok=False,
            error="rejected",
            code=code if code is not None else response.status_code,
        )

    return VerifyResult(ok=True, identity=_profile_identity(payload))


async def read_stored_token(session: AsyncSession) -> dict[str, Any] | None:
    """Token metadata for readiness checks (never returns the token itself)."""
    result = await session.execute(
        text("""
            SELECT expires_at, refreshed_at, updated_at,
                   refresh_token_encrypted IS NOT NULL AS has_refresh_token
            FROM broker_auth_tokens
            WHERE broker = 'fyers' AND token_scope = 'default'
        """)
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def evaluate_auth_readiness(
    session: AsyncSession,
    redis,
    *,
    verify: bool = True,
    token_provider=None,
    client: httpx.AsyncClient | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Single readiness surface used by the guard, the API status, and gates.

    ``verify=False`` skips the network call and reports expiry-only readiness
    (used by display paths that must stay cheap).
    """
    now_utc = _as_utc(now)
    stored = await read_stored_token(session)
    has_pin = bool((settings.fyers_pin or "").strip())

    status: dict[str, Any] = {
        "authenticated": False,
        "healthy": False,
        "reason": "no_token",
        "expires_at": None,
        "session_cutoff_ist": settings.fyers_session_cutoff_ist,
        "next_session_cutoff": next_session_cutoff_ist(now_utc).isoformat(),
        "has_refresh_token": False,
        "has_pin": has_pin,
        "headless_login_enabled": settings.auth_headless_login_enabled,
        "headless_login_configured": settings.headless_login_configured,
        "totp_configured": bool((settings.fyers_totp_key or "").strip()),
        "telegram_configured": settings.telegram_configured,
        "telegram_enabled": settings.telegram_notifications_enabled,
        "verified": False,
        "identity": None,
        "last_login_method": None,
        "last_verified_at": None,
    }

    if not stored:
        return status

    expires_at = stored.get("expires_at")
    issued_at = stored.get("refreshed_at") or stored.get("updated_at")
    effective_expiry = effective_token_expiry(expires_at, issued_at=issued_at)
    status["expires_at"] = expires_at.isoformat() if expires_at else None
    status["effective_expires_at"] = (
        effective_expiry.isoformat() if effective_expiry else None
    )
    status["has_refresh_token"] = bool(stored.get("has_refresh_token"))
    if stored.get("updated_at") is not None:
        status["last_verified_at"] = stored["updated_at"].isoformat()

    if not token_expiry_is_current(expires_at, issued_at=issued_at, now=now_utc):
        status["reason"] = "expired"
        return status

    status["authenticated"] = True
    status["healthy"] = True
    status["reason"] = "ok"

    if not verify:
        return status

    if token_provider is None:
        from app.services.auth_service import get_valid_access_token

        token_provider = get_valid_access_token

    try:
        token = await token_provider(redis)
    except Exception as exc:  # AuthUnavailableError and anything the provider raises
        logger.warning("Auth readiness: token provider unavailable: %s", exc)
        status["authenticated"] = False
        status["healthy"] = False
        status["reason"] = "unavailable"
        return status

    result = await verify_fyers_session(token, client=client, now=now_utc)
    status["verified"] = result.ok
    status["identity"] = result.identity
    if not result.ok:
        status["authenticated"] = False
        status["healthy"] = False
        status["reason"] = "unverified"
        status["error"] = result.error
        status["error_code"] = result.code
    return status


async def ensure_session_ready(
    redis,
    *,
    context: str = "Fyers session",
    verify: bool = True,
) -> None:
    """Fail closed when the broker session is missing for a new entry/add.

    Order management, exits, and reconciliation keep their own authoritative
    handling of ``AuthUnavailableError``; this gate exists specifically so a
    missing daily 2FA cannot arm a *new* leg (AGENTS.md §8).
    """
    from app.services.auth_service import (  # local import: avoid a cycle
        AuthUnavailableError,
        _set_auth_health,
        get_valid_access_token,
    )

    try:
        token = await get_valid_access_token(redis)
    except AuthUnavailableError as exc:
        await _set_auth_health(redis, False)
        raise AuthUnavailableError(f"{context} is unavailable: {exc}") from exc

    if not verify:
        return

    result = await verify_fyers_session(token)
    if not result.ok:
        await _set_auth_health(redis, False)
        raise AuthUnavailableError(
            f"{context} failed live verification ({result.error or 'rejected'})."
        )


def _as_utc(moment: dt.datetime | None) -> dt.datetime:
    if moment is None:
        return dt.datetime.now(dt.timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc)
