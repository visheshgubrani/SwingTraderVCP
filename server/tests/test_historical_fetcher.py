import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from zoneinfo import ZoneInfo

from app.services.historical_fetcher import (
    SyncProgress,
    build_date_chunks,
    enqueue_eod_scans,
    history_response_has_no_data,
    latest_completed_eod_date,
    next_sync_date,
    sync_date_ranges,
    sync_status_is_current,
)
from app.worker import WorkerSettings


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
    async def test_personal_scan_is_ensured_before_saas_refresh(self) -> None:
        events: list[str] = []
        scan_run_id = uuid4()

        async def ensure_personal(_redis, *, triggered_by):
            events.append(f"personal:{triggered_by}")
            return SimpleNamespace(
                scan_run_id=scan_run_id,
                status="queued",
            )

        class Redis:
            async def enqueue_job(self, function, *_args, **_kwargs):
                events.append(function)
                return SimpleNamespace()

        progress = SyncProgress()
        with patch(
            "app.services.historical_fetcher.ensure_personal_scan",
            ensure_personal,
        ):
            await enqueue_eod_scans(
                Redis(),
                progress,
                datetime.date(2026, 8, 12),
            )

        self.assertEqual(
            events,
            ["personal:eod_chain", "run_saas_global_standard_scan"],
        )
        self.assertEqual(progress.personal_scan_run_id, str(scan_run_id))


class HistoricalSyncScheduleTests(unittest.TestCase):
    def test_eod_sync_runs_weekdays_at_1830_ist(self) -> None:
        sync_jobs = [j for j in WorkerSettings.cron_jobs if j.name == "incremental_eod_sync"]
        self.assertEqual(len(sync_jobs), 1, "Expected exactly one incremental_eod_sync cron job")
        sync_job = sync_jobs[0]

        self.assertEqual(str(WorkerSettings.timezone), "Asia/Kolkata")
        self.assertEqual(sync_job.weekday, {0, 1, 2, 3, 4})
        self.assertEqual(sync_job.hour, 18)
        self.assertEqual(sync_job.minute, 30)

    def test_token_refresh_runs_weekdays_before_market_open(self) -> None:
        refresh_jobs = [j for j in WorkerSettings.cron_jobs if j.name == "fyers_token_refresh"]
        self.assertEqual(len(refresh_jobs), 1, "Expected exactly one fyers_token_refresh cron job")
        refresh_job = refresh_jobs[0]

        self.assertEqual(refresh_job.weekday, {0, 1, 2, 3, 4})
        self.assertEqual(refresh_job.hour, 8)
        self.assertEqual(refresh_job.minute, 50)


if __name__ == "__main__":
    unittest.main()
