import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.services.p10_rollout import (
    RolloutBlockedError,
    get_rollout_state,
    promote_rollout_stage,
    require_approvals_allowed,
)


class FakeResult:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row

    def one(self):
        if self.row is None:
            raise AssertionError("Expected one row")
        return self.row


def _state(stage: str, **overrides) -> dict:
    payload = {
        "stage": stage,
        "changed_by": "owner",
        "changed_at": None,
        "reason": "test",
        "next_stage": {
            "shadow": "paper",
            "paper": "reduced_live",
            "reduced_live": "full_live",
            "full_live": None,
        }[stage],
        "required_confirmation": {
            "shadow": "CONFIRM_P10_PAPER",
            "paper": "CONFIRM_P10_REDUCED_LIVE",
            "reduced_live": "CONFIRM_P10_FULL_LIVE",
            "full_live": None,
        }[stage],
        "execution_mode": settings.execution_mode,
        "live_order_placement_enabled": settings.live_order_placement_enabled,
        "approvals_allowed": stage != "shadow",
    }
    payload.update(overrides)
    return payload


class P10RolloutTests(unittest.IsolatedAsyncioTestCase):
    async def test_shadow_blocks_approve(self) -> None:
        db = AsyncMock()
        db.execute.return_value = FakeResult(
            {
                "stage": "shadow",
                "changed_by": "schema",
                "changed_at": None,
                "reason": "start",
            }
        )
        with self.assertRaises(RolloutBlockedError):
            await require_approvals_allowed(db)

    async def test_paper_allows_approve(self) -> None:
        db = AsyncMock()
        db.execute.return_value = FakeResult(
            {
                "stage": "paper",
                "changed_by": "owner",
                "changed_at": None,
                "reason": "paper",
            }
        )
        await require_approvals_allowed(db)

    async def test_get_rollout_reports_confirmation(self) -> None:
        db = AsyncMock()
        db.execute.return_value = FakeResult(
            {
                "stage": "shadow",
                "changed_by": "schema",
                "changed_at": None,
                "reason": "start",
            }
        )
        state = await get_rollout_state(db)
        self.assertEqual(state["stage"], "shadow")
        self.assertFalse(state["approvals_allowed"])
        self.assertEqual(state["next_stage"], "paper")
        self.assertEqual(state["required_confirmation"], "CONFIRM_P10_PAPER")
        self.assertEqual(state["execution_mode"], settings.execution_mode)

    async def test_promote_paper_requires_paper_env(self) -> None:
        db = AsyncMock()
        with (
            patch.object(settings, "execution_mode", "live"),
            patch.object(settings, "live_order_placement_enabled", False),
            patch(
                "app.services.p10_rollout.get_rollout_state",
                new=AsyncMock(return_value=_state("shadow")),
            ),
        ):
            with self.assertRaises(RolloutBlockedError) as ctx:
                await promote_rollout_stage(
                    db,
                    target_stage="paper",
                    confirmation="CONFIRM_P10_PAPER",
                    changed_by="owner",
                    reason="try paper",
                )
        self.assertIn("EXECUTION_MODE=paper", str(ctx.exception))

    async def test_promote_paper_seeds_one_lakh(self) -> None:
        db = AsyncMock()
        db.execute.side_effect = [
            FakeResult(),
            FakeResult(
                {"version": 1, "deployable_capital_override": Decimal("100000")}
            ),
            FakeResult(),
            FakeResult(),
        ]
        seed = AsyncMock(
            return_value={
                "starting_cash": Decimal("100000"),
                "cash_available": Decimal("100000"),
            }
        )
        with (
            patch.object(settings, "execution_mode", "paper"),
            patch.object(settings, "live_order_placement_enabled", False),
            patch(
                "app.services.p10_rollout.get_rollout_state",
                new=AsyncMock(
                    side_effect=[_state("shadow"), _state("paper")],
                ),
            ),
            patch("app.services.p10_rollout.seed_paper_account", new=seed),
        ):
            state = await promote_rollout_stage(
                db,
                target_stage="paper",
                confirmation="CONFIRM_P10_PAPER",
                changed_by="owner",
                reason="start paper at 1L",
            )
        self.assertEqual(state["stage"], "paper")
        seed.assert_awaited_once()
        self.assertEqual(
            seed.await_args.kwargs["starting_cash"],
            Decimal("100000"),
        )

    async def test_promote_live_blocked_while_paper_open(self) -> None:
        db = AsyncMock()
        db.execute.side_effect = [
            FakeResult(),
            FakeResult({"positions": 1, "intents": 0}),
        ]
        with (
            patch.object(settings, "execution_mode", "live"),
            patch.object(settings, "live_order_placement_enabled", True),
            patch(
                "app.services.p10_rollout.get_rollout_state",
                new=AsyncMock(return_value=_state("paper")),
            ),
        ):
            with self.assertRaises(RolloutBlockedError) as ctx:
                await promote_rollout_stage(
                    db,
                    target_stage="reduced_live",
                    confirmation="CONFIRM_P10_REDUCED_LIVE",
                    changed_by="owner",
                    reason="too soon",
                )
        self.assertIn("paper positions", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
