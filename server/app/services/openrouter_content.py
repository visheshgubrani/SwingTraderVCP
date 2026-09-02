"""Normalize OpenRouter chat completion message content for structured JSON."""

from __future__ import annotations

import json
import re
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


def _strip_markdown_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        return cleaned.strip()
    return cleaned


def _extract_json_block(text: str) -> str | None:
    start_brace = text.find("{")
    start_bracket = text.find("[")
    if start_brace != -1 and start_bracket != -1:
        start_idx = min(start_brace, start_bracket)
    elif start_brace != -1:
        start_idx = start_brace
    elif start_bracket != -1:
        start_idx = start_bracket
    else:
        return None

    end_brace = text.rfind("}")
    end_bracket = text.rfind("]")
    end_idx = max(end_brace, end_bracket)
    if end_idx == -1 or end_idx < start_idx:
        return None

    return text[start_idx : end_idx + 1]


def decode_openrouter_json_value(value: Any) -> Any:
    """JSON-decode strings once, or twice when the payload is double-encoded.

    Also handles markdown-wrapped JSON and extracts embedded JSON blocks when
    providers include preamble or postamble commentary.
    """
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        fence_stripped = _strip_markdown_fences(stripped)
        try:
            parsed = json.loads(fence_stripped)
        except json.JSONDecodeError:
            extracted = _extract_json_block(fence_stripped)
            if extracted is not None and extracted != fence_stripped:
                parsed = json.loads(extracted)
            else:
                raise

    if isinstance(parsed, str):
        try:
            return decode_openrouter_json_value(parsed)
        except json.JSONDecodeError:
            return parsed
    return parsed



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


def _object_from_content_parts(parts: list[Any]) -> dict[str, Any] | None:
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        for key in ("parsed", "json"):
            candidate = part.get(key)
            if isinstance(candidate, Mapping):
                return dict(candidate)
            if isinstance(candidate, str):
                try:
                    decoded = decode_openrouter_json_value(candidate)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, dict):
                    return decoded
    return None


def _as_json_object(value: Any, *, error: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, Mapping):
        return dict(value)
    raise ValueError(error)


def parse_openrouter_structured_content(
    choice: Mapping[str, Any] | str,
    *,
    usage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(choice, str):
        try:
            loaded = decode_openrouter_json_value(choice)
        except json.JSONDecodeError as exc:
            raise ValueError(f"OpenRouter choice is not valid JSON: {exc}") from exc
        if isinstance(loaded, Mapping) and "verdict" in loaded and "message" not in loaded:
            return _as_json_object(
                loaded,
                error="OpenRouter structured content must be a JSON object",
            )
        if isinstance(loaded, Mapping):
            choice = loaded
        else:
            raise ValueError("OpenRouter choice has no message")

    if not isinstance(choice, Mapping):
        raise ValueError("OpenRouter choice has no message")

    message = choice.get("message")
    if not isinstance(message, Mapping):
        if "verdict" in choice:
            return dict(choice)
        raise ValueError("OpenRouter choice has no message")

    finish_reason = choice.get("finish_reason")
    usage_hint = _usage_summary(usage)

    def parse_text(text: str) -> Any:
        try:
            return decode_openrouter_json_value(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "OpenRouter structured JSON is malformed: "
                f"{exc} (finish_reason={finish_reason!r}, {usage_hint})"
            ) from exc

    parsed_field = message.get("parsed")
    if isinstance(parsed_field, Mapping):
        return dict(parsed_field)
    if isinstance(parsed_field, str):
        decoded_parsed = parse_text(parsed_field)
        if isinstance(decoded_parsed, dict):
            return decoded_parsed

    content = message.get("content")

    if isinstance(content, str):
        parsed: Any = parse_text(content)
    elif isinstance(content, Mapping):
        parsed = dict(content)
    elif isinstance(content, list):
        structured = _object_from_content_parts(content)
        if structured is not None:
            parsed = structured
        else:
            text = _text_from_content_parts(content)
            if not text:
                raise ValueError(
                    "OpenRouter message content is empty multipart "
                    f"(finish_reason={finish_reason!r}, {usage_hint})"
                )
            parsed = parse_text(text)
    else:
        raise ValueError(
            "OpenRouter message content is missing or not structured JSON "
            f"(content_type={type(content).__name__}, "
            f"finish_reason={finish_reason!r}, {usage_hint})"
        )

    if not isinstance(parsed, dict):
        raise ValueError("OpenRouter structured content must be a JSON object")
    return parsed
