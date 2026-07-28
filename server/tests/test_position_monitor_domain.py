import unittest
from decimal import Decimal

from app.domain.trading import (
    ExitSignal,
    apply_step_pct_trail,
    evaluate_exit,
    realized_pnl_on_exit,
    snap_to_tick,
)


class PositionMonitorDomainTests(unittest.TestCase):
    def test_long_stop_loss_triggers_before_target(self) -> None:
        signal = evaluate_exit(
            side="long",
            ltp=Decimal("94.50"),
            stop=Decimal("95.00"),
            target=Decimal("110.00"),
            trailing_active=False,
        )
        self.assertEqual(
            signal,
            ExitSignal(
                intent_type="stop_loss_exit",
                trigger_price=Decimal("94.50"),
            ),
        )

    def test_long_target_triggers(self) -> None:
        signal = evaluate_exit(
            side="long",
            ltp=Decimal("112.00"),
            stop=Decimal("95.00"),
            target=Decimal("110.00"),
            trailing_active=False,
        )
        self.assertEqual(signal.intent_type, "target_exit")

    def test_trailing_active_uses_trailing_exit_on_stop(self) -> None:
        signal = evaluate_exit(
            side="long",
            ltp=Decimal("94.00"),
            stop=Decimal("95.00"),
            target=None,
            trailing_active=True,
        )
        self.assertEqual(signal.intent_type, "trailing_exit")

    def test_short_stop_and_target_direction(self) -> None:
        stop = evaluate_exit(
            side="short",
            ltp=Decimal("106.00"),
            stop=Decimal("105.00"),
            target=Decimal("90.00"),
            trailing_active=False,
        )
        target = evaluate_exit(
            side="short",
            ltp=Decimal("88.00"),
            stop=Decimal("105.00"),
            target=Decimal("90.00"),
            trailing_active=False,
        )
        self.assertEqual(stop.intent_type, "stop_loss_exit")
        self.assertEqual(target.intent_type, "target_exit")

    def test_step_pct_trail_ratchets_long_stop_only_up(self) -> None:
        unchanged = apply_step_pct_trail(
            side="long",
            ltp=Decimal("100.00"),
            current_stop=Decimal("95.00"),
            step_pct=Decimal("5"),
            tick_size=Decimal("0.05"),
        )
        self.assertIsNone(unchanged)

        moved = apply_step_pct_trail(
            side="long",
            ltp=Decimal("110.00"),
            current_stop=Decimal("95.00"),
            step_pct=Decimal("5"),
            tick_size=Decimal("0.05"),
        )
        self.assertEqual(moved, Decimal("104.50"))

    def test_step_pct_trail_ratchets_short_stop_only_down(self) -> None:
        moved = apply_step_pct_trail(
            side="short",
            ltp=Decimal("90.00"),
            current_stop=Decimal("105.00"),
            step_pct=Decimal("5"),
            tick_size=Decimal("0.05"),
        )
        self.assertEqual(moved, Decimal("94.50"))

    def test_snap_to_tick_is_side_conservative_for_stops(self) -> None:
        self.assertEqual(
            snap_to_tick(
                Decimal("104.53"),
                Decimal("0.05"),
                side="long",
                for_stop=True,
            ),
            Decimal("104.50"),
        )
        self.assertEqual(
            snap_to_tick(
                Decimal("94.47"),
                Decimal("0.05"),
                side="short",
                for_stop=True,
            ),
            Decimal("94.50"),
        )

    def test_realized_pnl_on_exit_for_long_and_short(self) -> None:
        self.assertEqual(
            realized_pnl_on_exit(
                side="long",
                average_entry_price=Decimal("100"),
                quantity=10,
                exit_price=Decimal("108"),
            ),
            Decimal("80"),
        )
        self.assertEqual(
            realized_pnl_on_exit(
                side="short",
                average_entry_price=Decimal("100"),
                quantity=10,
                exit_price=Decimal("92"),
            ),
            Decimal("80"),
        )


if __name__ == "__main__":
    unittest.main()
