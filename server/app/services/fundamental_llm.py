"""Auditable, grounded OpenRouter second opinions for P7 fundamentals."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.fundamental_data import canonical_json_hash
from app.services.openrouter_content import parse_openrouter_structured_content


class FundamentalLLMError(RuntimeError):
    """A model request failed, with safe provider metadata retained for tracing."""

    def __init__(
        self,
        message: str,
        *,
        response_payload: dict[str, Any] | None = None,
        http_status: int | None = None,
        request_id: str | None = None,
        usage: dict[str, Any] | None = None,
        cost: float = 0.0,
        retryable: bool = False,
        attempt_status: Literal[
            "invalid_response",
            "provider_error",
            "transport_unknown",
        ] = "provider_error",
    ) -> None:
        super().__init__(message)
        self.response_payload = response_payload
        self.http_status = http_status
        self.request_id = request_id
        self.usage = usage or {}
        self.cost = cost
        self.retryable = retryable
        self.attempt_status = attempt_status


class FundamentalReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    kind: Literal["metric", "history", "limitation"]
    label: str = Field(min_length=1, max_length=160)
    value: Any = None
    unit: str | None = None
    periods: list[str] = Field(default_factory=list, max_length=6)


class FundamentalCompanyContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    isin: str | None = None
    symbol: str | None = None
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    is_financial_sector: bool = False


class FundamentalPeriodContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latest_annual: str | None = None
    latest_quarterly: str | None = None


class FundamentalSecondOpinionPacketV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["fundamental_second_opinion_packet_v1"] = (
        "fundamental_second_opinion_packet_v1"
    )
    prompt_version: str
    source_provider: Literal["upstox"] = "upstox"
    statement_type: Literal["consolidated", "standalone"] = "consolidated"
    company: FundamentalCompanyContext
    periods: FundamentalPeriodContext
    references: list[FundamentalReference] = Field(default_factory=list, max_length=64)


class ReferenceNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=180)
    reference_ids: list[str] = Field(min_length=1, max_length=3)


class FundamentalSecondOpinion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pass", "fail", "uncertain"]
    summary: str = Field(min_length=1, max_length=400)
    verdict_reference_ids: list[str] = Field(min_length=1, max_length=5)
    strengths: list[ReferenceNote] = Field(default_factory=list, max_length=3)
    risks: list[ReferenceNote] = Field(default_factory=list, max_length=3)
    review_focus: list[ReferenceNote] = Field(default_factory=list, max_length=2)


@dataclass(frozen=True)
class PreparedFundamentalRequest:
    packet: FundamentalSecondOpinionPacketV1
    request_payload: dict[str, Any]
    input_hash: str
    has_usable_facts: bool


@dataclass(frozen=True)
class FundamentalLLMResult:
    opinion: FundamentalSecondOpinion
    request_id: str | None
    usage: dict[str, Any]
    input_hash: str
    cost: float
    request_payload: dict[str, Any]
    response_payload: dict[str, Any]


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def sanitize_provider_payload(value: Any) -> Any:
    """Remove reasoning payloads recursively before persistence or exposure."""

    if isinstance(value, Mapping):
        return {
            str(key): sanitize_provider_payload(item)
            for key, item in value.items()
            if str(key) != "reasoning_details"
        }
    if isinstance(value, list):
        return [sanitize_provider_payload(item) for item in value]
    return value


def _humanize_reference(value: str) -> str:
    return value.replace(".", " ").replace("_", " ").title()


def _compact_corporate_actions(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    compact: list[dict[str, Any]] = []
    for action in value[:5]:
        if not isinstance(action, Mapping):
            continue
        compact.append(
            {
                key: action.get(key)
                for key in ("name", "expiry_date", "amount", "ratio")
                if action.get(key) is not None
            }
        )
    return compact


def _usage_and_cost(payload: Mapping[str, Any]) -> tuple[dict[str, Any], float]:
    raw_usage = payload.get("usage")
    usage = dict(raw_usage) if isinstance(raw_usage, Mapping) else {}
    try:
        cost = float(usage.get("cost", 0) or 0)
    except (TypeError, ValueError):
        cost = 0.0
    return usage, cost


class OpenRouterFundamentalClient:
    """One blind, grounded AI opinion over normalized facts; never a trade action."""

    def __init__(
        self,
        *,
        api_key: str,
        api_url: str = "https://openrouter.ai/api/v1/chat/completions",
        model: str = "openai/gpt-5.6-luna-pro",
        reasoning_effort: str = "medium",
        prompt_version: str = "fundamental_second_opinion_v1",
        app_title: str = "SwingTraderVCP",
        http_referer: str = "",
        timeout_seconds: float = 60.0,
        max_attempts: int = 2,
        max_tokens: int = 8192,
        prompt_max_chars: int = 12000,
        temperature: float | None = 0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = _default_sleep,
    ) -> None:
        self._api_key = api_key
        self._api_url = api_url
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._prompt_version = prompt_version
        self._app_title = app_title
        self._http_referer = http_referer
        self._timeout = timeout_seconds
        self._max_attempts = min(max_attempts, 2)
        self._max_tokens = max_tokens
        self._prompt_max_chars = prompt_max_chars
        self._temperature = temperature
        self._transport = transport
        self._sleep = sleep

    @property
    def model(self) -> str:
        return self._model

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    @property
    def reasoning_effort(self) -> str:
        return self._reasoning_effort

    @staticmethod
    def _packet_references(facts: Mapping[str, Any]) -> list[FundamentalReference]:
        references: dict[str, FundamentalReference] = {}
        raw_evidence = facts.get("evidence")
        if isinstance(raw_evidence, Mapping):
            for key, raw in raw_evidence.items():
                if not isinstance(key, str) or not isinstance(raw, Mapping):
                    continue
                value = raw.get("value")
                if key == "corporate_actions.recent":
                    value = _compact_corporate_actions(value)
                references[key] = FundamentalReference(
                    id=key,
                    kind="metric",
                    label=str(raw.get("label") or _humanize_reference(key)),
                    value=value,
                    unit=str(raw["unit"]) if raw.get("unit") is not None else None,
                    periods=[
                        str(period)
                        for period in raw.get("periods", [])[:6]
                        if isinstance(period, str)
                    ]
                    if isinstance(raw.get("periods"), list)
                    else [],
                )

        histories = facts.get("histories")
        if isinstance(histories, Mapping):
            for scope in ("annual", "quarterly"):
                series = histories.get(scope)
                if not isinstance(series, Mapping):
                    continue
                for metric, points in series.items():
                    if not isinstance(metric, str) or not isinstance(points, list) or not points:
                        continue
                    compact_points = [dict(point) for point in points[:5] if isinstance(point, Mapping)]
                    if not compact_points:
                        continue
                    reference_id = f"history.{scope}.{metric}"
                    references[reference_id] = FundamentalReference(
                        id=reference_id,
                        kind="history",
                        label=f"{scope.title()} {_humanize_reference(metric)} history",
                        value=compact_points,
                        periods=[
                            str(point["period"])
                            for point in compact_points
                            if isinstance(point.get("period"), str)
                        ][:5],
                    )

            shareholding = histories.get("shareholding")
            if isinstance(shareholding, Mapping):
                for category, points in shareholding.items():
                    if not isinstance(category, str) or not isinstance(points, list) or not points:
                        continue
                    compact_points = [dict(point) for point in points[:4] if isinstance(point, Mapping)]
                    reference_id = f"history.shareholding.{category}"
                    references[reference_id] = FundamentalReference(
                        id=reference_id,
                        kind="history",
                        label=f"{_humanize_reference(category)} shareholding history",
                        value=compact_points,
                        unit="percent",
                        periods=[
                            str(point["period"])
                            for point in compact_points
                            if isinstance(point.get("period"), str)
                        ][:4],
                    )

        limitations = facts.get("provider_limitations")
        if isinstance(limitations, list):
            for limitation in limitations:
                if not isinstance(limitation, str):
                    continue
                reference_id = f"limitation.{limitation}"
                references[reference_id] = FundamentalReference(
                    id=reference_id,
                    kind="limitation",
                    label=f"Upstox limitation: {_humanize_reference(limitation)}",
                )
        return [references[key] for key in sorted(references)]

    def build_packet(self, facts: Mapping[str, Any]) -> FundamentalSecondOpinionPacketV1:
        raw_company = facts.get("company") if isinstance(facts.get("company"), Mapping) else {}
        raw_periods = facts.get("periods") if isinstance(facts.get("periods"), Mapping) else {}
        packet = FundamentalSecondOpinionPacketV1(
            prompt_version=self._prompt_version,
            statement_type=(
                facts.get("statement_type")
                if facts.get("statement_type") in {"consolidated", "standalone"}
                else "consolidated"
            ),
            company=FundamentalCompanyContext(
                isin=raw_company.get("isin"),
                symbol=raw_company.get("symbol"),
                name=raw_company.get("name"),
                sector=raw_company.get("sector"),
                industry=raw_company.get("industry"),
                is_financial_sector=bool(raw_company.get("is_financial_sector")),
            ),
            periods=FundamentalPeriodContext(
                latest_annual=raw_periods.get("latest_annual"),
                latest_quarterly=raw_periods.get("latest_quarterly"),
            ),
            references=self._packet_references(facts),
        )
        encoded = json.dumps(packet.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        if len(encoded) > self._prompt_max_chars:
            raise FundamentalLLMError(
                "Fundamental second-opinion packet exceeds configured size limit",
                attempt_status="invalid_response",
            )
        return packet

    @staticmethod
    def _response_schema(allowed_ids: list[str]) -> dict[str, Any]:
        schema = FundamentalSecondOpinion.model_json_schema()
        schema["required"] = [
            "verdict",
            "summary",
            "verdict_reference_ids",
            "strengths",
            "risks",
            "review_focus",
        ]
        string_item = {"type": "string"}
        schema["properties"]["verdict_reference_ids"]["items"] = string_item
        note_schema = schema.get("$defs", {}).get("ReferenceNote")
        if isinstance(note_schema, dict):
            note_schema["properties"]["reference_ids"]["items"] = string_item
        return schema

    def _request_from_packet(
        self,
        packet: FundamentalSecondOpinionPacketV1,
    ) -> dict[str, Any]:
        allowed_ids = [reference.id for reference in packet.references]
        request: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert equity research analyst evaluating fundamental quality for Indian swing trading setups (Minervini / SEPA framework).\n"
                        "Your objective: Provide an independent, grounded second opinion on earnings & sales growth, profit margins, operational quality, and institutional sponsorship using ONLY the supplied references packet.\n"
                        "Crucial rules:\n"
                        "1. Deterministic Python fit is separate and authoritative; focus on qualitative synthesis of growth trends, margin trajectory, and risks.\n"
                        "2. Upstox API provider limitations (e.g. missing quarterly EPS breakdown, promoter pledge, or debt-to-equity) are expected coverage constraints—NEVER treat missing optional provider fields as a red flag or reason to fail a stock. Work constructively with available metrics.\n"
                        "3. Return verdict as 'pass', 'fail', or 'uncertain'. Use 'pass' when sales/profit growth & margins demonstrate solid fundamental health; 'fail' when severe margin erosion, falling revenue, or heavy losses are evident; 'uncertain' when data is sparse.\n"
                        "4. Highlight top Strengths (key fundamental growth drivers), Risks (margin pressure, headwinds), and Review Focus (what the human trader should check).\n"
                        "5. Every conclusion must cite matching reference IDs from the supplied packet.\n"
                        "6. Never recommend specific trade execution, entry, exit, or position size actions."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        packet.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "plugins": [{"id": "response-healing"}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "fundamental_second_opinion_v1",
                    "strict": True,
                    "schema": self._response_schema(allowed_ids),
                },
            },
            "provider": {"require_parameters": True, "data_collection": "deny"},
            "reasoning": {"effort": self._reasoning_effort, "exclude": True},
            "max_tokens": self._max_tokens,
            "stream": False,
        }
        if self._temperature is not None:
            request["temperature"] = self._temperature
        return request

    def prepare(self, facts: Mapping[str, Any]) -> PreparedFundamentalRequest:
        packet = self.build_packet(facts)
        packet_json = packet.model_dump(mode="json")
        return PreparedFundamentalRequest(
            packet=packet,
            request_payload=self._request_from_packet(packet),
            input_hash=canonical_json_hash(packet_json),
            has_usable_facts=any(reference.kind != "limitation" for reference in packet.references),
        )

    def build_request(self, facts: Mapping[str, Any]) -> dict[str, Any]:
        return self.prepare(facts).request_payload

    async def send_once(
        self,
        prepared: PreparedFundamentalRequest,
    ) -> FundamentalLLMResult:
        if not self._api_key:
            raise FundamentalLLMError("OPENROUTER_API_KEY is not configured")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Title": self._app_title,
        }
        if self._http_referer:
            headers["HTTP-Referer"] = self._http_referer
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(
                    self._api_url,
                    headers=headers,
                    json=prepared.request_payload,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise FundamentalLLMError(
                f"OpenRouter request outcome is unknown: {type(exc).__name__}",
                attempt_status="transport_unknown",
            ) from exc

        try:
            raw_payload: Any = response.json()
        except ValueError:
            raw_payload = {"unparsed_body": response.text[:2000]}
        safe_payload = sanitize_provider_payload(raw_payload)
        payload = safe_payload if isinstance(safe_payload, dict) else {"response": safe_payload}
        usage, cost = _usage_and_cost(payload)
        request_id = (
            str(payload["id"])
            if payload.get("id") is not None
            else response.headers.get("X-Generation-Id")
        )

        if response.status_code >= 400:
            raise FundamentalLLMError(
                _format_openrouter_http_error(response, payload),
                response_payload=payload,
                http_status=response.status_code,
                request_id=request_id,
                usage=usage,
                cost=cost,
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        if isinstance(payload.get("error"), Mapping):
            message = str(payload["error"].get("message") or "provider error")
            raise FundamentalLLMError(
                f"OpenRouter returned an embedded provider error: {message[:500]}",
                response_payload=payload,
                http_status=response.status_code,
                request_id=request_id,
                usage=usage,
                cost=cost,
            )

        try:
            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
                raise ValueError("OpenRouter response has no usable choices")
            choice_error = choices[0].get("error")
            if isinstance(choice_error, Mapping):
                raise ValueError(
                    f"provider error: {choice_error.get('message') or choice_error.get('code')}"
                )
            parsed = parse_openrouter_structured_content(choices[0], usage=usage)
            allowed = {reference.id for reference in prepared.packet.references}

            def _clean_refs(raw_refs: Any) -> list[str]:
                if not isinstance(raw_refs, list):
                    return []
                return [r for r in raw_refs if isinstance(r, str) and r in allowed]

            if isinstance(parsed, dict):
                clean_v_refs = _clean_refs(parsed.get("verdict_reference_ids"))
                if not clean_v_refs and allowed:
                    clean_v_refs = [next(iter(allowed))]
                parsed["verdict_reference_ids"] = clean_v_refs

                for key in ("strengths", "risks", "review_focus"):
                    items = parsed.get(key)
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict):
                                clean_item_refs = _clean_refs(item.get("reference_ids"))
                                if not clean_item_refs and allowed:
                                    clean_item_refs = [next(iter(allowed))]
                                item["reference_ids"] = clean_item_refs

            opinion = FundamentalSecondOpinion.model_validate(parsed)
        except (ValueError, KeyError, TypeError, ValidationError) as exc:
            raise FundamentalLLMError(
                f"OpenRouter structured response was invalid: {exc}",
                response_payload=payload,
                http_status=response.status_code,
                request_id=request_id,
                usage=usage,
                cost=cost,
                attempt_status="invalid_response",
            ) from exc

        return FundamentalLLMResult(
            opinion=opinion,
            request_id=request_id,
            usage=usage,
            input_hash=prepared.input_hash,
            cost=cost,
            request_payload=prepared.request_payload,
            response_payload=payload,
        )

    async def analyze(self, facts: Mapping[str, Any]) -> FundamentalLLMResult:
        """Convenience path; the worker uses send_once so it can persist each call."""

        prepared = self.prepare(facts)
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await self.send_once(prepared)
            except FundamentalLLMError as exc:
                if not exc.retryable or attempt >= self._max_attempts:
                    raise
                await self._sleep(0.5 * (2 ** (attempt - 1)))
        raise FundamentalLLMError("OpenRouter second opinion failed")


def _format_openrouter_http_error(
    response: httpx.Response,
    payload: Mapping[str, Any] | None = None,
) -> str:
    detail: str | None = None
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping) and isinstance(error.get("message"), str):
            detail = error["message"].strip()
        elif isinstance(payload.get("message"), str):
            detail = payload["message"].strip()
    base = f"OpenRouter returned HTTP {response.status_code}"
    return f"{base}: {detail[:500]}" if detail else base
