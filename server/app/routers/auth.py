import datetime
import logging
import time
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from arq.connections import ArqRedis

from app.config import settings
from app.database import get_db
from app.dependencies.auth import (
    _extract_session_id,
    enforce_csrf,
    get_optional_authenticated_user,
    require_authenticated_user,
)
from app.services.auth_readiness import (
    clamp_token_expiry,
    evaluate_auth_readiness,
    verify_fyers_session,
)
from app.services.auth_service import (
    get_auth_status_from_db,
    persist_and_cache_fyers_token,
    refresh_and_save,
)
from app.services.fyers_totp import exchange_authorization_code
from app.services.session_service import (
    OAUTH_STATE_KIND_DIRECT,
    OAUTH_STATE_KIND_SESSION,
    check_login_rate_limit,
    clear_failed_logins,
    consume_direct_login_nonce_use,
    consume_oauth_state,
    create_oauth_state,
    create_user_session,
    extract_client_ip,
    peek_oauth_state,
    record_failed_login,
    revoke_user_session,
    verify_app_password,
)
from app.services.token_refresh import attempt_scheduled_headless_login
from fyers_apiv3 import fyersModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Simple cooldown for manual refresh — 30 seconds between attempts
_last_refresh_ts: float = 0.0
_REFRESH_COOLDOWN_SECONDS = 30

# One-tap login links are high-entropy but public: cap per-IP starts and cap
# manual headless-login triggers so a leaked link cannot spin the broker login.
_DIRECT_LOGIN_IP_PREFIX = "auth:direct_login_ip:"
_DIRECT_LOGIN_IP_WINDOW_SECONDS = 3600
_DIRECT_LOGIN_IP_MAX_PER_WINDOW = 20
_TOTP_LOGIN_COOLDOWN_KEY = "auth:manual_totp_login"
_TOTP_LOGIN_COOLDOWN_SECONDS = 60


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


async def _emit_auth_event(
    db: AsyncSession,
    severity: str,
    event_type: str,
    payload: dict | None = None,
    *,
    redis=None,
    cooldown_seconds: int | None = None,
) -> None:
    """Record an auth system event for this module (caller commits)."""
    from app.services.auth_service import _emit_system_event

    await _emit_system_event(
        db,
        severity,
        event_type,
        payload,
        redis=redis,
        cooldown_seconds=cooldown_seconds,
    )


async def _exchange_code_and_save(
    db: AsyncSession,
    redis: ArqRedis,
    auth_code: str,
    *,
    method: str,
) -> dict:
    """Exchange an already-validated broker auth code and persist the token.

    Ownership is verified before anything is written: a public App ID lets any
    Fyers account complete the authorization flow, so an auth code that belongs
    to a different account must never replace the owner's session.
    """
    if not settings.fyers_app_id or not settings.fyers_secret_key:
        raise HTTPException(
            status_code=500,
            detail="Fyers API credentials are not configured in the backend environment.",
        )

    exchange = await exchange_authorization_code(auth_code)
    if not exchange.get("ok"):
        raise HTTPException(status_code=400, detail=str(exchange.get("message") or "Fyers rejected the authorization code."))

    access_token = exchange["access_token"]
    verification = await verify_fyers_session(access_token)
    expected_identity = settings.resolved_fyers_user_id
    if (
        expected_identity
        and verification.identity
        and verification.identity != expected_identity
    ):
        await _emit_auth_event(
            db,
            "critical",
            "auth_login_owner_mismatch",
            {"method": method, "expected": expected_identity},
            redis=redis,
            cooldown_seconds=0,
        )
        await db.commit()
        raise HTTPException(
            status_code=403,
            detail="This Fyers account is not the configured owner account.",
        )
    if not verification.ok:
        logger.warning(
            "Fyers code exchange succeeded but the session did not verify: %s",
            verification.error,
        )

    expires_in = int(exchange.get("expires_in") or 86400)
    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = clamp_token_expiry(now, expires_in)

    await persist_and_cache_fyers_token(
        db,
        redis,
        access_token=access_token,
        refresh_token=exchange.get("refresh_token"),
        expires_at=expires_at,
        expires_in=expires_in,
    )

    await _emit_auth_event(
        db,
        "info",
        "auth_login_succeeded",
        {"method": method, "expires_at": expires_at.isoformat()},
        redis=redis,
        cooldown_seconds=0,
    )
    await db.commit()

    # Confirmation to the phone (the tap path) and a marker so the morning
    # guard does not double-notify for the same session.
    try:
        from app.services import telegram_service
        from app.services.auth_readiness import as_ist

        await telegram_service.send_auth_success(
            db,
            method=method,
            session_date=as_ist(now).date().isoformat(),
            expires_at_ist=as_ist(expires_at).strftime("%d %b %H:%M"),
        )
        await db.commit()
    except Exception as exc:  # notifications never break login
        logger.warning("Could not send login confirmation: %s", exc)

    try:
        from app.services.token_refresh import mark_session_authenticated

        await mark_session_authenticated(redis, method=method)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not record session-auth marker: %s", exc)

    return {
        "access_token": access_token,
        "refresh_token": exchange.get("refresh_token"),
        "expires_at": expires_at,
    }


