"""Normalize OpenRouter chat completion message content for structured JSON."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def _usage_summary(usage: Mapping[str, Any] | None) -> str:
    if not isinstance(usage, Mapping):
        return "usage=n/a"
    parts: list[str] = []
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if key in usage:
            parts.append(f"{key}={usage[key]}")
    details = usage.get("completion_tokens_details")
    if isinstance(details, Mapping) and details.get("reasoning_tokens") is not None:
        parts.append(f"reasoning_tokens={details['reasoning_tokens']}")
    return ", ".join(parts) if parts else "usage=n/a"


def _text_from_content_parts(parts: list[Any]) -> str:
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, str):
            chunks.append(part)
            continue
        if isinstance(part, Mapping):
            text = part.get("text")
            if isinstance(text, str):
                chunks.append(text)
                continue
            nested = part.get("content")
            if isinstance(nested, str):
                chunks.append(nested)
    return "".join(chunks)


def parse_openrouter_structured_content(
    choice: Mapping[str, Any],
    *,
    usage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise ValueError("OpenRouter choice has no message")

    finish_reason = choice.get("finish_reason")
    usage_hint = _usage_summary(usage)
    content = message.get("content")

    if isinstance(content, str):
        parsed: Any = json.loads(content)
    elif isinstance(content, Mapping):
        parsed = dict(content)
    elif isinstance(content, list):
        text = _text_from_content_parts(content)
        if not text:
            raise ValueError(
                "OpenRouter message content is empty multipart "
                f"(finish_reason={finish_reason!r}, {usage_hint})"
            )
        parsed = json.loads(text)
    else:
        raise ValueError(
            "OpenRouter message content is missing or not structured JSON "
            f"(content_type={type(content).__name__}, "
            f"finish_reason={finish_reason!r}, {usage_hint})"
        )

    if not isinstance(parsed, dict):
        raise ValueError("OpenRouter structured content must be a JSON object")
    return parsed
