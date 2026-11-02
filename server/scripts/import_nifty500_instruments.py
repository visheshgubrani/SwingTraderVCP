"""Import the Nifty 500 CSV into instruments and universe_memberships.

Thin CLI over ``app.services.instrument_importer`` so the script and the
service share one parser (series/ISIN filtering) and one upsert path.
Members absent from the CSV have their membership closed; instruments whose
ISIN changed under the same symbol are updated in place (history is kept).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.database import async_session, engine  # noqa: E402
from app.services.instrument_importer import (  # noqa: E402
    DEFAULT_UNIVERSE_CODE,
    import_nifty500_rows,
    read_nifty500_csv,
)

engine.echo = False

DEFAULT_CSV_PATH = SERVER_ROOT / "ind_nifty500list.csv"


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


async def async_main() -> None:
    args = parse_args()
    rows = read_nifty500_csv(args.csv)
    async with async_session() as session:
        imported_count, created_memberships, closed_memberships = (
            await import_nifty500_rows(
                session,
                rows,
                universe_code=args.universe_code,
                member_from=args.member_from,
            )
        )
    print(
        "Imported "
        f"{imported_count} instruments into {args.universe_code}; "
        f"created {created_memberships} and closed "
        f"{closed_memberships} current memberships."
    )


if __name__ == "__main__":
    asyncio.run(async_main())
