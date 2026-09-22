"""Headless Fyers login using the account's own External-2FA (TOTP) secret.

Fyers retires every API access token at 06:30 IST daily and, under SEBI's
April-2026 retail-algo framework, continuous refresh-token sessions are gone.
This module replays the broker's own web/app authentication steps with the
account holder's TOTP secret:

1. ``send_login_otp_v2``  → request key
2. ``verify_otp``         → request key (TOTP code from ``app.domain.totp``)
3. ``verify_pin_v2``      → identity bearer token
4. ``/api/v3/token``      → redirect URL carrying the one-time ``auth_code``
5. ``/api/v3/validate-authcode`` (pinned SDK) → access token

These are broker web endpoints, not part of the documented public API, so the
flow is opt-in (``AUTH_HEADLESS_LOGIN_ENABLED``), fully typed on failure, and
never the only path: the Telegram one-tap login remains the guaranteed route.
Secrets (TOTP key, PIN, tokens) are never logged.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import logging
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import httpx

from app.config import settings
from app.domain.totp import TotpSecretError, generate_totp, seconds_remaining

logger = logging.getLogger(__name__)

VAGATOR_BASE_URL = "https://api-t2.fyers.in/vagator/v2"
TOKEN_REQUEST_URL = "https://api-t1.fyers.in/api/v3/token"
# Fyers web/app 2FA application id used by the login endpoints.
WEB_APP_ID = "2"
REQUEST_TIMEOUT_SECONDS = 15.0
# A TOTP generated in the last few seconds of its step routinely expires in
# transit; wait for a fresh step instead of burning an attempt.
TOTP_MIN_REMAINING_SECONDS = 5.0

# Typed failure reasons — persisted in system_events, never a raw response body.
REASON_DISABLED = "headless_disabled"
REASON_NOT_CONFIGURED = "headless_not_configured"
REASON_TOTP_GENERATION = "totp_generation_failed"
REASON_OTP_REJECTED = "otp_rejected"
REASON_PIN_REJECTED = "pin_rejected"
REASON_IDENTITY_REJECTED = "identity_rejected"
REASON_AUTH_CODE_MISSING = "auth_code_missing"
REASON_EXCHANGE_REJECTED = "exchange_rejected"
REASON_NETWORK = "network"
REASON_NON_JSON = "non_json"

STEP_OTP = "send_login_otp_v2"
STEP_VERIFY_OTP = "verify_otp"
STEP_VERIFY_PIN = "verify_pin_v2"
STEP_TOKEN = "token"


@dataclass(frozen=True)
class HeadlessLoginResult:
    ok: bool
    access_token: str | None = None
    expires_in: int | None = None
    reason: str | None = None
    step: str | None = None
    detail: str | None = None
    request_keys: dict[str, str] = field(default_factory=dict)


class _StepFailure(Exception):
    def __init__(self, step: str, reason: str, detail: str | None = None) -> None:
        self.step = step
        self.reason = reason
        self.detail = detail
        super().__init__(f"{step}:{reason}")


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("ascii")).decode("ascii")


def extract_auth_code(url: str | None) -> str | None:
    """Pull the one-time ``auth_code`` out of the broker's redirect URL."""
    if not url:
        return None
    try:
        params = parse_qs(urlparse(url).query)
    except ValueError:
        return None
    for key in ("auth_code", "code"):
        values = params.get(key)
        if values and values[0]:
            return values[0]
    return None


async def _post_json(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    *,
    step: str,
    headers: dict[str, str] | None = None,
) -> dict:
    try:
        response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise _StepFailure(step, REASON_NETWORK, str(exc)) from exc

    try:
        data = response.json()
    except (ValueError, TypeError) as exc:
        raise _StepFailure(
            step, REASON_NON_JSON, f"status={response.status_code}"
        ) from exc
    if not isinstance(data, dict):
        raise _StepFailure(step, REASON_NON_JSON, "non-object payload")
    return data


def _request_key(data: dict, step: str) -> str:
    key = data.get("request_key")
    if not isinstance(key, str) or not key:
        message = str(data.get("message") or "no request_key")[:160]
        code = data.get("code")
        raise _StepFailure(step, _step_reason(step), f"{code}: {message}")
    return key


def _step_reason(step: str) -> str:
    if step in (STEP_OTP, STEP_VERIFY_OTP):
        return REASON_OTP_REJECTED
    if step == STEP_VERIFY_PIN:
        return REASON_PIN_REJECTED
    return REASON_IDENTITY_REJECTED


