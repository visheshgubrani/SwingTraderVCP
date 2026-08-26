import datetime as dt
import unittest
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.domain.p10_triggers import BREAKOUT_BAR_SIGNAL_POLICY_V2
from app.services.execution_engine import ExecutionBlockedError
from app.workers.entry_supervisor import (
    ConfirmedLeg,
    _attempt_confirmed_allocation,
    _entry_rejection_outcome,
    _load_volume_profile,
    _record_entry_eligibility,
    handle_five_minute_bar_event,
)


IST = ZoneInfo("Asia/Kolkata")


class FakeResult:
    def __init__(self, *, scalar=None, rows=None, mapping=None):
        self.scalar = scalar
        self.rows = rows or []
        self.mapping = mapping

    def scalar_one_or_none(self):
        return self.scalar

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def one_or_none(self):
        return self.mapping


class FakeSupervisorSession:
    def __init__(self, *, leg, signal_row=None):
        self.leg = leg
        self.signal_row = signal_row
        self.calls: list[tuple[str, dict]] = []

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def execute(self, statement, params=None):
        sql = str(statement)
        payload = params or {}
        self.calls.append((sql, payload))
        if "SELECT reconciliation_status" in sql:
            return FakeResult(scalar="verified")
        if "SELECT el.id AS leg_id" in sql:
            return FakeResult(rows=[self.leg])
        if "FROM volume_profiles" in sql:
            return FakeResult(
                mapping={
                    "adv20_robust": 1_000_000,
                    "sessions_used": 20,
                    "bucket_medians": [
                        {"time": "09:50", "cumulative_fraction": "0.240000"},
                        {"time": "09:55", "cumulative_fraction": "0.270000"},
                        {"time": "10:00", "cumulative_fraction": "0.300000"},
                        {"time": "10:05", "cumulative_fraction": "0.330000"},
                    ],
                }
            )
        if "SELECT volume" in sql and "LIMIT 12" in sql:
            return FakeResult(rows=[(10_000,)] * 12)
        if "FROM trigger_events" in sql and "bar_type = 'signal_bar'" in sql:
            return FakeResult(mapping=self.signal_row)
        return FakeResult()

    async def commit(self):
        return None


class FakeEligibilitySession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params or {}))
        if "SELECT tp.entry_trigger_policy_version" in sql:
            return FakeResult(scalar=BREAKOUT_BAR_SIGNAL_POLICY_V2)
        return FakeResult()

    async def commit(self):
        return None


class FakeProfileSession:
    def __init__(self, points, *, as_of_date=None, latest_verified_session=None):
        self.points = points
        self.as_of_date = as_of_date
        self.latest_verified_session = latest_verified_session

    async def execute(self, *_args, **_kwargs):
        return FakeResult(
            mapping={
                "adv20_robust": 1_000_000,
                "sessions_used": 20,
                "bucket_medians": self.points,
                "as_of_date": self.as_of_date,
                "latest_verified_session": self.latest_verified_session,
            }
        )


def leg_row(*, status: str):
    return {
        "leg_id": uuid4(),
        "proposal_id": uuid4(),
        "leg_index": 1,
        "risk_allocation_pct": Decimal("1"),
        "status": status,
        "trigger_price": Decimal("505"),
        "chase_ceiling": Decimal("515"),
        "relative_volume_threshold": Decimal("2"),
        "effective_stop": Decimal("480"),
        "confidence": Decimal("80"),
        "t1": Decimal("545"),
        "entry_trigger_policy_version": BREAKOUT_BAR_SIGNAL_POLICY_V2,
        "last_trigger_event_timestamp": None,
        "last_trigger_outcome": None,
        "last_entry_eligibility_outcome": None,
        "technical_score": Decimal("90"),
    }


def bar_payload(*, at: dt.time, close: int, volume: int, cumulative: int):
    timestamp = dt.datetime.combine(dt.date(2026, 8, 25), at, tzinfo=IST)
    return {
        "symbol": "NSE:TEST-EQ",
        "bar_time": timestamp.isoformat(),
        "open": "504",
        "high": "512",
        "low": "503",
        "close": str(close),
        "volume": volume,
        "cumulative_volume": cumulative,
    }


class BreakoutEntrySupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_persisted_unknown_submission_keeps_eligible_audit(self):
        candidate = ConfirmedLeg(
            leg_id=uuid4(),
            proposal_id=uuid4(),
            symbol="NSE:TEST-EQ",
            leg_index=1,
            risk_allocation_pct=Decimal("1"),
            trigger_price=Decimal("505"),
            chase_ceiling=Decimal("515"),
            initial_stop=Decimal("480"),
            scanner_score=Decimal("90"),
            confidence=Decimal("80"),
            conservative_rr=Decimal("1.5"),
            bar_time=dt.datetime(2026, 8, 25, 10, 5, tzinfo=IST),
        )
        with (
            patch(
                "app.workers.entry_supervisor.execute_confirmed_leg_allocation",
                return_value=False,
            ),
            patch(
                "app.workers.entry_supervisor._leg_has_persisted_intent",
                return_value=True,
            ),
            patch(
                "app.workers.entry_supervisor._record_entry_eligibility"
            ) as record,
        ):
            submitted = await _attempt_confirmed_allocation(object(), candidate)

        self.assertFalse(submitted)
        record.assert_awaited_once_with(candidate, outcome="eligible")

    async def test_stale_profile_is_blocked_against_latest_verified_session(self):
        session = FakeProfileSession(
            [
                {"time": "10:00", "cumulative_fraction": "0.300000"},
            ],
            as_of_date=dt.date(2026, 8, 21),
            latest_verified_session=dt.date(2026, 8, 24),
        )
        with self.assertRaisesRegex(ExecutionBlockedError, "stale"):
            await _load_volume_profile(
                session,
                symbol="NSE:TEST-EQ",
                bar_time=dt.datetime(2026, 8, 25, 10, 0, tzinfo=IST),
            )

    async def test_v2_profile_requires_adjacent_previous_bucket(self):
        session = FakeProfileSession(
            [
                {"time": "09:55", "cumulative_fraction": "0.270000"},
                {"time": "10:05", "cumulative_fraction": "0.330000"},
            ]
        )
        with self.assertRaisesRegex(
            ExecutionBlockedError,
            "adjacent previous bucket",
        ):
            await _load_volume_profile(
                session,
                symbol="NSE:TEST-EQ",
                bar_time=dt.datetime(2026, 8, 25, 10, 5, tzinfo=IST),
                require_bar_fraction=True,
            )

    async def test_weak_price_crossing_signal_waits_for_reset_and_is_audited(self):
        session = FakeSupervisorSession(leg=leg_row(status="armed"))
        with patch(
            "app.workers.entry_supervisor.async_session",
            session,
        ):
            confirmed = await handle_five_minute_bar_event(
                bar_payload(
                    at=dt.time(10, 0),
                    close=508,
                    volume=30_000,
                    cumulative=300_000,
                )
            )

        self.assertEqual(confirmed, [])
        event = next(
            params
            for sql, params in session.calls
            if "INSERT INTO trigger_events" in sql
        )
        self.assertEqual(event["bar_rvol"], Decimal("1.0000"))
        self.assertEqual(event["session_rvol"], Decimal("1.0000"))
        self.assertEqual(event["base_ratio"], Decimal("3.0000"))
        self.assertEqual(event["trigger_outcome"], "signal_rejected")
        transition = next(
            params
            for sql, params in session.calls
            if "status = 'waiting_for_reset'" in sql
        )
        self.assertEqual(transition["leg_id"], session.leg["leg_id"])

    async def test_waiting_leg_only_rearms_on_verified_close_at_trigger(self):
        session = FakeSupervisorSession(leg=leg_row(status="waiting_for_reset"))
        with patch(
            "app.workers.entry_supervisor.async_session",
            session,
        ):
            await handle_five_minute_bar_event(
                bar_payload(
                    at=dt.time(10, 0),
                    close=505,
                    volume=30_000,
                    cumulative=300_000,
                )
            )

        reset_event = next(
            params
            for sql, params in session.calls
            if "INSERT INTO trigger_events" in sql
        )
        self.assertEqual(reset_event["bar_type"], "reset_bar")
        self.assertEqual(reset_event["trigger_outcome"], "reset")
        self.assertTrue(
            any(
                "SET status = 'armed'" in sql
                and "waiting_for_reset" in sql
                for sql, _ in session.calls
            )
        )

    async def test_waiting_leg_ignores_later_above_trigger_volume(self):
        session = FakeSupervisorSession(leg=leg_row(status="waiting_for_reset"))
        with patch(
            "app.workers.entry_supervisor.async_session",
            session,
        ):
            await handle_five_minute_bar_event(
                bar_payload(
                    at=dt.time(10, 5),
                    close=509,
                    volume=120_000,
                    cumulative=420_000,
                )
            )

        self.assertFalse(
            any("INSERT INTO trigger_events" in sql for sql, _ in session.calls)
        )
        self.assertFalse(
            any("UPDATE entry_legs" in sql for sql, _ in session.calls)
        )

    async def test_replay_before_durable_reset_cannot_reject_rearmed_leg(self):
        leg = leg_row(status="armed")
        leg["last_trigger_event_timestamp"] = dt.datetime(
            2026, 8, 25, 10, 5, tzinfo=IST
        )
        session = FakeSupervisorSession(leg=leg)
        with patch("app.workers.entry_supervisor.async_session", session):
            confirmed = await handle_five_minute_bar_event(
                bar_payload(
                    at=dt.time(10, 0),
                    close=508,
                    volume=30_000,
                    cumulative=300_000,
                )
            )

        self.assertEqual(confirmed, [])
        self.assertFalse(
            any("INSERT INTO trigger_events" in sql for sql, _ in session.calls)
        )
        self.assertFalse(
            any("UPDATE entry_legs" in sql for sql, _ in session.calls)
        )

    async def test_duplicate_confirmed_bar_delivery_emits_no_second_candidate(self):
        confirmation_time = dt.datetime(2026, 8, 25, 10, 5, tzinfo=IST)
        leg = leg_row(status="trigger_observed")
        leg["last_trigger_event_timestamp"] = confirmation_time
        session = FakeSupervisorSession(leg=leg)
        with patch("app.workers.entry_supervisor.async_session", session):
            confirmed = await handle_five_minute_bar_event(
                bar_payload(
                    at=dt.time(10, 5),
                    close=509,
                    volume=10_000,
                    cumulative=310_000,
                )
            )

        self.assertEqual(confirmed, [])
        self.assertFalse(
            any("INSERT INTO trigger_events" in sql for sql, _ in session.calls)
        )

    async def test_later_bar_cannot_replace_pending_durable_confirmation(self):
        leg = leg_row(status="trigger_observed")
        leg["last_trigger_event_timestamp"] = dt.datetime(
            2026, 8, 25, 10, 5, tzinfo=IST
        )
        leg["last_trigger_outcome"] = "confirmed"
        leg["last_entry_eligibility_outcome"] = "pending"
        session = FakeSupervisorSession(leg=leg)
        with patch("app.workers.entry_supervisor.async_session", session):
            confirmed = await handle_five_minute_bar_event(
                bar_payload(
                    at=dt.time(10, 10),
                    close=504,
                    volume=10_000,
                    cumulative=320_000,
                )
            )

        self.assertEqual(confirmed, [])
        self.assertFalse(
            any("INSERT INTO trigger_events" in sql for sql, _ in session.calls)
        )
        self.assertFalse(
            any("UPDATE entry_legs" in sql for sql, _ in session.calls)
        )

    async def test_quiet_confirmation_confirms_price_without_volume_gate(self):
        signal_time = dt.datetime(
            2026, 8, 25, 10, 0, tzinfo=IST
        )
        session = FakeSupervisorSession(
            leg=leg_row(status="trigger_observed"),
            signal_row={
                "bar_timestamp": signal_time,
                "bar_open": Decimal("500"),
                "bar_high": Decimal("512"),
                "bar_low": Decimal("499"),
                "bar_close": Decimal("508"),
                "bar_volume": 120_000,
                "cumulative_volume": 300_000,
            },
        )
        with patch(
            "app.workers.entry_supervisor.async_session",
            session,
        ):
            confirmed = await handle_five_minute_bar_event(
                bar_payload(
                    at=dt.time(10, 5),
                    close=509,
                    volume=10_000,
                    cumulative=310_000,
                )
            )

        self.assertEqual(len(confirmed), 1)
        event = next(
            params
            for sql, params in session.calls
            if "INSERT INTO trigger_events" in sql
        )
        self.assertEqual(event["bar_rvol"], Decimal("0.3333"))
        self.assertIsNone(event["volume_gate_passed"])
        self.assertEqual(event["trigger_outcome"], "confirmed")
        self.assertEqual(event["entry_eligibility_outcome"], "pending")

    async def test_replay_does_not_treat_signal_bar_as_its_confirmation(self):
        signal_time = dt.datetime(2026, 8, 25, 10, 0, tzinfo=IST)
        session = FakeSupervisorSession(
            leg=leg_row(status="trigger_observed"),
            signal_row={
                "bar_timestamp": signal_time,
                "bar_open": Decimal("500"),
                "bar_high": Decimal("512"),
                "bar_low": Decimal("499"),
                "bar_close": Decimal("508"),
                "bar_volume": 120_000,
                "cumulative_volume": 300_000,
            },
        )
        with patch(
            "app.workers.entry_supervisor.async_session",
            session,
        ):
            confirmed = await handle_five_minute_bar_event(
                bar_payload(
                    at=dt.time(10, 0),
                    close=508,
                    volume=120_000,
                    cumulative=300_000,
                )
            )

        self.assertEqual(confirmed, [])
        self.assertFalse(
            any("INSERT INTO trigger_events" in sql for sql, _ in session.calls)
        )
        self.assertFalse(
            any("UPDATE entry_legs" in sql for sql, _ in session.calls)
        )

    async def test_failed_confirmation_enters_waiting_for_reset(self):
        signal_time = dt.datetime(2026, 8, 25, 10, 0, tzinfo=IST)
        session = FakeSupervisorSession(
            leg=leg_row(status="trigger_observed"),
            signal_row={
                "bar_timestamp": signal_time,
                "bar_open": Decimal("500"),
                "bar_high": Decimal("512"),
                "bar_low": Decimal("499"),
                "bar_close": Decimal("508"),
                "bar_volume": 120_000,
                "cumulative_volume": 300_000,
            },
        )
        with patch(
            "app.workers.entry_supervisor.async_session",
            session,
        ):
            confirmed = await handle_five_minute_bar_event(
                bar_payload(
                    at=dt.time(10, 5),
                    close=504,
                    volume=10_000,
                    cumulative=310_000,
                )
            )

        self.assertEqual(confirmed, [])
        event = next(
            params
            for sql, params in session.calls
            if "INSERT INTO trigger_events" in sql
        )
        self.assertEqual(event["trigger_outcome"], "confirmation_rejected")
        transition = next(
            params
            for sql, params in session.calls
            if "SET status = :status" in sql
        )
        self.assertEqual(transition["status"], "waiting_for_reset")

    async def test_chase_rejection_preserves_trigger_and_requires_reset(self):
        self.assertEqual(
            _entry_rejection_outcome(
                ExecutionBlockedError(
                    "Fresh price exceeds the immutable chase ceiling."
                )
            ),
            "rejected_chase",
        )
        session = FakeEligibilitySession()
        confirmed = ConfirmedLeg(
            leg_id=uuid4(),
            proposal_id=uuid4(),
            symbol="NSE:TEST-EQ",
            leg_index=1,
            risk_allocation_pct=Decimal("1"),
            trigger_price=Decimal("505"),
            chase_ceiling=Decimal("515"),
            initial_stop=Decimal("480"),
            scanner_score=Decimal("90"),
            confidence=Decimal("80"),
            conservative_rr=Decimal("2"),
            bar_time=dt.datetime(2026, 8, 25, 10, 5, tzinfo=IST),
        )
        with patch(
            "app.workers.entry_supervisor.async_session",
            session,
        ):
            await _record_entry_eligibility(
                confirmed,
                outcome="rejected_chase",
                reason="Fresh price exceeds the immutable chase ceiling.",
            )

        audit = next(
            params
            for sql, params in session.calls
            if "UPDATE trigger_events" in sql
        )
        self.assertEqual(audit["outcome"], "rejected_chase")
        transition = next(
            params
            for sql, params in session.calls
            if "UPDATE entry_legs" in sql
        )
        self.assertEqual(transition["status"], "waiting_for_reset")


if __name__ == "__main__":
    unittest.main()
