"""Daily Fyers auth guard (historically ``token_refresh``).

Fyers retires every API access token at 06:30 IST daily and SEBI's April-2026
retail-algo framework removed continuous refresh-token sessions, so unattended
refresh is no longer possible. This job is the single scheduled path that keeps
a live session:

1. verify the stored session with one live broker call,
2. re-authenticate headlessly through the account's own TOTP secret when that
   opt-in path is enabled,
3. otherwise alert the owner on Telegram with a one-tap official OAuth login
   link, escalating to CRITICAL at the last pre-market slot.

It runs on the IST slots in ``AUTH_GUARD_HOURS``/``AUTH_GUARD_MINUTES`` and is
idempotent per slot: per-day state lives in Redis, durable evidence in
``job_runs``/``system_events``. It never places orders and never blocks on a
notification failure.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from arq.connections import ArqRedis
from sqlalchemy import text

from app.config import settings
from app.database import async_session
from app.services import telegram_service
from app.services.auth_readiness import (
    IST_TZ,
    as_ist,
    clamp_token_expiry,
    evaluate_auth_readiness,
    is_nse_session,
    next_session_cutoff_ist,
    verify_fyers_session,
)
from app.services.auth_service import (
    _emit_system_event,
    persist_and_cache_fyers_token,
)
from app.services.fyers_totp import (
    REASON_NOT_CONFIGURED,
    attempt_headless_login,
)
from app.services.session_service import create_direct_login_nonce

logger = logging.getLogger(__name__)

GUARD_STATE_KEY_PREFIX = "auth:guard:state:"
GUARD_STATE_TTL_SECONDS = 36 * 60 * 60
MARKET_OPEN_IST = dt.time(9, 15)
JOB_TYPE = "auth_guard"


# --- Per-day guard state ---------------------------------------------------


def guard_state_key(session_date: dt.date) -> str:
    return f"{GUARD_STATE_KEY_PREFIX}{session_date.isoformat()}"


async def load_guard_state(redis, session_date: dt.date) -> dict[str, Any]:
    raw = await redis.get(guard_state_key(session_date))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


async def save_guard_state(redis, session_date: dt.date, state: dict[str, Any]) -> None:
    await redis.set(
        guard_state_key(session_date),
        json.dumps(state),
        ex=GUARD_STATE_TTL_SECONDS,
    )


async def mark_session_authenticated(redis, *, method: str) -> None:
    """Record that today's session is live so the guard does not re-notify.

    Called on every successful login path (headless TOTP, one-tap link, or the
    dashboard) so the morning digest and success ping are sent exactly once.
    """
    now_ist = dt.datetime.now(dt.timezone.utc).astimezone(IST_TZ)
    state = await load_guard_state(redis, now_ist.date())
    state["success_notified"] = True
    state["last_method"] = method
    state["authenticated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    await save_guard_state(redis, now_ist.date(), state)


# --- Job entrypoint --------------------------------------------------------


async def run_auth_guard(ctx: dict[str, Any]) -> dict[str, Any]:
    """arq job: keep the daily Fyers session alive and alert when it is not."""
    redis: ArqRedis = ctx["redis"]
    job_id = str(ctx.get("job_id", "scheduled"))
    triggered_by = str(ctx.get("triggered_by", "scheduler"))
    now = dt.datetime.now(dt.timezone.utc)

    async with async_session() as db:
        run_id = await _start_job_run(db, job_id, triggered_by)
        try:
            result = await _guard_tick(db, redis, now=now)
            await _finish_job_run(db, run_id, status="succeeded", result=result)
            logger.info("Auth guard tick: %s", result)
            return result
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Auth guard crashed")
            await _emit_system_event(
                db,
                "critical",
                "auth_guard_crashed",
                {"error": str(exc)[:300]},
                redis=redis,
            )
            await db.commit()
            await _finish_job_run(
                db,
                run_id,
                status="failed",
                result={"status": "crashed", "error": str(exc)[:300]},
            )
            return {"status": "crashed", "error": str(exc)[:300]}


async def _guard_tick(db, redis, *, now: dt.datetime) -> dict[str, Any]:
    now_ist = as_ist(now)
    session_date = now_ist.date()
    trading_day = is_nse_session(session_date)
    state = await load_guard_state(redis, session_date)

    readiness = await evaluate_auth_readiness(db, redis, verify=True, now=now)
    if readiness["healthy"]:
        return await _handle_healthy(
            db,
            redis,
            state=state,
            session_date=session_date,
            trading_day=trading_day,
            now_ist=now_ist,
            readiness=readiness,
        )

    failure_reason = str(readiness.get("reason") or "unavailable")
    headless_failure: dict[str, Any] | None = None

    if settings.auth_headless_login_enabled:
        if not settings.headless_login_configured:
            headless_failure = {"reason": REASON_NOT_CONFIGURED}
            await _emit_system_event(
                db,
                "critical",
                "auth_headless_login_failed",
                {"reason": REASON_NOT_CONFIGURED, "missing": "credentials"},
                redis=redis,
            )
            await db.commit()
        elif int(state.get("attempts", 0)) >= settings.auth_guard_max_headless_attempts:
            headless_failure = {"reason": "attempt_limit_reached"}
        else:
            state["attempts"] = int(state.get("attempts", 0)) + 1
            await save_guard_state(redis, session_date, state)
            outcome = await attempt_scheduled_headless_login(db, redis, now=now)
            if outcome["ok"]:
                state["success_notified"] = True
                state["last_method"] = "headless_totp"
                await save_guard_state(redis, session_date, state)
                return {
                    "status": "refreshed",
                    "method": "headless_totp",
                    "session_date": session_date.isoformat(),
                    "expires_at": outcome.get("expires_at"),
                }
            headless_failure = {
                "reason": outcome.get("reason"),
                "step": outcome.get("step"),
            }
            failure_reason = str(outcome.get("reason") or failure_reason)

    notified = await _raise_alert(
        db,
        redis,
        state=state,
        session_date=session_date,
        trading_day=trading_day,
        now_ist=now_ist,
        now=now,
        reason=failure_reason,
        headless_failure=headless_failure,
    )
    await save_guard_state(redis, session_date, state)

    return {
        "status": "unauthenticated",
        "reason": failure_reason,
        "headless": headless_failure,
        "notified": notified,
        "session_date": session_date.isoformat(),
    }


async def _handle_healthy(
    db,
    redis,
    *,
    state: dict[str, Any],
    session_date: dt.date,
    trading_day: bool,
    now_ist: dt.datetime,
    readiness: dict[str, Any],
) -> dict[str, Any]:
    if not state.get("verified_notified"):
        await _emit_system_event(
            db,
            "info",
            "auth_session_verified",
            {
                "session_date": session_date.isoformat(),
                "expires_at": readiness.get("effective_expires_at"),
                "method": state.get("last_method") or "existing_session",
            },
            redis=redis,
        )
        await db.commit()
        state["verified_notified"] = True

    if settings.auth_notify_success and not state.get("success_notified"):
        sent = await telegram_service.send_auth_success(
            db,
            method=str(state.get("last_method") or "existing_session"),
            session_date=session_date.isoformat(),
            expires_at_ist=_expires_at_ist_label(readiness),
        )
        if sent:
            state["success_notified"] = True
        await db.commit()

    if trading_day and _is_last_slot(now_ist) and not state.get("readiness_sent"):
        sent = await telegram_service.send_premarket_readiness(
            db,
            {
                "session_date": session_date.isoformat(),
                "expires_at_ist": _expires_at_ist_label(readiness),
                "headless_login_enabled": settings.auth_headless_login_enabled,
            },
        )
        if sent:
            state["readiness_sent"] = True
        await db.commit()

    await save_guard_state(redis, session_date, state)
    return {
        "status": "healthy",
        "session_date": session_date.isoformat(),
        "identity": readiness.get("identity"),
    }


async def attempt_scheduled_headless_login(
    db, redis, *, now: dt.datetime | None = None
) -> dict[str, Any]:
    """Headless login used by the guard and the manual dashboard trigger."""
    now_utc = now or dt.datetime.now(dt.timezone.utc)
    outcome = await _attempt_headless_login(db, redis, now=now_utc)
    if outcome["ok"]:
        await mark_session_authenticated(redis, method="headless_totp")
    return outcome


async def _attempt_headless_login(db, redis, *, now: dt.datetime) -> dict[str, Any]:
    """Run the headless TOTP chain, verify the account, and persist the token."""
    outcome = await attempt_headless_login(now=now)
    if not outcome.ok or not outcome.access_token:
        logger.warning(
            "Headless Fyers login failed: reason=%s step=%s",
            outcome.reason,
            outcome.step,
        )
        await _emit_system_event(
            db,
            "warning",
            "auth_headless_login_failed",
            {"reason": outcome.reason, "step": outcome.step, "detail": outcome.detail},
            redis=redis,
        )
        await db.commit()
        return {"ok": False, "reason": outcome.reason, "step": outcome.step}

    verification = await verify_fyers_session(outcome.access_token, now=now)
    expected_identity = settings.resolved_fyers_user_id
    if not verification.ok:
        await _emit_system_event(
            db,
            "warning",
            "auth_headless_login_failed",
            {"reason": "verification_failed", "error": verification.error},
            redis=redis,
        )
        await db.commit()
        return {
            "ok": False,
            "reason": "verification_failed",
            "step": "verify",
        }

    if (
        expected_identity
        and verification.identity
        and verification.identity != expected_identity
    ):
        await _emit_system_event(
            db,
            "critical",
            "auth_login_owner_mismatch",
            {"method": "headless_totp", "expected": expected_identity},
            redis=redis,
            cooldown_seconds=0,
        )
        await db.commit()
        return {"ok": False, "reason": "owner_mismatch", "step": "verify"}

    expires_in = int(outcome.expires_in or 86400)
    expires_at = clamp_token_expiry(now, expires_in)
    await persist_and_cache_fyers_token(
        db,
        redis,
        access_token=outcome.access_token,
        refresh_token=None,
        expires_at=expires_at,
        expires_in=expires_in,
    )
    await _emit_system_event(
        db,
        "info",
        "auth_login_succeeded",
        {"method": "headless_totp", "expires_at": expires_at.isoformat()},
        redis=redis,
        cooldown_seconds=0,
    )
    await db.commit()

    await telegram_service.send_auth_success(
        db,
        method="headless_totp",
        session_date=as_ist(now).date().isoformat(),
        expires_at_ist=_iso_to_ist_label(expires_at),
    )
    await db.commit()
    logger.info("Headless Fyers login succeeded; session ends %s", expires_at)
    return {"ok": True, "expires_at": expires_at.isoformat()}


async def _raise_alert(
    db,
    redis,
    *,
    state: dict[str, Any],
    session_date: dt.date,
    trading_day: bool,
    now_ist: dt.datetime,
    now: dt.datetime,
    reason: str,
    headless_failure: dict[str, Any] | None,
) -> bool:
    """Notify the owner once per cooldown, escalating at the pre-market slot."""
    critical = trading_day and _is_last_slot(now_ist)
    cooldown = dt.timedelta(minutes=settings.auth_notify_cooldown_minutes)
    last_notify_raw = state.get("last_notify_ts")
    within_cooldown = False
    if last_notify_raw:
        try:
            last_notify = dt.datetime.fromisoformat(str(last_notify_raw))
            if last_notify.tzinfo is None:
                last_notify = last_notify.replace(tzinfo=dt.timezone.utc)
            within_cooldown = (now - last_notify) < cooldown
        except ValueError:
            within_cooldown = False

    if critical and not state.get("critical_sent"):
        await _emit_system_event(
            db,
            "critical",
            "auth_premarket_critical",
            {
                "session_date": session_date.isoformat(),
                "reason": reason,
                "headless": headless_failure,
            },
            redis=redis,
        )
        await db.commit()
        state["critical_sent"] = True
        within_cooldown = False

    if within_cooldown:
        return False

    login_url = await build_magic_login_url(redis)
    sent = await telegram_service.send_auth_expired_alert(
        db,
        login_url=login_url,
        session_date=session_date.isoformat(),
        reason=reason,
        severity="critical" if critical else "warning",
        minutes_to_open=_minutes_to_open(now_ist) if trading_day else None,
        trading_day=trading_day,
    )
    await db.commit()

    if not settings.telegram_notifications_enabled:
        await _emit_system_event(
            db,
            "warning",
            "auth_notification_skipped",
            {"reason": "telegram_disabled", "auth_reason": reason},
            redis=redis,
        )
        await db.commit()

    state["last_notify_ts"] = now.isoformat()
    state["last_notify_sent"] = sent
    state["last_reason"] = reason
    return sent


async def build_magic_login_url(redis) -> str | None:
    """Single-use one-tap login link embedded in the Telegram alert button."""
    base = (settings.api_public_base_url or "").strip().rstrip("/")
    if not base:
        logger.warning(
            "API_PUBLIC_BASE_URL is not configured; Telegram alert will have no login button"
        )
        return None
    nonce = await create_direct_login_nonce(redis)
    return f"{base}/api/v1/auth/direct-login?t={nonce}"


# --- Small helpers ---------------------------------------------------------


def _is_last_slot(now_ist: dt.datetime) -> bool:
    last_hour = max(settings.auth_guard_hours)
    last_minute = max(settings.auth_guard_minutes)
    return now_ist.time() >= dt.time(last_hour, last_minute)


def _minutes_to_open(now_ist: dt.datetime) -> int:
    open_at = dt.datetime.combine(now_ist.date(), MARKET_OPEN_IST, tzinfo=IST_TZ)
    return max(int((open_at - now_ist).total_seconds() // 60), 0)


def _iso_to_ist_label(moment: dt.datetime | None) -> str | None:
    if moment is None:
        return None
    return as_ist(moment).strftime("%d %b %H:%M")


def _expires_at_ist_label(readiness: dict[str, Any]) -> str | None:
    raw = readiness.get("effective_expires_at") or readiness.get("expires_at")
    if not raw:
        return _iso_to_ist_label(next_session_cutoff_ist())
    try:
        return _iso_to_ist_label(dt.datetime.fromisoformat(str(raw)))
    except ValueError:
        return None


# --- job_runs bookkeeping --------------------------------------------------


async def _start_job_run(db, job_id: str, triggered_by: str):
    result = await db.execute(
        text("""
            INSERT INTO job_runs (
                job_type,
                job_key,
                triggered_by,
                status,
                started_at,
                input_payload
            )
            VALUES (
                :job_type,
                :job_key,
                :triggered_by,
                'running',
                now(),
                :input_payload
            )
            RETURNING id
        """),
        {
            "job_type": JOB_TYPE,
            "job_key": f"{JOB_TYPE}_{job_id}",
            "triggered_by": triggered_by,
            "input_payload": json.dumps({"job_id": job_id, "triggered_by": triggered_by}),
        },
    )
    run_id = result.scalar()
    await db.commit()
    return run_id


async def _finish_job_run(db, run_id, *, status: str, result: dict[str, Any]) -> None:
    await db.execute(
        text("""
            UPDATE job_runs
            SET status = :status,
                completed_at = now(),
                error_message = :error_message,
                result_payload = :result_payload
            WHERE id = :run_id
        """),
        {
            "run_id": run_id,
            "status": status,
            "error_message": None if status == "succeeded" else json.dumps(result)[:500],
            "result_payload": json.dumps(result),
        },
    )
    await db.commit()


# Backwards-compatible alias: the previous scheduled entrypoint name.
run_token_refresh = run_auth_guard