async def attempt_headless_login(
    *,
    user_id: str | None = None,
    pin: str | None = None,
    totp_key: str | None = None,
    redirect_uri: str | None = None,
    now: datetime.datetime | None = None,
    client: httpx.AsyncClient | None = None,
    sleep=asyncio.sleep,
) -> HeadlessLoginResult:
    """Run the full headless login chain. Never raises for broker/network errors."""
    if not settings.auth_headless_login_enabled:
        return HeadlessLoginResult(ok=False, reason=REASON_DISABLED)

    resolved_user_id = (user_id or settings.resolved_fyers_user_id or "").strip()
    resolved_pin = (pin if pin is not None else settings.fyers_pin or "").strip()
    resolved_totp = (totp_key if totp_key is not None else settings.fyers_totp_key or "").strip()
    resolved_redirect = (
        redirect_uri if redirect_uri is not None else settings.fyers_redirect_uri
    )

    missing = [
        name
        for name, value in (
            ("FYERS_USER_ID", resolved_user_id),
            ("FYERS_PIN", resolved_pin),
            ("FYERS_TOTP_KEY", resolved_totp),
        )
        if not value
    ]
    if missing:
        logger.warning(
            "Headless Fyers login enabled but missing configuration: %s",
            ", ".join(missing),
        )
        return HeadlessLoginResult(
            ok=False, reason=REASON_NOT_CONFIGURED, detail=",".join(missing)
        )

    if not settings.fyers_app_id or not settings.fyers_secret_key:
        return HeadlessLoginResult(
            ok=False, reason=REASON_NOT_CONFIGURED, detail="app_credentials"
        )

    try:
        remaining = seconds_remaining(at=now)
        if remaining < TOTP_MIN_REMAINING_SECONDS:
            wait_for = remaining + 0.5
            logger.info(
                "Waiting %.1fs for a fresh TOTP step before headless login",
                wait_for,
            )
            await sleep(wait_for)
            if now is not None:
                now = now + datetime.timedelta(seconds=wait_for)
        otp = generate_totp(resolved_totp, at=now)
    except TotpSecretError as exc:
        logger.warning("Headless Fyers login: unusable TOTP secret (%s)", exc)
        return HeadlessLoginResult(ok=False, reason=REASON_TOTP_GENERATION)

    owned_client = client is None
    http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        otp_payload = await _post_json(
            http,
            f"{VAGATOR_BASE_URL}/send_login_otp_v2",
            {"fy_id": _b64(resolved_user_id), "app_id": WEB_APP_ID},
            step=STEP_OTP,
        )
        request_key = _request_key(otp_payload, STEP_OTP)

        otp_verify_payload = await _post_json(
            http,
            f"{VAGATOR_BASE_URL}/verify_otp",
            {"request_key": request_key, "otp": otp},
            step=STEP_VERIFY_OTP,
        )
        request_key = _request_key(otp_verify_payload, STEP_VERIFY_OTP)

        pin_payload = await _post_json(
            http,
            f"{VAGATOR_BASE_URL}/verify_pin_v2",
            {
                "request_key": request_key,
                "identity_type": "pin",
                "identifier": _b64(resolved_pin),
            },
            step=STEP_VERIFY_PIN,
        )
        identity_token = (pin_payload.get("data") or {}).get("access_token")
        if not isinstance(identity_token, str) or not identity_token:
            code = pin_payload.get("code")
            message = str(pin_payload.get("message") or "no identity token")[:160]
            raise _StepFailure(
                STEP_VERIFY_PIN, REASON_IDENTITY_REJECTED, f"{code}: {message}"
            )

        token_payload = await _post_json(
            http,
            TOKEN_REQUEST_URL,
            {
                "fyers_id": resolved_user_id,
                "app_id": settings.fyers_app_id[:-4],
                "redirect_uri": resolved_redirect,
                "appType": "100",
                "code_challenge": "",
                "state": "None",
                "scope": "",
                "nonce": "",
                "response_type": "code",
                "create_cookie": True,
            },
            step=STEP_TOKEN,
            headers={"Authorization": f"Bearer {identity_token}"},
        )
        redirect_url = token_payload.get("Url") or token_payload.get("url")
        auth_code = extract_auth_code(redirect_url)
        if not auth_code:
            message = str(token_payload.get("message") or "no auth code")[:160]
            raise _StepFailure(
                STEP_TOKEN, REASON_AUTH_CODE_MISSING, message
            )
    except _StepFailure as failure:
        logger.warning(
            "Headless Fyers login failed at %s (%s)", failure.step, failure.reason
        )
        return HeadlessLoginResult(
            ok=False, reason=failure.reason, step=failure.step, detail=failure.detail
        )
    finally:
        if owned_client:
            await http.aclose()

    exchange = await exchange_authorization_code(auth_code)
    if not exchange.get("ok"):
        return HeadlessLoginResult(
            ok=False,
            reason=REASON_EXCHANGE_REJECTED,
            step=STEP_TOKEN,
            detail=str(exchange.get("message") or "")[:160],
        )

    return HeadlessLoginResult(
        ok=True,
        access_token=exchange["access_token"],
        expires_in=int(exchange.get("expires_in") or 86400),
    )


async def exchange_authorization_code(auth_code: str) -> dict:
    """Exchange a one-time auth code for an access token via the pinned SDK.

    The SDK call is synchronous (``requests``); it runs in a worker thread so
    the async money-path processes never block on broker I/O.
    """
    from fyers_apiv3 import fyersModel

    def _exchange() -> dict:
        session = fyersModel.SessionModel(
            client_id=settings.fyers_app_id,
            secret_key=settings.fyers_secret_key,
            redirect_uri=settings.fyers_redirect_uri,
            response_type="code",
            grant_type="authorization_code",
        )
        session.set_token(auth_code)
        return session.generate_token()

    try:
        response = await asyncio.to_thread(_exchange)
    except Exception as exc:  # broker/SDK/network
        logger.warning("Fyers authorization-code exchange failed: %s", exc)
        return {"ok": False, "message": "exchange_error"}

    if not isinstance(response, dict) or response.get("s") != "ok":
        message = "rejected"
        if isinstance(response, dict):
            message = str(response.get("message") or message)
        logger.warning("Fyers authorization-code exchange rejected: %s", message[:160])
        return {"ok": False, "message": message}

    access_token = response.get("access_token")
    if not access_token:
        return {"ok": False, "message": "no_access_token"}

    return {
        "ok": True,
        "access_token": access_token,
        "refresh_token": response.get("refresh_token"),
        "expires_in": int(response.get("expires_in") or 86400),
    }
