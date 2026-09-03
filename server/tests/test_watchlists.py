"""Tests for the watchlist + instrument-search REST API.

Follows the repo's dominant test convention: ``unittest``/``IsolatedAsyncioTestCase``
with mocked DB sessions (``AsyncMock``) exercising service/router functions
directly, plus a TestClient smoke test asserting the session-auth dependency
rejects unauthenticated callers (mirrors ``test_auth_protection.py``).

Requires a non-production ``APP_ENVIRONMENT`` (the config forbids running
without an app password in production).
"""

from __future__ import annotations

import datetime as dt
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import HTTPException

from app.routers.instruments import get_instruments
from app.routers.watchlists import (
    add_watchlist_item,
    create_watchlist,
    get_watchlists,
)
from app.schemas.watchlists import WatchlistCreate, WatchlistItemCreate
from app.services import watchlist_service as service


class Result:
    """Minimal fake for a SQLAlchemy ``CursorResult``.

    Exposes the handful of read APIs the service layer uses: row mappings via
    ``.mappings().all()/one()/one_or_none()`` plus direct scalar helpers and
    ``rowcount`` for DML.
    """

    _UNSET = object()

    def __init__(self, *, rows=None, row=None, scalar=_UNSET, rowcount=None):
        self._rows = list(rows) if rows is not None else []
        if row is not None:
            self._rows = [row]
        self._scalar = scalar
        self.rowcount = rowcount

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def one(self):
        if len(self._rows) != 1:
            raise AssertionError(f"expected one row, got {len(self._rows)}")
        return self._rows[0]

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        if self._scalar is self._UNSET:
            raise AssertionError("scalar value was not set")
        return self._scalar

    def scalar_one_or_none(self):
        if self._scalar is self._UNSET:
            return None
        return self._scalar


def make_db(*results: Result) -> AsyncMock:
    db = AsyncMock()
    db.execute.side_effect = list(results)
    return db


def sql_at(db: AsyncMock, index: int) -> str:
    return str(db.execute.await_args_list[index].args[0])


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def watchlist_row(
    *,
    name: str = "Core",
    description: str | None = "Default watchlist",
    is_active: bool = True,
    item_count: int = 0,
) -> dict:
    return {
        "id": uuid4(),
        "name": name,
        "description": description,
        "is_active": is_active,
        "created_at": now(),
        "updated_at": now(),
        "item_count": item_count,
    }


def item_row(*, instrument_id=None) -> dict:
    return {
        "id": uuid4(),
        "instrument_id": instrument_id or uuid4(),
        "symbol": "RELIANCE",
        "fyers_symbol": "NSE:RELIANCE-EQ",
        "name": "Reliance Industries",
        "added_at": now(),
    }


class WatchlistListTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_creates_default_when_none_exist(self) -> None:
        db = make_db(
            Result(rows=[]),  # first listing: empty
            Result(rowcount=1),  # INSERT ... WHERE NOT EXISTS
            Result(rows=[watchlist_row()]),  # listing after auto-create
        )
        watchlists = await service.list_watchlists(db, auto_create_default=True)

        self.assertEqual(len(watchlists), 1)
        view = watchlists[0]
        self.assertEqual(view.name, "Core")
        self.assertEqual(view.description, "Default watchlist")
        self.assertTrue(view.is_active)
        self.assertEqual(view.item_count, 0)
        self.assertIn("INSERT INTO watchlists", sql_at(db, 1))
        insert_params = db.execute.await_args_list[1].args[1]
        self.assertEqual(insert_params["name"], "Core")
        self.assertEqual(insert_params["description"], "Default watchlist")

    async def test_no_auto_create_when_watchlists_exist(self) -> None:
        db = make_db(Result(rows=[watchlist_row()]))
        watchlists = await service.list_watchlists(db, auto_create_default=True)

        self.assertEqual(len(watchlists), 1)
        db.execute.assert_awaited_once()

    async def test_item_count_surfaces_from_query(self) -> None:
        db = make_db(
            Result(
                rows=[
                    watchlist_row(name="A", item_count=0),
                    watchlist_row(name="B", item_count=3),
                ]
            )
        )
        watchlists = await service.list_watchlists(db, auto_create_default=False)
        self.assertEqual(watchlists[0].item_count, 0)
        self.assertEqual(watchlists[1].item_count, 3)


class WatchlistCreateTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_active_when_no_active_exists(self) -> None:
        db = make_db(
            Result(scalar=None),  # duplicate check
            Result(scalar=False),  # no active watchlist exists
            Result(row={"id": uuid4(), "created_at": now(), "updated_at": now()}),
        )
        view = await service.create_watchlist(
            db, name="Watchlist A", description="Momentum"
        )

        self.assertTrue(view.is_active)
        self.assertEqual(view.item_count, 0)
        self.assertEqual(view.name, "Watchlist A")

    async def test_creates_inactive_when_active_exists(self) -> None:
        db = make_db(
            Result(scalar=None),
            Result(scalar=True),
            Result(row={"id": uuid4(), "created_at": now(), "updated_at": now()}),
        )
        view = await service.create_watchlist(db, name="Watchlist B", description=None)

        self.assertFalse(view.is_active)
        self.assertIsNone(view.description)

    async def test_duplicate_name_is_case_insensitive_conflict(self) -> None:
        db = make_db(Result(scalar=1))  # existing 'CORE'
        with self.assertRaises(service.WatchlistConflictError):
            await service.create_watchlist(db, name="core", description=None)

    async def test_router_maps_duplicate_to_409_and_rolls_back(self) -> None:
        db = make_db(Result(scalar=1))
        with self.assertRaises(HTTPException) as raised:
            await create_watchlist(WatchlistCreate(name="core"), db)
        self.assertEqual(raised.exception.status_code, 409)
        db.rollback.assert_awaited_once()


class WatchlistUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_activate_switches_single_active_invariant(self) -> None:
        db = make_db(
            Result(
                row={
                    "id": uuid4(),
                    "name": "Core",
                    "description": None,
                    "is_active": False,
                }
            ),
            Result(rowcount=1),  # deactivate all other watchlists
            Result(rowcount=1),  # target update
            Result(rows=[watchlist_row(is_active=True)]),
        )
        view = await service.update_watchlist(db, uuid4(), is_active=True)

        self.assertTrue(view.is_active)
        # Deactivation of the others happens before the target is activated.
        self.assertIn("UPDATE watchlists", sql_at(db, 1))
        self.assertIn("is_active = false", sql_at(db, 1))
        self.assertIn("id <> :watchlist_id", sql_at(db, 1))

    async def test_deactivate_keeps_other_active(self) -> None:
        db = make_db(
            Result(
                row={"id": uuid4(), "name": "A", "description": None, "is_active": True}
            ),
            Result(scalar=1),  # another watchlist is still active
            Result(rowcount=1),
            Result(rows=[watchlist_row(is_active=False)]),
        )
        view = await service.update_watchlist(db, uuid4(), is_active=False)
        self.assertFalse(view.is_active)

    async def test_deactivate_only_active_is_rejected(self) -> None:
        db = make_db(
            Result(
                row={
                    "id": uuid4(),
                    "name": "Core",
                    "description": None,
                    "is_active": True,
                }
            ),
            Result(scalar=0),  # no other active watchlist
        )
        with self.assertRaises(service.WatchlistValidationError) as raised:
            await service.update_watchlist(db, uuid4(), is_active=False)
        self.assertIn("active watchlist is required", str(raised.exception))

    async def test_rename_duplicate_is_conflict(self) -> None:
        db = make_db(
            Result(
                row={
                    "id": uuid4(),
                    "name": "Core",
                    "description": None,
                    "is_active": True,
                }
            ),
            Result(scalar=1),  # 'core' taken by another watchlist
        )
        with self.assertRaises(service.WatchlistConflictError):
            await service.update_watchlist(db, uuid4(), name="CORE")

    async def test_missing_watchlist_is_not_found(self) -> None:
        db = make_db(Result(row=None))
        with self.assertRaises(service.WatchlistNotFoundError):
            await service.update_watchlist(db, uuid4(), description="x")


class WatchlistDeleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_removes_row(self) -> None:
        db = make_db(Result(rowcount=1))
        self.assertIsNone(await service.delete_watchlist(db, uuid4()))

    async def test_delete_missing_watchlist_is_not_found(self) -> None:
        db = make_db(Result(rowcount=0))
        with self.assertRaises(service.WatchlistNotFoundError):
            await service.delete_watchlist(db, uuid4())


