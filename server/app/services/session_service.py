"""Session management, password verification, rate limiting, and OAuth state service."""

from __future__ import annotations

import datetime
import json
import logging
import secrets
from typing import Any

from fastapi import Request

from app.config import settings

logger = logging.getLogger(__name__)

SESSION_KEY_PREFIX = "auth:session:"
OAUTH_STATE_KEY_PREFIX = "auth:oauth_state:"
DIRECT_LOGIN_KEY_PREFIX = "auth:direct_nonce:"
LOGIN_ATTEMPTS_PREFIX = "auth:login_attempts:"
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 900  # 15 minutes
TRUSTED_PROXY_IPS = {"127.0.0.1", "::1", "localhost", "testclient"}

OAUTH_STATE_KIND_SESSION = "session"
OAUTH_STATE_KIND_DIRECT = "direct"
OAUTH_STATE_TTL_SECONDS = 600  # 10 minutes


def extract_client_ip(request: Request) -> str:
    """
    Extract real client IP safely considering reverse proxies (e.g. Caddy).
    Only trusts X-Forwarded-For if the direct peer is a trusted local proxy.
    """
    direct_ip = request.client.host if request.client else "unknown"

    if direct_ip in TRUSTED_PROXY_IPS:
        x_forwarded_for = request.headers.get("x-forwarded-for")
        if x_forwarded_for:
            # Client IP is the leftmost address in X-Forwarded-For
            client_ip = x_forwarded_for.split(",")[0].strip()
            if client_ip:
                return client_ip

        x_real_ip = request.headers.get("x-real-ip")
        if x_real_ip:
            return x_real_ip.strip()

    return direct_ip


def verify_app_password(password: str) -> bool:
    """Constant-time password comparison against configured APP_PASSWORD."""
    if not password or not settings.app_password:
        return False
    return secrets.compare_digest(
        password.encode("utf-8"),
        settings.app_password.encode("utf-8"),
    )


async def check_login_rate_limit(redis: Any, ip: str) -> tuple[bool, int]:
    """
    Check if IP has exceeded maximum failed login attempts.
    Returns (is_allowed, retry_after_seconds).
    """
    key = f"{LOGIN_ATTEMPTS_PREFIX}{ip}"
    attempts = await redis.get(key)
    if attempts is not None and int(attempts) >= MAX_FAILED_ATTEMPTS:
        ttl = await redis.ttl(key)
        return False, max(1, ttl)
    return True, 0


async def record_failed_login(redis: Any, ip: str) -> int:
    """Record a failed login attempt for the IP."""
    key = f"{LOGIN_ATTEMPTS_PREFIX}{ip}"
    pipe = redis.pipeline()
    pipe.incr(key)
    pipe.expire(key, LOCKOUT_DURATION_SECONDS)
    results = await pipe.execute()
    return int(results[0])


async def clear_failed_logins(redis: Any, ip: str) -> None:
    """Clear failed login attempts counter on successful login."""
    key = f"{LOGIN_ATTEMPTS_PREFIX}{ip}"
    await redis.delete(key)


