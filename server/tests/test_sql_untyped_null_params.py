"""asyncpg cannot infer a bind parameter's type from `:param IS [NOT] NULL`.

When the same parameter is also compared to a typed column, Postgres raises
AmbiguousParameterError at prepare time on every call (this silently broke the
paper fill path in production). Any NULL test on a bind parameter must wrap it
in CAST(:param AS <type>).
"""

import os
import pathlib
import re
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
# A bare bind parameter (not inside CAST(...)) followed by IS [NOT] NULL.
UNTYPED_NULL_TEST = re.compile(
    r"(?<![\w:])(?<!CAST\()(:[A-Za-z_]\w*)\s+IS\s+(?:NOT\s+)?NULL",
    re.IGNORECASE,
)


class UntypedNullParameterLintTest(unittest.TestCase):
    def test_no_untyped_bind_parameter_null_tests(self):
        offenders: list[str] = []
        for path in APP.rglob("*.py"):
            source = path.read_text()
            for match in UNTYPED_NULL_TEST.finditer(source):
                line = source.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(APP)}:{line}: {match.group(0)}")
        self.assertEqual(offenders, [], "Wrap bind parameters in CAST(:x AS type) before IS [NOT] NULL")

    def test_pattern_flags_untyped_and_allows_cast(self):
        self.assertIsNotNone(UNTYPED_NULL_TEST.search("WHERE (:async_id IS NOT NULL AND x = :async_id)"))
        self.assertIsNone(UNTYPED_NULL_TEST.search("WHERE CAST(:async_id AS text) IS NOT NULL"))
        self.assertIsNone(UNTYPED_NULL_TEST.search("WHERE oi.fyers_async_id IS NULL"))


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "requires TEST_POSTGRES_URL (asyncpg)")
class FindIntentPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_find_intent_prepares_and_matches_each_correlation_id(self):
        from app.config import settings
        from app.services.order_gateway import _find_intent

        engine = create_async_engine(os.environ["TEST_POSTGRES_URL"])
        self.addAsyncCleanup(engine.dispose)
        intent_id = uuid.uuid4()
        position_id = uuid.uuid4()
        async with AsyncSession(engine) as db:
            await db.execute(text("""
                CREATE TEMP TABLE positions (
                    id uuid PRIMARY KEY, state text, open_quantity integer
                ) ON COMMIT DROP
            """))
            await db.execute(text("""
                CREATE TEMP TABLE order_intents (
                    id uuid PRIMARY KEY, position_id uuid, execution_mode text,
                    fyers_async_id text, fyers_order_id text, exchange_order_id text,
                    created_at timestamptz DEFAULT now()
                ) ON COMMIT DROP
            """))
            await db.execute(
                text("INSERT INTO positions VALUES (:id, 'pending_entry', 0)"),
                {"id": position_id},
            )
            await db.execute(
                text("""
                    INSERT INTO order_intents
                        (id, position_id, execution_mode, fyers_async_id)
                    VALUES (:id, :position_id, 'paper', 'paper-async:1')
                """),
                {"id": intent_id, "position_id": position_id},
            )
            with patch.object(settings, "execution_mode", "paper"):
                by_tag = await _find_intent(
                    db, async_id=None, fyers_order_id=None,
                    exchange_order_id=None, tagged_intent_id=intent_id,
                )
                by_async = await _find_intent(
                    db, async_id="paper-async:1", fyers_order_id=None,
                    exchange_order_id=None, tagged_intent_id=None,
                )
                missing = await _find_intent(
                    db, async_id=None, fyers_order_id="nope",
                    exchange_order_id=None, tagged_intent_id=None,
                )
            self.assertEqual(by_tag["id"], intent_id)
            self.assertEqual(by_async["id"], intent_id)
            self.assertEqual(by_tag["position_state"], "pending_entry")
            self.assertIsNone(missing)
