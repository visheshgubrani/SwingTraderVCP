"""Shared Redis pool creation with redis-py 8 socket timeouts and keepalive."""

from __future__ import annotations

from dataclasses import replace

import redis.asyncio as aioredis
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import settings


def redis_settings_from_config() -> RedisSettings:
    """Build arq Redis settings from REDIS_URL, including TLS and auth.

    Managed Redis providers and Compose both encode credentials in the DSN.
    ``RedisSettings.from_dsn`` preserves host, TLS, username, and password.
    """
    parsed = RedisSettings.from_dsn(settings.redis_url)
    return replace(
        parsed,
        conn_timeout=int(settings.redis_connect_timeout_seconds),
    )


def _async_redis_kwargs(*, decode_responses: bool) -> dict[str, object]:
    return {
        "decode_responses": decode_responses,
        "socket_timeout": settings.redis_socket_timeout,
        "socket_connect_timeout": settings.redis_connect_timeout_seconds,
        "socket_keepalive": True,
        "health_check_interval": settings.redis_health_check_interval,
        "retry_on_timeout": True,
        "max_connections": settings.redis_max_connections,
    }


async def tune_arq_redis_pool(pool: ArqRedis) -> None:
    """Apply socket read timeout and health checks to an existing arq pool."""
    connection_pool = pool.connection_pool
    kwargs = dict(connection_pool.connection_kwargs)
    kwargs["socket_timeout"] = settings.redis_socket_timeout
    kwargs["health_check_interval"] = settings.redis_health_check_interval
    kwargs["socket_keepalive"] = True
    kwargs["retry_on_timeout"] = True
    connection_pool.connection_kwargs = kwargs
    await connection_pool.disconnect()
    await pool.ping()


async def create_arq_pool() -> ArqRedis:
    pool = await create_pool(redis_settings_from_config())
    await tune_arq_redis_pool(pool)
    return pool


async def create_async_redis(*, decode_responses: bool = True) -> aioredis.Redis:
    """Long-lived redis.asyncio client for workers and the API pub/sub path."""
    client = aioredis.from_url(
        settings.redis_url,
        **_async_redis_kwargs(decode_responses=decode_responses),
    )
    await client.ping()
    return client
