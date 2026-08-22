"""P10 entry-window expiry: domain rule, supervisor sweep, and API derivation."""

import datetime as dt
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.domain.p10_triggers import (
    ENTRY_WINDOW_CLOSE_TIME,
    IST_TZ,
    entry_window_closed,
)
from app.routers.automation import _effective_leg_status, derive_proposal_entry_state


class FakeResult:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row

    def one(self):
        if self.row is None:
            raise AssertionError("Expected one row")
        return self.row

    def all(self):
        return self.rows


class EntryWindowClosedTests(unittest.TestCase):
    SESSION = dt.date(2026, 8, 21)

    def ist(self, day: int, hour: int, minute: int) -> dt.datetime:
        return dt.datetime(2026, 8, day, hour, minute, tzinfo=IST_TZ)

    def test_none_end_is_never_closed(self) -> None:
        self.assertFalse(entry_window_closed(None, self.ist(22, 9, 0)))

    def test_open_until_close_time_on_final_session(self) -> None:
        self.assertFalse(entry_window_closed(self.SESSION, self.ist(21, 15, 59)))

    def test_closed_at_and_after_close_time(self) -> None:
        self.assertTrue(entry_window_closed(self.SESSION, self.ist(21, 16, 0)))
        self.assertTrue(entry_window_closed(self.SESSION, self.ist(21, 16, 30)))
        self.assertTrue(entry_window_closed(self.SESSION, self.ist(21, 23, 59)))

    def test_closed_on_any_later_day(self) -> None:
        self.assertTrue(entry_window_closed(self.SESSION, self.ist(22, 9, 0)))

    def test_not_closed_before_final_session(self) -> None:
        self.assertFalse(entry_window_closed(self.SESSION, self.ist(20, 23, 59)))

    def test_naive_now_treated_as_ist(self) -> None:
        self.assertTrue(
            entry_window_closed(self.SESSION, dt.datetime(2026, 8, 21, 16, 0))
        )
        self.assertFalse(
            entry_window_closed(self.SESSION, dt.datetime(2026, 8, 21, 15, 59))
        )

    def test_utc_aware_now_converted(self) -> None:
        # 10:30 UTC == 16:00 IST
        self.assertTrue(
            entry_window_closed(
                self.SESSION, dt.datetime(2026, 8, 21, 10, 30, tzinfo=dt.timezone.utc)
            )
        )
        self.assertFalse(
            entry_window_closed(
                self.SESSION, dt.datetime(2026, 8, 21, 10, 29, tzinfo=dt.timezone.utc)
            )
        )

    def test_close_time_constant_is_after_session_end(self) -> None:
        self.assertGreater(
            ENTRY_WINDOW_CLOSE_TIME,
            dt.time(15, 30),
        )


class EffectiveLegStatusTests(unittest.TestCase):
    def test_armed_open_window_stays_armed(self) -> None:
        now = dt.datetime(2026, 8, 21, 12, 0, tzinfo=IST_TZ)
        self.assertEqual(
            _effective_leg_status("armed", dt.date(2026, 8, 21), now), "armed"
        )

    def test_armed_closed_window_derives_expired(self) -> None:
        now = dt.datetime(2026, 8, 21, 17, 0, tzinfo=IST_TZ)
        self.assertEqual(
            _effective_leg_status("armed", dt.date(2026, 8, 21), now), "expired"
        )

    def test_trigger_observed_closed_window_derives_expired(self) -> None:
        now = dt.datetime(2026, 8, 22, 9, 0, tzinfo=IST_TZ)
        self.assertEqual(
            _effective_leg_status("trigger_observed", dt.date(2026, 8, 21), now),
            "expired",
        )

    def test_intent_statuses_map_to_executing(self) -> None:
        now = dt.datetime(2026, 8, 21, 12, 0, tzinfo=IST_TZ)
        for status in ("intent_created", "submitted", "submission_unknown"):
            self.assertEqual(
                _effective_leg_status(status, dt.date(2026, 8, 21), now),
                "executing",
            )

    def test_filled_and_planned_pass_through(self) -> None:
        now = dt.datetime(2026, 8, 21, 12, 0, tzinfo=IST_TZ)
        self.assertEqual(
            _effective_leg_status("filled", dt.date(2026, 8, 21), now), "filled"
        )
        self.assertEqual(
            _effective_leg_status("planned", None, now), "planned"
        )


