from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session

logger = logging.getLogger(__name__)

# Search paths for ind_nifty500list.csv (local development and docker container layouts)
POTENTIAL_CSV_PATHS = [
    Path(__file__).resolve().parents[2] / "ind_nifty500list.csv",
    Path("/app/ind_nifty500list.csv"),
    Path("ind_nifty500list.csv"),
]
DEFAULT_CSV_PATH = POTENTIAL_CSV_PATHS[0]

DEFAULT_UNIVERSE_CODE = "NIFTY500"
DEFAULT_SOURCE = "nse_ind_nifty500_csv"
REQUIRED_HEADERS = {"Company Name", "Industry", "Symbol", "Series", "ISIN Code"}


@dataclass(frozen=True)
class Nifty500Row:
    company_name: str
    industry: str
    symbol: str
    series: str
    isin: str

    @property
    def fyers_symbol(self) -> str:
        return f"NSE:{self.symbol}-{self.series}"

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "industry": self.industry,
            "csv_series": self.series,
            "source": DEFAULT_SOURCE,
        }


def find_nifty500_csv_path(custom_path: Path | None = None) -> Path | None:
    if custom_path and custom_path.is_file():
        return custom_path
    for path in POTENTIAL_CSV_PATHS:
        if path.is_file():
            return path
    return None


