"""Shared arq Redis pool creation with redis-py 8 socket timeouts."""

from __future__ import annotations

import urllib.parse

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import settings


def redis_settings_from_config() -> RedisSettings:
    url = urllib.parse.urlparse(settings.redis_url)
    return RedisSettings(
        host=url.hostname or "127.0.0.1",
        port=url.port or 6379,
        database=int(url.path.lstrip("/")) if url.path else 0,
        conn_timeout=int(settings.redis_connect_timeout_seconds),
    )


async def tune_arq_redis_pool(pool: ArqRedis) -> None:
    """Apply socket read timeout and health checks to an existing arq pool."""
    connection_pool = pool.connection_pool
    kwargs = dict(connection_pool.connection_kwargs)
    kwargs["socket_timeout"] = settings.redis_socket_timeout
    kwargs["health_check_interval"] = settings.redis_health_check_interval
    connection_pool.connection_kwargs = kwargs
    await connection_pool.disconnect()
    await pool.ping()


async def create_arq_pool() -> ArqRedis:
    pool = await create_pool(redis_settings_from_config())
    await tune_arq_redis_pool(pool)
    return pool
