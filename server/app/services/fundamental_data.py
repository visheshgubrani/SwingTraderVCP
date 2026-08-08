"""Read-only Upstox fundamentals client and deterministic fact normalization."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError


class FundamentalsError(RuntimeError):
    """Base error for a fundamentals provider failure."""


class FundamentalsAuthError(FundamentalsError):
    """The configured read-only provider credential is invalid or expired."""


class FundamentalsDataUnavailable(FundamentalsError):
    """The provider has no usable data for the requested ISIN."""


class FundamentalsDataContractError(FundamentalsError):
    """Upstox returned a successful envelope with an unexpected data shape."""


class _UpstoxModel(BaseModel):
    """Provider DTOs accept additive fields while validating fields we consume."""

    model_config = ConfigDict(extra="allow")


class UpstoxHistoryPoint(_UpstoxModel):
    period: str
    value: float | int | str | None = None
    change: float | int | str | None = None


class UpstoxHistorySeries(_UpstoxModel):
    history: list[UpstoxHistoryPoint] | None = None


class UpstoxCategorySeries(UpstoxHistorySeries):
    category: str


class UpstoxStatementLine(UpstoxHistorySeries):
    particular: str


class UpstoxProfileData(_UpstoxModel):
    company_profile: str | None = None
    sector: str | None = None


class UpstoxIncomeData(_UpstoxModel):
    type: str | None = None
    time_period: str | None = None
    units_in: str | None = None
    income_statement: list[UpstoxCategorySeries] | None = None
    full_statement: list[UpstoxStatementLine] | None = None


class UpstoxBalanceSheetData(_UpstoxModel):
    type: str | None = None
    time_period: str | None = None
    units_in: str | None = None
    history: list[dict[str, Any]] | None = None
    full_statement: list[UpstoxStatementLine] | None = None


class UpstoxCashFlowData(_UpstoxModel):
    type: str | None = None
    time_period: str | None = None
    units_in: str | None = None
    cash_flow: list[UpstoxCategorySeries] | None = None
    full_statement: list[UpstoxStatementLine] | None = None


class UpstoxKeyRatio(_UpstoxModel):
    name: str
    company_value: float | int | str | None = None
    sector_value: float | int | str | None = None


class UpstoxShareHolding(UpstoxHistorySeries):
    category: str


class UpstoxCorporateAction(_UpstoxModel):
    name: str | None = None
    expiry_date: str | None = None
    amount: float | int | str | None = None
    ratio: float | int | str | None = None
    event_details: list[dict[str, Any]] | None = None


class UpstoxProfileEnvelope(_UpstoxModel):
    status: Literal["success"]
    data: UpstoxProfileData


class UpstoxIncomeEnvelope(_UpstoxModel):
    status: Literal["success"]
    data: UpstoxIncomeData


class UpstoxBalanceSheetEnvelope(_UpstoxModel):
    status: Literal["success"]
    data: UpstoxBalanceSheetData


class UpstoxCashFlowEnvelope(_UpstoxModel):
    status: Literal["success"]
    data: UpstoxCashFlowData


class UpstoxKeyRatiosEnvelope(_UpstoxModel):
    status: Literal["success"]
    data: list[UpstoxKeyRatio]


class UpstoxShareHoldingsEnvelope(_UpstoxModel):
    status: Literal["success"]
    data: list[UpstoxShareHolding]


class UpstoxCorporateActionsEnvelope(_UpstoxModel):
    status: Literal["success"]
    data: list[UpstoxCorporateAction]


UPSTOX_FUNDAMENTALS_ENDPOINTS: tuple[tuple[str, str, dict[str, str] | None], ...] = (
    ("company_profile", "profile", None),
    (
        "income_yearly",
        "income-statement",
        {"type": "consolidated", "time_period": "yearly", "fs": "true"},
    ),
    (
        "income_quarterly",
        "income-statement",
        {"type": "consolidated", "time_period": "quarterly", "fs": "false"},
    ),
    ("balance_sheet", "balance-sheet", {"type": "consolidated", "fs": "true"}),
    ("cash_flow", "cash-flow", {"type": "consolidated", "fs": "true"}),
    ("key_ratios", "key-ratios", None),
    ("share_holdings", "share-holdings", None),
    ("corporate_actions", "corporate-actions", None),
)


_UPSTOX_BUNDLE_CONTRACTS: dict[str, type[BaseModel]] = {
    "company_profile": UpstoxProfileEnvelope,
    "income_yearly": UpstoxIncomeEnvelope,
    "income_quarterly": UpstoxIncomeEnvelope,
    "balance_sheet": UpstoxBalanceSheetEnvelope,
    "cash_flow": UpstoxCashFlowEnvelope,
    "key_ratios": UpstoxKeyRatiosEnvelope,
    "share_holdings": UpstoxShareHoldingsEnvelope,
    "corporate_actions": UpstoxCorporateActionsEnvelope,
}


def validate_upstox_section(section: str, payload: Any) -> None:
    """Validate one endpoint boundary without replacing its exact raw JSON."""

    contract = _UPSTOX_BUNDLE_CONTRACTS[section]
    try:
        contract.model_validate(payload)
    except ValidationError as exc:
        raise FundamentalsDataContractError(
            f"Upstox {section} response did not match its contract: {exc.errors(include_url=False)}"
        ) from exc


def validate_upstox_bundle(bundle: Mapping[str, Any]) -> None:
    """Validate every endpoint boundary without discarding the raw provider JSON."""

    for section in _UPSTOX_BUNDLE_CONTRACTS:
        validate_upstox_section(section, bundle.get(section))


def upstox_endpoint_manifest(
    isin: str,
    *,
    statement_type: str = "consolidated",
) -> list[dict[str, Any]]:
    """Return the token-free request manifest shown in the personal trace UI."""

    manifest: list[dict[str, Any]] = []
    for section, endpoint, default_params in UPSTOX_FUNDAMENTALS_ENDPOINTS:
        params = dict(default_params or {})
        if "type" in params:
            params["type"] = statement_type
        manifest.append(
            {
                "section": section,
                "method": "GET",
                "path": f"/fundamentals/{isin}/{endpoint}",
                "params": params,
            }
        )
    return manifest


def canonical_json_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class UpstoxFundamentalsClient:
    """Async, GET-only client for the Upstox Company Fundamentals API."""

    def __init__(
        self,
        *,
        analytics_token: str,
        base_url: str = "https://api.upstox.com/v2",
        timeout_seconds: float = 20.0,
        max_attempts: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = _default_sleep,
    ) -> None:
        self._token = analytics_token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._transport = transport
        self._sleep = sleep
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "UpstoxFundamentalsClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_company_bundle(
        self,
        isin: str,
        *,
        statement_type: str = "consolidated",
    ) -> dict[str, Any]:
        if not self._token:
            raise FundamentalsAuthError("UPSTOX_ANALYTICS_TOKEN is not configured")
        if not isin:
            raise FundamentalsDataUnavailable("Instrument has no ISIN")

        bundle: dict[str, Any] = {}
        for key, endpoint, default_params in UPSTOX_FUNDAMENTALS_ENDPOINTS:
            params = dict(default_params or {})
            if "type" in params:
                params["type"] = statement_type
            payload = await self._get(
                f"/fundamentals/{isin}/{endpoint}",
                params=params or None,
            )
            validate_upstox_section(key, payload)
            bundle[key] = payload
        return bundle

    async def _get(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
        }
        last_error: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                if self._client is None:
                    async with httpx.AsyncClient(
                        base_url=self._base_url,
                        timeout=self._timeout,
                        transport=self._transport,
                    ) as transient_client:
                        response = await transient_client.get(path, params=params, headers=headers)
                else:
                    response = await self._client.get(path, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt == self._max_attempts:
                    break
                await self._sleep(0.5 * (2 ** (attempt - 1)))
                continue

            if response.status_code == 401:
                raise FundamentalsAuthError(
                    "Upstox Analytics Token was rejected or has expired"
                )
            if response.status_code == 404:
                raise FundamentalsDataUnavailable(
                    f"Upstox has no fundamentals for {path}"
                )
            if response.status_code == 429 or response.status_code >= 500:
                last_error = FundamentalsError(
                    f"Upstox returned HTTP {response.status_code} for {path}"
                )
                if attempt == self._max_attempts:
                    break
                retry_after = _number(response.headers.get("Retry-After"))
                delay = (
                    min(float(retry_after), 10.0)
                    if retry_after is not None
                    else 0.5 * (2 ** (attempt - 1))
                )
                await self._sleep(delay)
                continue
            if response.status_code >= 400:
                raise FundamentalsError(
                    f"Upstox returned HTTP {response.status_code} for {path}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                last_error = FundamentalsError(
                    f"Upstox returned invalid JSON for {path}"
                )
                if attempt == self._max_attempts:
                    break
                await self._sleep(0.5 * (2 ** (attempt - 1)))
                continue

            if not isinstance(payload, dict):
                raise FundamentalsError(f"Upstox returned a non-object for {path}")
            if payload.get("status") != "success":
                message = payload.get("message") or payload.get("errors") or "unknown"
                raise FundamentalsError(f"Upstox rejected {path}: {message}")
            if "data" not in payload:
                raise FundamentalsDataUnavailable(
                    f"Upstox returned no data field for {path}"
                )
            return payload

        raise FundamentalsError(
            f"Upstox request failed after {self._max_attempts} attempts: "
            f"{last_error or path}"
        )


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        normalized = value.strip().replace(",", "").replace("%", "")
        if not normalized or normalized in {"-", "--", "NA", "N/A", "null"}:
            return None
        try:
            number = float(normalized)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def _data(payload: Mapping[str, Any] | None) -> Any:
    return payload.get("data") if isinstance(payload, Mapping) else None


def _period_date(period: Any) -> datetime | None:
    if not isinstance(period, str):
        return None
    for pattern in ("%b %Y", "%B %Y", "%Y-%m-%d", "%Y"):
        try:
            return datetime.strptime(period.strip(), pattern)
        except ValueError:
            continue
    return None


def _history(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    normalized = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        value = _number(row.get("value"))
        period = row.get("period")
        if value is None or not isinstance(period, str):
            continue
        normalized.append(
            {
                "period": period,
                "value": value,
                "provider_change_pct": _number(row.get("change")),
            }
        )
    return normalized


def _category_history(
    rows: Any,
    category: str,
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    for row in rows:
        if (
            isinstance(row, Mapping)
            and str(row.get("category", "")).lower() == category.lower()
        ):
            return _history(row.get("history"))
    return []


def _statement_history(
    rows: Any,
    *,
    exact: tuple[str, ...] = (),
    contains: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    normalized_exact = {name.casefold() for name in exact}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        particular = str(row.get("particular", "")).strip().casefold()
        if particular in normalized_exact:
            return _history(row.get("history"))
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        particular = str(row.get("particular", "")).strip().casefold()
        if any(fragment.casefold() in particular for fragment in contains):
            return _history(row.get("history"))
    return []


def _history_by_period(history: list[dict[str, Any]]) -> dict[str, float]:
    return {str(item["period"]): float(item["value"]) for item in history}


def _cagr(history: list[dict[str, Any]]) -> float | None:
    dated = [
        (parsed, float(item["value"]))
        for item in history
        if (parsed := _period_date(item.get("period"))) is not None
    ]
    if len(dated) < 2:
        return None
    newest_date, newest = max(dated, key=lambda item: item[0])
    oldest_date, oldest = min(dated, key=lambda item: item[0])
    years = (newest_date - oldest_date).days / 365.25
    if years < 2.5 or oldest <= 0 or newest < 0:
        return None
    return ((newest / oldest) ** (1 / years) - 1) * 100


def _latest_yoy(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    dated: dict[tuple[int, int], tuple[str, float]] = {}
    for item in history:
        parsed = _period_date(item.get("period"))
        if parsed is not None:
            dated[(parsed.year, parsed.month)] = (
                str(item["period"]),
                float(item["value"]),
            )
    for year, month in sorted(dated, reverse=True):
        previous = dated.get((year - 1, month))
        current = dated[(year, month)]
        if previous is None or previous[1] == 0:
            continue
        return {
            "period": current[0],
            "prior_period": previous[0],
            "value_pct": ((current[1] - previous[1]) / abs(previous[1])) * 100,
        }
    return None


def _margin_history(
    profit_history: list[dict[str, Any]],
    revenue_history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    profits = _history_by_period(profit_history)
    revenues = _history_by_period(revenue_history)
    result = []
    for period in revenues:
        revenue = revenues[period]
        if period in profits and revenue != 0:
            result.append(
                {
                    "period": period,
                    "value_pct": profits[period] / revenue * 100,
                }
            )
    return result


def _latest_margin_change(
    margins: list[dict[str, Any]],
) -> dict[str, Any] | None:
    history = [
        {"period": item["period"], "value": item["value_pct"]}
        for item in margins
    ]
    dated = {
        (parsed.year, parsed.month): item
        for item in history
        if (parsed := _period_date(item.get("period"))) is not None
    }
    for year, month in sorted(dated, reverse=True):
        previous = dated.get((year - 1, month))
        current = dated[(year, month)]
        if previous is not None:
            return {
                "period": current["period"],
                "prior_period": previous["period"],
                "change_percentage_points": current["value"] - previous["value"],
            }
    return None


def _ratio_map(rows: Any) -> dict[str, dict[str, float | None]]:
    if not isinstance(rows, list):
        return {}
    result: dict[str, dict[str, float | None]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not row.get("name"):
            continue
        key = (
            str(row["name"])
            .casefold()
            .replace("/", "_")
            .replace(" ", "_")
            .replace("&", "and")
        )
        result[key] = {
            "company": _number(row.get("company_value")),
            "sector": _number(row.get("sector_value")),
        }
    return result


def _profile_value(profile: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = profile.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _is_financial_sector(sector: str | None, industry: str | None) -> bool:
    text = f"{sector or ''} {industry or ''}".casefold()
    terms = (
        "bank",
        "financial",
        "finance",
        "nbfc",
        "insurance",
        "asset management",
        "capital market",
        "lending",
    )
    return any(term in text for term in terms)


def _latest_ratio(
    numerator: list[dict[str, Any]],
    denominator: list[dict[str, Any]],
) -> dict[str, Any] | None:
    numerators = _history_by_period(numerator)
    denominators = _history_by_period(denominator)
    for item in numerator:
        period = str(item["period"])
        divisor = denominators.get(period)
        if divisor not in (None, 0):
            return {"period": period, "value": numerators[period] / divisor}
    return None


def _average_positive_pat_cash_conversion(
    cash_flow: list[dict[str, Any]],
    net_profit: list[dict[str, Any]],
) -> float | None:
    cash = _history_by_period(cash_flow)
    profit = _history_by_period(net_profit)
    ratios = [
        cash[period] / profit[period]
        for period in cash
        if period in profit and profit[period] > 0
    ]
    if not ratios:
        return None
    return sum(ratios[:3]) / min(len(ratios), 3)


def normalize_fundamentals(
    bundle: Mapping[str, Any],
    *,
    isin: str,
    symbol: str,
    company_name: str | None,
    statement_type: str = "consolidated",
) -> dict[str, Any]:
    """Build a compact, auditable fact set; all arithmetic happens here."""

    profile_data = _data(bundle.get("company_profile"))
    profile = profile_data if isinstance(profile_data, Mapping) else {}
    yearly_data = _data(bundle.get("income_yearly"))
    yearly = yearly_data if isinstance(yearly_data, Mapping) else {}
    quarterly_data = _data(bundle.get("income_quarterly"))
    quarterly = quarterly_data if isinstance(quarterly_data, Mapping) else {}
    balance_data = _data(bundle.get("balance_sheet"))
    balance = balance_data if isinstance(balance_data, Mapping) else {}
    cash_data = _data(bundle.get("cash_flow"))
    cash = cash_data if isinstance(cash_data, Mapping) else {}

    annual_income = yearly.get("income_statement")
    quarterly_income = quarterly.get("income_statement")
    annual_revenue = _category_history(annual_income, "revenue")
    annual_operating_profit = _category_history(
        annual_income,
        "operating_profit",
    )
    annual_net_profit = _category_history(annual_income, "net_profit")
    quarterly_revenue = _category_history(quarterly_income, "revenue")
    quarterly_operating_profit = _category_history(
        quarterly_income,
        "operating_profit",
    )
    quarterly_net_profit = _category_history(quarterly_income, "net_profit")
    annual_eps = _statement_history(
        yearly.get("full_statement"),
        exact=("EPS - Basic", "Basic EPS"),
        contains=("eps - basic", "basic eps"),
    )
    operating_cash_flow = _category_history(cash.get("cash_flow"), "operating")

    annual_margins = _margin_history(annual_operating_profit, annual_revenue)
    quarterly_margins = _margin_history(
        quarterly_operating_profit,
        quarterly_revenue,
    )
    revenue_yoy = _latest_yoy(quarterly_revenue)
    profit_yoy = _latest_yoy(quarterly_net_profit)
    annual_revenue_yoy = _latest_yoy(annual_revenue)
    annual_profit_yoy = _latest_yoy(annual_net_profit)
    annual_eps_yoy = _latest_yoy(annual_eps)
    margin_change = _latest_margin_change(quarterly_margins)
    annual_margin_change = _latest_margin_change(annual_margins)
    revenue_cagr = _cagr(annual_revenue)
    profit_cagr = _cagr(annual_net_profit)
    eps_cagr = _cagr(annual_eps)

    ratios = _ratio_map(_data(bundle.get("key_ratios")))
    shareholding_rows = _data(bundle.get("share_holdings"))
    shareholding: dict[str, list[dict[str, Any]]] = {}
    if isinstance(shareholding_rows, list):
        for row in shareholding_rows:
            if isinstance(row, Mapping) and row.get("category"):
                shareholding[str(row["category"])] = _history(row.get("history"))

    ownership_changes = {}
    for category, history in shareholding.items():
        if len(history) >= 2:
            ownership_changes[category] = {
                "latest_period": history[0]["period"],
                "oldest_period": history[-1]["period"],
                "change_percentage_points": history[0]["value"]
                - history[-1]["value"],
            }

    sector = _profile_value(profile, "sector", "sector_name")
    industry = _profile_value(profile, "industry", "industry_name")
    financial_sector = _is_financial_sector(sector, industry)

    cash_conversion = (
        None
        if financial_sector
        else _average_positive_pat_cash_conversion(
            operating_cash_flow,
            annual_net_profit,
        )
    )

    actions_data = _data(bundle.get("corporate_actions"))
    corporate_actions = []
    if isinstance(actions_data, list):
        for action in actions_data[:10]:
            if isinstance(action, Mapping):
                normalized_action = {
                    key: action.get(key)
                    for key in ("name", "expiry_date", "amount", "ratio")
                    if action.get(key) is not None
                }
                event_details = action.get("event_details")
                if isinstance(event_details, list):
                    normalized_action["event_details"] = [
                        {
                            "name": detail.get("name"),
                            "value": detail.get("value"),
                        }
                        for detail in event_details
                        if isinstance(detail, Mapping)
                        and detail.get("name") is not None
                        and detail.get("value") is not None
                    ][:20]
                corporate_actions.append(normalized_action)

    evidence: dict[str, dict[str, Any]] = {}

    def add_evidence(
        key: str,
        value: Any,
        *,
        label: str,
        unit: str | None = None,
        periods: list[str] | None = None,
    ) -> None:
        if value is None:
            return
        evidence[key] = {
            "label": label,
            "value": value,
            "unit": unit,
            "periods": periods or [],
        }

    add_evidence(
        "growth.annual_revenue_cagr",
        revenue_cagr,
        label="Annual revenue CAGR",
        unit="percent",
        periods=[item["period"] for item in annual_revenue],
    )
    add_evidence(
        "growth.annual_net_profit_cagr",
        profit_cagr,
        label="Annual net-profit CAGR",
        unit="percent",
        periods=[item["period"] for item in annual_net_profit],
    )
    add_evidence(
        "growth.annual_eps_cagr",
        eps_cagr,
        label="Annual basic EPS CAGR",
        unit="percent",
        periods=[item["period"] for item in annual_eps],
    )
    add_evidence(
        "growth.latest_annual_revenue_yoy",
        annual_revenue_yoy,
        label="Latest annual revenue growth",
        unit="percent",
    )
    add_evidence(
        "growth.latest_annual_net_profit_yoy",
        annual_profit_yoy,
        label="Latest annual net-profit growth",
        unit="percent",
    )
    add_evidence(
        "growth.latest_annual_eps_yoy",
        annual_eps_yoy,
        label="Latest annual basic EPS growth",
        unit="percent",
    )
    add_evidence(
        "growth.latest_quarter_revenue_yoy",
        revenue_yoy,
        label="Latest available quarterly revenue YoY",
        unit="percent",
    )
    add_evidence(
        "growth.latest_quarter_net_profit_yoy",
        profit_yoy,
        label="Latest available quarterly net-profit YoY",
        unit="percent",
    )
    add_evidence(
        "margins.latest_quarter_yoy_change",
        margin_change,
        label="Latest operating-margin YoY change",
        unit="percentage_points",
    )
    add_evidence(
        "margins.latest_annual_yoy_change",
        annual_margin_change,
        label="Latest annual operating-margin change",
        unit="percentage_points",
    )
    add_evidence(
        "quality.cash_from_operations_to_pat_3y",
        cash_conversion,
        label="Average cash from operations divided by PAT",
        unit="ratio",
    )
    for ratio_name in ("roe", "roce", "roa", "p_e", "p_b", "ev_ebitda"):
        values = ratios.get(ratio_name)
        if values:
            add_evidence(
                f"ratios.{ratio_name}",
                values,
                label=ratio_name.upper().replace("_", "/"),
            )
    for category, change in ownership_changes.items():
        add_evidence(
            f"ownership.{category}_change",
            change,
            label=f"{category.replace('_', ' ').title()} holding change",
            unit="percentage_points",
        )
    if corporate_actions:
        add_evidence(
            "corporate_actions.recent",
            corporate_actions,
            label="Recent corporate actions returned by Upstox",
        )

    # Documented Upstox endpoints do not provide quarterly EPS, promoter
    # pledge, or a canonical debt-to-equity metric. Do not infer those from
    # unrelated statement totals or present their absence as a red flag.
    provider_limitations = [
        "quarterly_eps_yoy",
        "quarterly_sales_yoy" if revenue_yoy is None else "quarterly_sales_yoy_history",
        "debt_to_equity",
        "promoter_pledge",
    ]
    missing_data: list[str] = []
    expected = {
        "annual_revenue_history": annual_revenue,
        "annual_net_profit_history": annual_net_profit,
        "quarterly_revenue_history": quarterly_revenue,
        "quarterly_net_profit_history": quarterly_net_profit,
        "annual_eps_history": annual_eps,
        "roe": ratios.get("roe"),
        "roce": ratios.get("roce"),
        "shareholding_history": shareholding,
    }
    missing_data.extend(key for key, value in expected.items() if not value)
    if not financial_sector and cash_conversion is None:
        missing_data.append("cash_conversion")

    latest_annual_period = (
        str(annual_revenue[0]["period"]) if annual_revenue else None
    )
    latest_quarterly_period = (
        str(quarterly_revenue[0]["period"]) if quarterly_revenue else None
    )

    return {
        "schema_version": "fundamental_facts_v3",
        "company": {
            "isin": isin,
            "symbol": symbol,
            "name": _profile_value(
                profile,
                "company_name",
                "name",
            )
            or company_name,
            "sector": sector,
            "industry": industry,
            "description": _profile_value(
                profile,
                "company_profile",
                "business_description",
                "description",
            ),
            "is_financial_sector": financial_sector,
        },
        "statement_type": statement_type,
        "periods": {
            "latest_annual": latest_annual_period,
            "latest_quarterly": latest_quarterly_period,
        },
        "histories": {
            "annual": {
                "revenue": annual_revenue,
                "operating_profit": annual_operating_profit,
                "net_profit": annual_net_profit,
                "basic_eps": annual_eps,
                "operating_margin": annual_margins,
                "cash_from_operations": operating_cash_flow,
            },
            "quarterly": {
                "revenue": quarterly_revenue,
                "operating_profit": quarterly_operating_profit,
                "net_profit": quarterly_net_profit,
                "operating_margin": quarterly_margins,
                "basic_eps": None,
            },
            "shareholding": shareholding,
        },
        "ratios": ratios,
        "applicability": {
            "cash_conversion": (
                "not_applicable" if financial_sector else "applicable"
            ),
            "industrial_leverage": (
                "not_applicable" if financial_sector else "applicable"
            ),
        },
        "coverage": {
            "annual_revenue": "available" if annual_revenue else "not_returned",
            "annual_net_profit": "available" if annual_net_profit else "not_returned",
            "annual_eps": "available" if annual_eps else "not_returned",
            "quarterly_revenue": "available" if quarterly_revenue else "not_returned",
            "quarterly_net_profit": "available" if quarterly_net_profit else "not_returned",
            "cash_conversion": (
                "not_applicable" if financial_sector else ("available" if cash_conversion is not None else "not_returned")
            ),
            "quarterly_eps": "unsupported_by_provider",
            "quarterly_eps_yoy": "unsupported_by_provider",
            "quarterly_sales_yoy": (
                "available" if revenue_yoy is not None else "not_returned"
            ),
            "debt_to_equity": "unsupported_by_provider",
            "promoter_pledge": "unsupported_by_provider",
        },
        "provider_limitations": provider_limitations,
        # Keep every documented read-only section available to the review API.
        # The OpenRouter packet intentionally does not include this raw detail.
        "provider_sections": {
            "company_profile": profile,
            "income_yearly": yearly,
            "income_quarterly": quarterly,
            "balance_sheet": balance,
            "cash_flow": cash,
            "key_ratios": _data(bundle.get("key_ratios")),
            "share_holdings": _data(bundle.get("share_holdings")),
            "corporate_actions": _data(bundle.get("corporate_actions")),
        },
        "evidence": evidence,
        "missing_data": sorted(set(missing_data)),
    }
