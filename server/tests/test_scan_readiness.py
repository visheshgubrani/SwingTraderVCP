import datetime
import unittest

from app.services.scan_readiness import (
    evaluate_scan_readiness,
    scan_readiness_error,
)


REFERENCE_DATE = datetime.date(2026, 8, 12)
STALE_DATE = REFERENCE_DATE - datetime.timedelta(days=1)


class ScanReadinessTests(unittest.TestCase):
    def test_474_of_500_is_not_ready(self) -> None:
        readiness = evaluate_scan_readiness(
            [(252, REFERENCE_DATE)] * 474
            + [(252, STALE_DATE)] * 26,
            reference_eod_date=REFERENCE_DATE,
            minimum_history_days=252,
        )

        self.assertFalse(readiness.scanner_ready)
        self.assertEqual(readiness.required_scoreable_instruments, 475)
        self.assertEqual(readiness.scoreable_instruments, 474)

    def test_475_of_500_is_ready(self) -> None:
        readiness = evaluate_scan_readiness(
            [(252, REFERENCE_DATE)] * 475
            + [(252, STALE_DATE)] * 25,
            reference_eod_date=REFERENCE_DATE,
            minimum_history_days=252,
        )

        self.assertTrue(readiness.scanner_ready)
        self.assertEqual(readiness.required_scoreable_instruments, 475)

    def test_stale_instrument_is_not_scoreable(self) -> None:
        readiness = evaluate_scan_readiness(
            [(300, REFERENCE_DATE)] * 474
            + [(300, STALE_DATE)]
            + [(0, None)] * 25,
            reference_eod_date=REFERENCE_DATE,
            minimum_history_days=252,
        )

        self.assertFalse(readiness.scanner_ready)
        self.assertEqual(readiness.scoreable_instruments, 474)
        self.assertEqual(readiness.listing_limited_instruments, 0)

    def test_current_recent_listings_leave_the_denominator(self) -> None:
        readiness = evaluate_scan_readiness(
            [(300, REFERENCE_DATE)] * 456
            + [(100, REFERENCE_DATE)] * 20
            + [(300, STALE_DATE)] * 24,
            reference_eod_date=REFERENCE_DATE,
            minimum_history_days=252,
        )

        self.assertTrue(readiness.scanner_ready)
        self.assertEqual(readiness.listing_limited_instruments, 20)
        self.assertEqual(readiness.eligible_instruments, 480)
        self.assertEqual(readiness.required_scoreable_instruments, 456)

    def test_stale_and_empty_names_still_count_against_readiness(self) -> None:
        readiness = evaluate_scan_readiness(
            [(300, REFERENCE_DATE)] * 455
            + [(100, REFERENCE_DATE)] * 20
            + [(300, STALE_DATE)] * 12
            + [(100, STALE_DATE)] * 6
            + [(0, None)] * 7,
            reference_eod_date=REFERENCE_DATE,
            minimum_history_days=252,
        )

        self.assertFalse(readiness.scanner_ready)
        self.assertEqual(readiness.listing_limited_instruments, 20)
        self.assertEqual(readiness.required_scoreable_instruments, 456)

    def test_too_many_short_current_histories_fail_closed(self) -> None:
        readiness = evaluate_scan_readiness(
            [(300, REFERENCE_DATE)] * 449 + [(100, REFERENCE_DATE)] * 51,
            reference_eod_date=REFERENCE_DATE,
            minimum_history_days=252,
        )

        self.assertTrue(readiness.listing_limited_over_cap)
        self.assertFalse(readiness.scanner_ready)
        self.assertIn("51/500", scan_readiness_error(readiness))
        self.assertIn("truncated history", scan_readiness_error(readiness))

    def test_ten_percent_recent_listings_are_still_allowed(self) -> None:
        readiness = evaluate_scan_readiness(
            [(300, REFERENCE_DATE)] * 450 + [(100, REFERENCE_DATE)] * 50,
            reference_eod_date=REFERENCE_DATE,
            minimum_history_days=252,
        )

        self.assertFalse(readiness.listing_limited_over_cap)
        self.assertTrue(readiness.scanner_ready)

    def test_empty_universe_is_not_ready(self) -> None:
        readiness = evaluate_scan_readiness(
            [],
            reference_eod_date=REFERENCE_DATE,
            minimum_history_days=252,
        )

        self.assertFalse(readiness.scanner_ready)

    def test_error_explains_exact_coverage_shortfall(self) -> None:
        readiness = evaluate_scan_readiness(
            [(300, STALE_DATE)] * 500,
            reference_eod_date=REFERENCE_DATE,
            minimum_history_days=252,
        )

        message = scan_readiness_error(readiness)

        self.assertIn("0/500", message)
        self.assertIn("252 sessions", message)
        self.assertIn("475 required (95%)", message)
        self.assertNotIn("recent listings", message)

    def test_error_reports_excluded_recent_listings(self) -> None:
        readiness = evaluate_scan_readiness(
            [(300, REFERENCE_DATE)] * 400
            + [(100, REFERENCE_DATE)] * 20
            + [(300, STALE_DATE)] * 80,
            reference_eod_date=REFERENCE_DATE,
            minimum_history_days=252,
        )

        message = scan_readiness_error(readiness)

        self.assertIn("400/480", message)
        self.assertIn("20 recent listings", message)
        self.assertIn("456 required (95%)", message)


if __name__ == "__main__":
    unittest.main()
