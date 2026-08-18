import datetime as dt
import unittest

from app.workers.proposal_worker import (
    cap_proposal_batch_limit,
    proposal_batch_deadline,
)


class TestProposalBatchDeadline(unittest.TestCase):
    def setUp(self) -> None:
        self.now = dt.datetime(2026, 8, 18, 17, 51, tzinfo=dt.timezone.utc)
        self.scan_completed_at = self.now - dt.timedelta(minutes=46)

    def test_auto_batch_expires_when_scan_is_older_than_budget(self) -> None:
        deadline = proposal_batch_deadline(
            scan_completed_at=self.scan_completed_at,
            now=self.now,
            budget_minutes=45,
            manual=False,
        )
        self.assertLess(deadline, self.now)
        self.assertEqual(
            deadline,
            self.scan_completed_at + dt.timedelta(minutes=45),
        )

    def test_manual_batch_starts_a_fresh_clock_from_now(self) -> None:
        deadline = proposal_batch_deadline(
            scan_completed_at=self.scan_completed_at,
            now=self.now,
            budget_minutes=45,
            manual=True,
        )
        remaining = (deadline - self.now).total_seconds()
        self.assertGreater(deadline, self.now)
        self.assertAlmostEqual(remaining, 45 * 60, places=0)

    def test_naive_datetimes_are_treated_as_utc(self) -> None:
        naive_scan = self.scan_completed_at.replace(tzinfo=None)
        naive_now = self.now.replace(tzinfo=None)
        deadline = proposal_batch_deadline(
            scan_completed_at=naive_scan,
            now=naive_now,
            budget_minutes=45,
            manual=False,
        )
        self.assertEqual(deadline.tzinfo, dt.timezone.utc)
        self.assertLess(deadline, self.now)

    def test_batch_limit_is_capped_at_configured_and_hard_max(self) -> None:
        self.assertEqual(cap_proposal_batch_limit(20, 10), 10)
        self.assertEqual(cap_proposal_batch_limit(5, 10), 5)
        self.assertEqual(cap_proposal_batch_limit(50, 20), 20)
