import datetime
import json
import secrets
import time
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from arq.connections import ArqRedis

from app.config import settings
from app.database import get_db
from app.security import save_fyers_token, get_fyers_token
from app.services.auth_service import (
    get_auth_status_from_db,
    refresh_and_save,
    AuthUnavailableError,
)
from fyers_apiv3 import fyersModel

router = APIRouter(prefix="/auth", tags=["auth"])

# Simple cooldown for manual refresh — 30 seconds between attempts
_last_refresh_ts: float = 0.0
_REFRESH_COOLDOWN_SECONDS = 30


class CallbackRequest(BaseModel):
    code: str
    state: str


@router.get("/url")
async def get_auth_url():
    if not settings.fyers_app_id or not settings.fyers_secret_key:
        raise HTTPException(
            status_code=500,
            detail="Fyers API credentials are not configured in the backend environment.",
        )

    state = secrets.token_hex(16)

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


async def _exchange_code_and_save(db: AsyncSession, auth_code: str) -> dict:
    """Common logic for both callback endpoints."""
    if not settings.fyers_app_id or not settings.fyers_secret_key:
        raise HTTPException(
            status_code=500,
            detail="Fyers API credentials are not configured in the backend environment.",
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
        raise HTTPException(
            status_code=400,
            detail=f"Failed to communicate with Fyers API: {e}",
        )

    if response.get("s") != "ok":
        raise HTTPException(
            status_code=400,
            detail=response.get("message", "Unknown error validating authorization code."),
        )

    access_token = response.get("access_token")
    refresh_token = response.get("refresh_token")

    if not access_token:
        raise HTTPException(status_code=400, detail="No access token was returned by Fyers.")

    expires_in = response.get("expires_in", 86400)
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=expires_in
    )

    await save_fyers_token(db, access_token, refresh_token, expires_at)

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

    return {"access_token": access_token, "refresh_token": refresh_token, "expires_at": expires_at}


@router.get("/callback")
async def handle_callback_get(
    auth_code: str,
    state: str,
    s: str = "ok",
    code: int = 200,
    db: AsyncSession = Depends(get_db),
):
    frontend_redirect_url = "http://localhost:3000"

    try:
        await _exchange_code_and_save(db, auth_code)
    except HTTPException as e:
        return RedirectResponse(f"{frontend_redirect_url}/?error={e.detail}")
    except Exception as e:
        return RedirectResponse(f"{frontend_redirect_url}/?error={e}")

    return RedirectResponse(frontend_redirect_url)


@router.post("/callback")
async def handle_callback_post(payload: CallbackRequest, db: AsyncSession = Depends(get_db)):
    await _exchange_code_and_save(db, payload.code)
    return {"status": "ok", "message": "Authenticated successfully"}


@router.get("/status")
async def get_auth_status(db: AsyncSession = Depends(get_db)):
    return await get_auth_status_from_db(db)


@router.get("/events")
async def get_auth_events(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """Recent auth-related system events for the UI banner / debugging."""
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
async def manual_refresh(request: Request, db: AsyncSession = Depends(get_db)):
    """Manual refresh trigger — UI can call this when banner shows auth failure."""
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
        raise HTTPException(status_code=500, detail=str(e))

    if new_token:
        return {"status": "ok", "message": "Token refreshed successfully"}
    raise HTTPException(status_code=400, detail="Token refresh failed — re-login required")
