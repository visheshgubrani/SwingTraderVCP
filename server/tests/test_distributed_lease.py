import unittest
from unittest.mock import AsyncMock

from app.services.distributed_lease import (
    RELEASE_LEASE_SCRIPT,
    RENEW_LEASE_SCRIPT,
    acquire_distributed_lease,
    release_distributed_lease,
    renew_distributed_lease,
)


class DistributedLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_acquire_lease_success(self) -> None:
        redis = AsyncMock()
        redis.set.return_value = True

        acquired = await acquire_distributed_lease(
            redis,
            key="test:singleton",
            owner_id="worker-1",
            ttl_seconds=30,
        )

        self.assertTrue(acquired)
        redis.set.assert_awaited_once_with(
            "test:singleton",
            "worker-1",
            nx=True,
            ex=30,
        )

    async def test_acquire_lease_collision_returns_false(self) -> None:
        redis = AsyncMock()
        redis.set.return_value = None

        acquired = await acquire_distributed_lease(
            redis,
            key="test:singleton",
            owner_id="worker-2",
            ttl_seconds=30,
        )

        self.assertFalse(acquired)

    async def test_acquire_lease_exception_returns_false(self) -> None:
        redis = AsyncMock()
        redis.set.side_effect = RuntimeError("Redis connection error")

        acquired = await acquire_distributed_lease(
            redis,
            key="test:singleton",
            owner_id="worker-1",
        )

        self.assertFalse(acquired)

    async def test_renew_lease_success(self) -> None:
        redis = AsyncMock()
        redis.eval.return_value = 1

        renewed = await renew_distributed_lease(
            redis,
            key="test:singleton",
            owner_id="worker-1",
            ttl_seconds=30,
        )

        self.assertTrue(renewed)
        redis.eval.assert_awaited_once_with(
            RENEW_LEASE_SCRIPT,
            1,
            "test:singleton",
            "worker-1",
            30,
        )

    async def test_renew_lease_failure_returns_false(self) -> None:
        redis = AsyncMock()
        redis.eval.return_value = 0

        renewed = await renew_distributed_lease(
            redis,
            key="test:singleton",
            owner_id="worker-1",
            ttl_seconds=30,
        )

        self.assertFalse(renewed)

    async def test_renew_lease_exception_returns_false(self) -> None:
        redis = AsyncMock()
        redis.eval.side_effect = RuntimeError("Redis eval error")

        renewed = await renew_distributed_lease(
            redis,
            key="test:singleton",
            owner_id="worker-1",
        )

        self.assertFalse(renewed)

    async def test_release_lease_success(self) -> None:
        redis = AsyncMock()
        redis.eval.return_value = 1

        released = await release_distributed_lease(
            redis,
            key="test:singleton",
            owner_id="worker-1",
        )

        self.assertTrue(released)
        redis.eval.assert_awaited_once_with(
            RELEASE_LEASE_SCRIPT,
            1,
            "test:singleton",
            "worker-1",
        )

    async def test_release_lease_not_owned_returns_false(self) -> None:
        redis = AsyncMock()
        redis.eval.return_value = 0

        released = await release_distributed_lease(
            redis,
            key="test:singleton",
            owner_id="worker-2",
        )

        self.assertFalse(released)

    async def test_release_lease_exception_returns_false(self) -> None:
        redis = AsyncMock()
        redis.eval.side_effect = RuntimeError("Redis eval error")

        released = await release_distributed_lease(
            redis,
            key="test:singleton",
            owner_id="worker-1",
        )

        self.assertFalse(released)


if __name__ == "__main__":
    unittest.main()
