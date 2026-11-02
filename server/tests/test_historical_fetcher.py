import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from zoneinfo import ZoneInfo

from app.services import historical_fetcher
from app.services.historical_fetcher import (
    SYNC_LOCK_KEY,
    SyncProgress,
    build_date_chunks,
    enqueue_eod_scans,
    record_scan_chain_blocked,
    run_eod_chain_catch_up,
    scan_chain_blocked_reasons,
    history_response_has_no_data,
    latest_completed_eod_date,
    next_sync_date,
    sync_date_ranges,
    sync_status_is_current,
)
from app.services.scan_readiness import evaluate_scan_readiness
from app.worker import WorkerSettings

IST = ZoneInfo("Asia/Kolkata")
TUESDAY = datetime.date(2026, 11, 3)


class FakeRedis:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.enqueued: list[tuple[str, tuple, dict]] = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def enqueue_job(self, function, *args, **kwargs):
        self.enqueued.append((function, args, kwargs))
        return SimpleNamespace()


class FakeSession:
    def __init__(self, statements: list) -> None:
        self.statements = statements

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), params))

    async def commit(self) -> None:
        return None


class HistoricalFetcherDateTests(unittest.TestCase):
    def test_existing_symbol_starts_day_after_latest_candle(self) -> None:
        today = datetime.date(2026, 7, 18)

        result = next_sync_date(datetime.date(2026, 7, 14), today, 1)

        self.assertEqual(result, datetime.date(2026, 7, 15))

    def test_new_symbol_uses_configured_backfill_window(self) -> None:
        today = datetime.date(2026, 7, 18)

        result = next_sync_date(None, today, 1)

        self.assertEqual(result, datetime.date(2025, 7, 18))

    def test_chunk_range_is_inclusive_and_fyers_safe(self) -> None:
        start = datetime.date(2025, 7, 18)
        end = datetime.date(2026, 7, 18)

        chunks = build_date_chunks(start, end)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], (start, datetime.date(2026, 7, 17)))
        self.assertEqual(chunks[1], (end, end))

    def test_current_symbol_has_no_ranges_to_fetch(self) -> None:
        today = datetime.date(2026, 7, 18)

        chunks = build_date_chunks(today + datetime.timedelta(days=1), today)

        self.assertEqual(chunks, [])

    def test_empty_symbol_gets_full_backfill_range(self) -> None:
        target = datetime.date(2026, 8, 12)

        ranges = sync_date_ranges(
            earliest_candle_date=None,
            latest_candle_date=None,
            target_date=target,
            backfill_years=2,
            repair_history=True,
        )

        self.assertEqual(
            ranges,
            [(datetime.date(2024, 8, 12), target)],
        )

    def test_current_short_history_gets_missing_prefix_only(self) -> None:
        target = datetime.date(2026, 8, 12)

        ranges = sync_date_ranges(
            earliest_candle_date=datetime.date(2025, 8, 12),
            latest_candle_date=target,
            target_date=target,
            backfill_years=2,
            repair_history=True,
        )

        self.assertEqual(
            ranges,
            [(datetime.date(2024, 8, 12), datetime.date(2025, 8, 11))],
        )

    def test_stale_short_history_gets_prefix_and_suffix(self) -> None:
        target = datetime.date(2026, 8, 12)

        ranges = sync_date_ranges(
            earliest_candle_date=datetime.date(2025, 8, 12),
            latest_candle_date=datetime.date(2026, 8, 10),
            target_date=target,
            backfill_years=2,
            repair_history=True,
        )

        self.assertEqual(
            ranges,
            [
                (datetime.date(2024, 8, 12), datetime.date(2025, 8, 11)),
                (datetime.date(2026, 8, 11), target),
            ],
        )

    def test_sufficient_current_history_needs_no_repair(self) -> None:
        target = datetime.date(2026, 8, 12)

        ranges = sync_date_ranges(
            earliest_candle_date=datetime.date(2024, 8, 1),
            latest_candle_date=target,
            target_date=target,
            backfill_years=2,
            repair_history=True,
        )

        self.assertEqual(ranges, [])

    def test_recent_listing_is_safely_probed_for_earlier_history(self) -> None:
        target = datetime.date(2026, 8, 12)

        ranges = sync_date_ranges(
            earliest_candle_date=datetime.date(2026, 1, 15),
            latest_candle_date=target,
            target_date=target,
            backfill_years=2,
            repair_history=True,
        )

        self.assertEqual(
            ranges,
            [(datetime.date(2024, 8, 12), datetime.date(2026, 1, 14))],
        )

    def test_incremental_sync_does_not_refetch_current_prefix(self) -> None:
        target = datetime.date(2026, 8, 12)

        ranges = sync_date_ranges(
            earliest_candle_date=datetime.date(2025, 8, 12),
            latest_candle_date=target,
            target_date=target,
            backfill_years=2,
            repair_history=False,
        )

        self.assertEqual(ranges, [])

    def test_fyers_pre_listing_no_data_is_recognized(self) -> None:
        self.assertTrue(
            history_response_has_no_data(
                {"s": "no_data", "message": "No data found"}
            )
        )
        self.assertFalse(
            history_response_has_no_data(
                {"s": "error", "message": "Could not authenticate the user"}
            )
        )

    def test_saturday_targets_fridays_completed_candle(self) -> None:
        saturday = datetime.datetime(
            2026,
            7,
            18,
            20,
            0,
            tzinfo=ZoneInfo("Asia/Kolkata"),
        )

        result = latest_completed_eod_date(saturday)

        self.assertEqual(result, datetime.date(2026, 7, 17))

    def test_before_evening_cutoff_targets_previous_weekday(self) -> None:
        monday_afternoon = datetime.datetime(
            2026,
            7,
            20,
            15,
            0,
            tzinfo=ZoneInfo("Asia/Kolkata"),
        )

        result = latest_completed_eod_date(monday_afternoon)

        self.assertEqual(result, datetime.date(2026, 7, 17))

    def test_successful_sync_for_current_target_is_reusable(self) -> None:
        status = {
            "state": "succeeded",
            "target_date": "2026-07-31",
        }

        self.assertTrue(
            sync_status_is_current(status, datetime.date(2026, 7, 31))
        )

    def test_successful_sync_for_old_target_is_stale(self) -> None:
        status = {
            "state": "succeeded",
            "target_date": "2026-07-30",
        }

        self.assertFalse(
            sync_status_is_current(status, datetime.date(2026, 7, 31))
        )

    def test_nearly_complete_partial_sync_is_reusable(self) -> None:
        status = {
            "state": "partial",
            "target_date": "2026-07-31",
            "total_symbols": 500,
            "successful_symbols": 496,
            "error_count": 4,
        }

        self.assertTrue(
            sync_status_is_current(status, datetime.date(2026, 7, 31))
        )


