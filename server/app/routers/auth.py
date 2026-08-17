import datetime
import json
import logging
import time
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from arq.connections import ArqRedis

from app.config import settings
from app.database import get_db
from app.dependencies.auth import _extract_session_id, require_authenticated_user
from app.security import save_fyers_token
from app.services.auth_service import (
    get_auth_status_from_db,
    persist_and_cache_fyers_token,
    refresh_and_save,
)
from app.services.session_service import (
    check_login_rate_limit,
    clear_failed_logins,
    create_oauth_state,
    create_user_session,
    extract_client_ip,
    record_failed_login,
    revoke_user_session,
    verify_and_consume_oauth_state,
    verify_app_password,
)
from fyers_apiv3 import fyersModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Simple cooldown for manual refresh — 30 seconds between attempts
_last_refresh_ts: float = 0.0
_REFRESH_COOLDOWN_SECONDS = 30


class LoginRequest(BaseModel):
    password: str


class CallbackRequest(BaseModel):
    code: str
    state: str


# --- Personal App Authentication (SEC-001) ---


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
):
    """
    Authenticate single owner/user against configured APP_PASSWORD.
    Creates a Redis-backed session, sets HttpOnly cookie, and returns CSRF token.
    (Session token is NEVER returned in response JSON to prevent XSS theft).
    """
    redis: ArqRedis = request.app.state.redis
    client_ip = extract_client_ip(request)

    # Check brute force rate limit
    allowed, retry_after = await check_login_rate_limit(redis, client_ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Try again in {retry_after}s.",
        )

    if not verify_app_password(payload.password):
        attempts = await record_failed_login(redis, client_ip)
        logger.warning(
            "Failed login attempt from %s (attempt %d)", client_ip, attempts
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password",
        )

    await clear_failed_logins(redis, client_ip)
    user_agent = request.headers.get("user-agent")
    session_info = await create_user_session(
        redis, user_agent=user_agent, ip=client_ip
    )

    # Set secure HttpOnly cookie (cookie-only isolation)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_info["session_id"],
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        domain=settings.session_cookie_domain,
        path="/",
    )

    return {
        "status": "ok",
        "csrf_token": session_info["csrf_token"],
        "expires_at": session_info["expires_at"],
    }


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
):
    """Revoke active session in Redis and clear session cookie."""
    redis: ArqRedis = request.app.state.redis
    session_id = _extract_session_id(request)
    if session_id:
        await revoke_user_session(redis, session_id)

    response.delete_cookie(
        key=settings.session_cookie_name,
        domain=settings.session_cookie_domain,
        path="/",
    )
    return {"status": "ok", "message": "Logged out successfully"}


@router.get("/session")
async def get_session_status(
    session: dict = Depends(require_authenticated_user),
):
    """Check if current caller holds a valid app session and return active CSRF token."""
    return {
        "authenticated": True,
        "csrf_token": session.get("csrf_token"),
        "expires_at": session.get("expires_at"),
    }


# --- Fyers Broker OAuth (SEC-003) ---


@router.get("/url")
async def get_auth_url(
    request: Request,
    user: dict = Depends(require_authenticated_user),
):
    """Generate Fyers OAuth URL and record state in Redis bound to caller session."""
    if not settings.fyers_app_id or not settings.fyers_secret_key:
        raise HTTPException(
            status_code=500,
            detail="Fyers API credentials are not configured in the backend environment.",
        )

    redis: ArqRedis = request.app.state.redis
    state = await create_oauth_state(redis, session_id=user["session_id"])

    session = fyersModel.SessionModel(
        client_id=settings.fyers_app_id,
        secret_key=settings.fyers_secret_key,
        redirect_uri=settings.fyers_redirect_uri,
        response_type="code",
        grant_type="authorization_code",
        state=state,
    )

    url = session.generate_authcode()
    return {"url": url, "state": state}


