import datetime
import unittest

from app.services.screener import candle_trading_date, select_reference_eod_date


class ScreenerDateTests(unittest.TestCase):
    def test_reference_date_uses_the_date_with_broadest_coverage(self) -> None:
        current = datetime.date(2026, 7, 27)
        stale = datetime.date(2026, 7, 16)

        result = select_reference_eod_date([current] * 499 + [stale])

        self.assertEqual(result, current)

    def test_reference_date_prefers_latest_date_when_coverage_is_tied(self) -> None:
        result = select_reference_eod_date(
            [
                datetime.date(2026, 7, 24),
                datetime.date(2026, 7, 27),
            ]
        )

        self.assertEqual(result, datetime.date(2026, 7, 27))

    def test_fyers_daily_timestamp_converts_to_india_trading_date(self) -> None:
        result = candle_trading_date(
            datetime.datetime(
                2026,
                7,
                27,
                0,
                0,
                tzinfo=datetime.timezone.utc,
            )
        )

        self.assertEqual(result, datetime.date(2026, 7, 27))


if __name__ == "__main__":
    unittest.main()
