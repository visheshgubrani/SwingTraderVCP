import datetime
import unittest

from zoneinfo import ZoneInfo

from app.services.historical_fetcher import (
    build_date_chunks,
    latest_completed_eod_date,
    next_sync_date,
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


class HistoricalSyncScheduleTests(unittest.TestCase):
    def test_eod_sync_runs_weekdays_at_1830_ist(self) -> None:
        [sync_job] = WorkerSettings.cron_jobs

        self.assertEqual(str(WorkerSettings.timezone), "Asia/Kolkata")
        self.assertEqual(sync_job.weekday, {0, 1, 2, 3, 4})
        self.assertEqual(sync_job.hour, 18)
        self.assertEqual(sync_job.minute, 30)


if __name__ == "__main__":
    unittest.main()
