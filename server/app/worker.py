import urllib.parse
from arq.connections import RedisSettings
from app.config import settings
from app.services.screener import run_technical_scan

# Parse Redis URL dynamically from app settings
url = urllib.parse.urlparse(settings.redis_url)
redis_host = url.hostname or '127.0.0.1'
redis_port = url.port or 6379
redis_db = int(url.path.lstrip('/')) if url.path else 0

class WorkerSettings:
    # Functions that the worker can execute
    functions = [run_technical_scan]
    
    # Redis configuration matching our Docker setup (port 6380)
    redis_settings = RedisSettings(
        host=redis_host,
        port=redis_port,
        database=redis_db
    )