class HistoricalScanChainTests(unittest.IsolatedAsyncioTestCase):
    async def test_p9_chain_is_queued_before_independent_saas_refresh(self) -> None:
        events: list[str] = []

        class Redis:
            async def enqueue_job(self, function, *_args, **_kwargs):
                events.append(function)
                return SimpleNamespace()

        progress = SyncProgress()
        await enqueue_eod_scans(
            Redis(),
            progress,
            datetime.date(2026, 8, 12),
        )

        self.assertEqual(
            events,
            ["run_market_context", "run_saas_global_standard_scan"],
        )
        self.assertIsNone(progress.personal_scan_run_id)


class EodChainCatchUpTests(unittest.IsolatedAsyncioTestCase):
    async def run_catch_up(self, redis, *, at, exists=False, holidays=()):
        with patch.object(
            historical_fetcher, "personal_scan_exists", AsyncMock(return_value=exists)
        ), patch.object(
            historical_fetcher.settings, "nse_trading_holidays", list(holidays)
        ), patch.object(
            historical_fetcher.settings, "eod_catch_up_hours", [19, 20, 21, 22, 23]
        ):
            return await run_eod_chain_catch_up({"redis": redis}, now=at)

    async def test_missing_scan_queues_an_incremental_sync(self) -> None:
        redis = FakeRedis()
        result = await self.run_catch_up(
            redis, at=datetime.datetime(2026, 11, 3, 19, 15, tzinfo=IST)
        )

        self.assertEqual(result["status"], "sync_enqueued")
        self.assertEqual(
            redis.enqueued,
            [(
                "run_historical_sync",
                ("catch_up",),
                {"_job_id": "eod-catch-up:2026-11-03:1915"},
            )],
        )

    async def test_last_slot_marks_the_sync_final(self) -> None:
        redis = FakeRedis()
        result = await self.run_catch_up(
            redis, at=datetime.datetime(2026, 11, 3, 23, 15, tzinfo=IST)
        )

        self.assertEqual(result["triggered_by"], "catch_up_final")
        self.assertEqual(redis.enqueued[0][1], ("catch_up_final",))

    async def test_existing_personal_scan_needs_no_catch_up(self) -> None:
        redis = FakeRedis()
        result = await self.run_catch_up(
            redis,
            at=datetime.datetime(2026, 11, 3, 20, 15, tzinfo=IST),
            exists=True,
        )

        self.assertEqual(result["status"], "chain_present")
        self.assertEqual(redis.enqueued, [])

    async def test_running_sync_is_left_alone(self) -> None:
        redis = FakeRedis({SYNC_LOCK_KEY: "owner"})
        result = await self.run_catch_up(
            redis, at=datetime.datetime(2026, 11, 3, 19, 15, tzinfo=IST)
        )

        self.assertEqual(result["status"], "sync_running")
        self.assertEqual(redis.enqueued, [])

    async def test_nse_holiday_is_skipped(self) -> None:
        redis = FakeRedis()
        result = await self.run_catch_up(
            redis,
            at=datetime.datetime(2026, 11, 3, 19, 15, tzinfo=IST),
            holidays=["2026-11-03"],
        )

        self.assertEqual(result["status"], "not_a_session")
        self.assertEqual(redis.enqueued, [])


