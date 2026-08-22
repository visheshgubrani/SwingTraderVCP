"""Apply ordered server/db/migrations files that have not yet been recorded.

Existing production databases applied 001–024 by hand before this ledger
existed. The first run records those filenames without re-executing them, then
applies 025 and later. Fresh schema.sql installs also get that baseline so
historical upgrade scripts are not replayed on top of current DDL.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import asyncpg


logger = logging.getLogger(__name__)

BASELINE_THROUGH = 24
DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"
LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def numeric_prefix(filename: str) -> int | None:
    stem = Path(filename).name.split("_", 1)[0]
    if stem.isdigit():
        return int(stem)
    return None


def sorted_migration_names(filenames: list[str]) -> list[str]:
    return sorted(
        filenames,
        key=lambda name: (numeric_prefix(name) is None, numeric_prefix(name) or 0, name),
    )


def baseline_filenames(
    filenames: list[str],
    *,
    through: int = BASELINE_THROUGH,
) -> list[str]:
    return [
        name
        for name in sorted_migration_names(filenames)
        if (numeric_prefix(name) or 0) <= through
    ]


def pending_filenames(filenames: list[str], applied: set[str]) -> list[str]:
    return [name for name in sorted_migration_names(filenames) if name not in applied]


def list_migration_files(migrations_dir: Path) -> list[Path]:
    return [
        path
        for path in sorted_migration_names_as_paths(migrations_dir)
        if path.suffix == ".sql"
    ]


def sorted_migration_names_as_paths(migrations_dir: Path) -> list[Path]:
    names = [path.name for path in migrations_dir.glob("*.sql")]
    by_name = {path.name: path for path in migrations_dir.glob("*.sql")}
    return [by_name[name] for name in sorted_migration_names(names)]


async def _connect() -> asyncpg.Connection:
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    user = os.environ.get("POSTGRES_USER", "algo")
    database = os.environ.get("POSTGRES_DB", "algo_trading")
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError("POSTGRES_PASSWORD is required to apply migrations")
    return await asyncpg.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
    )


async def apply_pending_migrations(
    *,
    migrations_dir: Path | None = None,
    connection: asyncpg.Connection | None = None,
) -> list[str]:
    directory = migrations_dir or DEFAULT_MIGRATIONS_DIR
    files = list_migration_files(directory)
    if not files:
        raise RuntimeError(f"No SQL migrations found in {directory}")

    own_connection = connection is None
    conn = connection or await _connect()
    applied_now: list[str] = []
    try:
        await conn.execute(LEDGER_DDL)
        applied = {
            row["filename"]
            for row in await conn.fetch("SELECT filename FROM schema_migrations")
        }
        instruments_exist = await conn.fetchval(
            "SELECT to_regclass('public.instruments') IS NOT NULL"
        )
        if not applied and instruments_exist:
            baseline = baseline_filenames([path.name for path in files])
            for name in baseline:
                await conn.execute(
                    """
                    INSERT INTO schema_migrations (filename)
                    VALUES ($1)
                    ON CONFLICT (filename) DO NOTHING
                    """,
                    name,
                )
            applied.update(baseline)
            logger.info(
                "Baselined %s historical migration file(s) through %03d",
                len(baseline),
                BASELINE_THROUGH,
            )

        pending = pending_filenames([path.name for path in files], applied)
        by_name = {path.name: path for path in files}
        for name in pending:
            sql = by_name[name].read_text(encoding="utf-8")
            logger.info("Applying %s", name)
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO schema_migrations (filename) VALUES ($1)",
                name,
            )
            applied_now.append(name)
        if not pending:
            logger.info("No pending SQL migrations")
        return applied_now
    finally:
        if own_connection:
            await conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    applied = asyncio.run(apply_pending_migrations())
    if applied:
        print("Applied:", ", ".join(applied))
    else:
        print("No pending SQL migrations")


if __name__ == "__main__":
    main()
