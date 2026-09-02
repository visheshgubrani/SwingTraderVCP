"""Unit tests for openrouter_content.py parsing, decoding, and fence stripping."""

from __future__ import annotations

import json
import unittest

from app.services.openrouter_content import (
    decode_openrouter_json_value,
    parse_openrouter_structured_content,
)


class OpenRouterContentDecodingTests(unittest.TestCase):
    def test_decodes_clean_json_object(self) -> None:
        raw = '{"classification": "valid", "confidence": 95}'
        result = decode_openrouter_json_value(raw)
        self.assertEqual(result, {"classification": "valid", "confidence": 95})

    def test_decodes_clean_json_array(self) -> None:
        raw = '[{"index": 1, "action": "confirm"}]'
        result = decode_openrouter_json_value(raw)
        self.assertEqual(result, [{"index": 1, "action": "confirm"}])

    def test_decodes_markdown_fenced_json_with_tag(self) -> None:
        raw = """```json
{
  "classification": "valid",
  "confidence": 88
}
```"""
        result = decode_openrouter_json_value(raw)
        self.assertEqual(result, {"classification": "valid", "confidence": 88})

    def test_decodes_markdown_fenced_json_without_tag(self) -> None:
        raw = """```
{
  "classification": "forming",
  "forming_state": "developing"
}
```"""
        result = decode_openrouter_json_value(raw)
        self.assertEqual(
            result,
            {"classification": "forming", "forming_state": "developing"},
        )

    def test_decodes_json_with_preamble_and_postamble_commentary(self) -> None:
        raw = """Here is the VCP analysis you requested:

{
  "classification": "valid",
  "confidence": 90
}

I hope this helps!"""
        result = decode_openrouter_json_value(raw)
        self.assertEqual(result, {"classification": "valid", "confidence": 90})

    def test_decodes_double_encoded_json_string(self) -> None:
        inner = json.dumps({"classification": "not_vcp", "confidence": 10})
        outer = json.dumps(inner)
        result = decode_openrouter_json_value(outer)
        self.assertEqual(result, {"classification": "not_vcp", "confidence": 10})

    def test_handles_bytes_input(self) -> None:
        raw = b'{"status": "ok"}'
        result = decode_openrouter_json_value(raw)
        self.assertEqual(result, {"status": "ok"})

    def test_handles_already_parsed_object_or_primitive(self) -> None:
        self.assertEqual(decode_openrouter_json_value({"key": "val"}), {"key": "val"})
        self.assertEqual(decode_openrouter_json_value(42), 42)
        self.assertIsNone(decode_openrouter_json_value(None))

    def test_raises_json_decode_error_for_completely_invalid_string(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            decode_openrouter_json_value("This has no JSON content whatsoever.")


class ParseOpenRouterStructuredContentTests(unittest.TestCase):
    def test_parses_standard_chat_choice_with_content_string(self) -> None:
        choice = {
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": '{"classification": "valid", "confidence": 92}',
            },
        }
        parsed = parse_openrouter_structured_content(choice)
        self.assertEqual(parsed, {"classification": "valid", "confidence": 92})

    def test_parses_choice_with_parsed_field(self) -> None:
        choice = {
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "parsed": {"classification": "valid", "confidence": 85},
            },
        }
        parsed = parse_openrouter_structured_content(choice)
        self.assertEqual(parsed, {"classification": "valid", "confidence": 85})

    def test_parses_choice_with_markdown_fenced_content(self) -> None:
        choice = {
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": "```json\n{\"classification\": \"valid\", \"confidence\": 90}\n```",
            },
        }
        parsed = parse_openrouter_structured_content(choice)
        self.assertEqual(parsed, {"classification": "valid", "confidence": 90})

    def test_parses_multipart_content_parts(self) -> None:
        choice = {
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": '{"classification": "valid", '},
                    {"type": "text", "text": '"confidence": 91}'},
                ],
            },
        }
        parsed = parse_openrouter_structured_content(choice)
        self.assertEqual(parsed, {"classification": "valid", "confidence": 91})

    def test_parses_choice_with_usage_hints_in_error(self) -> None:
        choice = {
            "finish_reason": "length",
            "message": {
                "role": "assistant",
                "content": "Incomplete string {",
            },
        }
        usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 4096,
            "completion_tokens_details": {"reasoning_tokens": 4000},
        }
        with self.assertRaises(ValueError) as ctx:
            parse_openrouter_structured_content(choice, usage=usage)
        self.assertIn("finish_reason='length'", str(ctx.exception))
        self.assertIn("reasoning_tokens=4000", str(ctx.exception))

    def test_parses_string_envelope_choice(self) -> None:
        raw_choice = json.dumps({
            "message": {
                "content": '{"classification": "valid", "confidence": 80}',
            }
        })
        parsed = parse_openrouter_structured_content(raw_choice)
        self.assertEqual(parsed, {"classification": "valid", "confidence": 80})
