import asyncio
import datetime as dt
import os
import unittest
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import data_validator as validator
from app.services import fyers_history as h
from app.services import historical_fetcher as eod


class WrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_redis_failure_never_calls_broker(self):
        broker = SimpleNamespace(history=AsyncMock())
        with self.assertRaises(h.HistoryUnavailable):
            await h.fetch_history(
                SimpleNamespace(eval=AsyncMock(side_effect=ConnectionError())),
                broker,
                {},
            )
        broker.history.assert_not_awaited()

    async def test_retry_same_payload_then_stop(self):
        redis = SimpleNamespace(
            eval=AsyncMock(side_effect=[[0, 0], 1, [2, 1], [0, 0], 1, [3, 86400000]])
        )
        broker = SimpleNamespace(
            history=AsyncMock(return_value={"code": -360, "s": "error"})
        )
        payload = {"symbol": "NSE:TEST-EQ", "range_from": "2025-01-01"}
        wait = AsyncMock()
        with (
            patch.object(h.asyncio, "sleep", new=AsyncMock()),
            self.assertRaises(h.HistoryUnavailable),
        ):
            await h.fetch_history(redis, broker, payload, on_wait=wait)
        self.assertEqual(broker.history.await_count, 2)
        self.assertTrue(
            all(c.kwargs["data"] == payload for c in broker.history.await_args_list)
        )
        self.assertEqual(wait.await_args_list[0].args[0], "rate_limit")
        self.assertEqual(redis.eval.await_count, 6)

    async def test_cancel_in_cooldown(self):
        broker = SimpleNamespace(history=AsyncMock())
        with (
            patch.object(h.asyncio, "sleep", new=AsyncMock()),
            self.assertRaises(h.HistoryCancelled),
        ):
            await h.fetch_history(
                SimpleNamespace(eval=AsyncMock(return_value=[2, 1])),
                broker,
                {},
                heartbeat=AsyncMock(side_effect=[None, h.HistoryCancelled()]),
            )
        broker.history.assert_not_awaited()

    def test_codes(self):
        for code in [429, -429, -360, "-360"]:
            self.assertEqual(h.rate_restriction({"code": code}), (True, False))
        self.assertEqual(
            h.rate_restriction({"code": -360, "message": "Daily limit exceeded"}),
            (True, True),
        )
        self.assertEqual(h.rate_restriction({"s": "no_data"}), (False, False))


@unittest.skipUnless(os.getenv("HISTORY_TEST_REDIS_URL"), "Requires disposable Redis")
class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from redis.asyncio import Redis

        self.redis = Redis.from_url(os.environ["HISTORY_TEST_REDIS_URL"])
        self.key = "history-test:" + uuid.uuid4().hex

    async def asyncTearDown(self):
        await self.redis.delete(self.key, self.key + ":requests")
        await self.redis.aclose()

    async def admit(self, daily=5000):
        return await self.redis.eval(
            h.ADMIT, 2, self.key, self.key + ":requests", 1334, daily, uuid.uuid4().hex
        )

    async def test_concurrency(self):
        results = await asyncio.gather(*(self.admit() for _ in range(100)))
        self.assertEqual(sum(r[0] == 0 for r in results), 1)
        self.assertEqual(await self.redis.zcard(self.key + ":requests"), 1)
        self.assertTrue(all(r[0] in {0, 1} for r in results))

    async def test_quota_and_expiry(self):
        first = await self.admit(1)
        self.assertEqual(await self.admit(1), [4, first[1] + 86400000])
        member = (await self.redis.zrange(self.key + ":requests", 0, -1))[0]
        await self.redis.zadd(self.key + ":requests", {member: first[1] - 86400001})
        await self.redis.hset(self.key, "next", 0)
        self.assertEqual((await self.admit(1))[0], 0)

    async def test_one_recovery_probe_then_block(self):
        await self.redis.eval(h.RESULT, 1, self.key, "original", 1, 0)
        self.assertEqual((await self.admit())[0], 2)
        await self.redis.hset(self.key, "cooldown", 1)
        self.assertEqual((await self.admit())[0], 0)
        self.assertEqual((await self.admit())[0], 2)
        probe = await self.redis.hget(self.key, "probe")
        await self.redis.eval(h.RESULT, 1, self.key, probe, 1, 0)
        self.assertEqual((await self.admit())[0], 3)

    async def test_abandoned_probe_blocks_once_and_block_expires(self):
        await self.redis.hset(self.key, mapping={"probe_until": 1, "cooldown": 1})
        self.assertEqual((await self.admit())[0], 3)
        await self.redis.hset(self.key, "blocked", 1)
        self.assertEqual((await self.admit())[0], 0)
        self.assertIsNone(await self.redis.hget(self.key, "cooldown"))

    async def test_success_and_explicit_block(self):
        await self.redis.hset(self.key, "cooldown", 1)
        await self.admit()
        probe = await self.redis.hget(self.key, "probe")
        await self.redis.eval(h.RESULT, 1, self.key, probe, 0, 0)
        self.assertIsNone(await self.redis.hget(self.key, "cooldown"))
        await self.redis.eval(h.RESULT, 1, self.key, "ordinary", 1, 1)
        self.assertEqual((await self.admit())[0], 3)


class EodTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_prefix_committed_and_scans_blocked(self):
        target = dt.date(2026, 10, 21)
        inst = SimpleNamespace(
            id=1,
            symbol="TEST",
            fyers_symbol="NSE:TEST-EQ",
            earliest_candle_date=target - dt.timedelta(days=300),
            latest_candle_date=target,
        )
        rows = MagicMock()
        rows.all.return_value = [inst]
        db = AsyncMock()
        db.execute.return_value = rows

        @asynccontextmanager
        async def session():
            yield db

        redis = AsyncMock()
        redis.get.return_value = None
        broker = SimpleNamespace(close=AsyncMock())
        fetch = AsyncMock(
            side_effect=[
                {"s": "ok", "candles": [[1700000000, 100, 101, 99, 100, 1000]]},
                h.HistoryUnavailable("Daily quota exhausted"),
            ]
        )
        with (
            patch.object(eod, "async_session", session),
            patch.object(eod, "latest_completed_eod_date", return_value=target),
            patch.object(
                eod, "get_valid_access_token", new=AsyncMock(return_value="token")
            ),
            patch.object(
                eod, "acquire_distributed_lease", new=AsyncMock(return_value=True)
            ),
            patch.object(eod, "release_distributed_lease", new=AsyncMock()),
            patch.object(eod, "fetch_history", fetch),
            patch.object(eod.fyersModel, "FyersModel", return_value=broker),
            patch(
                "app.services.instrument_importer.ensure_nifty500_universe_imported",
                new=AsyncMock(),
            ),
            patch.object(eod, "enqueue_eod_scans", new=AsyncMock()) as enqueue,
        ):
            result = await eod.run_historical_sync(
                {"redis": redis}, backfill_years=5, repair_history=True
            )
        self.assertEqual(result["status"], "failed")
        db.commit.assert_awaited_once()
        enqueue.assert_not_awaited()
        broker.close.assert_awaited_once()
        import json

        self.assertEqual(
            json.loads(redis.set.await_args_list[-1].args[1])["candles_upserted"], 1
        )
        first, second = [c.args[2] for c in fetch.await_args_list]
        self.assertGreater(first["range_from"], second["range_from"])
        remaining = eod.sync_date_ranges(
            earliest_candle_date=dt.date.fromisoformat(first["range_from"]),
            latest_candle_date=target,
            target_date=target,
            backfill_years=5,
            repair_history=True,
        )
        self.assertEqual(remaining[0][1].isoformat(), second["range_to"])

    def test_timeout(self):
        from app.worker import WorkerSettings

        job = next(
            f
            for f in WorkerSettings.functions
            if getattr(f, "name", None) == "run_historical_sync"
        )
        self.assertEqual(job.timeout_s, 7200)
        self.assertEqual(
            next(
                c for c in WorkerSettings.cron_jobs if c.name == "incremental_eod_sync"
            ).timeout_s,
            7200,
        )


class ValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_restriction_is_unknown_and_redis_lives_through_probe(self):
        start = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=730)
        inst = [
            SimpleNamespace(
                id=i, symbol=f"TEST{i}", fyers_symbol=f"NSE:TEST{i}-EQ", name="Test"
            )
            for i in range(6)
        ]
        candles = [
            SimpleNamespace(
                instrument_id=i,
                candle_start=dt.datetime.combine(
                    start + dt.timedelta(days=d + (20 if i == 5 else 0)),
                    dt.time(),
                    dt.timezone.utc,
                ),
                open_price=100,
                close_price=100,
                volume=1000,
            )
            for i in range(6)
            for d in range(10)
        ]
        a, b = MagicMock(), MagicMock()
        a.all.return_value = inst
        b.all.return_value = candles
        db = AsyncMock()
        db.execute.side_effect = [a, b]

        @asynccontextmanager
        async def session():
            yield db

        redis = AsyncMock()
        broker = SimpleNamespace(close=AsyncMock())

        async def fetch(*args, **kwargs):
            redis.aclose.assert_not_awaited()
            raise h.HistoryUnavailable("Daily limit exhausted")

        with (
            patch.object(validator, "async_session", session),
            patch.object(
                validator, "create_async_redis", new=AsyncMock(return_value=redis)
            ),
            patch.object(
                validator, "get_valid_access_token", new=AsyncMock(return_value="token")
            ),
            patch.object(validator, "fetch_history", side_effect=fetch),
            patch.object(validator.fyersModel, "FyersModel", return_value=broker),
        ):
            await validator.run_data_validation()
        report = validator.validation_progress.report
        self.assertEqual(report["unknown_listing_probes"], ["TEST5"])
        gap = next(
            r
            for r in report["completeness_check"]["symbols_with_gaps"]
            if r["symbol"] == "TEST5"
        )
        self.assertFalse(gap["is_recent_listing"])
        redis.aclose.assert_awaited_once()
        broker.close.assert_awaited_once()
