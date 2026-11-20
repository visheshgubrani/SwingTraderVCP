"""balanced_breakout_v4, multi-session entry window, end-of-session retirement,
paper-submission failure cleanup, and volume-profile robustness."""

import datetime as dt
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.domain.p10_sizing import breakout_bar_rvol_threshold
from app.domain.p10_triggers import (
    BALANCED_BREAKOUT_POLICY_V3,
    BALANCED_BREAKOUT_POLICY_V4,
    IST_TZ,
    FiveMinuteBar,
    add_nse_sessions,
    evaluate_buy_zone_breakout_v4,
    initial_entry_window_end,
)
from app.services.execution_engine import initial_leg_within_entry_window
from app.services.intraday_bar_reconciliation import build_volume_profile


class FakeResult:
    def __init__(self, row=None, rows=None, scalar=None):
        self.row = row
        self.rows = rows or []
        self._scalar = scalar

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row

    def all(self):
        return self.rows

    def first(self):
        return self.row

    def scalar_one_or_none(self):
        return self._scalar


def _session_cm(db):
    cm = AsyncMock()
    cm.__aenter__.return_value = db
    cm.__aexit__.return_value = False
    return cm


def _bar(hour: int, minute: int, *, close: str, volume: int, cumulative: int) -> FiveMinuteBar:
    return FiveMinuteBar(
        bar_time=dt.datetime(2026, 11, 23, hour, minute, tzinfo=IST_TZ),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=volume,
        cumulative_volume=cumulative,
    )


class BuyZoneBreakoutV4Tests(unittest.TestCase):
    # ADV20 100_000; bucket expects 1% (1_000 shares); cumulative expects 20%.
    COMMON = dict(
        trigger_price=Decimal("100"),
        chase_ceiling=Decimal("102"),
        adv20_robust=100_000,
        expected_bar_fraction=Decimal("0.01"),
        expected_cumulative_fraction=Decimal("0.20"),
    )

    def test_bar_branch_triggers(self):
        result = evaluate_buy_zone_breakout_v4(
            signal_bar=_bar(10, 0, close="101", volume=1_300, cumulative=10_000), **self.COMMON
        )
        self.assertTrue(result.is_triggered)
        self.assertEqual(result.volume_branch, "bar")

    def test_session_branch_triggers_when_bar_is_quiet(self):
        result = evaluate_buy_zone_breakout_v4(
            signal_bar=_bar(10, 0, close="101", volume=500, cumulative=25_000), **self.COMMON
        )
        self.assertTrue(result.is_triggered)
        self.assertEqual(result.volume_branch, "session")

    def test_both_volume_branches_failing_rejects(self):
        result = evaluate_buy_zone_breakout_v4(
            signal_bar=_bar(10, 0, close="101", volume=500, cumulative=20_000), **self.COMMON
        )
        self.assertFalse(result.is_triggered)
        self.assertEqual(result.outcome, "volume_insufficient")

    def test_close_above_chase_is_extended_not_triggered(self):
        result = evaluate_buy_zone_breakout_v4(
            signal_bar=_bar(10, 0, close="102.05", volume=9_000, cumulative=90_000), **self.COMMON
        )
        self.assertFalse(result.is_triggered)
        self.assertEqual(result.outcome, "extended_above_chase")

    def test_close_at_chase_is_inside_zone(self):
        result = evaluate_buy_zone_breakout_v4(
            signal_bar=_bar(10, 0, close="102", volume=9_000, cumulative=90_000), **self.COMMON
        )
        self.assertTrue(result.is_triggered)

    def test_first_fifteen_minutes_never_trigger(self):
        result = evaluate_buy_zone_breakout_v4(
            signal_bar=_bar(9, 25, close="101", volume=9_000, cumulative=90_000), **self.COMMON
        )
        self.assertEqual(result.outcome, "outside_window")

    def test_close_at_or_below_trigger(self):
        result = evaluate_buy_zone_breakout_v4(
            signal_bar=_bar(10, 0, close="100", volume=9_000, cumulative=90_000), **self.COMMON
        )
        self.assertEqual(result.outcome, "below_trigger")

    def test_missing_bar_fraction_falls_back_to_session_branch_only(self):
        params = {**self.COMMON, "expected_bar_fraction": None}
        quiet_session = evaluate_buy_zone_breakout_v4(
            signal_bar=_bar(15, 15, close="101", volume=50_000, cumulative=20_000), **params
        )
        self.assertFalse(quiet_session.is_triggered)
        self.assertIsNone(quiet_session.bar_rvol)
        busy_session = evaluate_buy_zone_breakout_v4(
            signal_bar=_bar(15, 15, close="101", volume=10, cumulative=25_000), **params
        )
        self.assertTrue(busy_session.is_triggered)
        self.assertEqual(busy_session.volume_branch, "session")


