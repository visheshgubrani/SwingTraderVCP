import datetime as dt
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.routers.automation import record_proposal_decision
from app.schemas.proposals import ProposalDecisionRequest


class ProposalDecisionSubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_approval_arms_subscription_without_placing_inline(self) -> None:
        proposal_id = uuid4()
        decision_id = uuid4()
        proposal_hash = "a" * 64
        proposal = SimpleNamespace(
            id=proposal_id,
            status="pending_approval",
            approval_deadline=dt.datetime.now(dt.timezone.utc)
            + dt.timedelta(hours=1),
            proposal_hash=proposal_hash,
            symbol="NSE:EXAMPLE-EQ",
            live_eligible=True,
            entry_session_date=dt.date.today(),
            approved_risk_budget_amount=Decimal("1000"),
            pivot_price=Decimal("100"),
            initial_stop=Decimal("95"),
            t1=Decimal("110"),
            t2=Decimal("120"),
            t3=Decimal("130"),
            entry_template="single",
            chase_ceiling=Decimal("102"),
        )
        proposal_result = MagicMock()
        proposal_result.fetchone.return_value = proposal
        decision_result = MagicMock()
        decision_result.scalar_one.return_value = decision_id
        db = AsyncMock()
        db.execute.side_effect = [
            proposal_result,
            decision_result,
            MagicMock(),
            MagicMock(),
        ]
        redis = AsyncMock()
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(redis=redis))
        )
        payload = ProposalDecisionRequest(
            decision="approved",
            expected_proposal_hash=proposal_hash,
        )

        with (
            patch(
                "app.routers.automation.require_approvals_allowed",
                new=AsyncMock(),
            ),
            patch(
                "app.routers.automation.publish_tick_subscriptions",
                new=AsyncMock(),
            ) as publish,
        ):
            response = await record_proposal_decision(
                proposal_id,
                payload,
                request,
                db,
            )

        self.assertEqual(response["status"], "approved")
        db.commit.assert_awaited_once()
        publish.assert_awaited_once_with(redis, ["NSE:EXAMPLE-EQ"])


if __name__ == "__main__":
    unittest.main()