class WatchlistItemsTests(unittest.IsolatedAsyncioTestCase):
    def _add_results(self, *, instrument_id, removed_row=None) -> list[Result]:
        return [
            Result(scalar=1),  # watchlist exists
            Result(row={"id": instrument_id}),  # fyers_symbol hit
            Result(scalar=None),  # no active duplicate
            Result(row=removed_row) if removed_row else Result(),  # removed row?
            Result(row={"id": uuid4()}),  # insert/update RETURNING id
            Result(row=item_row(instrument_id=instrument_id)),  # joined view
        ]

    async def test_add_by_fyers_symbol(self) -> None:
        instrument_id = uuid4()
        db = make_db(*self._add_results(instrument_id=instrument_id))
        view = await service.add_watchlist_item(db, uuid4(), symbol="NSE:RELIANCE-EQ")

        self.assertEqual(view.instrument_id, instrument_id)
        self.assertEqual(view.symbol, "RELIANCE")
        # First lookup must target fyers_symbol.
        self.assertIn("fyers_symbol", sql_at(db, 1))
        # Fresh adds go through INSERT (no historical removed row).
        self.assertIn("INSERT INTO watchlist_items", sql_at(db, 4))

    async def test_add_reuses_removed_row_as_readd(self) -> None:
        instrument_id = uuid4()
        old_item_id = uuid4()
        db = make_db(
            *self._add_results(
                instrument_id=instrument_id, removed_row={"id": old_item_id}
            )
        )
        view = await service.add_watchlist_item(db, uuid4(), symbol="NSE:RELIANCE-EQ")

        self.assertEqual(view.instrument_id, instrument_id)
        self.assertIn("UPDATE watchlist_items", sql_at(db, 4))
        update_sql = sql_at(db, 4)
        self.assertIn("removed_at = NULL", update_sql)
        self.assertIn("screening_result_id = NULL", update_sql)
        self.assertIn("added_at = now()", update_sql)

    async def test_add_duplicate_active_item_is_conflict(self) -> None:
        instrument_id = uuid4()
        db = make_db(
            Result(scalar=1),  # watchlist exists
            Result(row={"id": instrument_id}),
            Result(scalar=1),  # already active in this watchlist
        )
        with self.assertRaises(service.WatchlistConflictError) as raised:
            await service.add_watchlist_item(db, uuid4(), symbol="NSE:RELIANCE-EQ")
        self.assertEqual(str(raised.exception), "already in watchlist")

    async def test_router_maps_duplicate_add_to_409(self) -> None:
        instrument_id = uuid4()
        db = make_db(
            Result(scalar=1),
            Result(row={"id": instrument_id}),
            Result(scalar=1),
        )
        with self.assertRaises(HTTPException) as raised:
            await add_watchlist_item(
                uuid4(), WatchlistItemCreate(symbol="NSE:RELIANCE-EQ"), db
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail, "already in watchlist")
        db.rollback.assert_awaited_once()

    async def test_add_missing_watchlist_is_not_found(self) -> None:
        db = make_db(Result(row=None))
        with self.assertRaises(service.WatchlistNotFoundError):
            await service.add_watchlist_item(db, uuid4(), symbol="RELIANCE")

    async def test_remove_soft_deletes(self) -> None:
        db = make_db(Result(scalar=1), Result(rowcount=1))
        self.assertIsNone(await service.remove_watchlist_item(db, uuid4(), uuid4()))
        self.assertIn("removed_at = now()", sql_at(db, 1))

    async def test_remove_missing_active_item_is_not_found(self) -> None:
        db = make_db(Result(scalar=1), Result(rowcount=0))
        with self.assertRaises(service.WatchlistItemNotFoundError):
            await service.remove_watchlist_item(db, uuid4(), uuid4())

    async def test_remove_missing_watchlist_is_not_found(self) -> None:
        db = make_db(Result(scalar=None))
        with self.assertRaises(service.WatchlistNotFoundError):
            await service.remove_watchlist_item(db, uuid4(), uuid4())

    async def test_list_items_orders_by_added_at_and_filters_removed(self) -> None:
        db = make_db(
            Result(scalar=1),
            Result(rows=[item_row(), item_row()]),
        )
        items = await service.list_watchlist_items(db, uuid4())

        self.assertEqual(len(items), 2)
        self.assertIn("removed_at IS NULL", sql_at(db, 1))
        self.assertIn("ORDER BY wi.added_at ASC", sql_at(db, 1))

    async def test_list_items_missing_watchlist_is_not_found(self) -> None:
        db = make_db(Result(row=None))
        with self.assertRaises(service.WatchlistNotFoundError):
            await service.list_watchlist_items(db, uuid4())


class InstrumentResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_fyers_symbol_preferred(self) -> None:
        instrument_id = uuid4()
        db = make_db(Result(row={"id": instrument_id}))
        resolved = await service._resolve_instrument(db, "nse:reliance-eq")
        self.assertEqual(resolved, instrument_id)
        self.assertIn("fyers_symbol", sql_at(db, 0))

    async def test_falls_back_to_nse_symbol(self) -> None:
        instrument_id = uuid4()
        db = make_db(Result(), Result(rows=[{"id": instrument_id, "exchange": "NSE"}]))
        resolved = await service._resolve_instrument(db, "RELIANCE")
        self.assertEqual(resolved, instrument_id)
        self.assertIn("lower(symbol) = lower(:symbol)", sql_at(db, 1))

    async def test_prefers_nse_when_multiple_exchanges_match(self) -> None:
        nse_id, bse_id = uuid4(), uuid4()
        db = make_db(
            Result(),
            Result(
                rows=[
                    {"id": bse_id, "exchange": "BSE"},
                    {"id": nse_id, "exchange": "NSE"},
                ]
            ),
        )
        resolved = await service._resolve_instrument(db, "RELIANCE")
        self.assertEqual(resolved, nse_id)

    async def test_ambiguous_without_nse_is_not_found(self) -> None:
        db = make_db(
            Result(),
            Result(
                rows=[
                    {"id": uuid4(), "exchange": "BSE"},
                    {"id": uuid4(), "exchange": "MCX"},
                ]
            ),
        )
        with self.assertRaises(service.InstrumentNotFoundError) as raised:
            await service._resolve_instrument(db, "XYZ")
        self.assertEqual(str(raised.exception), "Instrument not found: XYZ")

    async def test_unknown_symbol_is_not_found(self) -> None:
        db = make_db(Result(), Result(rows=[]))
        with self.assertRaises(service.InstrumentNotFoundError):
            await service._resolve_instrument(db, "NOPE")


class InstrumentSearchTests(unittest.IsolatedAsyncioTestCase):
    def search_row(self) -> dict:
        return {
            "id": uuid4(),
            "symbol": "RELIANCE",
            "trading_symbol": "RELIANCE-EQ",
            "fyers_symbol": "NSE:RELIANCE-EQ",
            "name": "Reliance Industries",
            "exchange": "NSE",
            "segment": "EQ",
        }

    async def test_search_returns_matching_active_instruments(self) -> None:
        row = self.search_row()
        db = make_db(Result(rows=[row]))
        results = await get_instruments(db, q="relian", limit=25)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].symbol, "RELIANCE")
        self.assertEqual(results[0].fyers_symbol, "NSE:RELIANCE-EQ")
        search_sql = sql_at(db, 0)
        self.assertIn("ILIKE", search_sql)
        self.assertIn("active = true", search_sql)
        self.assertIn("ORDER BY symbol ASC", search_sql)
        self.assertIn("LIMIT :limit", search_sql)

    async def test_empty_query_after_trim_is_422(self) -> None:
        db = AsyncMock()
        with self.assertRaises(HTTPException) as raised:
            await get_instruments(db, q="   ")
        self.assertEqual(raised.exception.status_code, 422)
        db.execute.assert_not_awaited()

    async def test_like_pattern_escapes_wildcards(self) -> None:
        self.assertEqual(service._escape_like_pattern("A%_"), r"%A\%\_%")
        self.assertEqual(service._escape_like_pattern("plain"), "%plain%")


class GetWatchlistsRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_endpoint_commits_after_auto_create(self) -> None:
        db = make_db(
            Result(rows=[]),
            Result(rowcount=1),
            Result(rows=[watchlist_row()]),
        )
        views = await get_watchlists(db)
        self.assertEqual(len(views), 1)
        db.commit.assert_awaited_once()


class WatchlistAuthProtectionTests(unittest.TestCase):
    """Unauthenticated callers must be rejected with 401 (session dependency)."""

    def setUp(self):
        from fastapi.testclient import TestClient

        from main import app

        class FakeRedis:
            async def get(self, key: str) -> None:
                return None

        app.state.redis = FakeRedis()
        self.client = TestClient(app, base_url="http://localhost:8000")

    def tearDown(self):
        import main

        main.app.dependency_overrides.clear()

    def test_unauthenticated_watchlist_endpoints_return_401(self):
        for method, path in [
            ("GET", "/api/v1/watchlists"),
            ("POST", "/api/v1/watchlists"),
            ("PATCH", "/api/v1/watchlists/00000000-0000-0000-0000-000000000000"),
            ("DELETE", "/api/v1/watchlists/00000000-0000-0000-0000-000000000000"),
            ("GET", "/api/v1/watchlists/00000000-0000-0000-0000-000000000000/items"),
            ("POST", "/api/v1/watchlists/00000000-0000-0000-0000-000000000000/items"),
            ("GET", "/api/v1/instruments/search?q=rel"),
        ]:
            response = self.client.request(method, path)
            self.assertEqual(
                response.status_code,
                401,
                f"{method} {path} should require auth, got {response.status_code}",
            )


if __name__ == "__main__":
    unittest.main()