async def create_user_session(
    redis: Any,
    user_agent: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    """Create a new opaque Redis-backed session with bound CSRF token."""
    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = now + datetime.timedelta(seconds=settings.session_ttl_seconds)

    payload = {
        "session_id": session_id,
        "csrf_token": csrf_token,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "user_agent": user_agent or "",
        "ip": ip or "",
    }

    key = f"{SESSION_KEY_PREFIX}{session_id}"
    await redis.set(key, json.dumps(payload), ex=settings.session_ttl_seconds)
    logger.info("Created user session (ip=%s, expires_at=%s)", ip, expires_at.isoformat())

    return {
        "session_id": session_id,
        "csrf_token": csrf_token,
        "expires_at": expires_at.isoformat(),
    }


async def get_user_session(redis: Any, session_id: str | None) -> dict[str, Any] | None:
    """Retrieve and validate session from Redis."""
    if not session_id:
        return None
    key = f"{SESSION_KEY_PREFIX}{session_id}"
    raw = await redis.get(key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return data
    except (json.JSONDecodeError, TypeError):
        return None


async def revoke_user_session(redis: Any, session_id: str | None) -> None:
    """Revoke user session immediately from Redis."""
    if not session_id:
        return
    key = f"{SESSION_KEY_PREFIX}{session_id}"
    await redis.delete(key)
    logger.info("Revoked user session %s", session_id[:8] + "...")


async def create_oauth_state(
    redis: Any,
    session_id: str,
    *,
    kind: str = OAUTH_STATE_KIND_SESSION,
    nonce: str | None = None,
) -> str:
    """Generate and store a short-lived OAuth state in Redis (SEC-003).

    ``kind="session"`` (default) binds the state to an app session, exactly as
    before. ``kind="direct"`` binds it to a single-use direct-login nonce so the
    Telegram one-tap link can complete without an app session in the phone's
    browser; it carries no session id and is never redeemable as a session state.
    """
    state = secrets.token_hex(16)
    payload = {
        "kind": kind,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if kind == OAUTH_STATE_KIND_SESSION:
        payload["session_id"] = session_id
    else:
        payload["nonce"] = nonce or ""
    key = f"{OAUTH_STATE_KEY_PREFIX}{state}"
    await redis.set(key, json.dumps(payload), ex=OAUTH_STATE_TTL_SECONDS)
    return state


async def _read_oauth_state(redis: Any, state: str | None) -> dict[str, Any] | None:
    if not state:
        return None
    raw = await redis.get(f"{OAUTH_STATE_KEY_PREFIX}{state}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


async def peek_oauth_state(redis: Any, state: str | None) -> dict[str, Any] | None:
    """Read an OAuth state without consuming it (used to pick an enforcement path)."""
    return await _read_oauth_state(redis, state)


async def consume_oauth_state(redis: Any, state: str | None) -> dict[str, Any] | None:
    """Read and consume an OAuth state exactly once."""
    data = await _read_oauth_state(redis, state)
    if data is None:
        return None
    await redis.delete(f"{OAUTH_STATE_KEY_PREFIX}{state}")
    return data


async def create_direct_login_nonce(
    redis: Any,
    *,
    ttl_seconds: int | None = None,
    purpose: str = "fyers_direct_login",
) -> str:
    """Mint the capability embedded in the Telegram one-tap login link.

    The nonce only lets a caller *start* the official Fyers OAuth flow. It
    cannot mint a token by itself: redemption still requires a broker-issued
    auth code and a post-exchange owner check before anything is persisted.
    """
    nonce = secrets.token_urlsafe(24)
    ttl = ttl_seconds if ttl_seconds is not None else settings.auth_magic_link_ttl_minutes * 60
    payload = {
        "purpose": purpose,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    await redis.set(
        f"{DIRECT_LOGIN_KEY_PREFIX}{nonce}", json.dumps(payload), ex=max(int(ttl), 60)
    )
    return nonce


async def consume_direct_login_nonce_use(
    redis: Any,
    nonce: str | None,
    *,
    max_uses: int | None = None,
    ttl_seconds: int | None = None,
) -> bool:
    """Validate a direct-login nonce and count one use. False when unknown/exhausted."""
    if not nonce:
        return False
    key = f"{DIRECT_LOGIN_KEY_PREFIX}{nonce}"
    if not await redis.get(key):
        return False

    limit = max_uses if max_uses is not None else settings.auth_magic_link_max_uses
    uses_key = f"{key}:uses"
    uses = int(await redis.incr(uses_key))
    if uses == 1:
        ttl = (
            ttl_seconds
            if ttl_seconds is not None
            else settings.auth_magic_link_ttl_minutes * 60
        )
        await redis.expire(uses_key, max(int(ttl), 60))
    if uses > max(int(limit), 1):
        logger.warning("Direct login nonce exhausted; rejecting use %d", uses)
        return False
    return True


async def verify_and_consume_oauth_state(
    redis: Any,
    state: str | None,
    expected_session_id: str,
) -> bool:
    """
    Validate and consume-once a session-bound OAuth state from Redis.
    Rejects missing, expired, replayed states, states generated by another
    session, and direct (non-session) states.
    """
    if not state or not expected_session_id:
        return False
    key = f"{OAUTH_STATE_KEY_PREFIX}{state}"
    raw = await redis.get(key)
    if not raw:
        return False
    await redis.delete(key)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return False
        if data.get("kind", OAUTH_STATE_KIND_SESSION) != OAUTH_STATE_KIND_SESSION:
            return False
        bound_session_id = data.get("session_id")
        if not bound_session_id:
            return False
        return secrets.compare_digest(
            bound_session_id.encode("utf-8"),
            expected_session_id.encode("utf-8"),
        )
    except (json.JSONDecodeError, TypeError):
        return False