def read_nifty500_csv(csv_path: Path) -> list[Nifty500Row]:
    with csv_path.open(newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        headers = set(reader.fieldnames or [])
        missing_headers = REQUIRED_HEADERS - headers
        if missing_headers:
            missing = ", ".join(sorted(missing_headers))
            raise ValueError(f"CSV is missing required columns: {missing}")

        rows: list[Nifty500Row] = []
        seen_symbols: set[str] = set()
        for line_number, row in enumerate(reader, start=2):
            company_name = (row["Company Name"] or "").strip()
            industry = (row["Industry"] or "").strip()
            symbol = (row["Symbol"] or "").strip().upper()
            series = (row["Series"] or "").strip().upper()
            isin = (row["ISIN Code"] or "").strip().upper()

            if not company_name or not symbol or not series or not isin:
                raise ValueError(f"CSV row {line_number} has a blank required value.")
            if symbol in seen_symbols:
                raise ValueError(f"CSV row {line_number} duplicates symbol {symbol}.")
            seen_symbols.add(symbol)

            rows.append(
                Nifty500Row(
                    company_name=company_name,
                    industry=industry,
                    symbol=symbol,
                    series=series,
                    isin=isin,
                )
            )

    return rows


async def import_nifty500_rows(
    session: AsyncSession,
    rows: list[Nifty500Row],
    *,
    universe_code: str = DEFAULT_UNIVERSE_CODE,
    member_from: date | None = None,
) -> tuple[int, int, int]:
    effective_member_from = member_from or date.today()

    instrument_update_stmt = (
        text(
            """
            UPDATE instruments
            SET
                exchange = 'NSE',
                segment = 'EQUITY',
                symbol = :symbol,
                trading_symbol = :trading_symbol,
                fyers_symbol = :fyers_symbol,
                name = :name,
                active = true,
                active_to = NULL,
                metadata = instruments.metadata || :metadata
            WHERE isin = :isin
            RETURNING id
            """
        )
        .bindparams(bindparam("metadata", type_=JSONB))
    )

    instrument_insert_stmt = (
        text(
            """
            INSERT INTO instruments (
                exchange,
                segment,
                symbol,
                trading_symbol,
                fyers_symbol,
                isin,
                name,
                active,
                active_from,
                active_to,
                metadata
            )
            VALUES (
                'NSE',
                'EQUITY',
                :symbol,
                :trading_symbol,
                :fyers_symbol,
                :isin,
                :name,
                true,
                :member_from,
                NULL,
                :metadata
            )
            ON CONFLICT (fyers_symbol) DO UPDATE SET
                exchange = EXCLUDED.exchange,
                segment = EXCLUDED.segment,
                symbol = EXCLUDED.symbol,
                trading_symbol = EXCLUDED.trading_symbol,
                isin = EXCLUDED.isin,
                name = EXCLUDED.name,
                active = true,
                active_to = NULL,
                metadata = instruments.metadata || EXCLUDED.metadata
            RETURNING id
            """
        )
        .bindparams(bindparam("metadata", type_=JSONB))
    )

    membership_stmt = text(
        """
        INSERT INTO universe_memberships (
            instrument_id,
            universe_code,
            member_from,
            source
        )
        SELECT
            id,
            :universe_code,
            :member_from,
            :source
        FROM instruments
        WHERE fyers_symbol = :fyers_symbol
          AND NOT EXISTS (
              SELECT 1
              FROM universe_memberships
              WHERE instrument_id = instruments.id
                AND universe_code = :universe_code
                AND member_to IS NULL
          )
        """
    )

    close_absent_memberships_stmt = text(
        """
        UPDATE universe_memberships AS membership
        SET member_to = GREATEST(
            membership.member_from,
            CAST(:member_from AS date) - 1
        )
        FROM instruments AS instrument
        WHERE membership.instrument_id = instrument.id
          AND membership.universe_code = :universe_code
          AND membership.member_to IS NULL
          AND NOT (instrument.fyers_symbol = ANY(:current_fyers_symbols))
        """
    )

    imported_count = 0
    created_memberships = 0

    for row in rows:
        instrument_params = {
            "symbol": row.symbol,
            "trading_symbol": f"{row.symbol}-{row.series}",
            "fyers_symbol": row.fyers_symbol,
            "isin": row.isin,
            "name": row.company_name,
            "member_from": effective_member_from,
            "metadata": row.metadata,
        }
        update_result = await session.execute(
            instrument_update_stmt,
            instrument_params,
        )
        if update_result.scalar_one_or_none() is None:
            await session.execute(
                instrument_insert_stmt,
                instrument_params,
            )
        membership_result = await session.execute(
            membership_stmt,
            {
                "universe_code": universe_code,
                "member_from": effective_member_from,
                "source": DEFAULT_SOURCE,
                "fyers_symbol": row.fyers_symbol,
            },
        )
        imported_count += 1
        created_memberships += membership_result.rowcount or 0

    closed_result = await session.execute(
        close_absent_memberships_stmt,
        {
            "universe_code": universe_code,
            "member_from": effective_member_from,
            "current_fyers_symbols": [row.fyers_symbol for row in rows],
        },
    )
    closed_memberships = closed_result.rowcount or 0
    await session.commit()

    return imported_count, created_memberships, closed_memberships


async def ensure_nifty500_universe_imported(
    session: AsyncSession | None = None,
    csv_path: Path | None = None,
) -> int:
    """
    Ensure the active Nifty 500 universe memberships are present in the database.
    If 0 active Nifty 500 memberships exist, automatically parse ind_nifty500list.csv
    and import all instruments. Returns the count of active Nifty 500 instruments.
    """
    async def _check_and_import(s: AsyncSession) -> int:
        res = await s.execute(
            text(
                """
                SELECT count(*)
                FROM instruments i
                JOIN universe_memberships m ON i.id = m.instrument_id
                WHERE m.universe_code = :universe_code
                  AND m.member_to IS NULL
                  AND i.active = true
                """
            ),
            {"universe_code": DEFAULT_UNIVERSE_CODE},
        )
        current_count = res.scalar() or 0
        if current_count > 0:
            return current_count

        resolved_csv = find_nifty500_csv_path(csv_path)
        if not resolved_csv:
            logger.warning(
                "Cannot auto-import Nifty 500 instruments: ind_nifty500list.csv not found"
            )
            return 0

        logger.info(
            "Auto-importing Nifty 500 universe from %s into database...",
            resolved_csv,
        )
        rows = read_nifty500_csv(resolved_csv)
        imported, created, _ = await import_nifty500_rows(
            s,
            rows,
            universe_code=DEFAULT_UNIVERSE_CODE,
        )
        logger.info(
            "Auto-imported %d instruments (%d new memberships) for %s",
            imported,
            created,
            DEFAULT_UNIVERSE_CODE,
        )
        return imported

    if session is not None:
        return await _check_and_import(session)

    async with async_session() as s:
        return await _check_and_import(s)
