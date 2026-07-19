import urllib.parse
from zoneinfo import ZoneInfo

from arq import cron
from arq.connections import RedisSettings

from app.config import settings
from app.services.historical_fetcher import run_historical_sync
from app.services.screener import run_technical_scan

# Parse Redis URL dynamically from app settings
url = urllib.parse.urlparse(settings.redis_url)
redis_host = url.hostname or '127.0.0.1'
redis_port = url.port or 6379
redis_db = int(url.path.lstrip('/')) if url.path else 0

class WorkerSettings:
    # Functions that the worker can execute
    functions = [run_technical_scan, run_historical_sync]

    # arq evaluates cron expressions in this explicit timezone.
    timezone = ZoneInfo(settings.scheduler_timezone)
    cron_jobs = (
        [
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
        ]
        if settings.eod_sync_enabled
        else []
    )
    job_timeout = 60 * 60
    
    # Redis configuration matching our Docker setup (port 6380)
    redis_settings = RedisSettings(
        host=redis_host,
        port=redis_port,
        database=redis_db
    )
