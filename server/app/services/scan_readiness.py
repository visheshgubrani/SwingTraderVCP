"""Shared Nifty 500 history-coverage rules for EOD scans."""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


SCAN_HISTORY_WINDOW_DAYS = 450
SCAN_READY_RATIO = 0.95


@dataclass(frozen=True)
class ScanReadiness:
    scanner_ready: bool
    active_instruments: int
    scoreable_instruments: int
    required_scoreable_instruments: int
    minimum_history_days: int
    reference_eod_date: datetime.date


def evaluate_scan_readiness(
    histories: Iterable[tuple[int, datetime.date | None]],
    *,
    reference_eod_date: datetime.date,
    minimum_history_days: int,
) -> ScanReadiness:
    """Apply the shared current-date, minimum-history, and 95% coverage gate."""
    history_rows = list(histories)
    active_instruments = len(history_rows)
    scoreable_instruments = sum(
        sessions >= minimum_history_days and latest_date == reference_eod_date
        for sessions, latest_date in history_rows
    )
    required_scoreable_instruments = math.ceil(
        active_instruments * SCAN_READY_RATIO
    )
    scanner_ready = (
        active_instruments > 0
        and scoreable_instruments >= required_scoreable_instruments
    )
    return ScanReadiness(
        scanner_ready=scanner_ready,
        active_instruments=active_instruments,
        scoreable_instruments=scoreable_instruments,
        required_scoreable_instruments=required_scoreable_instruments,
        minimum_history_days=minimum_history_days,
        reference_eod_date=reference_eod_date,
    )


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
    return (
        "Insufficient Nifty 500 scan history: "
        f"{readiness.scoreable_instruments}/{readiness.active_instruments} "
        "instruments are current through "
        f"{readiness.reference_eod_date.isoformat()} with at least "
        f"{readiness.minimum_history_days} sessions; "
        f"{readiness.required_scoreable_instruments} required (95%). "
        "Run a two-year history repair before scanning."
    )
