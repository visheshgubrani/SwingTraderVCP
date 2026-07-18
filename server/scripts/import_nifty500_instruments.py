from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.database import async_session, engine  # noqa: E402

engine.echo = False


DEFAULT_CSV_PATH = SERVER_ROOT / "ind_nifty500list.csv"
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import the Nifty 500 CSV into instruments and universe_memberships.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help=f"CSV path. Defaults to {DEFAULT_CSV_PATH}.",
    )
    parser.add_argument(
        "--universe-code",
        default=DEFAULT_UNIVERSE_CODE,
        help=f"Universe code to assign. Defaults to {DEFAULT_UNIVERSE_CODE}.",
    )
    parser.add_argument(
        "--member-from",
        type=date.fromisoformat,
        default=date.today(),
        help="Membership start date in YYYY-MM-DD format. Defaults to today.",
    )
    return parser.parse_args()


def read_csv(csv_path: Path) -> list[Nifty500Row]:
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


async def import_rows(
    rows: list[Nifty500Row],
    *,
    universe_code: str,
    member_from: date,
) -> tuple[int, int]:
    instrument_stmt = (
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

    async with async_session() as session:
        imported_count = 0
        created_memberships = 0

        for row in rows:
            await session.execute(
                instrument_stmt,
                {
                    "symbol": row.symbol,
                    "trading_symbol": f"{row.symbol}-{row.series}",
                    "fyers_symbol": row.fyers_symbol,
                    "isin": row.isin,
                    "name": row.company_name,
                    "member_from": member_from,
                    "metadata": row.metadata,
                },
            )
            membership_result = await session.execute(
                membership_stmt,
                {
                    "universe_code": universe_code,
                    "member_from": member_from,
                    "source": DEFAULT_SOURCE,
                    "fyers_symbol": row.fyers_symbol,
                },
            )
            imported_count += 1
            created_memberships += membership_result.rowcount or 0

        await session.commit()

    return imported_count, created_memberships


async def async_main() -> None:
    args = parse_args()
    rows = read_csv(args.csv)
    imported_count, created_memberships = await import_rows(
        rows,
        universe_code=args.universe_code,
        member_from=args.member_from,
    )
    print(
        "Imported "
        f"{imported_count} instruments into {args.universe_code}; "
        f"created {created_memberships} current memberships."
    )


if __name__ == "__main__":
    asyncio.run(async_main())
