import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.domain.trading import ExitSignal
from app.services.position_monitor import (
    MonitoredPosition,
    process_position_tick,
)


class PositionMonitorServiceTests(unittest.IsolatedAsyncioTestCase):
    def _position(self, **overrides) -> MonitoredPosition:
        base = {
            "id": uuid4(),
            "symbol": "NSE:SBIN-EQ",
            "side": "long",
            "state": "open",
            "quantity": 10,
            "open_quantity": 10,
            "product_type": "CNC",
            "average_entry_price": Decimal("100.00"),
            "current_stop_loss": Decimal("95.00"),
            "current_target": Decimal("110.00"),
            "trailing_rule": {"type": "none"},
            "tick_size": Decimal("0.05"),
        }
        base.update(overrides)
        return MonitoredPosition(**base)

    async def test_kill_switch_blocks_exit(self) -> None:
        db = AsyncMock()
        result = await process_position_tick(
            db,
            position=self._position(),
            ltp=Decimal("94.00"),
            kill_switch_engaged=True,
        )
        self.assertIsNone(result)
        db.execute.assert_not_called()

    @patch("app.services.position_monitor.create_exit_intent", new_callable=AsyncMock)
    @patch("app.services.position_monitor.complete_paper_exit", new_callable=AsyncMock)
    @patch("app.services.position_monitor.settings.execution_mode", "paper")
    async def test_stop_loss_triggers_paper_exit(
        self,
        complete_paper_exit: AsyncMock,
        create_exit_intent: AsyncMock,
    ) -> None:
        intent_id = uuid4()
        create_exit_intent.return_value = type(
            "Ref",
            (),
            {"id": intent_id, "idempotency_key": "k", "execution_mode": "paper"},
        )()
        db = AsyncMock()

        result = await process_position_tick(
            db,
            position=self._position(),
            ltp=Decimal("94.00"),
            kill_switch_engaged=False,
        )

        self.assertEqual(result, intent_id)
        create_exit_intent.assert_awaited_once()
        complete_paper_exit.assert_awaited_once_with(
            db,
            order_intent_id=intent_id,
            position_id=create_exit_intent.await_args.kwargs["position_id"],
            exit_price=Decimal("94.00"),
        )

    @patch("app.services.position_monitor.create_exit_intent", new_callable=AsyncMock)
    @patch("app.services.position_monitor.complete_paper_exit", new_callable=AsyncMock)
    @patch("app.services.position_monitor.settings.execution_mode", "paper")
    async def test_target_hit_before_stop(
        self,
        complete_paper_exit: AsyncMock,
        create_exit_intent: AsyncMock,
    ) -> None:
        intent_id = uuid4()
        create_exit_intent.return_value = type(
            "Ref",
            (),
            {"id": intent_id, "idempotency_key": "k", "execution_mode": "paper"},
        )()
        db = AsyncMock()

        await process_position_tick(
            db,
            position=self._position(),
            ltp=Decimal("111.00"),
            kill_switch_engaged=False,
        )

        self.assertEqual(
            create_exit_intent.await_args.kwargs["intent_type"],
            "target_exit",
        )

    @patch("app.services.position_monitor.apply_trailing_update", new_callable=AsyncMock)
    async def test_no_exit_when_price_inside_band(
        self,
        apply_trailing_update: AsyncMock,
    ) -> None:
        position = self._position()
        apply_trailing_update.return_value = position
        db = AsyncMock()

        result = await process_position_tick(
            db,
            position=position,
            ltp=Decimal("102.00"),
            kill_switch_engaged=False,
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
