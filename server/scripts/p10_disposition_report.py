#!/usr/bin/env python3
"""Read-only P10 pipeline report: why proposals/entries did or did not happen.

    python scripts/p10_disposition_report.py [--days 30]

Or, against an image that predates this script, pipe it into the worker:

    ssh ... 'docker exec -i swingtradervcp-proposal-worker-1 python - --days 30' \
        < server/scripts/p10_disposition_report.py

Every query runs in a READ ONLY transaction; image columns are never
selected and no secret values are printed (only set/unset).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

try:
    SERVER_ROOT = Path(__file__).resolve().parents[1]
except NameError:  # piped through `python -`
    SERVER_ROOT = Path.cwd()
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import engine  # noqa: E402

SECTIONS: dict[str, str] = {
    "rollout stage": "SELECT stage, changed_at FROM p10_rollout_state",
    "controls (a MISSING proposal_processing_paused row is treated as PAUSED)": (
        "SELECT control_key, enabled, changed_at FROM system_controls ORDER BY 1"
    ),
    "paper accounts": "SELECT count(*) AS accounts FROM paper_broker_account",
    "recent personal scans vs proposal batches": """
        SELECT s.as_of_date, s.status AS scan, ar.status AS batch, ar.candidates_total AS cand,
               ar.proposals_generated AS gen, ar.proposals_rejected AS rej,
               ar.proposals_uncertain AS forming, ar.proposals_failed AS failed
        FROM scan_runs s LEFT JOIN automation_runs ar ON ar.scan_run_id = s.id
        WHERE s.visibility = 'personal' AND s.created_at > now() - make_interval(days => :days)
        ORDER BY s.created_at DESC""",
    "latest-attempt dispositions by prompt version": """
        WITH latest AS (
          SELECT DISTINCT ON (automation_run_id, screening_result_id) *
          FROM proposal_attempts WHERE started_at > now() - make_interval(days => :days)
          ORDER BY automation_run_id, screening_result_id, attempt_number DESC, started_at DESC)
        SELECT prompt_version, status, error_type,
               structured_output->>'classification' AS cls,
               structured_output->>'primary_reason' AS reason, count(*)
        FROM latest GROUP BY 1,2,3,4,5 ORDER BY 1, 6 DESC""",
    "structural gate codes": """
        WITH latest AS (
          SELECT DISTINCT ON (automation_run_id, screening_result_id) *
          FROM proposal_attempts WHERE started_at > now() - make_interval(days => :days)
          ORDER BY automation_run_id, screening_result_id, attempt_number DESC, started_at DESC)
        SELECT prompt_version, g.code, count(*)
        FROM latest, jsonb_array_elements_text(coalesce(error_details->'gate_codes', '[]')) g(code)
        GROUP BY 1,2 ORDER BY 1, 3 DESC""",
    "approved proposals: D1 data and breakout": """
        SELECT tp.symbol, tp.entry_session_date AS d1, el.status AS leg, el.trigger_price,
          (SELECT count(*) FROM five_minute_bars b WHERE b.symbol = tp.symbol
             AND (b.bar_time AT TIME ZONE 'Asia/Kolkata')::date = tp.entry_session_date) AS bars,
          (SELECT max(b.high) FROM five_minute_bars b WHERE b.symbol = tp.symbol
             AND (b.bar_time AT TIME ZONE 'Asia/Kolkata')::date = tp.entry_session_date) AS d1_high,
          (SELECT count(*) FROM trigger_events te WHERE te.leg_id = el.id) AS trig_events
        FROM trade_proposals tp JOIN entry_legs el ON el.proposal_id = tp.id AND el.leg_index = 1
        WHERE tp.status = 'approved' AND tp.created_at > now() - make_interval(days => :days)
        ORDER BY tp.entry_session_date DESC""",
    "trigger outcomes": """
        SELECT trigger_outcome, entry_eligibility_outcome, left(entry_rejection_reason, 100) AS reason, count(*)
        FROM trigger_events WHERE created_at > now() - make_interval(days => :days)
        GROUP BY 1,2,3 ORDER BY 4 DESC""",
    "entry legs": "SELECT status, count(*) FROM entry_legs GROUP BY 1 ORDER BY 2 DESC",
    "auth/market-data events (7d)": """
        SELECT severity, event_type, count(*), max(created_at) AS latest FROM system_events
        WHERE created_at > now() - interval '7 days'
          AND (event_type ILIKE '%auth%' OR event_type ILIKE '%upstox%' OR component = 'market_data')
        GROUP BY 1,2 ORDER BY 3 DESC""",
}

FORBIDDEN = ("insert ", "update ", "delete ", "alter ", "create ", "drop ", "truncate ", "copy ", "grant ")


def _print_rows(title: str, rows) -> None:
    print(f"\n== {title} ==")
    if not rows:
        print("  (no rows)")
        return
    keys = list(rows[0].keys())
    print("  " + " | ".join(keys))
    for row in rows:
        print("  " + " | ".join("" if row[k] is None else str(row[k]) for k in keys))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    print("== effective settings ==")
    for name in (
        "execution_mode", "live_order_placement_enabled", "paper_auto_arm_proposals",
        "proposal_automation_enabled", "proposal_batch_limit", "p7_fundamental_pass_enabled",
        "vcp_vision_model", "market_data_provider",
    ):
        print(f"  {name} = {getattr(settings, name, 'n/a')}")
    for secret in ("upstox_analytics_token", "openrouter_api_key", "telegram_bot_token", "fyers_totp_key"):
        print(f"  {secret} = {'set' if str(getattr(settings, secret, '') or '').strip() else 'UNSET'}")
    print(f"  nse_trading_holidays = {len(settings.nse_trading_holidays)} configured")

    for title, sql in SECTIONS.items():
        assert not any(word in sql.lower() for word in FORBIDDEN), title
        async with engine.connect() as conn:
            try:
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                await conn.execute(text("SET LOCAL statement_timeout = '20s'"))
                rows = (await conn.execute(text(sql), {"days": args.days})).mappings().all()
                _print_rows(title, rows)
            except Exception as exc:  # one broken section never hides the rest
                print(f"\n== {title} ==\n  ERROR: {type(exc).__name__}: {exc}")
            finally:
                await conn.rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