@router.post("/callback")
async def handle_callback_post(
    payload: CallbackRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict | None = Depends(get_optional_authenticated_user),
):
    """Complete the Fyers OAuth bounce.

    Two state kinds are accepted, and the state (not the caller) decides which
    enforcement applies:

    * ``session`` — the dashboard flow. Requires the app session plus CSRF, and
      the state must be bound to that same session (SEC-001/SEC-003).
    * ``direct``  — the Telegram one-tap flow, redeemed from a phone browser
      that has no app session. Requires a live single-use direct-login nonce
      binding, still exchanges server-side, and still verifies ownership.
    """
    redis: ArqRedis = request.app.state.redis
    state_data = await peek_oauth_state(redis, payload.state)
    if not state_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid, expired, or already-used OAuth state.",
        )

    kind = state_data.get("kind", OAUTH_STATE_KIND_SESSION)
    if kind == OAUTH_STATE_KIND_SESSION:
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        enforce_csrf(request, user)
        consumed = await consume_oauth_state(redis, payload.state)
        if not consumed or consumed.get("session_id") != user.get("session_id"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid, expired, or mismatched OAuth state.",
            )
        method = "browser"
    elif kind == OAUTH_STATE_KIND_DIRECT:
        if not state_data.get("nonce"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid direct login state.",
            )
        consumed = await consume_oauth_state(redis, payload.state)
        if not consumed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid, expired, or already-used OAuth state.",
            )
        method = "direct_link"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported OAuth state.",
        )

    await _exchange_code_and_save(db, redis, payload.code, method=method)
    return {"status": "ok", "message": "Authenticated successfully"}


# --- One-tap login (Telegram) ---------------------------------------------


async def _check_direct_login_rate_limit(redis: ArqRedis, client_ip: str) -> None:
    key = f"{_DIRECT_LOGIN_IP_PREFIX}{client_ip}"
    uses = int(await redis.incr(key))
    if uses == 1:
        await redis.expire(key, _DIRECT_LOGIN_IP_WINDOW_SECONDS)
    if uses > _DIRECT_LOGIN_IP_MAX_PER_WINDOW:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login link uses. Wait and request a fresh link.",
        )


