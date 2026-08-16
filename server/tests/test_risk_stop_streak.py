from decimal import Decimal
import unittest

from app.services.risk_stop_streak import advance_stop_streak, classify_stop_closure


class RiskStopStreakRuleTests(unittest.TestCase):
    def test_only_pure_negative_stop_loss_increments(self) -> None:
        self.assertEqual(classify_stop_closure({"stop_loss"}, Decimal("-1")), "increment")
        self.assertEqual(classify_stop_closure({"stop_loss"}, Decimal("0")), "reset")
        self.assertEqual(classify_stop_closure({"target"}, Decimal("10")), "reset")
        self.assertEqual(classify_stop_closure({"target", "stop_loss"}, Decimal("-1")), "reset")

    def test_manual_external_and_correction_outcomes_are_ignored(self) -> None:
        for purpose in ("manual", "external", "invalid_fill", "risk_reduction"):
            self.assertEqual(classify_stop_closure({purpose}, Decimal("-100")), "ignored")
        self.assertEqual(classify_stop_closure(set(), Decimal("-100")), "ignored")

    def test_third_increment_trips_and_latches(self) -> None:
        count, tripped, newly = advance_stop_streak(
            count=2, tripped=False, classification="increment", limit=3
        )
        self.assertEqual((count, tripped, newly), (3, True, True))
        self.assertEqual(
            advance_stop_streak(count=count, tripped=tripped, classification="reset", limit=3),
            (3, True, False),
        )

    def test_normal_close_resets_only_an_untripped_streak(self) -> None:
        self.assertEqual(
            advance_stop_streak(count=2, tripped=False, classification="reset", limit=3),
            (0, False, False),
        )
        self.assertEqual(
            advance_stop_streak(count=2, tripped=False, classification="ignored", limit=3),
            (2, False, False),
        )


if __name__ == "__main__":
    unittest.main()
