import datetime as dt
import unittest

from app.services.p10_forming_watch import (
    FORMING_EXPIRY_SESSIONS,
    FORMING_RECHECK_CAP,
    completed_nse_sessions_since,
)


class TestP10FormingWatch(unittest.TestCase):
    def test_caps_are_locked(self) -> None:
        self.assertEqual(FORMING_EXPIRY_SESSIONS, 10)
        self.assertEqual(FORMING_RECHECK_CAP, 10)

    def test_completed_sessions_skip_weekends(self) -> None:
        first_seen = dt.date(2026, 8, 10)  # Monday
        as_of = dt.date(2026, 8, 24)  # Monday two weeks later
        self.assertEqual(completed_nse_sessions_since(first_seen, as_of, holidays=set()), 10)

    def test_holidays_do_not_count(self) -> None:
        first_seen = dt.date(2026, 8, 10)
        as_of = dt.date(2026, 8, 13)
        holidays = {dt.date(2026, 8, 12)}
        self.assertEqual(completed_nse_sessions_since(first_seen, as_of, holidays=holidays), 2)

    def test_same_day_is_zero(self) -> None:
        day = dt.date(2026, 8, 10)
        self.assertEqual(completed_nse_sessions_since(day, day, holidays=set()), 0)


if __name__ == "__main__":
    unittest.main()