@router.get("/direct-login")
async def direct_login(
    t: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Start the official Fyers OAuth flow from a one-tap Telegram link.

    The nonce is a capability to *start* a login, not to mint a token: the
    code still has to come back from Fyers to the registered redirect URI and
    pass the post-exchange ownership check.
    """
    redis: ArqRedis = request.app.state.redis
    if not settings.fyers_app_id or not settings.fyers_secret_key:
        raise HTTPException(
            status_code=500,
            detail="Fyers API credentials are not configured in the backend environment.",
        )

    client_ip = extract_client_ip(request)
    try:
        await _check_direct_login_rate_limit(redis, client_ip)
    except HTTPException:
        await _emit_auth_event(
            db,
            "warning",
            "auth_direct_login_rejected",
            {"reason": "rate_limited", "ip": client_ip},
            redis=redis,
        )
        await db.commit()
        raise

    if not await consume_direct_login_nonce_use(redis, t):
        await _emit_auth_event(
            db,
            "warning",
            "auth_direct_login_rejected",
            {"reason": "invalid_or_expired_nonce", "ip": client_ip},
            redis=redis,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This login link is invalid or has expired. Send a fresh link from the dashboard.",
        )

    state = await create_oauth_state(
        redis,
        session_id="",
        kind=OAUTH_STATE_KIND_DIRECT,
        nonce=t,
    )
    session = fyersModel.SessionModel(
        client_id=settings.fyers_app_id,
        secret_key=settings.fyers_secret_key,
        redirect_uri=settings.fyers_redirect_uri,
        response_type="code",
        grant_type="authorization_code",
        state=state,
    )

    await _emit_auth_event(
        db,
        "info",
        "auth_direct_login_started",
        {"ip": client_ip},
        redis=redis,
    )
    await db.commit()

    return RedirectResponse(session.generate_authcode(), status_code=status.HTTP_302_FOUND)


@router.post("/totp-login")
async def manual_totp_login(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_authenticated_user),
):
    """Manually run the headless TOTP login (dashboard button / debugging)."""
    if not settings.auth_headless_login_enabled:
        raise HTTPException(
            status_code=400,
            detail="Headless TOTP login is disabled. Set AUTH_HEADLESS_LOGIN_ENABLED=true.",
        )
    if not settings.headless_login_configured:
        raise HTTPException(
            status_code=400,
            detail="FYERS_USER_ID, FYERS_PIN and FYERS_TOTP_KEY are required.",
        )

    redis: ArqRedis = request.app.state.redis
    if not await redis.set(_TOTP_LOGIN_COOLDOWN_KEY, "1", ex=_TOTP_LOGIN_COOLDOWN_SECONDS, nx=True):
        raise HTTPException(
            status_code=429,
            detail=f"TOTP login cooldown active. Try again in {_TOTP_LOGIN_COOLDOWN_SECONDS}s.",
        )

    outcome = await attempt_scheduled_headless_login(db, redis)
    if not outcome.get("ok"):
        await _emit_auth_event(
            db,
            "warning",
            "auth_headless_login_failed",
            {
                "reason": outcome.get("reason"),
                "step": outcome.get("step"),
                "trigger": "manual",
            },
            redis=redis,
        )
        await db.commit()
        raise HTTPException(
            status_code=400,
            detail=f"Headless login failed ({outcome.get('reason')}).",
        )

    return {"status": "ok", "expires_at": outcome.get("expires_at")}


@router.post("/send-login-link")
async def send_login_link(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_authenticated_user),
):
    """Push a one-tap Fyers login link to Telegram on demand.

    Used from the dashboard banner when the session is missing but the phone is
    closer than the keyboard; the guard sends the same link automatically.
    """
    from app.services import telegram_service
    from app.services.auth_readiness import as_ist, is_nse_session
    from app.services.token_refresh import build_magic_login_url

    if not settings.telegram_notifications_enabled:
        raise HTTPException(
            status_code=400,
            detail="Telegram notifications are disabled (TELEGRAM_NOTIFICATIONS_ENABLED=false).",
        )

    redis: ArqRedis = request.app.state.redis
    login_url = await build_magic_login_url(redis)
    if not login_url:
        raise HTTPException(
            status_code=400,
            detail="API_PUBLIC_BASE_URL is not configured, so a login link cannot be built.",
        )

    now_ist = as_ist(datetime.datetime.now(datetime.timezone.utc))
    sent = await telegram_service.send_auth_expired_alert(
        db,
        login_url=login_url,
        session_date=now_ist.date().isoformat(),
        reason="manual_request",
        severity="warning",
        trading_day=is_nse_session(now_ist.date()),
    )
    await db.commit()
    if not sent:
        raise HTTPException(status_code=502, detail="Telegram send failed.")

    return {
        "status": "ok",
        "link_expires_in_minutes": settings.auth_magic_link_ttl_minutes,
    }


@router.post("/verify")
async def verify_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_authenticated_user),
):
    """Live broker check on demand (dashboard "Test connection")."""
    redis: ArqRedis = request.app.state.redis
    readiness = await evaluate_auth_readiness(db, redis, verify=True)
    return {
        "authenticated": readiness["authenticated"],
        "healthy": readiness["healthy"],
        "verified": readiness.get("verified", False),
        "reason": readiness.get("reason"),
        "identity": readiness.get("identity"),
        "expires_at": readiness.get("effective_expires_at")
        or readiness.get("expires_at"),
        "session_cutoff_ist": readiness["session_cutoff_ist"],
    }


@router.get("/status")
async def get_auth_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_authenticated_user),
):
    """Broker auth status check for authenticated user."""
    redis: ArqRedis = getattr(request.app.state, "redis", None)
    return await get_auth_status_from_db(db, redis)


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
