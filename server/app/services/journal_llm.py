"""Strict structured OpenRouter journal coach."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.fundamental_data import canonical_json_hash
from app.services.openrouter_content import parse_openrouter_structured_content


class JournalLLMError(RuntimeError):
    """The coach request or structured result was not usable."""


class CoachEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_ids: list[str] = Field(max_length=20)
    metric_keys: list[str] = Field(max_length=10)


class CoachCohortInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cohort: str = Field(min_length=1, max_length=120)
    insight: str = Field(min_length=1, max_length=400)
    evidence: CoachEvidence


class CoachReviewQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=300)
    evidence: CoachEvidence


class JournalCoachReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strengths: list[str] = Field(min_length=1, max_length=8)
    weaknesses: list[str] = Field(max_length=8)
    setup_cohorts: list[CoachCohortInsight] = Field(max_length=8)
    regime_cohorts: list[CoachCohortInsight] = Field(max_length=8)
    recurring_mistakes: list[str] = Field(max_length=10)
    data_quality_warnings: list[str] = Field(max_length=10)
    review_questions: list[CoachReviewQuestion] = Field(max_length=8)


@dataclass(frozen=True)
class JournalLLMResult:
    report: JournalCoachReport
    request_id: str | None
    usage: dict[str, Any]
    input_hash: str


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class OpenRouterJournalCoachClient:
    def __init__(
        self,
        *,
        api_key: str,
        api_url: str = "https://openrouter.ai/api/v1/chat/completions",
        model: str = "openai/gpt-5.6-luna-pro",
        prompt_version: str = "journal_coach_v1",
        app_title: str = "SwingTraderVCP",
        http_referer: str = "",
        timeout_seconds: float = 45.0,
        max_attempts: int = 3,
        max_tokens: int = 2400,
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
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._transport = transport
        self._sleep = sleep

    def build_request(self, coach_input: dict[str, Any]) -> dict[str, Any]:
        schema = JournalCoachReport.model_json_schema()
        return {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a read-only swing-trading journal coach. "
                        "Analyze closed-trade metrics and human review fields. "
                        "Never recommend live trades, order placement, or sizing. "
                        "Return strict JSON matching the schema."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "prompt_version": self._prompt_version,
                            "coach_input": coach_input,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                },
            ],
            "plugins": [{"id": "response-healing"}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "journal_coach_report",
                    "strict": True,
                    "schema": schema,
                },
            },
            "provider": {"require_parameters": True, "data_collection": "deny"},
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "reasoning": {"effort": "low", "exclude": True},
        }

    async def analyze(self, coach_input: dict[str, Any]) -> JournalLLMResult:
        payload = self.build_request(coach_input)
        input_hash = canonical_json_hash(coach_input)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Title": self._app_title,
        }
        if self._http_referer:
            headers["HTTP-Referer"] = self._http_referer

        last_error: Exception | None = None
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=self._timeout_seconds,
        ) as client:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    response = await client.post(
                        self._api_url,
                        headers=headers,
                        json=payload,
                    )
                    if response.status_code in {429, 500, 502, 503, 504}:
                        raise JournalLLMError(
                            f"OpenRouter transient error {response.status_code}"
                        )
                    response.raise_for_status()
                    body = response.json()
                    choices = body.get("choices")
                    if not isinstance(choices, list) or not choices:
                        raise KeyError("choices")
                    first = choices[0]
                    if not isinstance(first, dict):
                        raise TypeError("choice")
                    usage = body.get("usage")
                    usage_map = dict(usage) if isinstance(usage, dict) else None
                    parsed = parse_openrouter_structured_content(
                        first,
                        usage=usage_map,
                    )
                    report = JournalCoachReport.model_validate(parsed)
                    return JournalLLMResult(
                        report=report,
                        request_id=body.get("id"),
                        usage=body.get("usage") or {},
                        input_hash=input_hash,
                    )
                except (
                    httpx.HTTPError,
                    KeyError,
                    TypeError,
                    json.JSONDecodeError,
                    ValueError,
                    ValidationError,
                ) as exc:
                    last_error = exc
                    if attempt < self._max_attempts:
                        await self._sleep(min(2**attempt, 8))
                        continue
                    break

        raise JournalLLMError(f"Journal coach request failed: {last_error}")
