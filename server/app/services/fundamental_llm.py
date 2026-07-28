"""Strict, single-turn OpenRouter annotation for normalized fundamentals."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.services.fundamental_data import canonical_json_hash


class FundamentalLLMError(RuntimeError):
    """The model request or its structured result was not usable."""


CriterionName = Literal[
    "sales_growth",
    "earnings_growth",
    "margin_trend",
    "return_quality",
    "cash_conversion",
    "ownership_trend",
    "leverage",
    "corporate_actions",
    "data_quality",
]
CriterionStatus = Literal[
    "positive",
    "negative",
    "mixed",
    "unknown",
    "not_applicable",
]


class FundamentalCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CriterionName
    status: CriterionStatus
    explanation: str = Field(min_length=1, max_length=300)
    evidence_keys: list[str] = Field(max_length=8)


class FundamentalVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pass", "fail", "uncertain"]
    summary: str = Field(min_length=1, max_length=600)
    criteria: list[FundamentalCriterion] = Field(min_length=1, max_length=9)
    red_flags: list[str] = Field(max_length=10)
    missing_data: list[str] = Field(max_length=20)

    @model_validator(mode="after")
    def criteria_names_must_be_unique(self) -> "FundamentalVerdict":
        names = [criterion.name for criterion in self.criteria]
        if len(names) != len(set(names)):
            raise ValueError("criteria names must be unique")
        return self


@dataclass(frozen=True)
class FundamentalLLMResult:
    verdict: FundamentalVerdict
    request_id: str | None
    usage: dict[str, Any]
    input_hash: str


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class OpenRouterFundamentalClient:
    def __init__(
        self,
        *,
        api_key: str,
        api_url: str = "https://openrouter.ai/api/v1/chat/completions",
        model: str = "xiaomi/mimo-v2.5-pro",
        prompt_version: str = "sepa_fundamentals_v1",
        app_title: str = "SwingTraderVCP",
        http_referer: str = "",
        timeout_seconds: float = 20.0,
        max_attempts: int = 3,
        max_tokens: int = 1600,
        temperature: float = 0.1,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = _default_sleep,
    ) -> None:
        self._api_key = api_key
        self._api_url = api_url
        self._model = model
        self._prompt_version = prompt_version
        self._app_title = app_title
        self._http_referer = http_referer
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._transport = transport
        self._sleep = sleep

    @property
    def model(self) -> str:
        return self._model

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    def build_request(self, facts: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a read-only fundamental analyst for an Indian "
                        "equity swing-trading shortlist. Assess only the supplied "
                        "normalized facts. Do not use outside knowledge, invent "
                        "missing figures, recommend an order, size a trade, or "
                        "override the human decision. Apply a SEPA-style rubric: "
                        "prioritize sales and earnings trajectory, margin trend, "
                        "return quality, cash conversion, ownership trend, and "
                        "material corporate actions. Treat sector-marked fields "
                        "as not_applicable. A pass needs broadly supportive, "
                        "defensible evidence; fail requires material adverse "
                        "evidence; use uncertain for conflicting or insufficient "
                        "critical data. Every evidence_keys value must be copied "
                        "exactly from the input evidence object."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "prompt_version": self._prompt_version,
                            "fundamental_facts": facts,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "fundamental_verdict",
                    "strict": True,
                    "schema": FundamentalVerdict.model_json_schema(),
                },
            },
            "provider": {
                "require_parameters": True,
                "data_collection": "deny",
            },
            "reasoning": {
                "enabled": True,
                "exclude": True,
            },
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "stream": False,
        }

    async def analyze(
        self,
        facts: Mapping[str, Any],
    ) -> FundamentalLLMResult:
        if not self._api_key:
            raise FundamentalLLMError("OPENROUTER_API_KEY is not configured")

        request_payload = self.build_request(facts)
        input_hash = canonical_json_hash(dict(facts))
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Title": self._app_title,
        }
        if self._http_referer:
            headers["HTTP-Referer"] = self._http_referer

        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    transport=self._transport,
                ) as client:
                    response = await client.post(
                        self._api_url,
                        headers=headers,
                        json=request_payload,
                    )
                if response.status_code == 401:
                    raise FundamentalLLMError("OpenRouter API key was rejected")
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"OpenRouter returned HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                if response.status_code >= 400:
                    raise FundamentalLLMError(
                        f"OpenRouter returned HTTP {response.status_code}"
                    )

                payload = response.json()
                verdict = self._parse_verdict(payload, facts)
                usage = payload.get("usage")
                return FundamentalLLMResult(
                    verdict=verdict,
                    request_id=(
                        str(payload["id"]) if payload.get("id") is not None else None
                    ),
                    usage=dict(usage) if isinstance(usage, Mapping) else {},
                    input_hash=input_hash,
                )
            except FundamentalLLMError:
                raise
            except (
                httpx.TimeoutException,
                httpx.TransportError,
                httpx.HTTPStatusError,
                ValueError,
                KeyError,
                TypeError,
                ValidationError,
            ) as exc:
                last_error = exc
                if attempt == self._max_attempts:
                    break
                await self._sleep(0.5 * (2 ** (attempt - 1)))

        raise FundamentalLLMError(
            f"OpenRouter annotation failed after {self._max_attempts} attempts: "
            f"{last_error or 'unknown error'}"
        )

    @staticmethod
    def _parse_verdict(
        payload: Mapping[str, Any],
        facts: Mapping[str, Any],
    ) -> FundamentalVerdict:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("OpenRouter response has no choices")
        first = choices[0]
        if not isinstance(first, Mapping):
            raise ValueError("OpenRouter choice is not an object")
        message = first.get("message")
        if not isinstance(message, Mapping):
            raise ValueError("OpenRouter choice has no message")
        content = message.get("content")
        if isinstance(content, str):
            parsed = json.loads(content)
        elif isinstance(content, Mapping):
            parsed = dict(content)
        else:
            raise ValueError("OpenRouter message content is not structured JSON")

        verdict = FundamentalVerdict.model_validate(parsed)
        evidence = facts.get("evidence")
        available_keys = set(evidence) if isinstance(evidence, Mapping) else set()
        referenced = {
            key
            for criterion in verdict.criteria
            for key in criterion.evidence_keys
        }
        unknown = sorted(referenced - available_keys)
        if unknown:
            raise ValueError(
                f"Model cited evidence keys absent from the snapshot: {unknown}"
            )
        return verdict
