"""Normalize Pydantic JSON Schema for OpenRouter Gemini structured outputs."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


def gemini_compatible_json_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return a Gemini-via-OpenRouter-safe copy of a Pydantic JSON schema.

    Inlines ``$ref`` / ``$defs``, collapses Decimal ``anyOf`` number|string
    unions to a number, collapses ``T | null`` unions to a JSON type list,
    and applies the same strict object rules used by VCP
    vision (no defaults, every property required, ``additionalProperties``
    false, ``const`` rewritten as ``enum``).
    """
    normalized = copy.deepcopy(dict(schema))
    normalized = _inline_refs(normalized)
    _collapse_decimal_any_of(normalized)
    _collapse_nullable_any_of(normalized)
    _make_strict(normalized)
    return normalized


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.get("$defs") or schema.get("definitions") or {}
    if not isinstance(defs, dict):
        defs = {}

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.rsplit("/", 1)[-1]
                if name in defs:
                    resolved = copy.deepcopy(defs[name])
                    extras = {key: value for key, value in node.items() if key != "$ref"}
                    if extras and isinstance(resolved, dict):
                        for key, value in extras.items():
                            resolved.setdefault(key, value)
                    return resolve(resolved)
            return {
                key: resolve(value)
                for key, value in node.items()
                if key not in {"$defs", "definitions"}
            }
        if isinstance(node, list):
            return [resolve(value) for value in node]
        return node

    inlined = resolve(schema)
    if isinstance(inlined, dict):
        inlined.pop("$defs", None)
        inlined.pop("definitions", None)
        return inlined
    return schema


def _is_number_string_union(options: list[Any]) -> bool:
    types: set[str] = set()
    for option in options:
        if not isinstance(option, dict) or "$ref" in option:
            return False
        option_type = option.get("type")
        if option_type not in {"number", "string"}:
            return False
        types.add(option_type)
    return types == {"number", "string"}


def _collapse_decimal_any_of(node: Any) -> None:
    if isinstance(node, dict):
        options = node.get("anyOf")
        if isinstance(options, list) and _is_number_string_union(options):
            exclusive_min: Any = None
            exclusive_max: Any = None
            minimum: Any = None
            maximum: Any = None
            for option in options:
                if option.get("type") != "number":
                    continue
                if "exclusiveMinimum" in option:
                    exclusive_min = option["exclusiveMinimum"]
                if "exclusiveMaximum" in option:
                    exclusive_max = option["exclusiveMaximum"]
                if "minimum" in option:
                    minimum = option["minimum"]
                if "maximum" in option:
                    maximum = option["maximum"]
            node.pop("anyOf", None)
            node["type"] = "number"
            if exclusive_min is not None:
                node["exclusiveMinimum"] = exclusive_min
            if exclusive_max is not None:
                node["exclusiveMaximum"] = exclusive_max
            if minimum is not None:
                node["minimum"] = minimum
            if maximum is not None:
                node["maximum"] = maximum
        for value in node.values():
            _collapse_decimal_any_of(value)
    elif isinstance(node, list):
        for value in node:
            _collapse_decimal_any_of(value)


def _collapse_nullable_any_of(node: Any) -> None:
    """Rewrite ``T | null`` as a JSON type list so Gemini strict schema stays anyOf-free."""
    if isinstance(node, dict):
        options = node.get("anyOf")
        if isinstance(options, list) and len(options) == 2:
            null_opt = next(
                (
                    option
                    for option in options
                    if isinstance(option, dict) and option.get("type") == "null" and set(option) <= {"type"}
                ),
                None,
            )
            other = next((option for option in options if option is not null_opt), None)
            if null_opt is not None and isinstance(other, dict) and "$ref" not in other:
                node.pop("anyOf", None)
                for key, value in other.items():
                    node.setdefault(key, value)
                other_type = other.get("type")
                if isinstance(other_type, str):
                    node["type"] = [other_type, "null"]
                elif isinstance(other_type, list):
                    node["type"] = list(dict.fromkeys([*other_type, "null"]))
                if "enum" in node and None not in node["enum"]:
                    node["enum"] = [*node["enum"], None]
        for value in node.values():
            _collapse_nullable_any_of(value)
    elif isinstance(node, list):
        for value in node:
            _collapse_nullable_any_of(value)


def _make_strict(node: Any) -> None:
    if isinstance(node, dict):
        node.pop("default", None)
        if node.get("type") == "object" and isinstance(node.get("properties"), dict):
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
        if "const" in node:
            node["enum"] = [node.pop("const")]
        for value in node.values():
            _make_strict(value)
    elif isinstance(node, list):
        for value in node:
            _make_strict(value)
