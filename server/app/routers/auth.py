import datetime
import secrets
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.security import save_fyers_token, get_fyers_token
from fyers_apiv3 import fyersModel

router = APIRouter(prefix="/auth", tags=["auth"])

class CallbackRequest(BaseModel):
    code: str
    state: str

@router.get("/url")
async def get_auth_url():
    """
    Generates the Fyers login URL and returns it to the client along with a random state
    for verification.
    """
    if not settings.fyers_app_id or not settings.fyers_secret_key:
        raise HTTPException(
            status_code=500,
            detail="Fyers API credentials are not configured in the backend environment."
        )
    
    state = secrets.token_hex(16)
    
    # Initialize the Fyers SessionModel
    session = fyersModel.SessionModel(
        client_id=settings.fyers_app_id,
        secret_key=settings.fyers_secret_key,
        redirect_uri=settings.fyers_redirect_uri,
        response_type="code",
        grant_type="authorization_code",
        state=state
    )
    
    url = session.generate_authcode()
    return {"url": url, "state": state}

@router.get("/callback")
async def handle_callback_get(
    auth_code: str,
    state: str,
    s: str = "ok",
    code: int = 200,
    db: AsyncSession = Depends(get_db)
):
    """
    Handles GET callback directly from Fyers (server-level redirect).
    Exchanges the code, saves it, and redirects the browser back to the frontend.
    """
    if not settings.fyers_app_id or not settings.fyers_secret_key:
        raise HTTPException(
            status_code=500,
            detail="Fyers API credentials are not configured in the backend environment."
        )
    
    # Recreate session for token generation
    session = fyersModel.SessionModel(
        client_id=settings.fyers_app_id,
        secret_key=settings.fyers_secret_key,
        redirect_uri=settings.fyers_redirect_uri,
        response_type="code",
        grant_type="authorization_code"
    )
    
    session.set_token(auth_code)
    
    frontend_redirect_url = "http://localhost:3000"
    
    try:
        response = session.generate_token()
    except Exception as e:
        return RedirectResponse(f"{frontend_redirect_url}/?error={str(e)}")
        
    if response.get("s") != "ok":
        error_msg = response.get("message", "Unknown error validating authorization code.")
        return RedirectResponse(f"{frontend_redirect_url}/?error={error_msg}")
        
    access_token = response.get("access_token")
    refresh_token = response.get("refresh_token")
    
    if not access_token:
        return RedirectResponse(f"{frontend_redirect_url}/?error=no_access_token_returned")
        
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
    await save_fyers_token(db, access_token, refresh_token, expires_at)
    
    return RedirectResponse(frontend_redirect_url)

@router.post("/callback")
async def handle_callback_post(payload: CallbackRequest, db: AsyncSession = Depends(get_db)):
    """
    Handles POST callback from the frontend (client-level redirect).
    Exchanges the authorization code and stores the tokens securely.
    """
    if not settings.fyers_app_id or not settings.fyers_secret_key:
        raise HTTPException(
            status_code=500,
            detail="Fyers API credentials are not configured in the backend environment."
        )
    
    # Recreate session for token generation
    session = fyersModel.SessionModel(
        client_id=settings.fyers_app_id,
        secret_key=settings.fyers_secret_key,
        redirect_uri=settings.fyers_redirect_uri,
        response_type="code",
        grant_type="authorization_code"
    )
    
    # Set the auth code received from Fyers
    session.set_token(payload.code)
    
    try:
        response = session.generate_token()
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to communicate with Fyers API: {str(e)}"
        )
    
    if response.get("s") != "ok":
        raise HTTPException(
            status_code=400,
            detail=response.get("message", "Unknown error validating authorization code.")
        )
    
    access_token = response.get("access_token")
    refresh_token = response.get("refresh_token")
    
    if not access_token:
        raise HTTPException(
            status_code=400,
            detail="No access token was returned by Fyers."
        )
    
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
    
    await save_fyers_token(db, access_token, refresh_token, expires_at)
    
    return {"status": "ok", "message": "Authenticated successfully"}

@router.get("/status")
async def get_auth_status(db: AsyncSession = Depends(get_db)):
    """
    Retrieves the current authentication status and expiration.
    """
    token_data = await get_fyers_token(db)
    if not token_data:
        return {"authenticated": False}
    
    expires_at = token_data["expires_at"]
    # Check if the token is expired or close to expiring (within 5 minutes buffer)
    now = datetime.datetime.now(datetime.timezone.utc)
    if expires_at < now + datetime.timedelta(minutes=5):
        return {"authenticated": False, "message": "Token expired"}
        
    return {
        "authenticated": True,
        "expires_at": expires_at.isoformat()
    }
