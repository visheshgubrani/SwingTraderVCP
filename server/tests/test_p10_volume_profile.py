import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from app.services.intraday_bar_reconciliation import build_volume_profile


class VolumeProfileTests(unittest.TestCase):
    def test_expected_fraction_is_normalized_per_session(self):
        ist = ZoneInfo("Asia/Kolkata")
        candles: list[list[float | int]] = []
        for session_index in range(15):
            session = dt.date(2026, 7, 1) + dt.timedelta(days=session_index)
            per_bar_volume = 100 + (session_index * 50)
            for bucket in range(70):
                timestamp = dt.datetime.combine(
                    session,
                    dt.time(9, 15),
                    tzinfo=ist,
                ) + dt.timedelta(minutes=5 * bucket)
                candles.append(
                    [int(timestamp.timestamp()), 100, 101, 99, 100, per_bar_volume]
                )

        result = build_volume_profile(candles, today_ist=dt.date(2026, 8, 1))
        self.assertIsNotNone(result)
        _, adv20, points, sessions_used = result  # type: ignore[misc]
        self.assertGreater(adv20, 0)
        self.assertEqual(sessions_used, 15)
        self.assertEqual(points[-1]["cumulative_fraction"], "1.000000")
        self.assertEqual(points[34]["cumulative_fraction"], "0.500000")


if __name__ == "__main__":
    unittest.main()
