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
from app.services.auth_readiness import (
    clamp_token_expiry,
    effective_token_expiry,
    seconds_until_session_cutoff,
)

logger = logging.getLogger(__name__)

# Redis keys
_REDIS_TOKEN_KEY = "auth:fyers:access_token"
_REDIS_EXPIRY_KEY = "auth:fyers:expires_at"
_REDIS_HEALTH_KEY = "auth:fyers:healthy"
_REDIS_REFRESH_ATTEMPT_KEY = "auth:fyers:refresh_attempt"
_REDIS_EVENT_COOLDOWN_PREFIX = "auth:events:"

# Buffer before expiry — refresh this many seconds early
_EXPIRY_BUFFER_SECONDS = 300  # 5 minutes

# Fyers refresh endpoint (not in SDK Config). Refresh tokens no longer survive
# SEBI's daily-2FA framework; this stays only as a cheap best-effort fallback.
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
    *,
    redis=None,
    cooldown_seconds: int | None = None,
) -> None:
    """Insert a system_events row for auth issues. Caller controls commit.

    When ``redis`` is supplied the event is de-duplicated per type for
    ``cooldown_seconds`` (default from settings). Without it a dead session
    would emit a critical event on every worker poll — the tick worker retries
    authentication every 5 seconds — burying real signals.
    """
    if redis is not None:
        window = (
            settings.auth_event_cooldown_seconds
            if cooldown_seconds is None
            else cooldown_seconds
        )
        if window > 0:
            key = f"{_REDIS_EVENT_COOLDOWN_PREFIX}{event_type}"
            try:
                if not await redis.set(key, "1", ex=window, nx=True):
                    logger.debug("Suppressed duplicate %s system event", event_type)
                    return
            except Exception as exc:  # never let dedup break auth reporting
                logger.warning("Auth event dedup check failed: %s", exc)

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


async def _set_auth_health(redis, healthy: bool, *, ttl_seconds: int | None = None) -> None:
    """Write the health flag to Redis so any process can check instantly.

    A healthy flag lives until the daily session cutoff (not a fixed hour):
    otherwise a perfectly good token reports unhealthy an hour after login.
    """
    if ttl_seconds is None:
        ttl_seconds = (
            int(seconds_until_session_cutoff()) if healthy else 3600
        )
    await redis.set(_REDIS_HEALTH_KEY, "1" if healthy else "0", ex=max(int(ttl_seconds), 60))


async def _refresh_attempt_allowed(redis) -> bool:
    """Rate-limit broker refresh attempts so a dead session does not hammer Fyers."""
    window = settings.auth_refresh_attempt_cooldown_seconds
    if window <= 0:
        return True
    try:
        return bool(await redis.set(_REDIS_REFRESH_ATTEMPT_KEY, "1", ex=window, nx=True))
    except Exception as exc:
        logger.warning("Refresh attempt cooldown check failed: %s", exc)
        return True


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
                db,
                "warning",
                "auth_invalidated",
                {"reason": "token_rejected_by_fyers"},
                redis=redis,
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
    pin = (settings.fyers_pin or "").strip()
    if not pin:
        logger.error("Cannot refresh Fyers token: FYERS_PIN is not configured.")
        return None

    if not settings.fyers_app_id or not settings.fyers_secret_key:
        logger.error("Cannot refresh Fyers token: FYERS_APP_ID or FYERS_SECRET_KEY is not configured.")
        return None

    app_id_hash = hashlib.sha256(
        f"{settings.fyers_app_id}:{settings.fyers_secret_key}".encode()
    ).hexdigest()

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "appIdHash": app_id_hash,
        "pin": pin,
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
        logger.warning(
            "Fyers refresh rejected: %s (code=%s)",
            data.get("message", "Unknown rejection"),
            data.get("code"),
        )
        return None

    return {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token", refresh_token),
        "expires_in": data.get("expires_in", 86400),
    }


