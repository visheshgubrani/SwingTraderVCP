"""FastAPI authentication and CSRF dependencies for personal trading endpoints."""

from __future__ import annotations

import logging
import secrets
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from arq.connections import ArqRedis

from app.config import settings
from app.services.session_service import get_user_session

logger = logging.getLogger(__name__)

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _extract_session_id(request: Request) -> str | None:
    """
    Extract session token strictly from HttpOnly cookie (SEC-001).
    Header-based session tokens (Bearer / X-Session-ID) are intentionally disabled
    to prevent XSS script access to session tokens.
    """
    return request.cookies.get(settings.session_cookie_name)


async def require_authenticated_user(request: Request) -> dict[str, Any]:
    """
    Dependency that enforces a valid Redis-backed session (SEC-001)
    and validates CSRF tokens on mutating requests.
    """
    redis: ArqRedis = request.app.state.redis
    session_id = _extract_session_id(request)

    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    session = await get_user_session(redis, session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid",
        )

    # CSRF check on mutating methods
    if request.method.upper() in MUTATING_METHODS:
        csrf_header = (
            request.headers.get("x-csrf-token")
            or request.headers.get("X-CSRF-Token")
            or ""
        )
        expected_csrf = session.get("csrf_token", "")
        if not csrf_header or not expected_csrf:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing CSRF token",
            )
        if not secrets.compare_digest(csrf_header.encode("utf-8"), expected_csrf.encode("utf-8")):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid CSRF token",
            )

    return session


async def get_optional_authenticated_user(request: Request) -> dict[str, Any] | None:
    """Optional session dependency that returns None instead of raising 401."""
    redis: ArqRedis = request.app.state.redis
    session_id = _extract_session_id(request)
    if not session_id:
        return None
    return await get_user_session(redis, session_id)


authenticated_user_dep = Annotated[dict[str, Any], Depends(require_authenticated_user)]
