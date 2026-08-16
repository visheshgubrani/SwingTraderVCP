#!/usr/bin/env python3
"""Validate every P9 index against a downloaded FYERS NSE symbol master."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.database import async_session  # noqa: E402
from app.domain.p9_market_context import POLICY_VERSION  # noqa: E402
from app.domain.p9_sector_taxonomy import SECTORS  # noqa: E402
from app.services.market_context import TREND_SYMBOLS  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, required=True, help="Current FYERS NSE symbol-master CSV")
    return parser.parse_args()


def _master_symbols(path: Path) -> set[str]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    # FYERS has changed column labels/order over time. Match exact field values,
    # not a guessed column, and never accept substring matches.
    return {field.strip() for row in rows for field in row if field.strip().startswith("NSE:")}


async def _run(path: Path) -> None:
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    available = _master_symbols(path)
    required = set(TREND_SYMBOLS) | {sector.fyers_symbol for sector in SECTORS}
    missing = sorted(required - available)
    if missing:
        raise SystemExit("P9 remains fail-closed; symbols absent from FYERS master:\n" + "\n".join(missing))
    evidence = {
        "verified": True,
        "verified_on": date.today().isoformat(),
        "master_sha256": content_hash,
        "policy_version": POLICY_VERSION,
    }
    statement = text(
        """
        UPDATE instruments
        SET metadata = metadata || :evidence
        WHERE fyers_symbol = ANY(:symbols)
        """
    ).bindparams(bindparam("evidence", type_=JSONB))
    async with async_session() as db:
        result = await db.execute(statement, {"evidence": evidence, "symbols": sorted(required)})
        if result.rowcount != len(required):
            await db.rollback()
            raise SystemExit(f"Expected {len(required)} P9 instruments in Postgres; updated {result.rowcount}")
        await db.commit()
    print(json.dumps({"verified_symbols": len(required), **evidence}, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_run(_args().master))
