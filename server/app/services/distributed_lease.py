"""Reusable, atomic Redis distributed singleton leases.

Provides owner-valued lease acquisition, compare-and-expire renewal,
and compare-and-delete release using atomic Redis operations and Lua scripts.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

RENEW_LEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  return 1
end
return 0
"""

RELEASE_LEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


async def acquire_distributed_lease(
    redis: Any,
    key: str,
    owner_id: str,
    ttl_seconds: int = 30,
) -> bool:
    """Attempt to atomically acquire a lease with an owner ID and TTL."""
    try:
        acquired = await redis.set(
            key,
            owner_id,
            nx=True,
            ex=ttl_seconds,
        )
        return bool(acquired)
    except Exception as exc:
        logger.error("Failed to acquire distributed lease for key '%s': %s", key, exc)
        return False


async def renew_distributed_lease(
    redis: Any,
    key: str,
    owner_id: str,
    ttl_seconds: int = 30,
) -> bool:
    """Atomically extend the lease TTL only if currently owned by owner_id."""
    try:
        result = await redis.eval(
            RENEW_LEASE_SCRIPT,
            1,
            key,
            owner_id,
            ttl_seconds,
        )
        return int(result or 0) == 1
    except Exception as exc:
        logger.error("Failed to renew distributed lease for key '%s': %s", key, exc)
        return False


async def release_distributed_lease(
    redis: Any,
    key: str,
    owner_id: str,
) -> bool:
    """Atomically release the lease only if currently owned by owner_id."""
    try:
        result = await redis.eval(
            RELEASE_LEASE_SCRIPT,
            1,
            key,
            owner_id,
        )
        return int(result or 0) == 1
    except Exception as exc:
        logger.error("Failed to release distributed lease for key '%s': %s", key, exc)
        return False