class EntryWindowTests(unittest.TestCase):
    def test_v4_window_spans_five_sessions_skipping_weekends_and_holidays(self):
        d1 = dt.date(2026, 11, 20)  # Friday
        holidays = frozenset({dt.date(2026, 11, 24)})
        end = initial_entry_window_end(d1, BALANCED_BREAKOUT_POLICY_V4, holidays)
        # Fri 20, Mon 23, (Tue 24 holiday), Wed 25, Thu 26, Fri 27
        self.assertEqual(end, dt.date(2026, 11, 27))

    def test_pre_v4_policies_stay_d1_only(self):
        d1 = dt.date(2026, 11, 20)
        self.assertEqual(
            initial_entry_window_end(d1, BALANCED_BREAKOUT_POLICY_V3, frozenset()), d1
        )

    def test_add_nse_sessions_zero_is_identity(self):
        day = dt.date(2026, 11, 20)
        self.assertEqual(add_nse_sessions(day, 0, frozenset()), day)

    def test_initial_leg_window_guard(self):
        d1 = dt.date(2026, 11, 20)
        d5 = dt.date(2026, 11, 26)
        self.assertTrue(initial_leg_within_entry_window(dt.date(2026, 11, 24), entry_session_date=d1, eligible_session_end=d5))
        self.assertFalse(initial_leg_within_entry_window(dt.date(2026, 11, 27), entry_session_date=d1, eligible_session_end=d5))
        self.assertFalse(initial_leg_within_entry_window(dt.date(2026, 11, 19), entry_session_date=d1, eligible_session_end=d5))
        # Legacy rows without an eligible end stay D1-only.
        self.assertTrue(initial_leg_within_entry_window(d1, entry_session_date=d1, eligible_session_end=None))
        self.assertFalse(initial_leg_within_entry_window(dt.date(2026, 11, 23), entry_session_date=d1, eligible_session_end=None))

    def test_v4_uses_one_bar_threshold_and_older_policies_keep_ladder(self):
        self.assertEqual(breakout_bar_rvol_threshold("single", BALANCED_BREAKOUT_POLICY_V4), Decimal("1.25"))
        self.assertEqual(breakout_bar_rvol_threshold("two_leg", BALANCED_BREAKOUT_POLICY_V4), Decimal("1.25"))
        self.assertEqual(breakout_bar_rvol_threshold("single", BALANCED_BREAKOUT_POLICY_V3), Decimal("1.75"))
        self.assertEqual(breakout_bar_rvol_threshold("two_leg", BALANCED_BREAKOUT_POLICY_V3), Decimal("1.50"))


class VolumeProfileMonotonicTests(unittest.TestCase):
    def test_cumulative_medians_never_decrease(self):
        # Sessions alternate between a full 75-bar day and a 74-bar day that
        # is missing 15:20, which skews the per-label medians around it.
        candles = []
        start_day = dt.date(2026, 9, 1)
        sessions = []
        day = start_day
        while len(sessions) < 30:
            if day.weekday() < 5:
                sessions.append(day)
            day += dt.timedelta(days=1)
        for index, session in enumerate(sessions):
            open_at = dt.datetime.combine(session, dt.time(9, 15), tzinfo=IST_TZ)
            for bucket in range(75):
                ts = open_at + dt.timedelta(minutes=5 * bucket)
                if index % 2 and ts.time() == dt.time(15, 20):
                    continue
                volume = 10_000 if ts.time() >= dt.time(15, 15) and index % 2 else 100
                candles.append([int(ts.timestamp()), 1, 1, 1, 1, volume])
        profile = build_volume_profile(candles, evaluation_session_date=day)
        self.assertIsNotNone(profile)
        _, _, points, _ = profile
        fractions = [Decimal(point["cumulative_fraction"]) for point in points]
        self.assertEqual(fractions, sorted(fractions))


class PaperSubmissionFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_crash_after_claim_rejects_intent_releases_leg_and_alerts(self):
        from app.services.execution_engine import _complete_paper_submission

        intent_id = uuid4()
        snapshot = {
            "id": intent_id,
            "symbol": "NSE:FEDERALBNK-EQ",
            "intent_type": "entry",
            "position_id": uuid4(),
            "entry_leg_id": uuid4(),
            "entry_leg_index": 1,
            "trade_instruction_id": None,
        }
        statements: list[str] = []

        async def fake_execute(statement, params=None):
            sql = str(statement)
            statements.append(sql)
            return FakeResult(row={"id": intent_id})

        db = AsyncMock()
        db.execute.side_effect = fake_execute
        paper_result = AsyncMock()
        with (
            patch("app.services.paper_broker.place_paper_order", new=AsyncMock(return_value=paper_result)),
            patch(
                "app.services.order_gateway.process_order_message",
                new=AsyncMock(side_effect=RuntimeError("could not determine data type of parameter $2")),
            ),
        ):
            result = await _complete_paper_submission(
                db, AsyncMock(), snapshot=snapshot, fill_price=Decimal("367.3")
            )

        self.assertEqual(result.outcome, "rejected")
        db.rollback.assert_awaited()
        joined = "\n".join(statements)
        self.assertIn("status = 'rejected'", joined)
        self.assertIn("UPDATE entry_legs el", joined)
        self.assertIn("INSERT INTO system_events", joined)
        db.commit.assert_awaited()