class DeriveProposalEntryStateTests(unittest.TestCase):
    SESSION = dt.date(2026, 8, 21)

    def ist(self, day: int, hour: int) -> dt.datetime:
        return dt.datetime(2026, 8, day, hour, 0, tzinfo=IST_TZ)

    def leg(self, status, *, end=None, shares=0, index=1):
        return {
            "leg_index": index,
            "status": status,
            "eligible_session_end": end,
            "filled_shares": shares,
        }

    def test_no_legs(self) -> None:
        self.assertIsNone(derive_proposal_entry_state([], self.ist(21, 12)))

    def test_planned_only(self) -> None:
        self.assertIsNone(
            derive_proposal_entry_state([self.leg("planned")], self.ist(21, 12))
        )

    def test_armed_open_window(self) -> None:
        state = derive_proposal_entry_state(
            [self.leg("armed", end=self.SESSION)], self.ist(21, 12)
        )
        self.assertEqual(state, "armed")

    def test_armed_closed_window_is_expired(self) -> None:
        state = derive_proposal_entry_state(
            [self.leg("armed", end=self.SESSION)], self.ist(21, 17)
        )
        self.assertEqual(state, "expired")

    def test_trigger_observed_open_window(self) -> None:
        state = derive_proposal_entry_state(
            [self.leg("trigger_observed", end=self.SESSION)], self.ist(21, 12)
        )
        self.assertEqual(state, "trigger_observed")

    def test_intent_created_is_executing(self) -> None:
        state = derive_proposal_entry_state(
            [self.leg("intent_created", end=self.SESSION)], self.ist(21, 12)
        )
        self.assertEqual(state, "executing")

    def test_partial_fill_counts_as_filled_when_shares_held(self) -> None:
        state = derive_proposal_entry_state(
            [self.leg("partially_filled", end=self.SESSION, shares=5)],
            self.ist(21, 12),
        )
        self.assertEqual(state, "filled")

    def test_expired_l1_with_planned_sibling_is_expired(self) -> None:
        state = derive_proposal_entry_state(
            [
                self.leg("armed", end=self.SESSION, index=1),
                self.leg("planned", index=2),
            ],
            self.ist(22, 9),
        )
        self.assertEqual(state, "expired")

    def test_filled_l1_beats_armed_l2(self) -> None:
        state = derive_proposal_entry_state(
            [
                self.leg("filled", shares=10, index=1),
                self.leg("armed", end=self.SESSION, index=2),
            ],
            self.ist(21, 12),
        )
        self.assertEqual(state, "filled")

    def test_datetime_session_end_is_accepted(self) -> None:
        state = derive_proposal_entry_state(
            [
                self.leg(
                    "armed",
                    end=dt.datetime(2026, 8, 21, 9, 0, tzinfo=IST_TZ),
                )
            ],
            self.ist(21, 17),
        )
        self.assertEqual(state, "expired")


class ExpireStaleEntryLegsTests(unittest.IsolatedAsyncioTestCase):
    async def test_expires_closed_windows_and_cancels_planned_siblings(self) -> None:
        from app.workers.entry_supervisor import expire_stale_entry_legs

        proposal_id = uuid4()
        stale_armed = uuid4()
        stale_observed = uuid4()
        open_armed = uuid4()
        calls: list[tuple[str, dict]] = []

        async def fake_execute(stmt, params=None):
            calls.append((str(stmt), params))
            if len(calls) == 1:
                return FakeResult(
                    rows=[
                        {
                            "id": stale_armed,
                            "proposal_id": proposal_id,
                            "leg_index": 1,
                            "eligible_session_end": dt.date(2026, 8, 21),
                        },
                        {
                            "id": stale_observed,
                            "proposal_id": proposal_id,
                            "leg_index": 2,
                            "eligible_session_end": dt.date(2026, 8, 21),
                        },
                        {
                            "id": open_armed,
                            "proposal_id": proposal_id,
                            "leg_index": 1,
                            "eligible_session_end": dt.date(2099, 1, 1),
                        },
                    ]
                )
            return FakeResult(rows=[])

        db = AsyncMock()
        db.execute.side_effect = fake_execute
        session_cm = AsyncMock()
        session_cm.__aenter__.return_value = db
        session_cm.__aexit__.return_value = False

        with patch(
            "app.workers.entry_supervisor.async_session", return_value=session_cm
        ):
            expired = await expire_stale_entry_legs()

        self.assertEqual(expired, 2)
        select_call = calls[0]
        self.assertIn("eligible_session_end", select_call[0])
        self.assertIn("'armed', 'trigger_observed'", select_call[0])

        update_call = calls[1]
        self.assertIn("SET status = 'expired'", update_call[0])
        self.assertEqual(
            set(update_call[1]["leg_ids"]), {stale_armed, stale_observed}
        )

        cancel_params = [params for _, params in calls[2:]]
        self.assertTrue(cancel_params)
        for params in cancel_params:
            self.assertEqual(params["proposal_id"], proposal_id)
            self.assertIn(params["leg_index"], (1, 2))
        db.commit.assert_awaited_once()

    async def test_no_closed_windows_is_a_noop(self) -> None:
        from app.workers.entry_supervisor import expire_stale_entry_legs

        db = AsyncMock()
        db.execute.return_value = FakeResult(
            rows=[
                {
                    "id": uuid4(),
                    "proposal_id": uuid4(),
                    "leg_index": 1,
                    "eligible_session_end": dt.date(2099, 1, 1),
                }
            ]
        )
        session_cm = AsyncMock()
        session_cm.__aenter__.return_value = db
        session_cm.__aexit__.return_value = False

        with patch(
            "app.workers.entry_supervisor.async_session", return_value=session_cm
        ):
            expired = await expire_stale_entry_legs()

        self.assertEqual(expired, 0)
        self.assertEqual(db.execute.await_count, 1)
        db.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
