"""
Central Fyers auth token service.

Every component that needs a Fyers access token MUST go through this module.
Never read tokens directly from the DB or cache elsewhere.

Responsibilities:
- Read encrypted token from Postgres (via security.py)
- Cache valid access token in Redis with TTL
- Attempt refresh via Fyers refresh-token API when token nears expiry
- Emit system_events on auth failure so workers pause and UI surfaces a banner
- Provide is_auth_healthy() for kill-switch / pause logic
"""

import datetime
import hashlib
import json
import logging

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.security import get_fyers_token, save_fyers_token

logger = logging.getLogger(__name__)

# Redis keys
_REDIS_TOKEN_KEY = "auth:fyers:access_token"
_REDIS_EXPIRY_KEY = "auth:fyers:expires_at"
_REDIS_HEALTH_KEY = "auth:fyers:healthy"

# Buffer before expiry — refresh this many seconds early
_EXPIRY_BUFFER_SECONDS = 300  # 5 minutes

# Fyers refresh endpoint (not in SDK Config)
_FYERS_REFRESH_URL = "https://api-t1.fyers.in/api/v3/validate-refresh-token"


class AuthUnavailableError(Exception):
    """Raised when no valid Fyers access token is available."""

    def __init__(self, reason: str = "No valid token"):
        self.reason = reason
        super().__init__(reason)


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


async def _try_refresh_token(
    refresh_token: str,
) -> dict | None:
    """
    Call Fyers refresh-token endpoint.
    Returns {"access_token": ..., "refresh_token": ..., "expires_in": ...} on success,
    None on failure.
    """
    app_id_hash = hashlib.sha256(
        f"{settings.fyers_app_id}:{settings.fyers_secret_key}".encode()
    ).hexdigest()

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "appIdHash": app_id_hash,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(_FYERS_REFRESH_URL, json=payload)
    except httpx.HTTPError as e:
        logger.error("Fyers refresh HTTP error: %s", e)
        return None

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("Fyers returned non-JSON response (status %d): %s", resp.status_code, e)
        return None

    if data.get("s") != "ok":
        logger.warning("Fyers refresh rejected: %s", data)
        return None

    return {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token", refresh_token),
        "expires_in": data.get("expires_in", 86400),
    }


async def refresh_and_save(db: AsyncSession, redis) -> str | None:
    """
    Attempt to refresh the Fyers token. Returns new access_token on success,
    None on failure. Emits system_events accordingly.

    Commits the transaction once at the end (token save + system event).
    """
    token_data = await get_fyers_token(db)
    if not token_data or not token_data.get("refresh_token"):
        logger.error("No refresh token available for Fyers auth refresh")
        await _emit_system_event(
            db, "critical", "auth_refresh_failed", {"reason": "no_refresh_token"}
        )
        await db.commit()
        await _set_auth_health(redis, False)
        return None

    result = await _try_refresh_token(token_data["refresh_token"])
    if not result:
        logger.error("Fyers token refresh failed")
        await _emit_system_event(
            db, "critical", "auth_refresh_failed", {"reason": "refresh_rejected"}
        )
        await db.commit()
        await _set_auth_health(redis, False)
        return None

    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=result["expires_in"]
    )

    await persist_and_cache_fyers_token(
        db,
        redis,
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        expires_at=expires_at,
        expires_in=result["expires_in"],
    )

    await _emit_system_event(
        db, "info", "auth_refresh_succeeded", {"expires_at": expires_at.isoformat()}
    )
    await db.commit()
    logger.info("Fyers token refreshed, expires at %s", expires_at)
    return result["access_token"]


async def persist_and_cache_fyers_token(
    db: AsyncSession,
    redis,
    *,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime.datetime,
    expires_in: int = 86400,
) -> None:
    """
    Unified entrypoint to persist Fyers token to Postgres and sync Redis token caches (AUTH-002).
    Ensures Redis hot token, expiry cache, and auth health are updated synchronously.
    """
    await save_fyers_token(db, access_token, refresh_token, expires_at)
    ttl = max(int(expires_in) - _EXPIRY_BUFFER_SECONDS, 60)
    await redis.set(_REDIS_TOKEN_KEY, access_token, ex=ttl)
    await redis.set(_REDIS_EXPIRY_KEY, expires_at.isoformat(), ex=ttl)
    await _set_auth_health(redis, True)


async def get_valid_access_token(redis) -> str:
    """
    THE single entry point for getting a Fyers access token.

    1. Check Redis cache (fast path)
    2. On miss, read from DB, cache if still valid
    3. If expired/near-expiry, attempt refresh
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

        # If expires within buffer, try refresh
        if expires_at < now + datetime.timedelta(seconds=_EXPIRY_BUFFER_SECONDS):
            new_token = await refresh_and_save(db, redis)
            if new_token:
                return new_token
            # Refresh failed — but if old token hasn't actually expired yet, use it
            # (Fyers may still accept it for a short window)
            if expires_at > now:
                logger.warning("Using near-expiry token as fallback")
                ttl = max(int((expires_at - now).total_seconds()) - _EXPIRY_BUFFER_SECONDS, 30)
                await redis.set(_REDIS_TOKEN_KEY, token_data["access_token"], ex=ttl)
                return token_data["access_token"]

            await _set_auth_health(redis, False)
            raise AuthUnavailableError(
                "Fyers token expired and refresh failed. Re-login required."
            )

        # Token is valid — cache it
        ttl = max(int((expires_at - now).total_seconds()) - _EXPIRY_BUFFER_SECONDS, 60)
        await redis.set(_REDIS_TOKEN_KEY, token_data["access_token"], ex=ttl)
        await redis.set(_REDIS_EXPIRY_KEY, expires_at.isoformat(), ex=ttl)
        await _set_auth_health(redis, True)
        return token_data["access_token"]


async def get_auth_status_from_db(db: AsyncSession) -> dict:
    """
    Returns auth status for the API /auth/status endpoint.
    Includes health flag, expiry, and last refresh event.
    """
    token_data = await get_fyers_token(db)
    if not token_data:
        return {"authenticated": False, "healthy": False, "reason": "no_token"}

    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = token_data["expires_at"]

    if expires_at < now + datetime.timedelta(seconds=_EXPIRY_BUFFER_SECONDS):
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