class EndOfSessionRetirementTests(unittest.IsolatedAsyncioTestCase):
    def _row(self, *, status, mode, created_at, has_fill=False, open_quantity=0):
        return {
            "id": uuid4(),
            "status": status,
            "execution_mode": mode,
            "created_at": created_at,
            "quantity": 203,
            "position_id": uuid4(),
            "entry_leg_id": uuid4(),
            "entry_leg_index": 1,
            "proposal_id": uuid4(),
            "symbol": "NSE:FEDERALBNK-EQ",
            "open_quantity": open_quantity,
            "has_fill": has_fill,
            "has_paper_fill": False,
        }

    async def test_retirement_matrix(self):
        from app.workers.entry_supervisor import retire_unfilled_entry_intents

        yesterday = dt.datetime(2026, 11, 16, 10, 0, tzinfo=IST_TZ)
        today = dt.datetime(2026, 11, 20, 10, 0, tzinfo=IST_TZ)
        paper_stuck = self._row(status="submission_pending", mode="paper", created_at=yesterday)
        live_never_sent = self._row(status="created", mode="live", created_at=yesterday)
        live_unknown = self._row(status="submission_unknown", mode="live", created_at=yesterday)
        same_session = self._row(status="submission_pending", mode="paper", created_at=today)
        partially_filled = self._row(
            status="submitted", mode="paper", created_at=yesterday, has_fill=True, open_quantity=50
        )
        rows = [paper_stuck, live_never_sent, live_unknown, same_session, partially_filled]
        calls: list[tuple[str, dict]] = []

        async def fake_execute(statement, params=None):
            sql = str(statement)
            calls.append((sql, params or {}))
            if "FROM order_intents oi" in sql and "JOIN entry_legs el" in sql:
                return FakeResult(rows=rows)
            return FakeResult(row={"id": uuid4()})

        db = AsyncMock()
        db.execute.side_effect = fake_execute
        cancelled_ids: list = []

        async def fake_cancel(db_arg, *, snapshot, reason, from_statuses):
            cancelled_ids.append((snapshot["id"], from_statuses))
            return True

        with (
            patch("app.workers.entry_supervisor.async_session", return_value=_session_cm(db)),
            patch("app.services.execution_engine.cancel_unsent_entry_intent", new=fake_cancel),
        ):
            # Mid-session today: yesterday's entries are past their window
            # close, today's are still live.
            retired = await retire_unfilled_entry_intents(
                now_ist=dt.datetime(2026, 11, 20, 15, 0, tzinfo=IST_TZ)
            )

        self.assertEqual(retired, 2)
        by_id = dict(cancelled_ids)
        self.assertIn(paper_stuck["id"], by_id)
        self.assertIn("submission_pending", by_id[paper_stuck["id"]])
        self.assertEqual(by_id[live_never_sent["id"]], ("created",))
        self.assertNotIn(live_unknown["id"], by_id)
        self.assertNotIn(same_session["id"], by_id)
        self.assertNotIn(partially_filled["id"], by_id)
        paper_order_cancels = [
            params for sql, params in calls if "UPDATE paper_broker_orders" in sql
        ]
        self.assertEqual([p["intent_id"] for p in paper_order_cancels], [paper_stuck["id"]])
        critical = [
            params for sql, params in calls
            if "INSERT INTO system_events" in sql and params.get("severity") == "critical"
        ]
        self.assertEqual(len(critical), 1)
        self.assertEqual(critical[0]["order_intent_id"], live_unknown["id"])
        db.commit.assert_awaited_once()

    async def test_nothing_retired_before_the_session_closes(self):
        from app.workers.entry_supervisor import retire_unfilled_entry_intents

        created = dt.datetime(2026, 11, 20, 10, 0, tzinfo=IST_TZ)
        row = self._row(status="submission_pending", mode="paper", created_at=created)
        db = AsyncMock()
        db.execute.return_value = FakeResult(rows=[row])
        with patch("app.workers.entry_supervisor.async_session", return_value=_session_cm(db)):
            retired = await retire_unfilled_entry_intents(
                now_ist=dt.datetime(2026, 11, 20, 15, 59, tzinfo=IST_TZ)
            )
        self.assertEqual(retired, 0)


