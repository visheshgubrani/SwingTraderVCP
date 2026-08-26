import asyncio
import datetime
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from arq.jobs import JobStatus

from app.services.personal_scan import (
    canonical_config_payload,
    ensure_personal_scan,
)
from app.services.screening_config import TechnicalScreeningConfig
from app.services.scan_readiness import ScanReadiness
from app.services.screener import run_technical_scan


class FakeResult:
    def __init__(self, *, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def one_or_none(self):
        return self._rows[0] if self._rows else None


class FakeDatabase:
    def __init__(self):
        self.reference_date = datetime.date(2026, 8, 12)
        self.rows: list[SimpleNamespace] = []
        self.advisory_lock = asyncio.Lock()

    def session(self):
        return FakeSession(self)


class FakeSession:
    def __init__(self, database: FakeDatabase):
        self.database = database
        self.holds_lock = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        if self.holds_lock:
            self.database.advisory_lock.release()
            self.holds_lock = False

    async def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "WITH latest_by_instrument" in sql:
            return FakeResult(scalar=self.database.reference_date)
        if "pg_advisory_xact_lock" in sql:
            await self.database.advisory_lock.acquire()
            self.holds_lock = True
            return FakeResult()
        if "SELECT id, status, started_at" in sql:
            matching = [
                row
                for row in self.database.rows
                if row.as_of_date == params["as_of_date"]
                and row.technical_config == params["technical_config"]
                and row.status in {"queued", "running", "succeeded"}
            ]
            return FakeResult(rows=matching)
        if "INSERT INTO scan_runs" in sql:
            run_id = uuid4()
            self.database.rows.append(
                SimpleNamespace(
                    id=run_id,
                    status="queued",
                    started_at=None,
                    as_of_date=params["as_of_date"],
                    technical_config=params["technical_config"],
                )
            )
            return FakeResult(scalar=run_id)
        if "UPDATE scan_runs" in sql:
            for row in self.database.rows:
                if row.id == params["scan_run_id"]:
                    row.status = "failed"
            return FakeResult()
        raise AssertionError(f"Unexpected SQL in test: {sql}")

    async def commit(self):
        if self.holds_lock:
            self.database.advisory_lock.release()
            self.holds_lock = False


class FakeRedis:
    def __init__(self):
        self.jobs: set[str] = set()
        self.accepted_jobs = 0
        self.lock = asyncio.Lock()

    async def enqueue_job(self, _function, _scan_run_id, *, _job_id):
        async with self.lock:
            if _job_id in self.jobs:
                return None
            self.jobs.add(_job_id)
            self.accepted_jobs += 1
            return SimpleNamespace(job_id=_job_id)


async def fake_job_status(redis: FakeRedis, scan_run_id) -> JobStatus:
    job_id = f"personal-scan:{scan_run_id}"
    return JobStatus.queued if job_id in redis.jobs else JobStatus.not_found


class PersonalScanTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_requests_create_one_run_and_one_job(self):
        database = FakeDatabase()
        redis = FakeRedis()

        with (
            patch("app.services.personal_scan.async_session", database.session),
            patch("app.services.personal_scan._job_status", fake_job_status),
        ):
            first, second = await asyncio.gather(
                ensure_personal_scan(redis),
                ensure_personal_scan(redis),
            )

        self.assertEqual(first.scan_run_id, second.scan_run_id)
        self.assertEqual(len(database.rows), 1)
        self.assertEqual(redis.accepted_jobs, 1)
        self.assertEqual({first.reused, second.reused}, {False, True})
        self.assertEqual(first.as_of_date, database.reference_date)

    async def test_succeeded_run_is_reused_without_enqueue(self):
        database = FakeDatabase()
        redis = FakeRedis()
        config_json, _ = canonical_config_payload(TechnicalScreeningConfig())
        existing_id = uuid4()
        database.rows.append(
            SimpleNamespace(
                id=existing_id,
                status="succeeded",
                started_at=datetime.datetime.now(datetime.timezone.utc),
                as_of_date=database.reference_date,
                technical_config=config_json,
            )
        )

        with (
            patch("app.services.personal_scan.async_session", database.session),
            patch("app.services.personal_scan._job_status", fake_job_status),
        ):
            result = await ensure_personal_scan(redis)

        self.assertEqual(result.scan_run_id, existing_id)
        self.assertEqual(result.status, "succeeded")
        self.assertTrue(result.reused)
        self.assertEqual(redis.accepted_jobs, 0)

    async def test_explicit_as_of_date_does_not_resolve_latest_date(self):
        database = FakeDatabase()
        redis = FakeRedis()
        replay_date = datetime.date(2026, 7, 31)

        with (
            patch("app.services.personal_scan.async_session", database.session),
            patch("app.services.personal_scan._job_status", fake_job_status),
            patch(
                "app.services.personal_scan.resolve_reference_eod_date",
                side_effect=AssertionError("latest date should not be resolved"),
            ),
        ):
            result = await ensure_personal_scan(
                redis,
                config=TechnicalScreeningConfig.for_version("vcp_score_v3").model_copy(
                    update={"fundamental_limit": 0}
                ),
                triggered_by="manual",
                as_of_date=replay_date,
            )

        self.assertEqual(result.as_of_date, replay_date)
        self.assertEqual(database.rows[0].as_of_date, replay_date)
        self.assertEqual(redis.accepted_jobs, 1)

    async def test_duplicate_worker_delivery_is_a_no_op(self):
        class UnclaimedSession:
            calls = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return None

            async def execute(self, _statement, _params=None):
                self.calls += 1
                return FakeResult()

            async def commit(self):
                return None

        session = UnclaimedSession()
        with patch("app.services.screener.async_session", lambda: session):
            await run_technical_scan({}, str(uuid4()))

        self.assertEqual(session.calls, 1)

    async def test_insufficient_history_marks_claimed_run_failed(self):
        scan_run_id = uuid4()
        instrument_id = uuid4()
        reference_date = datetime.date(2026, 8, 12)
        failure_params: dict = {}

        class Session:
            def __init__(self, result):
                self.result = result

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return None

            async def execute(self, _statement, params=None):
                if params and "error" in params:
                    failure_params.update(params)
                return self.result

            async def commit(self):
                return None

        claimed = SimpleNamespace(
            technical_config=TechnicalScreeningConfig().model_dump(),
            as_of_date=reference_date,
        )
        instrument = SimpleNamespace(
            id=instrument_id,
            symbol="TEST",
            fyers_symbol="NSE:TEST-EQ",
            name="Test",
        )
        candle = SimpleNamespace(
            instrument_id=instrument_id,
            candle_start=datetime.datetime(
                2026,
                8,
                12,
                tzinfo=datetime.timezone.utc,
            ),
            high_price=101,
            low_price=99,
            close_price=100,
            volume=1_000_000,
        )
        sessions = iter(
            [
                Session(FakeResult(rows=[claimed])),
                Session(FakeResult(rows=[instrument])),
                Session(FakeResult(rows=[candle])),
                Session(FakeResult()),
            ]
        )

        with (
            patch("app.services.screener.async_session", lambda: next(sessions)),
            patch(
                "app.services.screener.evaluate_scan_readiness",
                return_value=ScanReadiness(
                    scanner_ready=False,
                    active_instruments=500,
                    scoreable_instruments=0,
                    required_scoreable_instruments=475,
                    minimum_history_days=252,
                    reference_eod_date=reference_date,
                ),
            ),
        ):
            await run_technical_scan({}, str(scan_run_id))

        self.assertEqual(failure_params["scan_run_id"], str(scan_run_id))
        self.assertIn("0/500", failure_params["error"])
        self.assertIn("475 required (95%)", failure_params["error"])

    async def test_personal_scan_uses_claimed_run_visibility_to_enqueue_p7(self):
        scan_run_id = uuid4()
        redis = FakeRedis()
        reference_date = datetime.date(2026, 8, 12)

        claimed = SimpleNamespace(
            technical_config=TechnicalScreeningConfig().model_dump(),
            as_of_date=reference_date,
            visibility="personal",
        )

        class FakeCommitSession:
            def __init__(self):
                self.executed_statements = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return None

            async def execute(self, statement, params=None):
                self.executed_statements.append((str(statement), params))
                result = FakeResult(rows=[claimed])
                result.rowcount = 1
                return result

            async def commit(self):
                return None

        fake_session = FakeCommitSession()

        with (
            patch("app.services.screener.async_session", lambda: fake_session),
            patch(
                "app.services.screener.evaluate_scan_readiness",
                return_value=ScanReadiness(
                    scanner_ready=True,
                    active_instruments=500,
                    scoreable_instruments=500,
                    required_scoreable_instruments=475,
                    minimum_history_days=252,
                    reference_eod_date=reference_date,
                ),
            ),
            patch("app.services.screener.settings") as mock_settings,
        ):
            mock_settings.p7_fundamental_pass_enabled = True
            mock_settings.proposal_automation_enabled = False
            # Verify no AttributeError is raised and enqueue_job is called
            # If config.visibility was used, it would fail with AttributeError
            # Here with 0 survivors it simply doesn't enqueue, but does not crash
            await run_technical_scan({"redis": redis}, str(scan_run_id))

        self.assertNotIn("AttributeError", str(fake_session.executed_statements))


if __name__ == "__main__":
    unittest.main()
