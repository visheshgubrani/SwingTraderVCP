import datetime
import unittest

from app.services.scan_readiness import (
    evaluate_scan_readiness,
    scan_readiness_error,
)


REFERENCE_DATE = datetime.date(2026, 8, 12)


class ScanReadinessTests(unittest.TestCase):
    def test_474_of_500_is_not_ready(self) -> None:
        readiness = evaluate_scan_readiness(
            [(252, REFERENCE_DATE)] * 474
            + [(251, REFERENCE_DATE)] * 26,
            reference_eod_date=REFERENCE_DATE,
            minimum_history_days=252,
        )

        self.assertFalse(readiness.scanner_ready)
        self.assertEqual(readiness.required_scoreable_instruments, 475)
        self.assertEqual(readiness.scoreable_instruments, 474)

    def test_475_of_500_is_ready(self) -> None:
        readiness = evaluate_scan_readiness(
            [(252, REFERENCE_DATE)] * 475
            + [(251, REFERENCE_DATE)] * 25,
            reference_eod_date=REFERENCE_DATE,
            minimum_history_days=252,
        )

        self.assertTrue(readiness.scanner_ready)
        self.assertEqual(readiness.required_scoreable_instruments, 475)

    def test_stale_instrument_is_not_scoreable(self) -> None:
        readiness = evaluate_scan_readiness(
            [(300, REFERENCE_DATE)] * 474
            + [(300, REFERENCE_DATE - datetime.timedelta(days=1))]
            + [(100, REFERENCE_DATE)] * 25,
            reference_eod_date=REFERENCE_DATE,
            minimum_history_days=252,
        )

        self.assertFalse(readiness.scanner_ready)
        self.assertEqual(readiness.scoreable_instruments, 474)

    def test_error_explains_exact_coverage_shortfall(self) -> None:
        readiness = evaluate_scan_readiness(
            [(247, REFERENCE_DATE)] * 500,
            reference_eod_date=REFERENCE_DATE,
            minimum_history_days=252,
        )

        message = scan_readiness_error(readiness)

        self.assertIn("0/500", message)
        self.assertIn("252 sessions", message)
        self.assertIn("475 required (95%)", message)


if __name__ == "__main__":
    unittest.main()
