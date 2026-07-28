"""Read-only Fyers broker book fetches for reconciliation."""

from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings


class FyersBrokerReadError(RuntimeError):
    """Raised when a Fyers read API returns an error response."""


@dataclass(frozen=True)
class BrokerBooks:
    orders: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    positions: list[dict[str, Any]]
    holdings: list[dict[str, Any]]


class FyersBrokerReadClient:
    """Async httpx client for Fyers order/trade/position/holding reads."""

    def __init__(
        self,
        *,
        app_id: str,
        base_url: str | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._app_id = app_id
        self._base_url = (base_url or settings.fyers_api_base_url).rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    async def fetch_all(self, *, access_token: str) -> BrokerBooks:
        headers = {
            "Authorization": f"{self._app_id}:{access_token}",
            "Content-Type": "application/json",
            "version": "3",
        }
        async with httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            orders = await self._fetch_list(
                client,
                headers=headers,
                path="/orders",
                list_key="orderBook",
            )
            trades = await self._fetch_list(
                client,
                headers=headers,
                path="/tradebook",
                list_key="tradeBook",
            )
            positions = await self._fetch_list(
                client,
                headers=headers,
                path="/positions",
                list_key="netPositions",
            )
            holdings = await self._fetch_list(
                client,
                headers=headers,
                path="/holdings",
                list_key="holdings",
            )
        return BrokerBooks(
            orders=[normalize_order(row) for row in orders],
            trades=[normalize_trade(row) for row in trades],
            positions=[normalize_position(row) for row in positions],
            holdings=[normalize_holding(row) for row in holdings],
        )

    async def _fetch_list(
        self,
        client: httpx.AsyncClient,
        *,
        headers: dict[str, str],
        path: str,
        list_key: str,
    ) -> list[dict[str, Any]]:
        response = await client.get(f"{self._base_url}{path}", headers=headers)
        try:
            data = response.json()
        except ValueError as exc:
            raise FyersBrokerReadError(
                f"Fyers {path} returned non-JSON (HTTP {response.status_code})."
            ) from exc
        if not isinstance(data, dict):
            raise FyersBrokerReadError(f"Fyers {path} returned an unexpected payload.")
        if data.get("s") == "error" or response.is_error:
            message = str(data.get("message") or f"Fyers {path} request failed.")
            raise FyersBrokerReadError(message)
        rows = data.get(list_key)
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise FyersBrokerReadError(
                f"Fyers {path} returned unexpected {list_key} payload."
            )
        return [row for row in rows if isinstance(row, dict)]

    def assert_read_only_paths(self, url: httpx.URL) -> None:
        path = url.path.lower()
        forbidden = ("/orders/async", "/exit", "/convert")
        if any(fragment in path for fragment in forbidden):
            raise AssertionError(f"Reconciliation client must not call {url.path}")


def normalize_order(row: dict[str, Any]) -> dict[str, Any]:
    """Map REST order fields to gateway-compatible aliases."""
    return {
        **row,
        "id": row.get("id") or row.get("orderNumber"),
        "orderNumber": row.get("orderNumber") or row.get("id"),
        "exchOrdId": row.get("exchOrdId") or row.get("exchangeOrderNo"),
        "exchangeOrderNo": row.get("exchangeOrderNo") or row.get("exchOrdId"),
        "id_fyers": row.get("id_fyers") or row.get("idFyers"),
        "idFyers": row.get("idFyers") or row.get("id_fyers"),
        "orderTag": row.get("orderTag") or row.get("ordertag"),
    }


def normalize_trade(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "orderNumber": row.get("orderNumber") or row.get("id"),
        "exchangeOrderNo": row.get("exchangeOrderNo") or row.get("exchOrdId"),
        "exchOrdId": row.get("exchOrdId") or row.get("exchangeOrderNo"),
        "id_fyers": row.get("id_fyers") or row.get("idFyers"),
        "idFyers": row.get("idFyers") or row.get("id_fyers"),
        "tradedQty": row.get("tradedQty") or row.get("tradeQty") or row.get("qty"),
        "tradePrice": row.get("tradePrice") or row.get("tradedPrice"),
    }


def normalize_position(row: dict[str, Any]) -> dict[str, Any]:
    net_qty = row.get("netQty")
    if net_qty is None:
        net_qty = row.get("qty")
    return {
        **row,
        "netQty": net_qty,
        "avgPrice": row.get("avgPrice") or row.get("netAvg"),
    }


def normalize_holding(row: dict[str, Any]) -> dict[str, Any]:
    remaining = row.get("remainingQuantity")
    if remaining is None:
        remaining = row.get("quantity")
    return {
        **row,
        "remainingQuantity": remaining,
    }