class V4LegEvaluationTests(unittest.IsolatedAsyncioTestCase):
    def _leg(self, **overrides):
        leg = {
            "leg_id": uuid4(),
            "proposal_id": uuid4(),
            "leg_index": 1,
            "risk_allocation_pct": Decimal("1.0"),
            "status": "armed",
            "trigger_price": Decimal("100"),
            "chase_ceiling": Decimal("102"),
            "relative_volume_threshold": Decimal("1.25"),
            "effective_stop": Decimal("95"),
            "initial_stop": Decimal("95"),
            "confidence": Decimal("70"),
            "t1": Decimal("112"),
            "technical_score": Decimal("80"),
        }
        leg.update(overrides)
        return leg

    def _db(self):
        statements: list[str] = []

        async def fake_execute(statement, params=None):
            statements.append(str(statement))
            return FakeResult(row={"id": uuid4()})

        db = AsyncMock()
        db.execute.side_effect = fake_execute
        return db, statements

    def _profile(self):
        from app.workers.entry_supervisor import VolumeProfilePoint

        return VolumeProfilePoint(100_000, Decimal("0.20"), Decimal("0.01"))

    async def test_close_below_initial_stop_expires_setup(self):
        from app.workers.entry_supervisor import _evaluate_v4_leg_on_bar

        db, statements = self._db()
        result = await _evaluate_v4_leg_on_bar(
            db, leg=self._leg(), bar=_bar(11, 0, close="94.5", volume=10, cumulative=10), symbol="NSE:X-EQ"
        )
        self.assertIsNone(result)
        self.assertTrue(any("SET status = 'expired'" in sql for sql in statements))
        self.assertTrue(any("SET status = 'cancelled'" in sql for sql in statements))

    async def test_in_zone_session_volume_confirms(self):
        from app.workers.entry_supervisor import _evaluate_v4_leg_on_bar

        db, statements = self._db()
        with (
            patch("app.workers.entry_supervisor._try_load_volume_profile", new=AsyncMock(return_value=self._profile())),
            patch("app.workers.entry_supervisor._recent_base_volume_diagnostic", new=AsyncMock(return_value=(None, None))),
            patch("app.workers.entry_supervisor._persist_trigger_event", new=AsyncMock()) as persist,
        ):
            result = await _evaluate_v4_leg_on_bar(
                db, leg=self._leg(), bar=_bar(11, 0, close="101", volume=200, cumulative=30_000), symbol="NSE:X-EQ"
            )
        self.assertIsNotNone(result)
        self.assertEqual(persist.await_args.kwargs["trigger_outcome"], "confirmed")
        self.assertTrue(any("'trigger_observed'" in sql for sql in statements))

    async def test_extended_bar_is_audited_and_leg_stays_armed(self):
        from app.workers.entry_supervisor import _evaluate_v4_leg_on_bar

        db, statements = self._db()
        with (
            patch("app.workers.entry_supervisor._try_load_volume_profile", new=AsyncMock(return_value=self._profile())),
            patch("app.workers.entry_supervisor._recent_base_volume_diagnostic", new=AsyncMock(return_value=(None, None))),
            patch("app.workers.entry_supervisor._persist_trigger_event", new=AsyncMock()) as persist,
        ):
            result = await _evaluate_v4_leg_on_bar(
                db, leg=self._leg(), bar=_bar(11, 0, close="103", volume=9_000, cumulative=90_000), symbol="NSE:X-EQ"
            )
        self.assertIsNone(result)
        self.assertEqual(persist.await_args.kwargs["trigger_outcome"], "signal_rejected")
        self.assertFalse(any("'trigger_observed'" in sql for sql in statements))

    async def test_pre_0930_bar_skips_profile_entirely(self):
        from app.workers.entry_supervisor import _evaluate_v4_leg_on_bar

        db, _ = self._db()
        loader = AsyncMock()
        with patch("app.workers.entry_supervisor._try_load_volume_profile", new=loader):
            result = await _evaluate_v4_leg_on_bar(
                db, leg=self._leg(), bar=_bar(9, 15, close="101", volume=9_000, cumulative=9_000), symbol="NSE:X-EQ"
            )
        self.assertIsNone(result)
        loader.assert_not_awaited()


class V4EligibilityRearmTests(unittest.IsolatedAsyncioTestCase):
    async def _status_after(self, outcome: str) -> str:
        from app.workers.entry_supervisor import ConfirmedLeg, _record_entry_eligibility

        updates: list[dict] = []

        async def fake_execute(statement, params=None):
            sql = str(statement)
            if "SELECT tp.entry_trigger_policy_version" in sql:
                return FakeResult(scalar=BALANCED_BREAKOUT_POLICY_V4)
            if "UPDATE entry_legs" in sql:
                updates.append(params)
            return FakeResult()

        db = AsyncMock()
        db.execute.side_effect = fake_execute
        confirmed = ConfirmedLeg(
            leg_id=uuid4(), proposal_id=uuid4(), symbol="NSE:X-EQ", leg_index=1,
            risk_allocation_pct=Decimal("1"), trigger_price=Decimal("100"),
            chase_ceiling=Decimal("102"), initial_stop=Decimal("95"),
            scanner_score=Decimal("80"), confidence=Decimal("70"),
            conservative_rr=Decimal("2"), bar_time=dt.datetime(2026, 11, 23, 11, 0, tzinfo=IST_TZ),
        )
        with patch("app.workers.entry_supervisor.async_session", return_value=_session_cm(db)):
            await _record_entry_eligibility(confirmed, outcome=outcome, reason="x")
        return updates[-1]["status"]

    async def test_chase_and_reversal_rearm_directly(self):
        self.assertEqual(await self._status_after("rejected_chase"), "armed")
        self.assertEqual(await self._status_after("rejected_price_reversal"), "armed")

    async def test_capacity_waits_for_reset(self):
        self.assertEqual(await self._status_after("rejected_capacity"), "waiting_for_reset")


if __name__ == "__main__":
    unittest.main()
