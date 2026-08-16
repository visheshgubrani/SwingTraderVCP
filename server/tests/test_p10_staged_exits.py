import unittest
from decimal import Decimal
from uuid import uuid4

from app.services.staged_exit_manager import (
    StagedPositionState,
    allocate_cumulative_target_fill,
    evaluate_staged_position_tick,
)


class TestP10StagedExits(unittest.TestCase):
    def setUp(self):
        self.pos = StagedPositionState(
            id=uuid4(),
            symbol="TESTSTOCK",
            side="long",
            state="open",
            open_quantity=100,
            weighted_entry_price=Decimal("500.00"),
            current_stop=Decimal("475.00"),
            t1_target=Decimal("530.00"),
            t2_target=Decimal("560.00"),
            t3_target=Decimal("590.00"),
            t1_shares=25,
            t2_shares=25,
            t3_shares=25,
            runner_shares=25,
            t1_filled_shares=0,
            t2_filled_shares=0,
            t3_filled_shares=0,
            runner_filled_shares=0,
            high_water_mark=None,
            trailing_stop=None,
            atr14=Decimal("15.00"),
            tick_size=Decimal("0.05"),
        )

    def test_stop_loss_trigger_exits_all(self):
        # LTP falls to 474.00 (below stop 475.00)
        action = evaluate_staged_position_tick(self.pos, Decimal("474.00"))
        self.assertEqual(action.action_type, "stop_loss")
        self.assertEqual(action.exit_shares, 100)
        self.assertEqual(action.exit_purpose, "stop_loss")

    def test_t1_trigger_requests_25_without_moving_stop_before_fill(self):
        # LTP reaches 532.00 (above T1 530.00)
        action = evaluate_staged_position_tick(self.pos, Decimal("532.00"))
        self.assertEqual(action.action_type, "target_exit")
        self.assertEqual(action.exit_shares, 25)
        self.assertEqual(action.exit_purpose, "target_1")
        self.assertIsNone(action.new_stop)

    def test_multi_target_gap_consolidates_orders(self):
        # Price gaps directly to 565.00 (crossing both T1 530.00 and T2 560.00)
        action = evaluate_staged_position_tick(self.pos, Decimal("565.00"))
        self.assertEqual(action.action_type, "target_exit")
        self.assertEqual(action.exit_shares, 50)  # 25 (T1) + 25 (T2) = 50
        self.assertEqual(action.exit_purpose, "target_2")
        self.assertEqual(action.crossed_targets, (1, 2))
        self.assertIsNone(action.new_stop)
        self.assertIsNone(action.new_high_water_mark)

    def test_runner_high_water_mark_trailing_stop(self):
        # After T1 and T2 are filled, position is trailing_active with 50 open shares
        self.pos.state = "trailing_active"
        self.pos.open_quantity = 50
        self.pos.t1_filled_shares = 25
        self.pos.t2_filled_shares = 25
        self.pos.high_water_mark = Decimal("600.00")
        # Trail distance = 2 * 15 = 30. Stop = 600 - 30 = 570.00
        self.pos.trailing_stop = Decimal("570.00")

        # Price drops to 568.00 (below trail stop 570.00)
        action = evaluate_staged_position_tick(self.pos, Decimal("568.00"))
        self.assertEqual(action.action_type, "trailing_exit")
        self.assertEqual(action.exit_shares, 50)
        self.assertEqual(action.exit_purpose, "runner_trail")

    def test_cumulative_gap_fill_allocates_lower_target_first(self):
        allocation = allocate_cumulative_target_fill(
            self.pos,
            exit_purpose="target_2",
            fill_quantity=40,
        )
        self.assertEqual((allocation.t1, allocation.t2, allocation.t3), (25, 15, 0))


if __name__ == "__main__":
    unittest.main()
