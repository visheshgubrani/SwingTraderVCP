"""Strict, compact OpenRouter explanations for deterministic P7 scorecards."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.fundamental_data import canonical_json_hash
from app.services.openrouter_content import parse_openrouter_structured_content


class FundamentalLLMError(RuntimeError):
    """The read-only explanation request or its response was not usable."""


class EvidenceNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=180)
    evidence_keys: list[str] = Field(max_length=3)


class FundamentalExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=400)
    strengths: list[EvidenceNote] = Field(max_length=3)
    risks: list[EvidenceNote] = Field(max_length=3)
    review_focus: list[EvidenceNote] = Field(max_length=2)

    @property
    def highlights(self) -> list[EvidenceNote]:
        """Compatibility alias for persisted v2 annotation consumers."""
        return self.strengths


# Compatibility parser for historical v1 snapshots/tests. P7 v2 never calls
# this path: it always supplies a deterministic scorecard.
class LegacyCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    status: str
    explanation: str
    evidence_keys: list[str]


class LegacyVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: str
    summary: str
    criteria: list[LegacyCriterion]
    red_flags: list[str]
    missing_data: list[str]


@dataclass(frozen=True)
class FundamentalLLMResult:
    explanation: FundamentalExplanation
    request_id: str | None
    usage: dict[str, Any]
    input_hash: str
    cost: float
    verdict: LegacyVerdict | None = None


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class OpenRouterFundamentalClient:
    """One OpenRouter call per scorecard; it may explain, never decide."""

    def __init__(
        self,
        *,
        api_key: str,
        api_url: str = "https://openrouter.ai/api/v1/chat/completions",
        model: str = "openai/gpt-5.6-luna-pro",
        reasoning_effort: str = "low",
        prompt_version: str = "sepa_fundamentals_v1",
        app_title: str = "SwingTraderVCP",
        http_referer: str = "",
        timeout_seconds: float = 60.0,
        max_attempts: int = 2,
        max_tokens: int = 3200,
        prompt_max_chars: int = 6000,
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

    def _packet(self, facts: Mapping[str, Any], scorecard: Mapping[str, Any]) -> dict[str, Any]:
        evidence = facts.get("evidence") if isinstance(facts.get("evidence"), Mapping) else {}
        packet = {
            "prompt_version": self._prompt_version,
            "rules_are_authoritative": True,
            "assessment": {
                "rubric_version": scorecard.get("rubric_version"),
                "score": scorecard.get("score"),
                "grade": scorecard.get("grade"),
                "coverage_pct": scorecard.get("coverage_pct"),
                "components": scorecard.get("components", []),
                "red_flags": scorecard.get("red_flags", []),
            },
            "provider_limitations": scorecard.get("provider_limitations", []),
            "company": facts.get("company", {}),
            "periods": facts.get("periods", {}),
            "evidence": evidence,
        }
        encoded = json.dumps(packet, sort_keys=True, separators=(",", ":"))
        if len(encoded) > self._prompt_max_chars:
            # Evidence values are deterministic facts. Trim labels/descriptions first,
            # never the scorecard or evidence keys, to preserve citation validity.
            compact_evidence = {
                key: {"value": value.get("value"), "unit": value.get("unit")}
                for key, value in evidence.items()
                if isinstance(value, Mapping)
            }
            packet["evidence"] = compact_evidence
            encoded = json.dumps(packet, sort_keys=True, separators=(",", ":"))
        if len(encoded) > self._prompt_max_chars:
            raise FundamentalLLMError("Fundamental analysis packet exceeds configured size limit")
        return packet

    def build_request(
        self,
        facts: Mapping[str, Any],
        scorecard: Mapping[str, Any],
    ) -> dict[str, Any]:
        packet = self._packet(facts, scorecard)
        request: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a read-only explanation layer for a deterministic, Minervini-inspired "
                        "Indian-equity fundamental fit assessment. The supplied score, grade, coverage, "
                        "components, and red flags are authoritative. Do not change them, issue a verdict, "
                        "recommend a trade, or mention an entry, exit, size, or order. Write a concise factual "
                        "summary, up to three strengths, up to three risks, and up to two review-focus items. "
                        "Every note may cite only evidence keys in the packet. Never infer missing data. "
                        "Provider limitations are neutral context, not risks."
                    ),
                },
                {"role": "user", "content": json.dumps(packet, sort_keys=True, separators=(",", ":"))},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "fundamental_explanation",
                    "strict": True,
                    "schema": FundamentalExplanation.model_json_schema(),
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

    def _build_legacy_request(self, facts: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "model": self._model,
            "messages": [{"role": "system", "content": "Read-only fundamental analyst. Cite only supplied evidence."}, {"role": "user", "content": json.dumps({"prompt_version": self._prompt_version, "fundamental_facts": facts}, sort_keys=True, separators=(",", ":"))}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "fundamental_verdict", "strict": True, "schema": LegacyVerdict.model_json_schema()}},
            "provider": {"require_parameters": True, "data_collection": "deny"},
            "reasoning": {"effort": self._reasoning_effort, "exclude": True},
            "max_tokens": self._max_tokens,
            "stream": False,
            **({"temperature": self._temperature} if self._temperature is not None else {}),
        }

    async def analyze(
        self,
        facts: Mapping[str, Any],
        scorecard: Mapping[str, Any] | None = None,
    ) -> FundamentalLLMResult:
        if not self._api_key:
            raise FundamentalLLMError("OPENROUTER_API_KEY is not configured")
        if scorecard is None:
            return await self._analyze_legacy(facts)
        request_payload = self.build_request(facts, scorecard)
        input_hash = canonical_json_hash({"facts": dict(facts), "scorecard": dict(scorecard), "model": self._model, "reasoning": self._reasoning_effort, "prompt": self._prompt_version})
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json", "X-Title": self._app_title}
        if self._http_referer:
            headers["HTTP-Referer"] = self._http_referer

        for attempt in range(1, self._max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                    response = await client.post(self._api_url, headers=headers, json=request_payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # An ambiguous transport error may have reached the provider: never retry it.
                raise FundamentalLLMError(f"OpenRouter request outcome is unknown: {type(exc).__name__}") from exc

            if response.status_code in (401, 402):
                raise FundamentalLLMError(_format_openrouter_http_error(response))
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < self._max_attempts:
                    await self._sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise FundamentalLLMError(_format_openrouter_http_error(response))
            if response.status_code >= 400:
                raise FundamentalLLMError(_format_openrouter_http_error(response))
            try:
                payload = response.json()
                explanation = self._parse_explanation(payload, facts)
            except (ValueError, KeyError, TypeError, ValidationError) as exc:
                # A 200 response can be billable. Never retry malformed output.
                raise FundamentalLLMError(f"OpenRouter structured response was invalid: {exc}") from exc
            usage = payload.get("usage")
            usage_map = dict(usage) if isinstance(usage, Mapping) else {}
            return FundamentalLLMResult(
                explanation=explanation,
                request_id=str(payload["id"]) if payload.get("id") is not None else None,
                usage=usage_map,
                input_hash=input_hash,
                cost=float(payload.get("usage", {}).get("cost", 0) or 0) if isinstance(payload.get("usage"), Mapping) else 0.0,
            )
        raise FundamentalLLMError("OpenRouter annotation failed")

    async def _analyze_legacy(self, facts: Mapping[str, Any]) -> FundamentalLLMResult:
        """Compatibility only; retains v1 retry behavior for old persisted callers."""
        if not self._api_key:
            raise FundamentalLLMError("OPENROUTER_API_KEY is not configured")
        payload = self._build_legacy_request(facts)
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json", "X-Title": self._app_title}
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                    response = await client.post(self._api_url, headers=headers, json=payload)
                if response.status_code == 401:
                    raise FundamentalLLMError("OpenRouter API key was rejected")
                if response.status_code >= 400:
                    raise FundamentalLLMError(_format_openrouter_http_error(response))
                data = response.json()
                choices = data.get("choices")
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
                    raise ValueError("OpenRouter response has no usable choices")
                parsed = parse_openrouter_structured_content(choices[0], usage=dict(data.get("usage") or {}))
                verdict = LegacyVerdict.model_validate(parsed)
                evidence = facts.get("evidence") if isinstance(facts.get("evidence"), Mapping) else {}
                unknown = {key for criterion in verdict.criteria for key in criterion.evidence_keys} - set(evidence)
                if unknown:
                    raise ValueError(f"Model cited evidence keys absent from the snapshot: {sorted(unknown)}")
                explanation = FundamentalExplanation(summary=verdict.summary[:400], strengths=[], risks=[], review_focus=[])
                return FundamentalLLMResult(explanation=explanation, request_id=str(data.get("id")) if data.get("id") else None, usage=dict(data.get("usage") or {}), input_hash=canonical_json_hash(dict(facts)), cost=0.0, verdict=verdict)
            except FundamentalLLMError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < self._max_attempts:
                    await self._sleep(0.5 * (2 ** (attempt - 1)))
        raise FundamentalLLMError(f"OpenRouter annotation failed after {self._max_attempts} attempts: {last_error or 'unknown error'}")

    @staticmethod
    def _parse_explanation(payload: Mapping[str, Any], facts: Mapping[str, Any]) -> FundamentalExplanation:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ValueError("OpenRouter response has no usable choices")
        usage = payload.get("usage")
        parsed = parse_openrouter_structured_content(choices[0], usage=dict(usage) if isinstance(usage, Mapping) else None)
        explanation = FundamentalExplanation.model_validate(parsed)
        evidence = facts.get("evidence") if isinstance(facts.get("evidence"), Mapping) else {}
        cited = {
            key
            for note in [*explanation.strengths, *explanation.risks, *explanation.review_focus]
            for key in note.evidence_keys
        }
        unknown = sorted(cited - set(evidence))
        if unknown:
            raise ValueError(f"Model cited evidence keys absent from the snapshot: {unknown}")
        return explanation


def _format_openrouter_http_error(response: httpx.Response) -> str:
    detail: str | None = None
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping) and isinstance(error.get("message"), str):
            detail = error["message"].strip()
        elif isinstance(payload.get("message"), str):
            detail = payload["message"].strip()
    base = f"OpenRouter returned HTTP {response.status_code}"
    return f"{base}: {detail[:500]}" if detail else base
