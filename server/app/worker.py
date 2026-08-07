import urllib.parse
from zoneinfo import ZoneInfo

from arq import cron
from arq.connections import RedisSettings

from app.config import settings
from app.services.fundamental_pass import run_fundamental_pass
from app.services.historical_fetcher import run_historical_sync
from app.services.journal_ai_coach import run_journal_ai_coach
from app.services.journal_processor import run_journal_dispatcher
from app.services.reconciliation import run_reconciliation
from app.services.screener import run_technical_scan
from app.services.token_refresh import run_token_refresh

# Parse Redis URL dynamically from app settings
url = urllib.parse.urlparse(settings.redis_url)
redis_host = url.hostname or '127.0.0.1'
redis_port = url.port or 6379
redis_db = int(url.path.lstrip('/')) if url.path else 0

class WorkerSettings:
    # Functions that the worker can execute
    functions = [
        run_technical_scan,
        run_fundamental_pass,
        run_historical_sync,
        run_token_refresh,
        run_reconciliation,
        run_journal_dispatcher,
        run_journal_ai_coach,
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

    if settings.token_refresh_enabled:
        cron_jobs.append(
            cron(
                run_token_refresh,
                name="fyers_token_refresh",
                weekday={0, 1, 2, 3, 4},
                hour=settings.token_refresh_hour,
                minute=settings.token_refresh_minute,
                second=0,
                timeout=60,
                max_tries=2,
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
            run_journal_dispatcher,
            name="journal_fill_dispatcher",
            minute=set(range(60)),
            second={0, 30},
            timeout=120,
            max_tries=1,
        )
    )

    job_timeout = 60 * 60

    # Redis configuration matching our Docker setup (port 6380)
    redis_settings = RedisSettings(
        host=redis_host,
        port=redis_port,
        database=redis_db
    )
