import asyncio
import datetime
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from app.services.fundamental_data import (
    FundamentalsAuthError,
    FundamentalsDataContractError,
    FundamentalsError,
    UpstoxFundamentalsClient,
    canonical_json_hash,
    normalize_fundamentals,
)
from app.services.fundamental_llm import (
    FundamentalLLMError,
    FundamentalLLMResult,
    FundamentalSecondOpinion,
    OpenRouterFundamentalClient,
    sanitize_provider_payload,
)
from app.services.fundamental_pass import (
    Snapshot,
    Survivor,
    _finish_ai_attempt,
    _finish_unprocessed_results,
    _get_snapshot,
    _load_survivors,
    _start_ai_attempt,
    _store_annotation,
    ensure_fundamental_survivors_selected,
)
from app.services.fundamental_rules import (
    score_balanced_sepa,
    score_minervini_inspired,
    unresolved_scorecard_evidence,
)
from app.worker import WorkerSettings


def success(data):
    return {"status": "success", "data": data}


def fundamentals_bundle(*, financial_sector: bool = False):
    return {
        "company_profile": success(
            {
                "company_profile": "Example Limited makes industrial equipment.",
                "sector": "Banking" if financial_sector else "Industrials",
            }
        ),
        "income_yearly": success(
            {
                "income_statement": [
                    {
                        "category": "revenue",
                        "history": [
                            {"period": "Mar 2026", "value": 180},
                            {"period": "Mar 2025", "value": 150},
                            {"period": "Mar 2024", "value": 120},
                            {"period": "Mar 2023", "value": 100},
                        ],
                    },
                    {
                        "category": "operating_profit",
                        "history": [
                            {"period": "Mar 2026", "value": 45},
                            {"period": "Mar 2025", "value": 35},
                            {"period": "Mar 2024", "value": 28},
                            {"period": "Mar 2023", "value": 20},
                        ],
                    },
                    {
                        "category": "net_profit",
                        "history": [
                            {"period": "Mar 2026", "value": 30},
                            {"period": "Mar 2025", "value": 24},
                            {"period": "Mar 2024", "value": 18},
                            {"period": "Mar 2023", "value": 12},
                        ],
                    },
                ],
                "full_statement": [
                    {
                        "particular": "EPS - Basic",
                        "history": [
                            {"period": "Mar 2026", "value": 15},
                            {"period": "Mar 2025", "value": 12},
                            {"period": "Mar 2024", "value": 9},
                            {"period": "Mar 2023", "value": 6},
                        ],
                    }
                ],
            }
        ),
        "income_quarterly": success(
            {
                "income_statement": [
                    {
                        "category": "revenue",
                        "history": [
                            {"period": "Mar 2026", "value": 150},
                            {"period": "Dec 2025", "value": 135},
                            {"period": "Sep 2025", "value": 125},
                            {"period": "Jun 2025", "value": 115},
                            {"period": "Mar 2025", "value": 100},
                        ],
                    },
                    {
                        "category": "operating_profit",
                        "history": [
                            {"period": "Mar 2026", "value": 45},
                            {"period": "Dec 2025", "value": 38},
                            {"period": "Sep 2025", "value": 34},
                            {"period": "Jun 2025", "value": 29},
                            {"period": "Mar 2025", "value": 25},
                        ],
                    },
                    {
                        "category": "net_profit",
                        "history": [
                            {"period": "Mar 2026", "value": 30},
                            {"period": "Dec 2025", "value": 27},
                            {"period": "Sep 2025", "value": 24},
                            {"period": "Jun 2025", "value": 22},
                            {"period": "Mar 2025", "value": 20},
                        ],
                    },
                ]
            }
        ),
        "balance_sheet": success(
            {
                "full_statement": [
                    {
                        "particular": "Total Borrowings",
                        "history": [{"period": "Mar 2026", "value": 25}],
                    },
                    {
                        "particular": "Total Equity",
                        "history": [{"period": "Mar 2026", "value": 100}],
                    },
                ]
            }
        ),
        "cash_flow": success(
            {
                "cash_flow": [
                    {
                        "category": "operating",
                        "history": [
                            {"period": "Mar 2026", "value": 33},
                            {"period": "Mar 2025", "value": 25},
                            {"period": "Mar 2024", "value": 19},
                        ],
                    }
                ],
                "full_statement": [],
            }
        ),
        "key_ratios": success(
            [
                {"name": "ROE", "company_value": "18%", "sector_value": "14%"},
                {"name": "ROCE", "company_value": "21%", "sector_value": "16%"},
                {"name": "P/E", "company_value": "20", "sector_value": "24"},
            ]
        ),
        "share_holdings": success(
            [
                {
                    "category": "promoters",
                    "history": [
                        {"period": "Mar 2026", "value": 55},
                        {"period": "Dec 2025", "value": 54.5},
                        {"period": "Sep 2025", "value": 54},
                        {"period": "Jun 2025", "value": 53.5},
                    ],
                }
            ]
        ),
        "corporate_actions": success(
            [
                {
                    "name": "Dividend",
                    "expiry_date": "14 Aug 2026",
                    "amount": 5.5,
                    "ratio": None,
                    "event_details": [
                        {"name": "Announcement date", "value": "25 Apr 2026"},
                        {"name": "Record date", "value": "14 Aug 2026"},
                    ],
                }
            ]
        ),
    }


