from zoneinfo import ZoneInfo
from typing import Any

from arq import cron

from app.config import settings
from app.redis_pool import redis_settings_from_config, tune_arq_redis_pool
from app.services.fundamental_pass import run_fundamental_pass
from app.services.historical_fetcher import run_historical_sync
from app.services.journal_ai_coach import run_journal_ai_coach
from app.services.journal_processor import run_journal_dispatcher
from app.services.market_context import run_market_context
from app.services.reconciliation import run_reconciliation
from app.services.saas_scan import run_saas_global_standard_scan
from app.services.screener import run_technical_scan
from app.services.token_refresh import run_auth_guard
from app.services.vcp_vision import run_vcp_vision_analysis
from app.services.intraday_bar_reconciliation import reconcile_intraday_bars


async def worker_on_startup(ctx: dict[str, Any]) -> None:
    await tune_arq_redis_pool(ctx["redis"])


class WorkerSettings:
    # Set default job timeout to 30 minutes to allow batch fundamental passes
    job_timeout = 1800

    # Functions that the worker can execute
    functions = [
        run_technical_scan,
        run_fundamental_pass,
        run_historical_sync,
        run_market_context,
        run_auth_guard,
        run_reconciliation,
        run_journal_dispatcher,
        run_journal_ai_coach,
        run_saas_global_standard_scan,
        run_vcp_vision_analysis,
        reconcile_intraday_bars,
    ]

    # arq evaluates cron expressions in this explicit timezone.
    timezone = ZoneInfo(settings.scheduler_timezone)

    cron_jobs = []

    if settings.eod_sync_enabled:
        cron_jobs.append(
            cron(
                run_historical_sync,
                name="incremental_eod_sync",
                weekday={0, 1, 2, 3, 4},
                hour=settings.eod_sync_hour,
                minute=settings.eod_sync_minute,
                second=0,
                timeout=60 * 60,
                max_tries=1,
            )
        )

    if settings.saas_standard_scan_fallback_enabled:
        cron_jobs.append(
            cron(
                run_saas_global_standard_scan,
                name="saas_standard_scan_fallback",
                weekday={0, 1, 2, 3, 4},
                hour=settings.saas_standard_scan_fallback_hour,
                minute=settings.saas_standard_scan_fallback_minute,
                second=0,
                timeout=60 * 60,
                max_tries=1,
            )
        )

    if settings.token_refresh_enabled and settings.auth_guard_enabled:
        # Daily broker-auth guard. Fyers retires every access token at 06:30 IST
        # and SEBI's daily-2FA framework removed continuous refresh sessions, so
        # this runs on the configured morning slots: verify the session, retry
        # the headless TOTP login, and alert on Telegram when it is still
        # missing (escalating at the last pre-market slot).
        cron_jobs.append(
            cron(
                run_auth_guard,
                name="fyers_auth_guard",
                weekday={0, 1, 2, 3, 4},
                hour=settings.auth_guard_hours,
                minute=settings.auth_guard_minutes,
                second=0,
                timeout=180,
                max_tries=1,
            )
        )

    if settings.reconciliation_enabled:
        cron_jobs.append(
            cron(
                run_reconciliation,
                name="fyers_reconciliation",
                weekday={0, 1, 2, 3, 4},
                hour={9, 10, 11, 12, 13, 14, 15},
                minute={0, 15, 30, 45},
                second=0,
                timeout=120,
                max_tries=1,
            )
        )
        cron_jobs.append(
            cron(
                reconcile_intraday_bars,
                name="fyers_intraday_bar_reconciliation",
                weekday={0, 1, 2, 3, 4},
                hour={9, 10, 11, 12, 13, 14, 15},
                minute={0, 15, 30, 45},
                second=20,
                timeout=180,
                max_tries=1,
            )
        )

    cron_jobs.append(
        cron(
            run_journal_dispatcher,
            name="journal_fill_dispatcher",
            minute=set(range(60)),
            second={0, 30},
            timeout=120,
            max_tries=1,
        )
    )

    job_timeout = 60 * 60
    max_jobs = 1
    on_startup = worker_on_startup

    redis_settings = redis_settings_from_config()
