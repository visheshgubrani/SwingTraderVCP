import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.services.journal_processor import _process_outbox_event


class FakeResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self):
        return self

    def one_or_none(self):
        if not self._rows:
            return None
        return self._rows[0]

    def all(self):
        return self._rows

    def one(self):
        return self._rows[0]

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar


class JournalOutboxIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.journal_processor._finalize_journal_closure", new_callable=AsyncMock)
    @patch("app.services.journal_processor._append_exit_fill", new_callable=AsyncMock)
    @patch("app.services.journal_processor._get_journal_id", new_callable=AsyncMock)
    @patch("app.services.journal_processor._load_fill_context", new_callable=AsyncMock)
    async def test_exit_fill_requires_existing_journal(
        self,
        load_fill,
        get_journal,
        append_exit,
        finalize,
    ):
        db = AsyncMock()
        position_id = uuid4()
        load_fill.return_value = {
            "order_fill_id": uuid4(),
            "position_id": position_id,
            "fill_side": "exit",
        }
        get_journal.return_value = None

        with self.assertRaises(RuntimeError):
            await _process_outbox_event(
                db,
                {
                    "id": uuid4(),
                    "order_fill_id": uuid4(),
                    "position_id": position_id,
                    "fill_side": "exit",
                },
            )

    @patch("app.services.journal_processor._create_journal_on_first_entry", new_callable=AsyncMock)
    @patch("app.services.journal_processor._get_journal_id", new_callable=AsyncMock)
    @patch("app.services.journal_processor._load_fill_context", new_callable=AsyncMock)
    async def test_first_entry_creates_journal(
        self,
        load_fill,
        get_journal,
        create_journal,
    ):
        db = AsyncMock()
        position_id = uuid4()
        load_fill.return_value = {"position_id": position_id}
        get_journal.return_value = None

        await _process_outbox_event(
            db,
            {
                "id": uuid4(),
                "order_fill_id": uuid4(),
                "position_id": position_id,
                "fill_side": "entry",
            },
        )
        create_journal.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
