import datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import settings, build_redis_url
from app.redis_pool import (
    create_async_redis,
    redis_settings_from_config,
    tune_arq_redis_pool,
)
from app.services.historical_fetcher import sync_status_blocks_enqueue
from app.worker import WorkerSettings


class RedisPoolConfigTests(unittest.TestCase):
    def test_redis_settings_use_connect_timeout_from_config(self) -> None:
        redis_settings = redis_settings_from_config()
        self.assertEqual(redis_settings.conn_timeout, int(settings.redis_connect_timeout_seconds))

    def test_redis_settings_preserve_upstash_tls_and_password(self) -> None:
        original = settings.redis_url
        try:
            settings.redis_url = (
                "rediss://default:upstash-secret@example.upstash.io:6379/0"
            )
            redis_settings = redis_settings_from_config()
        finally:
            settings.redis_url = original

        self.assertEqual(redis_settings.host, "example.upstash.io")
        self.assertEqual(redis_settings.port, 6379)
        self.assertEqual(redis_settings.username, "default")
        self.assertEqual(redis_settings.password, "upstash-secret")
        self.assertTrue(redis_settings.ssl)
        self.assertEqual(
            redis_settings.conn_timeout,
            int(settings.redis_connect_timeout_seconds),
        )

    def test_worker_max_jobs_is_one(self) -> None:
        self.assertEqual(WorkerSettings.max_jobs, 1)

    def test_worker_has_on_startup_hook(self) -> None:
        self.assertIsNotNone(WorkerSettings.on_startup)


class TuneArqRedisPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_tune_applies_socket_timeout_and_health_check(self) -> None:
        pool = MagicMock()
        pool.connection_pool.connection_kwargs = {"host": "localhost", "port": 6380}
        pool.ping = AsyncMock()

        async def disconnect() -> None:
            return None

        pool.connection_pool.disconnect = AsyncMock(side_effect=disconnect)

        await tune_arq_redis_pool(pool)

        kwargs = pool.connection_pool.connection_kwargs
        self.assertEqual(kwargs["socket_timeout"], settings.redis_socket_timeout)
        self.assertEqual(
            kwargs["health_check_interval"],
            settings.redis_health_check_interval,
        )
        self.assertTrue(kwargs["socket_keepalive"])
        self.assertTrue(kwargs["retry_on_timeout"])
        pool.connection_pool.disconnect.assert_awaited_once()
        pool.ping.assert_awaited_once()


class SyncStatusBlocksEnqueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_running_without_lock_is_not_blocking(self) -> None:
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)

        blocks = await sync_status_blocks_enqueue(
            redis,
            {"state": "running"},
        )

        self.assertFalse(blocks)
        redis.get.assert_awaited_once()

    async def test_running_with_lock_blocks(self) -> None:
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=b"run-1")

        blocks = await sync_status_blocks_enqueue(
            redis,
            {"state": "running"},
        )

        self.assertTrue(blocks)

    async def test_queued_without_timestamp_does_not_block(self) -> None:
        redis = AsyncMock()

        blocks = await sync_status_blocks_enqueue(redis, {"state": "queued"})

        self.assertFalse(blocks)
        redis.get.assert_not_called()

    async def test_fresh_queued_blocks(self) -> None:
        redis = AsyncMock()
        enqueued_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        blocks = await sync_status_blocks_enqueue(
            redis,
            {"state": "queued", "enqueued_at": enqueued_at},
        )

        self.assertTrue(blocks)

    async def test_stale_queued_does_not_block(self) -> None:
        redis = AsyncMock()
        stale = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            seconds=settings.sync_queued_stale_seconds + 60
        )

        blocks = await sync_status_blocks_enqueue(
            redis,
            {"state": "queued", "enqueued_at": stale.isoformat()},
        )

        self.assertFalse(blocks)


class BuildRedisUrlTests(unittest.TestCase):
    def test_encodes_special_characters_in_password(self) -> None:
        url = build_redis_url(
            host="redis",
            port=6379,
            password="p@ss/word",
            db=0,
        )
        self.assertEqual(url, "redis://:p%40ss%2Fword@redis:6379/0")

    def test_omits_auth_when_password_missing(self) -> None:
        url = build_redis_url(host="localhost", port=6380)
        self.assertEqual(url, "redis://localhost:6380/0")


class CreateAsyncRedisTests(unittest.IsolatedAsyncioTestCase):
    async def test_applies_keepalive_timeouts_and_pings(self) -> None:
        mock_client = AsyncMock()
        with patch("app.redis_pool.aioredis.from_url", return_value=mock_client) as from_url:
            client = await create_async_redis()

        self.assertIs(client, mock_client)
        kwargs = from_url.call_args.kwargs
        self.assertTrue(kwargs["decode_responses"])
        self.assertTrue(kwargs["socket_keepalive"])
        self.assertTrue(kwargs["retry_on_timeout"])
        self.assertEqual(kwargs["socket_timeout"], settings.redis_socket_timeout)
        self.assertEqual(
            kwargs["socket_connect_timeout"],
            settings.redis_connect_timeout_seconds,
        )
        self.assertEqual(
            kwargs["health_check_interval"],
            settings.redis_health_check_interval,
        )
        self.assertEqual(kwargs["max_connections"], settings.redis_max_connections)
        mock_client.ping.assert_awaited_once()


class HistoricalSyncLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_skips_when_lease_is_held(self) -> None:
        from app.services.historical_fetcher import run_historical_sync

        redis = AsyncMock()
        with patch(
            "app.services.historical_fetcher.acquire_distributed_lease",
            new_callable=AsyncMock,
            return_value=False,
        ) as acquire:
            result = await run_historical_sync(
                {"redis": redis, "job_id": "job-1"},
                triggered_by="manual",
            )

        self.assertEqual(result["status"], "already_running")
        acquire.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
