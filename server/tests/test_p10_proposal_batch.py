import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.workers.proposal_worker import (
    WorkerSettings,
    _persist_proposal,
    cap_proposal_batch_limit,
    finalize_interrupted_proposal_run,
    proposal_batch_deadline,
    run_eod_proposal_batch,
    run_single_proposal,
)


class FakeResult:
    def __init__(self, *, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def one_or_none(self):
        if not self._rows:
            return None
        if len(self._rows) > 1:
            raise RuntimeError("Multiple rows")
        return self._rows[0]

    def one(self):
        if len(self._rows) != 1:
            raise RuntimeError(f"Expected one row, received {len(self._rows)}")
        return self._rows[0]


class FakeProposalBatchSession:
    def __init__(self, candidate, automation_run_id):
        self.candidate = candidate
        self.automation_run_id = automation_run_id
        self.sql: list[str] = []

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def execute(self, statement, params=None):
        del params
        sql = str(statement)
        self.sql.append(sql)
        if "SELECT enabled" in sql:
            return FakeResult(scalar=False)
        if "FROM screening_results" in sql:
            return FakeResult(rows=[self.candidate])
        if "INSERT INTO automation_runs" in sql:
            return FakeResult(scalar=self.automation_run_id)
        return FakeResult()

    async def commit(self):
        return None


class TestProposalBatchDeadline(unittest.IsolatedAsyncioTestCase):
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

    def test_proposal_worker_registers_single_stock_job(self) -> None:
        self.assertIn(run_single_proposal, WorkerSettings.functions)

    async def test_all_rejected_batch_completes_without_trade_proposal_rows(self) -> None:
        candidate = SimpleNamespace(
            screening_result_id=uuid4(),
            symbol="NSE:EXAMPLE-EQ",
            as_of_date=dt.date(2026, 8, 18),
            scan_completed_at=self.now,
        )
        automation_run_id = uuid4()
        fake_session = FakeProposalBatchSession(candidate, automation_run_id)
        with (
            patch("app.workers.proposal_worker.async_session", fake_session),
            patch(
                "app.workers.proposal_worker.process_proposal_candidate",
                new=AsyncMock(return_value="rejected"),
            ) as process_candidate,
        ):
            result = await run_eod_proposal_batch(
                {}, str(uuid4()), limit=1, manual=True
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["counts"]["rejected"], 1)
        process_candidate.assert_awaited_once()
        self.assertFalse(
            any("INSERT INTO trade_proposals" in sql for sql in fake_session.sql)
        )

    async def test_single_proposal_processes_only_the_requested_candidate(self) -> None:
        scan_run_id = uuid4()
        screening_result_id = uuid4()
        candidate = SimpleNamespace(
            screening_result_id=screening_result_id,
            symbol="NSE:EXAMPLE-EQ",
            as_of_date=dt.date(2026, 8, 18),
            scan_completed_at=self.now,
            scan_run_id=scan_run_id,
        )
        automation_run_id = uuid4()
        fake_session = FakeProposalBatchSession(candidate, automation_run_id)
        with (
            patch("app.workers.proposal_worker.async_session", fake_session),
            patch(
                "app.workers.proposal_worker.process_proposal_candidate",
                new=AsyncMock(return_value="rejected"),
            ) as process_candidate,
        ):
            result = await run_single_proposal({}, str(screening_result_id))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["scan_run_id"], str(scan_run_id))
        self.assertEqual(result["counts"]["rejected"], 1)
        process_candidate.assert_awaited_once()
        self.assertEqual(process_candidate.await_args.kwargs["candidate"], candidate)

    async def test_single_proposal_returns_no_candidates_when_missing(self) -> None:
        fake_session = FakeProposalBatchSession(None, uuid4())
        with patch("app.workers.proposal_worker.async_session", fake_session):
            result = await run_single_proposal({}, str(uuid4()))
        self.assertEqual(result["status"], "no_candidates")

    def test_proposal_worker_settings_has_max_tries_one_and_no_retry(self) -> None:
        self.assertEqual(WorkerSettings.max_tries, 1)
        self.assertFalse(WorkerSettings.retry_jobs)

    async def test_interrupted_attempt_is_closed_when_deadline_has_passed(self) -> None:
        class InterruptedSession:
            def __init__(self):
                self.calls: list[tuple[str, dict]] = []
                self.committed = False

            async def execute(self, statement, params=None):
                sql = str(statement)
                self.calls.append((sql, params or {}))
                if "UPDATE proposal_attempts" in sql:
                    return FakeResult(rows=[{"id": uuid4()}])
                return FakeResult(scalar=uuid4())

            async def commit(self):
                self.committed = True

        session = InterruptedSession()
        deadline = self.now - dt.timedelta(seconds=1)

        finalized = await finalize_interrupted_proposal_run(
            session,
            automation_run_id=str(uuid4()),
            batch_deadline=deadline,
            now=self.now,
        )

        self.assertTrue(finalized)
        self.assertTrue(session.committed)
        attempt_params = session.calls[0][1]
        run_params = session.calls[1][1]
        self.assertEqual(attempt_params["attempt_status"], "timed_out")
        self.assertEqual(run_params["status"], "timed_out")
        self.assertEqual(run_params["interrupted_attempts"], 1)
        self.assertEqual(run_params["failed_attempts"], 0)

    async def test_duplicate_immutable_proposal_is_not_reported_as_created(self) -> None:
        existing_id = uuid4()

        class DuplicateSession:
            def __init__(self):
                self.calls = 0

            async def execute(self, statement, params=None):
                del statement, params
                self.calls += 1
                if self.calls == 1:
                    return FakeResult(scalar=None)
                return FakeResult(
                    rows=[{"id": existing_id, "status": "expired_unapproved"}]
                )

        result = await _persist_proposal(
            DuplicateSession(),
            automation_run_id=str(uuid4()),
            proposal={
                "leg_risk_allocations": [1.0],
                "gemini_evidence": {},
                "geometry": {},
            },
            charts=SimpleNamespace(context_png=b"context", detail_png=b"detail"),
            request_id=None,
            usage={},
            cost=0,
        )

        self.assertFalse(result.created)
        self.assertEqual(result.proposal_id, str(existing_id))
        self.assertEqual(result.status, "expired_unapproved")

    async def test_auto_batch_skips_when_run_already_exists(self) -> None:
        existing_run_id = uuid4()
        scan_run_id = uuid4()

        class FakeDedupeSession:
            def __call__(self):
                return self

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def execute(self, statement, params=None):
                sql = str(statement)
                if "SELECT enabled" in sql:
                    return FakeResult(scalar=False)
                if "FROM automation_runs" in sql:
                    return FakeResult(rows=[{"id": existing_run_id, "status": "completed"}])
                return FakeResult()

            async def commit(self):
                return None

        with patch("app.workers.proposal_worker.async_session", FakeDedupeSession()):
            result = await run_eod_proposal_batch(
                {}, str(scan_run_id), limit=10, manual=False
            )

        self.assertEqual(result["status"], "already_exists")
        self.assertEqual(result["scan_run_id"], str(scan_run_id))
        self.assertEqual(result["automation_run_id"], str(existing_run_id))
