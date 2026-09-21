"""
Central Fyers auth token service.

Every component that needs a Fyers access token MUST go through this module.
Never read tokens directly from the DB or cache elsewhere.

Responsibilities:
- Read encrypted token from Postgres (via security.py)
- Cache valid access token in Redis with TTL
- Cap stored expiry at the next 06:30 Asia/Kolkata Fyers daily cutoff
- Emit system_events on auth failure so workers pause and UI surfaces a banner
- Provide is_auth_healthy() for kill-switch / pause logic

Daily operator OAuth + 2FA is the only way to obtain a token. Do not call
validate-refresh-token.
"""

import datetime
import json
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.security import get_fyers_token, save_fyers_token

logger = logging.getLogger(__name__)

# Redis keys
_REDIS_TOKEN_KEY = "auth:fyers:access_token"
_REDIS_EXPIRY_KEY = "auth:fyers:expires_at"
_REDIS_HEALTH_KEY = "auth:fyers:healthy"

# Treat a token as expired this many seconds early so workers fail closed
# before the official Fyers cutoff rather than mid-request.
_EXPIRY_BUFFER_SECONDS = 300  # 5 minutes

_IST = ZoneInfo("Asia/Kolkata")
_FYERS_DAILY_EXPIRY_HOUR = 6
_FYERS_DAILY_EXPIRY_MINUTE = 30


class AuthUnavailableError(Exception):
    """Raised when no valid Fyers access token is available."""

    def __init__(self, reason: str = "No valid token"):
        self.reason = reason
        super().__init__(reason)


def fyers_access_token_expires_at(
    *,
    now: datetime.datetime | None = None,
    expires_in: int | None = None,
) -> datetime.datetime:
    """Return the UTC expiry to persist for a newly issued Fyers access token.

    Fyers v3 access tokens expire daily at 06:30 Asia/Kolkata. Cap at that
    cutoff even when the API still reports a 86400s TTL. A shorter API TTL
    still wins.
    """
    now_utc = now or datetime.datetime.now(datetime.timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=datetime.timezone.utc)

    ist_now = now_utc.astimezone(_IST)
    daily_ist = ist_now.replace(
        hour=_FYERS_DAILY_EXPIRY_HOUR,
        minute=_FYERS_DAILY_EXPIRY_MINUTE,
        second=0,
        microsecond=0,
    )
    if ist_now >= daily_ist:
        daily_ist = daily_ist + datetime.timedelta(days=1)
    daily_utc = daily_ist.astimezone(datetime.timezone.utc)

    if expires_in is None:
        return daily_utc
    api_expiry = now_utc + datetime.timedelta(seconds=int(expires_in))
    return min(api_expiry, daily_utc)


def _cache_ttl_seconds(
    expires_at: datetime.datetime,
    now: datetime.datetime | None = None,
) -> int:
    now_utc = now or datetime.datetime.now(datetime.timezone.utc)
    remaining = (expires_at - now_utc).total_seconds() - _EXPIRY_BUFFER_SECONDS
    return max(int(remaining), 60)


def _token_is_fresh(
    expires_at: datetime.datetime,
    now: datetime.datetime | None = None,
) -> bool:
    now_utc = now or datetime.datetime.now(datetime.timezone.utc)
    return expires_at >= now_utc + datetime.timedelta(seconds=_EXPIRY_BUFFER_SECONDS)


async def _emit_system_event(
    session: AsyncSession,
    severity: str,
    event_type: str,
    payload: dict | None = None,
) -> None:
    """Insert a system_events row for auth issues. Caller controls commit."""
    await session.execute(
        text("""
            INSERT INTO system_events (component, severity, event_type, payload)
            VALUES ('auth_service', :severity, :event_type, :payload)
        """),
        {
            "severity": severity,
            "event_type": event_type,
            "payload": "{}" if payload is None else json.dumps(payload),
        },
    )
    await session.flush()


async def _set_auth_health(redis, healthy: bool) -> None:
    """Write health flag to Redis so any process can check instantly."""
    await redis.set(_REDIS_HEALTH_KEY, "1" if healthy else "0", ex=3600)