class FundamentalNormalizationTests(unittest.TestCase):
    def test_bhel_composite_ownership_cites_real_provider_keys(self) -> None:
        bundle = fundamentals_bundle()
        fixture = Path(__file__).parent / "fixtures" / "upstox_bhel_shareholdings.json"
        bundle["share_holdings"] = json.loads(fixture.read_text())
        facts = normalize_fundamentals(
            bundle,
            isin="INE257A01026",
            symbol="BHEL",
            company_name="Bharat Heavy Electricals Ltd.",
        )

        scorecard = score_minervini_inspired(facts)
        sponsorship = next(
            item for item in scorecard["components"] if item["name"] == "sponsorship"
        )
        institutional = next(
            item for item in sponsorship["metrics"] if item["key"] == "institutional_change"
        )
        self.assertAlmostEqual(institutional["value"], 7.09, places=2)
        self.assertEqual(
            set(institutional["evidence_keys"]),
            {
                "ownership.fii_change",
                "ownership.mutual_funds_change",
                "ownership.other_dii_change",
            },
        )
        self.assertEqual(unresolved_scorecard_evidence(facts, scorecard), [])
        prepared = OpenRouterFundamentalClient(api_key="unused").prepare(facts)
        allowed = {
            reference.id for reference in prepared.packet.references
        }
        self.assertNotIn("ownership.institutional_change", allowed)
        self.assertTrue(set(institutional["evidence_keys"]) <= allowed)

    def test_normalizes_growth_quality_and_explicit_unknowns(self) -> None:
        facts = normalize_fundamentals(
            fundamentals_bundle(),
            isin="INE000000001",
            symbol="EXAMPLE",
            company_name="Example",
        )

        evidence = facts["evidence"]
        self.assertAlmostEqual(
            evidence["growth.latest_quarter_revenue_yoy"]["value"]["value_pct"],
            50.0,
        )
        self.assertAlmostEqual(
            evidence["growth.latest_quarter_net_profit_yoy"]["value"][
                "value_pct"
            ],
            50.0,
        )
        self.assertAlmostEqual(
            evidence["margins.latest_quarter_yoy_change"]["value"][
                "change_percentage_points"
            ],
            5.0,
        )
        self.assertNotIn("quality.debt_to_equity", evidence)
        self.assertNotIn("quarterly_eps", facts["missing_data"])
        self.assertEqual(facts["coverage"]["quarterly_eps"], "unsupported_by_provider")
        self.assertEqual(facts["coverage"]["debt_to_equity"], "unsupported_by_provider")
        self.assertEqual(facts["periods"]["latest_quarterly"], "Mar 2026")
        self.assertEqual(
            facts["company"]["description"],
            "Example Limited makes industrial equipment.",
        )
        action = facts["evidence"]["corporate_actions.recent"]["value"][0]
        self.assertEqual(action["event_details"][0]["name"], "Announcement date")

    def test_financial_sector_marks_industrial_metrics_not_applicable(self) -> None:
        bundle = fundamentals_bundle(financial_sector=True)
        bundle["balance_sheet"] = success({"full_statement": []})
        bundle["cash_flow"] = success({"cash_flow": [], "full_statement": []})

        facts = normalize_fundamentals(
            bundle,
            isin="INE000000002",
            symbol="BANK",
            company_name="Example Bank",
        )

        self.assertTrue(facts["company"]["is_financial_sector"])
        self.assertEqual(
            facts["applicability"]["cash_conversion"],
            "not_applicable",
        )
        self.assertNotIn("cash_conversion", facts["missing_data"])
        self.assertNotIn("debt_to_equity", facts["missing_data"])

    def test_zero_denominators_losses_and_absent_periods_do_not_invent_metrics(
        self,
    ) -> None:
        bundle = fundamentals_bundle()
        annual_rows = bundle["income_yearly"]["data"]["income_statement"]
        annual_rows[0]["history"] = [{"period": "Mar 2026", "value": 180}]
        annual_rows[2]["history"][-1]["value"] = -12
        quarterly_rows = bundle["income_quarterly"]["data"]["income_statement"]
        quarterly_rows[0]["history"][-1]["value"] = 0

        facts = normalize_fundamentals(
            bundle,
            isin="INE000000003",
            symbol="LOSS",
            company_name="Loss Example",
        )

        self.assertNotIn(
            "growth.annual_revenue_cagr",
            facts["evidence"],
        )
        self.assertNotIn(
            "growth.annual_net_profit_cagr",
            facts["evidence"],
        )
        self.assertNotIn(
            "growth.latest_quarter_revenue_yoy",
            facts["evidence"],
        )


class UpstoxFundamentalsClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_only_documented_get_endpoints_and_retries_429(self) -> None:
        requests: list[httpx.Request] = []
        profile_attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal profile_attempts
            requests.append(request)
            if request.url.path.endswith("/profile"):
                profile_attempts += 1
                if profile_attempts == 1:
                    return httpx.Response(429, headers={"Retry-After": "0"})
            if request.url.path.endswith(("/key-ratios", "/share-holdings", "/corporate-actions")):
                return httpx.Response(200, json=success([]))
            return httpx.Response(200, json=success({}))

        client = UpstoxFundamentalsClient(
            analytics_token="read-only-token",
            base_url="https://upstox.test/v2",
            transport=httpx.MockTransport(handler),
            sleep=AsyncMock(),
        )
        bundle = await client.fetch_company_bundle("INE000000001")

        self.assertEqual(len(bundle), 8)
        self.assertEqual(len(requests), 9)
        self.assertTrue(all(request.method == "GET" for request in requests))
        self.assertTrue(
            all(
                request.headers["Authorization"] == "Bearer read-only-token"
                for request in requests
            )
        )
        self.assertTrue(
            all("/fundamentals/INE000000001/" in request.url.path for request in requests)
        )
        self.assertEqual(requests[0].url.path, "/v2/fundamentals/INE000000001/profile")

    async def test_contract_accepts_additive_fields_and_missing_histories(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/key-ratios"):
                data = [{"name": "ROE", "company_value": "12%", "future_field": 1}]
            elif request.url.path.endswith(("/share-holdings", "/corporate-actions")):
                data = []
            elif request.url.path.endswith("/income-statement"):
                data = {
                    "full_statement": [
                        {
                            "particular": "EPS - Basic",
                            "history": [{"period": "Mar 2026"}],
                        }
                    ],
                    "income_statement": None,
                    "future_field": {"nested": True},
                }
            else:
                data = {"future_field": {"nested": True}}
            return httpx.Response(200, json={"status": "success", "data": data, "added": True})

        client = UpstoxFundamentalsClient(
            analytics_token="token",
            base_url="https://upstox.test/v2",
            transport=httpx.MockTransport(handler),
        )
        bundle = await client.fetch_company_bundle("INE000000001")
        self.assertTrue(bundle["company_profile"]["data"]["future_field"]["nested"])

    async def test_contract_rejects_incorrect_data_shape(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            data = {} if request.url.path.endswith("/key-ratios") else []
            return httpx.Response(200, json=success(data))

        client = UpstoxFundamentalsClient(
            analytics_token="token",
            base_url="https://upstox.test/v2",
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(FundamentalsDataContractError):
            await client.fetch_company_bundle("INE000000001")

    async def test_401_is_terminal_and_not_retried(self) -> None:
        handler = AsyncMock(return_value=httpx.Response(401, json={}))
        client = UpstoxFundamentalsClient(
            analytics_token="expired",
            base_url="https://upstox.test/v2",
            transport=httpx.MockTransport(handler),
            sleep=AsyncMock(),
        )
        with self.assertRaises(FundamentalsAuthError):
            await client.fetch_company_bundle("INE000000001")
        self.assertEqual(handler.await_count, 1)

    async def test_malformed_json_has_bounded_retries(self) -> None:
        calls = 0

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )

        client = UpstoxFundamentalsClient(
            analytics_token="token",
            base_url="https://upstox.test/v2",
            max_attempts=2,
            transport=httpx.MockTransport(handler),
            sleep=AsyncMock(),
        )
        with self.assertRaises(FundamentalsError):
            await client.fetch_company_bundle("INE000000001")
        self.assertEqual(calls, 2)


class OpenRouterFundamentalClientTests(unittest.IsolatedAsyncioTestCase):
    def facts(self):
        return {
            "schema_version": "fundamental_facts_v3",
            "statement_type": "consolidated",
            "company": {"isin": "INE000000001", "symbol": "EXAMPLE"},
            "periods": {"latest_annual": "Mar 2026"},
            "evidence": {
                "growth.annual_revenue_cagr": {
                    "label": "Revenue CAGR",
                    "value": 20,
                    "unit": "percent",
                }
            },
            "provider_limitations": ["quarterly_eps_yoy"],
        }

    @staticmethod
    def opinion(reference_id: str, *, verdict: str = "pass") -> dict:
        return {
            "verdict": verdict,
            "summary": "Available growth evidence supports a grounded second opinion.",
            "verdict_reference_ids": [reference_id],
            "strengths": [
                {"text": "Revenue growth is supportive.", "reference_ids": [reference_id]}
            ],
            "risks": [],
            "review_focus": [],
        }

    def test_builds_bounded_blind_packet_and_dynamic_reference_enums(self) -> None:
        client = OpenRouterFundamentalClient(
            api_key="openrouter-key",
            prompt_max_chars=6_000,
        )
        prepared = client.prepare(self.facts())
        request = prepared.request_payload
        user_content = request["messages"][1]["content"]
        packet = json.loads(user_content)

        self.assertLessEqual(len(user_content), 6_000)
        self.assertEqual(packet["prompt_version"], "fundamental_second_opinion_v1")
        self.assertNotIn("score", user_content)
        self.assertNotIn("grade", user_content)
        self.assertNotIn("red_flags", user_content)
        self.assertNotIn("rubric", user_content)
        allowed = {item["id"] for item in packet["references"]}
        self.assertIn("growth.annual_revenue_cagr", allowed)
        self.assertIn("limitation.quarterly_eps_yoy", allowed)
        schema = request["response_format"]["json_schema"]["schema"]
        self.assertEqual(
            schema["properties"]["verdict_reference_ids"]["items"]["type"],
            "string",
        )
        self.assertEqual(
            schema["$defs"]["ReferenceNote"]["properties"]["reference_ids"]["items"]["type"],
            "string",
        )

    def test_rejects_irreducible_packet_over_configured_limit(self) -> None:
        facts = self.facts()
        facts["evidence"]["large"] = {"label": "Large", "value": "x" * 7_000}
        client = OpenRouterFundamentalClient(
            api_key="openrouter-key",
            prompt_max_chars=6_000,
        )

        with self.assertRaisesRegex(
            FundamentalLLMError,
            "second-opinion packet exceeds configured size limit",
        ):
            client.build_request(facts)

    def test_no_usable_facts_is_detected_without_a_model_call(self) -> None:
        prepared = OpenRouterFundamentalClient(api_key="unused").prepare(
            {
                "schema_version": "fundamental_facts_v3",
                "provider_limitations": ["quarterly_eps_yoy"],
            }
        )
        self.assertFalse(prepared.has_usable_facts)
        self.assertEqual(
            [reference.kind for reference in prepared.packet.references],
            ["limitation"],
        )

    async def test_hashes_exact_packet_and_accepts_valid_opinion(self) -> None:
        seen_packet = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            request_payload = json.loads(request.content)
            seen_packet.update(json.loads(request_payload["messages"][1]["content"]))
            reference_id = seen_packet["references"][0]["id"]
            return httpx.Response(
                200,
                json={
                    "id": "or-compact-1",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.002},
                    "choices": [
                        {"message": {"content": json.dumps(self.opinion(reference_id))}}
                    ],
                },
            )

        client = OpenRouterFundamentalClient(
            api_key="openrouter-key",
            api_url="https://openrouter.test/chat/completions",
            transport=httpx.MockTransport(handler),
        )

        result = await client.analyze(self.facts())

        self.assertEqual(result.input_hash, canonical_json_hash(seen_packet))
        self.assertEqual(result.request_id, "or-compact-1")
        self.assertEqual(result.opinion.verdict, "pass")
        self.assertEqual(result.cost, 0.002)

    async def test_sparse_facts_can_return_uncertain(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    self.opinion(
                                        "growth.annual_revenue_cagr",
                                        verdict="uncertain",
                                    )
                                )
                            }
                        }
                    ]
                },
            )

        client = OpenRouterFundamentalClient(
            api_key="key",
            api_url="https://openrouter.test/chat/completions",
            transport=httpx.MockTransport(handler),
        )
        result = await client.analyze(self.facts())
        self.assertEqual(result.opinion.verdict, "uncertain")

    async def test_unknown_reference_ids_are_sanitized_softly(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    self.opinion("unknown_nonexistent_metric_id")
                                )
                            }
                        }
                    ]
                },
            )

        client = OpenRouterFundamentalClient(
            api_key="key",
            api_url="https://openrouter.test/chat/completions",
            transport=httpx.MockTransport(handler),
        )
        result = await client.analyze(self.facts())
        self.assertEqual(result.opinion.verdict, "pass")
        self.assertNotIn("unknown_nonexistent_metric_id", result.opinion.verdict_reference_ids)
        self.assertGreater(len(result.opinion.verdict_reference_ids), 0)

    async def test_invalid_paid_output_is_not_retried_and_keeps_usage(self) -> None:
        calls = 0

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                json={
                    "id": "invalid-paid",
                    "usage": {"prompt_tokens": 33, "completion_tokens": 10, "cost": 0.004},
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"verdict": "invalid_verdict_value", "summary": "Bad verdict value"}
                                )
                            }
                        }
                    ]
                },
            )

        client = OpenRouterFundamentalClient(
            api_key="openrouter-key",
            api_url="https://openrouter.test/chat/completions",
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(FundamentalLLMError) as context:
            await client.analyze(self.facts())
        self.assertEqual(calls, 1)
        self.assertEqual(context.exception.attempt_status, "invalid_response")
        self.assertEqual(context.exception.request_id, "invalid-paid")
        self.assertEqual(context.exception.cost, 0.004)
        self.assertEqual(context.exception.usage["prompt_tokens"], 33)

    async def test_sends_strict_non_streaming_reasoning_excluded_request(self) -> None:
        seen_payload = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen_payload.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "or-request-1",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        **self.opinion("growth.annual_revenue_cagr"),
                                    }
                                )
                            }
                        }
                    ],
                },
            )

        client = OpenRouterFundamentalClient(
            api_key="openrouter-key",
            api_url="https://openrouter.test/chat/completions",
            transport=httpx.MockTransport(handler),
            sleep=AsyncMock(),
        )
        result = await client.analyze(self.facts())

        self.assertEqual(result.opinion.verdict, "pass")
        self.assertEqual(result.request_id, "or-request-1")
        self.assertEqual(seen_payload["model"], "openai/gpt-5.6-luna-pro")
        self.assertFalse(seen_payload["stream"])
        self.assertEqual(
            seen_payload["response_format"]["type"],
            "json_schema",
        )
        self.assertTrue(
            seen_payload["response_format"]["json_schema"]["strict"]
        )
        schema = seen_payload["response_format"]["json_schema"]["schema"]
        self.assertEqual(
            set(schema["required"]),
            {"verdict", "summary", "verdict_reference_ids", "strengths", "risks", "review_focus"},
        )
        self.assertEqual(
            seen_payload["plugins"],
            [{"id": "response-healing"}],
        )
        self.assertEqual(
            seen_payload["provider"],
            {"require_parameters": True, "data_collection": "deny"},
        )
        self.assertEqual(
            seen_payload["reasoning"],
            {"effort": "medium", "exclude": True},
        )
        self.assertEqual(seen_payload["temperature"], 0)
        self.assertNotIn("enabled", seen_payload["reasoning"])
        self.assertNotIn("reasoning_details", json.dumps(seen_payload))

    async def test_surfaces_openrouter_error_message(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={
                    "error": {
                        "message": "No endpoints support all requested parameters"
                    }
                },
            )

        client = OpenRouterFundamentalClient(
            api_key="openrouter-key",
            api_url="https://openrouter.test/chat/completions",
            transport=httpx.MockTransport(handler),
            sleep=AsyncMock(),
        )
        with self.assertRaises(FundamentalLLMError) as ctx:
            await client.analyze(self.facts())
        self.assertIn(
            "No endpoints support all requested parameters",
            str(ctx.exception),
        )

    async def test_invalid_200_is_not_retried(self) -> None:
        calls = 0

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                json={
                    "usage": {
                        "completion_tokens": 1600,
                        "completion_tokens_details": {"reasoning_tokens": 1500},
                    },
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": None},
                        }
                    ],
                },
            )

        client = OpenRouterFundamentalClient(
            api_key="key",
            api_url="https://openrouter.test/chat/completions",
            max_attempts=2,
            transport=httpx.MockTransport(handler),
            sleep=AsyncMock(),
        )
        with self.assertRaises(FundamentalLLMError) as ctx:
            await client.analyze(self.facts())
        self.assertEqual(calls, 1)
        self.assertIn("finish_reason='length'", str(ctx.exception))
        self.assertIn("reasoning_tokens=1500", str(ctx.exception))

    async def test_retries_one_clearly_transient_http_error(self) -> None:
        calls = 0

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(503, json={"error": {"message": "temporarily unavailable"}})
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps(self.opinion("growth.annual_revenue_cagr"))}}
                    ]
                },
            )

        client = OpenRouterFundamentalClient(
            api_key="key",
            api_url="https://openrouter.test/chat/completions",
            max_attempts=2,
            transport=httpx.MockTransport(handler),
            sleep=AsyncMock(),
        )
        result = await client.analyze(self.facts())
        self.assertEqual(calls, 2)
        self.assertEqual(result.opinion.verdict, "pass")

    async def test_transport_unknown_is_not_retried(self) -> None:
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("unknown outcome", request=request)

        client = OpenRouterFundamentalClient(
            api_key="key",
            api_url="https://openrouter.test/chat/completions",
            max_attempts=2,
            transport=httpx.MockTransport(handler),
            sleep=AsyncMock(),
        )
        with self.assertRaises(FundamentalLLMError) as context:
            await client.analyze(self.facts())
        self.assertEqual(calls, 1)
        self.assertEqual(context.exception.attempt_status, "transport_unknown")

    def test_reasoning_details_are_removed_recursively(self) -> None:
        sanitized = sanitize_provider_payload(
            {
                "reasoning_details": "secret",
                "nested": [{"reasoning_details": {"secret": True}, "safe": 1}],
            }
        )
        self.assertEqual(sanitized, {"nested": [{"safe": 1}]})


class FundamentalAttemptPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_started_and_invalid_attempt_keep_exact_trace_fields(self) -> None:
        captured: list[dict] = []
        attempt_id = uuid4()

        class FakeResult:
            def one(self):
                return SimpleNamespace(id=attempt_id, attempt_number=1)

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def execute(self, _query, params):
                captured.append(params)
                return FakeResult()

            async def commit(self):
                return None

        survivor = Survivor(
            result_id=uuid4(),
            scan_run_id=uuid4(),
            instrument_id=uuid4(),
            isin="INE000000001",
            symbol="EXAMPLE",
            company_name="Example",
        )
        client = OpenRouterFundamentalClient(api_key="key")
        prepared = client.prepare(
            {
                "schema_version": "fundamental_facts_v3",
                "evidence": {
                    "growth.annual_revenue_cagr": {
                        "label": "Revenue CAGR",
                        "value": 20,
                    }
                },
            }
        )
        error = FundamentalLLMError(
            "invalid paid output",
            response_payload={"id": "request", "choices": []},
            http_status=200,
            request_id="request",
            usage={"prompt_tokens": 10, "cost": 0.003},
            cost=0.003,
            attempt_status="invalid_response",
        )

        with patch(
            "app.services.fundamental_pass.async_session",
            side_effect=FakeSession,
        ):
            stored_id, number = await _start_ai_attempt(
                uuid4(), survivor, client, prepared
            )
            await _finish_ai_attempt(
                stored_id,
                status="invalid_response",
                error=error,
            )

        self.assertEqual(stored_id, attempt_id)
        self.assertEqual(number, 1)
        self.assertEqual(
            json.loads(captured[0]["request_payload"]),
            prepared.request_payload,
        )
        self.assertEqual(captured[1]["status"], "invalid_response")
        self.assertEqual(captured[1]["request_id"], "request")
        self.assertEqual(captured[1]["cost"], 0.003)
        self.assertEqual(
            json.loads(captured[1]["usage"])["prompt_tokens"],
            10,
        )

    async def test_successful_annotation_links_to_source_attempt(self) -> None:
        captured: dict = {}

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def execute(self, _query, params):
                captured.update(params)

            async def commit(self):
                return None

        client = OpenRouterFundamentalClient(api_key="key")
        attempt_id = uuid4()
        result = FundamentalLLMResult(
            opinion=FundamentalSecondOpinion(
                **OpenRouterFundamentalClientTests.opinion(
                    "growth.annual_revenue_cagr"
                )
            ),
            request_id="request",
            usage={"prompt_tokens": 5},
            input_hash="input-hash",
            cost=0.001,
            request_payload={"messages": []},
            response_payload={"id": "request"},
        )
        with patch(
            "app.services.fundamental_pass.async_session",
            side_effect=FakeSession,
        ):
            await _store_annotation("analysis-key", client, result, attempt_id)

        self.assertEqual(captured["source_attempt_id"], attempt_id)
        self.assertEqual(captured["input_hash"], "input-hash")
        self.assertEqual(json.loads(captured["payload"])["verdict"], "pass")


class FundamentalPassOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_finishes_unprocessed_results_with_terminal_status(self) -> None:
        captured_sql = ""
        captured_params = {}

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def execute(self, query, params):
                nonlocal captured_sql, captured_params
                captured_sql = str(query)
                captured_params = params

            async def commit(self):
                return None

        scan_run_id = str(uuid4())
        with patch(
            "app.services.fundamental_pass.async_session",
            side_effect=FakeSession,
        ):
            await _finish_unprocessed_results(
                scan_run_id,
                llm_status="skipped",
                ai_status="paused",
                reason="Fundamental processing is paused",
            )

        self.assertIn("llm_status IN ('queued', 'running')", captured_sql)
        self.assertEqual(captured_params["scan_run_id"], scan_run_id)
        self.assertEqual(captured_params["llm_status"], "skipped")
        self.assertEqual(captured_params["ai_status"], "paused")

    async def test_ensure_fundamental_survivors_selected_backfills_unselected_candidates(self) -> None:
        scan_run_id = str(uuid4())
        cand1_id = uuid4()
        cand2_id = uuid4()

        class FakeCountResult:
            def scalar_one_or_none(self):
                return 0

        class FakeCandidateRow:
            def __init__(self, id, rank, sym, ind):
                self.id = id
                self.instrument_id = uuid4()
                self.result_rank = rank
                self.symbol = sym
                self.industry = ind
                self.technical_metrics = {}

        class FakeCandidatesResult:
            def all(self):
                return [
                    FakeCandidateRow(cand1_id, 1, "SYM1", "IT"),
                    FakeCandidateRow(cand2_id, 2, "SYM2", "Banking"),
                ]

        executed_queries = []
        executed_params = []

        class FakeSession:
            def __init__(self):
                self.call_count = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def execute(self, query, params=None):
                self.call_count += 1
                executed_queries.append(str(query))
                executed_params.append(params)
                if self.call_count == 1:
                    return FakeCountResult()
                elif self.call_count == 2:
                    return FakeCandidatesResult()
                return None

            async def commit(self):
                return None

        with patch(
            "app.services.fundamental_pass.async_session",
            side_effect=FakeSession,
        ):
            selected_count = await ensure_fundamental_survivors_selected(scan_run_id)

        self.assertEqual(selected_count, 2)
        # Should have run count check, candidate load, and 2 updates
        self.assertEqual(len(executed_queries), 4)
        self.assertIn("UPDATE screening_results", executed_queries[2])
        self.assertIn("UPDATE screening_results", executed_queries[3])

    async def test_ensure_fundamental_survivors_selected_noop_when_already_selected(self) -> None:
        scan_run_id = str(uuid4())

        class FakeCountResult:
            def scalar_one_or_none(self):
                return 20

        executed_queries = []

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def execute(self, query, _params=None):
                executed_queries.append(str(query))
                return FakeCountResult()

            async def commit(self):
                return None

        with patch(
            "app.services.fundamental_pass.async_session",
            side_effect=FakeSession,
        ):
            selected_count = await ensure_fundamental_survivors_selected(scan_run_id)

        self.assertEqual(selected_count, 20)
        self.assertEqual(len(executed_queries), 1)

    async def test_survivor_loader_reads_persisted_technical_results_only(self) -> None:
        captured_sql = ""

        class FakeResult:
            def all(self):
                return []

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def execute(self, query, _params):
                nonlocal captured_sql
                captured_sql = str(query)
                return FakeResult()

        with patch(
            "app.services.fundamental_pass.async_session",
            side_effect=FakeSession,
        ):
            self.assertEqual(await _load_survivors(str(uuid4())), [])

        self.assertIn("FROM screening_results", captured_sql)
        self.assertIn("s.technical_passed = true", captured_sql)
        self.assertIn("fundamental_selected", captured_sql)
        self.assertIn("LIMIT 20", captured_sql)
        self.assertNotIn("universe_memberships", captured_sql)
        self.assertNotIn("market_candles", captured_sql)

    async def test_recent_snapshot_is_reused_without_provider_call(self) -> None:
        survivor = Survivor(
            result_id=uuid4(),
            scan_run_id=uuid4(),
            instrument_id=uuid4(),
            isin="INE000000001",
            symbol="EXAMPLE",
            company_name="Example",
        )
        snapshot = Snapshot(
            snapshot_id=uuid4(),
            facts={"evidence": {}},
            fetched_at=datetime.datetime.now(datetime.timezone.utc),
            latest_annual_period="Mar 2026",
            latest_quarterly_period="Mar 2026",
            cache_hit=True,
        )
        provider = AsyncMock()

        with patch(
            "app.services.fundamental_pass._linked_snapshot",
            return_value=None,
        ), patch(
            "app.services.fundamental_pass._cached_snapshot",
            return_value=snapshot,
        ):
            result = await _get_snapshot(survivor, provider)

        self.assertEqual(result, snapshot)
        provider.fetch_company_bundle.assert_not_awaited()

    async def test_result_linked_snapshot_ignores_ttl_on_retry(self) -> None:
        survivor = Survivor(
            result_id=uuid4(),
            scan_run_id=uuid4(),
            instrument_id=uuid4(),
            isin="INE000000001",
            symbol="EXAMPLE",
            company_name="Example",
        )
        linked = Snapshot(
            snapshot_id=uuid4(),
            facts={"schema_version": "fundamental_facts_v3"},
            fetched_at=datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
            latest_annual_period="Mar 2020",
            latest_quarterly_period="Mar 2020",
            cache_hit=True,
        )
        provider = AsyncMock()

        with patch(
            "app.services.fundamental_pass._linked_snapshot",
            return_value=linked,
        ), patch(
            "app.services.fundamental_pass._cached_snapshot",
            new=AsyncMock(),
        ) as ttl_cache:
            result = await _get_snapshot(survivor, provider)

        self.assertEqual(result, linked)
        ttl_cache.assert_not_awaited()
        provider.fetch_company_bundle.assert_not_awaited()

    def test_rules_are_authoritative_and_provider_limitations_are_neutral(self) -> None:
        facts = normalize_fundamentals(
            fundamentals_bundle(), isin="INE000000010", symbol="RULES", company_name="Rules"
        )
        scorecard = score_balanced_sepa(facts)
        self.assertEqual(scorecard["rubric_version"], "balanced_sepa_v3")
        self.assertIn("debt_to_equity", scorecard["provider_limitations"])
        self.assertNotIn("leverage", scorecard["red_flags"])

    def test_worker_registers_p7_job(self) -> None:
        names = [function.__name__ for function in WorkerSettings.functions]
        self.assertIn("run_fundamental_pass", names)


if __name__ == "__main__":
    unittest.main()