class ScanChainBlockedTests(unittest.IsolatedAsyncioTestCase):
    def readiness(self):
        return evaluate_scan_readiness(
            [(300, TUESDAY)] * 422
            + [(100, TUESDAY)] * 10
            + [(300, datetime.date(2026, 10, 30))] * 65,
            reference_eod_date=TUESDAY,
            minimum_history_days=252,
        )

    def progress(self, triggered_by: str) -> SyncProgress:
        return SyncProgress(
            run_id="run-1",
            triggered_by=triggered_by,
            total_symbols=519,
            successful_symbols=518,
            error_count=1,
        )

    def test_reasons_separate_sync_coverage_from_readiness(self) -> None:
        progress = SyncProgress(total_symbols=519, successful_symbols=449, error_count=70)
        reasons = scan_chain_blocked_reasons(
            progress, TUESDAY, self.readiness(), date_coverage_ready=False
        )

        self.assertEqual(len(reasons), 2)
        self.assertIn("449/519 symbols, 70 errors; at most 5 allowed", reasons[0])
        self.assertIn("422/487 instruments are scoreable", reasons[1])
        self.assertIn("10 recent listings excluded", reasons[1])

    async def record(self, redis, triggered_by: str, statements: list, sender):
        with patch.object(
            historical_fetcher, "async_session", lambda: FakeSession(statements)
        ), patch.object(historical_fetcher.telegram_service, "send_message", sender):
            await record_scan_chain_blocked(
                redis,
                self.progress(triggered_by),
                TUESDAY,
                self.readiness(),
                ["422/487 instruments are scoreable; 463 required"],
            )

    async def test_first_block_records_warning_and_notifies_once(self) -> None:
        redis = FakeRedis()
        statements: list = []
        sender = AsyncMock(return_value=True)

        await self.record(redis, "scheduled", statements, sender)
        await self.record(redis, "catch_up", statements, sender)

        self.assertEqual(len(statements), 2)
        self.assertEqual(statements[0][1]["severity"], "warning")
        self.assertIn("eod_scan_chain_blocked", statements[0][0])
        sender.assert_awaited_once()
        self.assertIn("EOD scan chain blocked", sender.await_args.args[1])

    async def test_final_slot_is_critical_and_notifies_again(self) -> None:
        redis = FakeRedis()
        statements: list = []
        sender = AsyncMock(return_value=True)

        await self.record(redis, "scheduled", statements, sender)
        await self.record(redis, "catch_up_final", statements, sender)

        self.assertEqual(statements[1][1]["severity"], "critical")
        self.assertEqual(sender.await_count, 2)
        self.assertIn("did not run tonight", sender.await_args.args[1])

    async def test_reporting_failures_never_raise(self) -> None:
        class BrokenSession(FakeSession):
            async def execute(self, statement, params=None):
                raise RuntimeError("db down")

        sender = AsyncMock(side_effect=RuntimeError("telegram down"))
        with patch.object(
            historical_fetcher, "async_session", lambda: BrokenSession([])
        ), patch.object(historical_fetcher.telegram_service, "send_message", sender):
            await record_scan_chain_blocked(
                FakeRedis(), self.progress("scheduled"), TUESDAY, self.readiness(), []
            )

        sender.assert_awaited_once()


class HistoricalSyncScheduleTests(unittest.TestCase):
    def test_eod_sync_runs_weekdays_at_1830_ist(self) -> None:
        sync_jobs = [j for j in WorkerSettings.cron_jobs if j.name == "incremental_eod_sync"]
        self.assertEqual(len(sync_jobs), 1, "Expected exactly one incremental_eod_sync cron job")
        sync_job = sync_jobs[0]

        self.assertEqual(str(WorkerSettings.timezone), "Asia/Kolkata")
        self.assertEqual(sync_job.weekday, {0, 1, 2, 3, 4})
        self.assertEqual(sync_job.hour, 18)
        self.assertEqual(sync_job.minute, 30)

    def test_eod_chain_catch_up_runs_hourly_through_the_evening(self) -> None:
        jobs = [j for j in WorkerSettings.cron_jobs if j.name == "eod_chain_catch_up"]
        self.assertEqual(len(jobs), 1, "Expected exactly one eod_chain_catch_up cron job")
        job = jobs[0]

        self.assertEqual(job.weekday, {0, 1, 2, 3, 4})
        self.assertEqual(job.hour, {19, 20, 21, 22, 23})
        self.assertEqual(job.minute, 15)
        self.assertEqual(job.timeout_s, 60)

    def test_auth_guard_runs_weekday_morning_slots(self) -> None:
        """Fyers tokens die at 06:30 IST daily, so the guard runs after that."""
        guard_jobs = [j for j in WorkerSettings.cron_jobs if j.name == "fyers_auth_guard"]
        self.assertEqual(len(guard_jobs), 1, "Expected exactly one fyers_auth_guard cron job")
        guard_job = guard_jobs[0]

        self.assertEqual(guard_job.weekday, {0, 1, 2, 3, 4})
        self.assertEqual(sorted(guard_job.hour), [7, 8])
        self.assertEqual(sorted(guard_job.minute), [0, 15, 30, 45])


if __name__ == "__main__":
    unittest.main()
