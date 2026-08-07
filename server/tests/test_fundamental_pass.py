import asyncio
import datetime
import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from app.services.fundamental_data import (
    FundamentalsAuthError,
    FundamentalsError,
    UpstoxFundamentalsClient,
    normalize_fundamentals,
)
from app.services.fundamental_llm import (
    FundamentalLLMError,
    OpenRouterFundamentalClient,
)
from app.services.fundamental_pass import (
    Snapshot,
    Survivor,
    _get_snapshot,
    _load_survivors,
)
from app.services.fundamental_rules import score_balanced_sepa
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
            "schema_version": "fundamental_facts_v1",
            "evidence": {
                "growth.annual_revenue_cagr": {
                    "label": "Revenue CAGR",
                    "value": 20,
                }
            },
            "missing_data": ["quarterly_eps"],
        }

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
                                        "verdict": "pass",
                                        "summary": "Growth evidence is supportive.",
                                        "criteria": [
                                            {
                                                "name": "sales_growth",
                                                "status": "positive",
                                                "explanation": "Revenue CAGR is positive.",
                                                "evidence_keys": [
                                                    "growth.annual_revenue_cagr"
                                                ],
                                            }
                                        ],
                                        "red_flags": [],
                                        "missing_data": ["quarterly_eps"],
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

        self.assertEqual(result.verdict.verdict, "pass")
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
            {"verdict", "summary", "criteria", "red_flags", "missing_data"},
        )
        self.assertEqual(
            seen_payload["provider"],
            {"require_parameters": True, "data_collection": "deny"},
        )
        self.assertEqual(
            seen_payload["reasoning"],
            {"effort": "low", "exclude": True},
        )
        self.assertEqual(seen_payload["temperature"], 0)
        self.assertNotIn("enabled", seen_payload["reasoning"])
        self.assertNotIn("reasoning_details", json.dumps(seen_payload))

    async def test_includes_temperature_only_when_explicitly_configured(self) -> None:
        seen_payload = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen_payload.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "verdict": "uncertain",
                                        "summary": "Only limited evidence is available.",
                                        "criteria": [
                                            {
                                                "name": "sales_growth",
                                                "status": "unknown",
                                                "explanation": "Evidence is limited.",
                                                "evidence_keys": [],
                                            }
                                        ],
                                        "red_flags": [],
                                        "missing_data": ["quarterly_eps"],
                                    }
                                )
                            }
                        }
                    ]
                },
            )

        client = OpenRouterFundamentalClient(
            api_key="openrouter-key",
            api_url="https://openrouter.test/chat/completions",
            temperature=0.2,
            transport=httpx.MockTransport(handler),
            sleep=AsyncMock(),
        )
        await client.analyze(self.facts())
        self.assertEqual(seen_payload["temperature"], 0.2)

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

    async def test_parses_content_parts_array(self) -> None:
        verdict_payload = {
            "verdict": "pass",
            "summary": "Growth evidence is supportive.",
            "criteria": [
                {
                    "name": "sales_growth",
                    "status": "positive",
                    "explanation": "Revenue CAGR is positive.",
                    "evidence_keys": ["growth.annual_revenue_cagr"],
                }
            ],
            "red_flags": [],
            "missing_data": ["quarterly_eps"],
        }

        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": json.dumps(verdict_payload),
                                    }
                                ]
                            }
                        }
                    ]
                },
            )

        client = OpenRouterFundamentalClient(
            api_key="openrouter-key",
            api_url="https://openrouter.test/chat/completions",
            transport=httpx.MockTransport(handler),
            sleep=AsyncMock(),
        )
        result = await client.analyze(self.facts())
        self.assertEqual(result.verdict.verdict, "pass")

    async def test_null_content_fails_with_finish_reason_after_retries(self) -> None:
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
        self.assertEqual(calls, 2)
        self.assertIn("finish_reason='length'", str(ctx.exception))
        self.assertIn("reasoning_tokens=1500", str(ctx.exception))

    async def test_rejects_unverifiable_evidence_after_bounded_retries(self) -> None:
        calls = 0

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "verdict": "pass",
                                        "summary": "Unsupported.",
                                        "criteria": [
                                            {
                                                "name": "sales_growth",
                                                "status": "positive",
                                                "explanation": "Invented evidence.",
                                                "evidence_keys": ["invented.metric"],
                                            }
                                        ],
                                        "red_flags": [],
                                        "missing_data": [],
                                    }
                                )
                            }
                        }
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
        with self.assertRaises(FundamentalLLMError):
            await client.analyze(self.facts())
        self.assertEqual(calls, 2)


class FundamentalPassOrchestrationTests(unittest.IsolatedAsyncioTestCase):
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
            "app.services.fundamental_pass._cached_snapshot",
            return_value=snapshot,
        ):
            result = await _get_snapshot(survivor, provider)

        self.assertEqual(result, snapshot)
        provider.fetch_company_bundle.assert_not_awaited()

    def test_rules_are_authoritative_and_provider_limitations_are_neutral(self) -> None:
        facts = normalize_fundamentals(
            fundamentals_bundle(), isin="INE000000010", symbol="RULES", company_name="Rules"
        )
        scorecard = score_balanced_sepa(facts)
        self.assertEqual(scorecard["rubric_version"], "balanced_sepa_v2")
        self.assertIn("debt_to_equity", scorecard["provider_limitations"])
        self.assertNotIn("leverage", scorecard["red_flags"])

    def test_worker_registers_p7_job(self) -> None:
        names = [function.__name__ for function in WorkerSettings.functions]
        self.assertIn("run_fundamental_pass", names)


if __name__ == "__main__":
    unittest.main()