async def _exchange_code_and_save(
    db: AsyncSession,
    redis: ArqRedis,
    auth_code: str,
    state: str,
    expected_session_id: str,
) -> dict:
    """Validate session-bound OAuth state (SEC-003) and exchange code with Fyers API."""
    if not settings.fyers_app_id or not settings.fyers_secret_key:
        raise HTTPException(
            status_code=500,
            detail="Fyers API credentials are not configured in the backend environment.",
        )

    # Validate state from Redis bound to this session (consume once)
    is_valid_state = await verify_and_consume_oauth_state(
        redis, state, expected_session_id=expected_session_id
    )
    if not is_valid_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid, expired, or mismatched OAuth state.",
        )

    session = fyersModel.SessionModel(
        client_id=settings.fyers_app_id,
        secret_key=settings.fyers_secret_key,
        redirect_uri=settings.fyers_redirect_uri,
        response_type="code",
        grant_type="authorization_code",
    )

    session.set_token(auth_code)

    try:
        response = session.generate_token()
    except Exception as e:
        logger.error("Failed to exchange Fyers auth code: %s", e)
        raise HTTPException(
            status_code=400,
            detail="Failed to communicate with Fyers API.",
        )

    if response.get("s") != "ok":
        error_msg = response.get("message", "Unknown error validating authorization code.")
        logger.warning("Fyers token exchange rejected: %s", error_msg)
        raise HTTPException(
            status_code=400,
            detail=error_msg,
        )

    access_token = response.get("access_token")
    refresh_token = response.get("refresh_token")

    if not access_token:
        raise HTTPException(
            status_code=400, detail="No access token was returned by Fyers."
        )

    expires_in = response.get("expires_in", 86400)
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=expires_in
    )

    await persist_and_cache_fyers_token(
        db,
        redis,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        expires_in=expires_in,
    )

    # Emit system event for auth success
    await db.execute(
        text("""
            INSERT INTO system_events (component, severity, event_type, payload)
            VALUES ('auth_service', 'info', 'auth_login_succeeded',
                    :payload)
        """),
        {"payload": json.dumps({"expires_at": expires_at.isoformat()})},
    )
    await db.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
    }


@router.post("/callback")
async def handle_callback_post(
    payload: CallbackRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_authenticated_user),
):
    """Direct POST exchange from authenticated client callback."""
    redis: ArqRedis = request.app.state.redis
    await _exchange_code_and_save(
        db,
        redis,
        payload.code,
        payload.state,
        expected_session_id=user["session_id"],
    )
    return {"status": "ok", "message": "Authenticated successfully"}


@router.get("/status")
async def get_auth_status(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_authenticated_user),
):
    """Broker auth status check for authenticated user."""
    return await get_auth_status_from_db(db)


@router.get("/events")
async def get_auth_events(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_authenticated_user),
):
    """Recent auth-related system events for UI banner / debugging."""
    result = await db.execute(
        text("""
            SELECT event_ts, severity, event_type, payload
            FROM system_events
            WHERE component = 'auth_service'
            ORDER BY event_ts DESC
            LIMIT :limit
        """),
        {"limit": limit},
    )
    rows = result.mappings().all()
    return [
        {
            "event_ts": r["event_ts"].isoformat(),
            "severity": r["severity"],
            "event_type": r["event_type"],
            "payload": r["payload"],
        }
        for r in rows
    ]


@router.post("/refresh")
async def manual_refresh(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_authenticated_user),
):
    """Manual Fyers token refresh trigger for authenticated user."""
    global _last_refresh_ts
    now = time.monotonic()
    elapsed = now - _last_refresh_ts
    if elapsed < _REFRESH_COOLDOWN_SECONDS:
        raise HTTPException(
            status_code=429,
            detail=f"Refresh cooldown active. Try again in {int(_REFRESH_COOLDOWN_SECONDS - elapsed)}s.",
        )
    _last_refresh_ts = now

    redis: ArqRedis = request.app.state.redis
    try:
        new_token = await refresh_and_save(db, redis)
    except Exception as e:
        logger.error("Manual refresh error: %s", e)
        raise HTTPException(status_code=500, detail="Token refresh failed")

    if new_token:
        return {"status": "ok", "message": "Token refreshed successfully"}
    raise HTTPException(status_code=400, detail="Token refresh failed — re-login required")