async def is_auth_healthy(redis) -> bool:
    """Quick check — can be called by any worker before money-path ops."""
    val = await redis.get(_REDIS_HEALTH_KEY)
    return val == b"1" or val == "1"


async def invalidate_fyers_token(redis) -> None:
    """Invalidate cached Fyers access token in Redis and Postgres, and mark auth unhealthy."""
    try:
        await redis.delete(_REDIS_TOKEN_KEY, _REDIS_EXPIRY_KEY)
        await _set_auth_health(redis, False)
        async with async_session() as db:
            await db.execute(
                text("""
                    UPDATE broker_auth_tokens
                    SET expires_at = now() - interval '1 second', updated_at = now()
                    WHERE broker = 'fyers'
                """)
            )
            await _emit_system_event(
                db, "warning", "auth_invalidated", {"reason": "token_rejected_by_fyers"}
            )
            await db.commit()
    except Exception as e:
        logger.error("Failed to invalidate Fyers token: %s", e)


async def persist_and_cache_fyers_token(
    db: AsyncSession,
    redis,
    *,
    access_token: str,
    expires_at: datetime.datetime,
    refresh_token: str | None = None,
) -> None:
    """
    Unified entrypoint to persist Fyers token to Postgres and sync Redis token caches (AUTH-002).
    Ensures Redis hot token, expiry cache, and auth health are updated synchronously.

    refresh_token is unused after the April 2026 daily-2FA change; callers pass None
    so the nullable DB column is cleared.
    """
    await save_fyers_token(db, access_token, refresh_token, expires_at)
    ttl = _cache_ttl_seconds(expires_at)
    await redis.set(_REDIS_TOKEN_KEY, access_token, ex=ttl)
    await redis.set(_REDIS_EXPIRY_KEY, expires_at.isoformat(), ex=ttl)
    await _set_auth_health(redis, True)


async def get_valid_access_token(redis) -> str:
    """
    THE single entry point for getting a Fyers access token.

    1. Check Redis cache (fast path)
    2. On miss, read from DB and cache if still valid
    3. If expired/near-expiry, fail closed — daily 2FA re-login is required
    4. Raise AuthUnavailableError if nothing works

    Callers: historical_fetcher, tick_ingestion, order_gateway, execution_engine.
    """
    # Fast path — cached
    cached = await redis.get(_REDIS_TOKEN_KEY)
    if cached:
        token = cached.decode() if isinstance(cached, bytes) else cached
        return token

    # Slow path — read DB
    async with async_session() as db:
        token_data = await get_fyers_token(db)

        if not token_data:
            await _emit_system_event(
                db, "critical", "auth_unavailable", {"reason": "no_token_in_db"}
            )
            await db.commit()
            await _set_auth_health(redis, False)
            raise AuthUnavailableError("No Fyers token in database. Log in via /auth/url.")

        expires_at = token_data["expires_at"]
        now = datetime.datetime.now(datetime.timezone.utc)

        if not _token_is_fresh(expires_at, now):
            await _emit_system_event(
                db, "critical", "auth_unavailable", {"reason": "expired"}
            )
            await db.commit()
            await _set_auth_health(redis, False)
            raise AuthUnavailableError(
                "Fyers token expired. Daily 2FA re-login is required."
            )

        ttl = _cache_ttl_seconds(expires_at, now)
        await redis.set(_REDIS_TOKEN_KEY, token_data["access_token"], ex=ttl)
        await redis.set(_REDIS_EXPIRY_KEY, expires_at.isoformat(), ex=ttl)
        await _set_auth_health(redis, True)
        return token_data["access_token"]


async def get_auth_status_from_db(db: AsyncSession) -> dict:
    """
    Returns auth status for the API /auth/status endpoint.
    Includes health flag and expiry. There is no unattended refresh path.
    """
    token_data = await get_fyers_token(db)
    if not token_data:
        return {
            "authenticated": False,
            "healthy": False,
            "reason": "no_token",
        }

    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = token_data["expires_at"]

    if not _token_is_fresh(expires_at, now):
        return {
            "authenticated": False,
            "healthy": False,
            "reason": "expired",
            "expires_at": expires_at.isoformat(),
        }

    return {
        "authenticated": True,
        "healthy": True,
        "expires_at": expires_at.isoformat(),
    }