async def refresh_and_save(db: AsyncSession, redis) -> str | None:
    """
    Best-effort legacy refresh via the Fyers refresh-token endpoint.

    Refresh tokens no longer survive SEBI's daily-2FA framework, so this is a
    fallback only — the daily path is the auth guard (headless TOTP or a
    Telegram one-tap login). Attempts are rate-limited so a dead session cannot
    hammer the broker or spam system_events. Returns the new access token, or
    None on failure. Emits system_events accordingly.
    """
    if not await _refresh_attempt_allowed(redis):
        logger.info("Skipping Fyers refresh attempt (cooldown active)")
        return None

    token_data = await get_fyers_token(db)
    if not token_data or not token_data.get("refresh_token"):
        logger.error("No refresh token available for Fyers auth refresh")
        await _emit_system_event(
            db,
            "critical",
            "auth_refresh_failed",
            {"reason": "no_refresh_token"},
            redis=redis,
        )
        await db.commit()
        await _set_auth_health(redis, False)
        return None

    pin = (settings.fyers_pin or "").strip()
    if not pin:
        logger.error("FYERS_PIN is not configured for token refresh")
        await _emit_system_event(
            db,
            "critical",
            "auth_refresh_failed",
            {"reason": "missing_fyers_pin"},
            redis=redis,
        )
        await db.commit()
        await _set_auth_health(redis, False)
        return None

    result = await _try_refresh_token(token_data["refresh_token"])
    if not result:
        logger.error("Fyers token refresh failed")
        await _emit_system_event(
            db,
            "critical",
            "auth_refresh_failed",
            {"reason": "refresh_rejected"},
            redis=redis,
        )
        await db.commit()
        await _set_auth_health(redis, False)
        return None

    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = clamp_token_expiry(now, result["expires_in"])

    await persist_and_cache_fyers_token(
        db,
        redis,
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        expires_at=expires_at,
        expires_in=result["expires_in"],
    )

    await _emit_system_event(
        db,
        "info",
        "auth_refresh_succeeded",
        {"expires_at": expires_at.isoformat(), "method": "refresh_token"},
        redis=redis,
    )
    await db.commit()
    logger.info("Fyers token refreshed, session expires at %s", expires_at)
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

    ``expires_at`` is clamped to the daily Fyers session cutoff (06:30 IST):
    Fyers retires every access token at that boundary regardless of the
    ``expires_in`` it reports, and an optimistic stored expiry is what makes a
    session look alive while every broker call fails.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    session_expires_at = min(
        expires_at.astimezone(datetime.timezone.utc),
        clamp_token_expiry(now, expires_in),
    )

    await save_fyers_token(db, access_token, refresh_token, session_expires_at)
    ttl = max(int((session_expires_at - now).total_seconds()) - _EXPIRY_BUFFER_SECONDS, 60)
    await redis.set(_REDIS_TOKEN_KEY, access_token, ex=ttl)
    await redis.set(_REDIS_EXPIRY_KEY, session_expires_at.isoformat(), ex=ttl)
    await _set_auth_health(
        redis,
        True,
        ttl_seconds=max(int((session_expires_at - now).total_seconds()), 60),
    )


async def get_valid_access_token(redis) -> str:
    """
    THE single entry point for getting a Fyers access token.

    1. Check Redis cache (fast path)
    2. On miss, read from DB, cache if still valid
    3. If expired/near-expiry, attempt the legacy refresh
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
                db,
                "critical",
                "auth_unavailable",
                {"reason": "no_token_in_db"},
                redis=redis,
            )
            await db.commit()
            await _set_auth_health(redis, False)
            raise AuthUnavailableError("No Fyers token in database. Log in via /auth/url.")

        expires_at = token_data["expires_at"]
        now = datetime.datetime.now(datetime.timezone.utc)
        # The broker session also dies at the daily cutoff, so a token stored
        # before this rule existed must not be trusted on expiry alone.
        effective_expiry = effective_token_expiry(
            expires_at, issued_at=token_data.get("refreshed_at")
        ) or expires_at

        if effective_expiry < now + datetime.timedelta(seconds=_EXPIRY_BUFFER_SECONDS):
            new_token = await refresh_and_save(db, redis)
            if new_token:
                return new_token
            # Refresh failed — but if the token has not actually expired yet, use it
            # (Fyers may still accept it for a short window)
            if effective_expiry > now:
                logger.warning("Using near-expiry token as fallback")
                ttl = max(
                    int((effective_expiry - now).total_seconds()) - _EXPIRY_BUFFER_SECONDS,
                    30,
                )
                await redis.set(_REDIS_TOKEN_KEY, token_data["access_token"], ex=ttl)
                return token_data["access_token"]

            await _set_auth_health(redis, False)
            raise AuthUnavailableError(
                "Fyers token expired and refresh failed. Re-login required."
            )

        # Token is valid — cache it
        ttl = max(
            int((effective_expiry - now).total_seconds()) - _EXPIRY_BUFFER_SECONDS,
            60,
        )
        await redis.set(_REDIS_TOKEN_KEY, token_data["access_token"], ex=ttl)
        await redis.set(_REDIS_EXPIRY_KEY, effective_expiry.isoformat(), ex=ttl)
        await _set_auth_health(redis, True, ttl_seconds=ttl)
        return token_data["access_token"]


async def get_auth_status_from_db(db: AsyncSession, redis=None) -> dict:
    """
    Returns auth status for the API /auth/status endpoint.

    Cutoff-aware: the reported expiry is the earlier of the stored value and
    the daily 06:30 IST session boundary derived from issuance, so the UI stops
    claiming "Connected" for a token the broker has already retired.
    """
    from app.services.auth_readiness import evaluate_auth_readiness

    readiness = await evaluate_auth_readiness(db, redis, verify=False)
    return {
        "authenticated": readiness["authenticated"],
        "healthy": readiness["healthy"],
        "reason": readiness["reason"],
        "expires_at": readiness.get("effective_expires_at") or readiness.get("expires_at"),
        "session_cutoff_ist": readiness["session_cutoff_ist"],
        "has_refresh_token": readiness["has_refresh_token"],
        "has_pin": readiness["has_pin"],
        "totp_configured": readiness["totp_configured"],
        "headless_login_enabled": readiness["headless_login_enabled"],
        "headless_login_configured": readiness["headless_login_configured"],
        "telegram_configured": readiness["telegram_configured"],
        "telegram_enabled": readiness["telegram_enabled"],
    }

