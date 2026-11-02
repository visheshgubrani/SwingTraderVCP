"""Shared Nifty 500 history-coverage rules for EOD scans."""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, replace
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


SCAN_HISTORY_WINDOW_DAYS = 450
SCAN_READY_RATIO = 0.95
# Current instruments with too little history are recent listings the scanner
# can never score, so they leave the 95% denominator. More than this share of
# them means truncated history rather than IPOs, and the gate fails closed.
MAX_LISTING_LIMITED_RATIO = 0.10


@dataclass(frozen=True)
class ScanReadiness:
    scanner_ready: bool
    active_instruments: int
    scoreable_instruments: int
    required_scoreable_instruments: int
    minimum_history_days: int
    reference_eod_date: datetime.date
    listing_limited_instruments: int = 0

    @property
    def eligible_instruments(self) -> int:
        return self.active_instruments - self.listing_limited_instruments

    @property
    def listing_limited_over_cap(self) -> bool:
        return (
            self.listing_limited_instruments
            > self.active_instruments * MAX_LISTING_LIMITED_RATIO
        )


def evaluate_scan_readiness(
    histories: Iterable[tuple[int, datetime.date | None]],
    *,
    reference_eod_date: datetime.date,
    minimum_history_days: int,
) -> ScanReadiness:
    """Apply the shared current-date, minimum-history, and 95% coverage gate.

    Stale and empty instruments count as failures. Current instruments below
    the minimum history are listing-limited and leave the denominator.
    """
    history_rows = list(histories)
    active_instruments = len(history_rows)
    current_sessions = [
        sessions
        for sessions, latest_date in history_rows
        if latest_date == reference_eod_date
    ]
    scoreable_instruments = sum(
        sessions >= minimum_history_days for sessions in current_sessions
    )
    listing_limited_instruments = len(current_sessions) - scoreable_instruments
    required_scoreable_instruments = math.ceil(
        (active_instruments - listing_limited_instruments) * SCAN_READY_RATIO
    )
    readiness = ScanReadiness(
        scanner_ready=False,
        active_instruments=active_instruments,
        scoreable_instruments=scoreable_instruments,
        required_scoreable_instruments=required_scoreable_instruments,
        minimum_history_days=minimum_history_days,
        reference_eod_date=reference_eod_date,
        listing_limited_instruments=listing_limited_instruments,
    )
    scanner_ready = (
        readiness.eligible_instruments > 0
        and not readiness.listing_limited_over_cap
        and scoreable_instruments >= required_scoreable_instruments
    )
    return replace(readiness, scanner_ready=scanner_ready)


async def load_scan_readiness(
    session: AsyncSession,
    *,
    reference_eod_date: datetime.date,
    minimum_history_days: int,
) -> ScanReadiness:
    """Load active-universe histories in the same trailing window as the scanner."""
    window_start = reference_eod_date - datetime.timedelta(
        days=SCAN_HISTORY_WINDOW_DAYS
    )
    end_date = reference_eod_date + datetime.timedelta(days=1)
    rows = (
        await session.execute(
            text(
                """
                SELECT
                    COUNT(c.instrument_id) AS session_count,
                    (MAX(c.candle_start) AT TIME ZONE 'Asia/Kolkata')::date
                        AS latest_candle_date
                FROM instruments i
                JOIN universe_memberships m
                    ON m.instrument_id = i.id
                   AND m.universe_code = 'NIFTY500'
                   AND m.member_to IS NULL
                LEFT JOIN market_candles c
                    ON c.instrument_id = i.id
                   AND c.timeframe = '1d'
                   AND c.candle_start >= :window_start
                   AND c.candle_start < :end_date
                WHERE i.active = true
                GROUP BY i.id
                """
            ),
            {
                "window_start": window_start,
                "end_date": end_date,
            },
        )
    ).all()
    return evaluate_scan_readiness(
        (
            (int(row.session_count), row.latest_candle_date)
            for row in rows
        ),
        reference_eod_date=reference_eod_date,
        minimum_history_days=minimum_history_days,
    )


def scan_readiness_error(readiness: ScanReadiness) -> str:
    if readiness.listing_limited_over_cap:
        return (
            "Insufficient Nifty 500 scan history: "
            f"{readiness.listing_limited_instruments}/"
            f"{readiness.active_instruments} current instruments have fewer "
            f"than {readiness.minimum_history_days} sessions, above the "
            f"{MAX_LISTING_LIMITED_RATIO:.0%} recent-listing allowance; this "
            "looks like truncated history. "
            "Run a two-year history repair before scanning."
        )
    excluded = (
        f" ({readiness.listing_limited_instruments} recent listings with fewer "
        f"than {readiness.minimum_history_days} sessions excluded)"
        if readiness.listing_limited_instruments
        else ""
    )
    return (
        "Insufficient Nifty 500 scan history: "
        f"{readiness.scoreable_instruments}/{readiness.eligible_instruments} "
        "instruments are current through "
        f"{readiness.reference_eod_date.isoformat()} with at least "
        f"{readiness.minimum_history_days} sessions{excluded}; "
        f"{readiness.required_scoreable_instruments} required (95%). "
        "Run a two-year history repair before scanning."
    )
