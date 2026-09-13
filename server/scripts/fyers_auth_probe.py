#!/usr/bin/env python3
"""Live probe for the daily Fyers auth chain (run it on the always-on VPS).

Phase-0 validation before trusting the guard:

    # 1. Does the headless TOTP chain still exist and accept our credentials?
    python scripts/fyers_auth_probe.py --dry-run

    # 2. Full login + one live authenticated call (prints no secrets)
    python scripts/fyers_auth_probe.py --verify

    # 3. Can the owner be reached on Telegram?
    python scripts/fyers_auth_probe.py --telegram-test

The probe never writes a token to Postgres/Redis — the guard owns persistence —
and it never prints tokens, auth codes, the TOTP secret, or the PIN.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.domain.totp import TotpSecretError, generate_totp, seconds_remaining  # noqa: E402
from app.services.auth_readiness import (  # noqa: E402
    next_session_cutoff_ist,
    verify_fyers_session,
)
from app.services.fyers_totp import (  # noqa: E402
    STEP_OTP,
    STEP_TOKEN,
    STEP_VERIFY_OTP,
    STEP_VERIFY_PIN,
    TOKEN_REQUEST_URL,
    VAGATOR_BASE_URL,
    WEB_APP_ID,
    _b64,
    _post_json,
    extract_auth_code,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Run the login chain and report each step's status shape only.")
    parser.add_argument("--verify", action="store_true", help="Full headless login followed by a live /profile check.")
    parser.add_argument("--telegram-test", action="store_true", help="Send a Telegram test message to TELEGRAM_CHAT_ID.")
    parser.add_argument("--otp-peek", action="store_true", help="Print the current TOTP code length/step timing (never the code).")
    return parser.parse_args()


def _shape(step: str, payload: dict) -> dict:
    """Response shape without any credential material."""
    data = payload.get("data")
    return {
        "step": step,
        "s": payload.get("s"),
        "code": payload.get("code"),
        "message": str(payload.get("message") or "")[:160],
        "has_request_key": bool(payload.get("request_key")),
        "data_keys": sorted(data.keys()) if isinstance(data, dict) else None,
        "has_url": bool(payload.get("Url") or payload.get("url")),
    }


async def _dry_run() -> int:
    user_id = settings.resolved_fyers_user_id
    pin = (settings.fyers_pin or "").strip()
    totp_key = (settings.fyers_totp_key or "").strip()
    if not (user_id and pin and totp_key):
        print("Missing FYERS_USER_ID / FYERS_PIN / FYERS_TOTP_KEY — cannot probe.")
        return 2
    if not (settings.fyers_app_id and settings.fyers_secret_key):
        print("Missing FYERS_APP_ID / FYERS_SECRET_KEY — cannot probe.")
        return 2

    try:
        remaining = seconds_remaining()
        if remaining < 5:
            await asyncio.sleep(remaining + 0.5)
        otp = generate_totp(totp_key)
    except TotpSecretError as exc:
        print(f"TOTP secret unusable: {exc}")
        return 2

    print(f"session cutoff (IST): {settings.fyers_session_cutoff_ist}")
    print(f"next cutoff: {next_session_cutoff_ist().isoformat()}")
    print(f"totp step remaining at generate time: {seconds_remaining():.1f}s (code length {len(otp)})")

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            first = await _post_json(
                client,
                f"{VAGATOR_BASE_URL}/send_login_otp_v2",
                {"fy_id": _b64(user_id), "app_id": WEB_APP_ID},
                step=STEP_OTP,
            )
            print(_shape(STEP_OTP, first))
            request_key = first.get("request_key")
            if not request_key:
                return 1

            second = await _post_json(
                client,
                f"{VAGATOR_BASE_URL}/verify_otp",
                {"request_key": request_key, "otp": otp},
                step=STEP_VERIFY_OTP,
            )
            print(_shape(STEP_VERIFY_OTP, second))
            request_key = second.get("request_key")
            if not request_key:
                return 1

            third = await _post_json(
                client,
                f"{VAGATOR_BASE_URL}/verify_pin_v2",
                {
                    "request_key": request_key,
                    "identity_type": "pin",
                    "identifier": _b64(pin),
                },
                step=STEP_VERIFY_PIN,
            )
            print(_shape(STEP_VERIFY_PIN, third))
            identity_token = (third.get("data") or {}).get("access_token")
            if not identity_token:
                return 1

            fourth = await _post_json(
                client,
                TOKEN_REQUEST_URL,
                {
                    "fyers_id": user_id,
                    "app_id": settings.fyers_app_id[:-4],
                    "redirect_uri": settings.fyers_redirect_uri,
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
            print(_shape(STEP_TOKEN, fourth))
            print(f"auth_code extracted: {bool(extract_auth_code(fourth.get('Url') or fourth.get('url')))}")
        except Exception as exc:  # noqa: BLE001 - probe reports, never raises
            print(f"probe failed: {type(exc).__name__}: {str(exc)[:200]}")
            return 1
    print("dry run complete — no token was stored")
    return 0


async def _verify() -> int:
    from app.services.fyers_totp import attempt_headless_login

    # The probe must work even with the opt-in flag still off.
    settings.auth_headless_login_enabled = True
    outcome = await attempt_headless_login()
    if not outcome.ok or not outcome.access_token:
        print(f"headless login failed: reason={outcome.reason} step={outcome.step} detail={outcome.detail}")
        return 1

    verification = await verify_fyers_session(outcome.access_token)
    print(
        {
            "login": "ok",
            "expires_in": outcome.expires_in,
            "verify_ok": verification.ok,
            "identity": verification.identity,
            "expected_identity": settings.resolved_fyers_user_id,
            "verify_error": verification.error,
        }
    )
    if not verification.ok:
        return 1
    if (
        settings.resolved_fyers_user_id
        and verification.identity
        and verification.identity != settings.resolved_fyers_user_id
    ):
        print("identity mismatch — check FYERS_USER_ID")
        return 1
    print("verified — token was NOT persisted by this probe")
    return 0


async def _telegram_test() -> int:
    from app.services import telegram_service

    if not settings.telegram_notifications_enabled:
        print("TELEGRAM_NOTIFICATIONS_ENABLED is false; nothing sent.")
        return 2
    if not settings.telegram_configured:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing.")
        return 2
    sent = await telegram_service.send_message(
        None,
        "🔔 <b>SwingTraderVCP</b> probe: Telegram notifications are working.",
    )
    print("sent" if sent else "send failed")
    return 0 if sent else 1


async def _main() -> int:
    args = _args()
    if args.otp_peek:
        try:
            code = generate_totp(settings.fyers_totp_key)
        except TotpSecretError as exc:
            print(f"TOTP secret unusable: {exc}")
            return 2
        print(f"code length={len(code)} step_remaining={seconds_remaining():.1f}s")
        return 0
    if args.telegram_test:
        return await _telegram_test()
    if args.dry_run:
        return await _dry_run()
    return await _verify()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
