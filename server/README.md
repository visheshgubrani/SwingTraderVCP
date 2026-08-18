# SwingTraderVCP Server

The backend defaults to `APP_ENVIRONMENT=production` and therefore fails
closed. Set `APP_ENVIRONMENT=development` in the local `server/.env` (or
`test` in tests) so paid scanner development does not require subscription
fixtures. Protected production calls use short-lived HMAC assertions minted
by Next from Better Auth sessions; FastAPI should remain BFF-only.

## P7 survivor-only fundamental annotations

P7 runs as a separate arq job after a technical scan has persisted its
shortlist. It queries only those `screening_results` rows, fetches read-only
Upstox fundamentals by ISIN, stores a normalized primary snapshot, enriches
technical survivors with best-effort official NSE shareholding and integrated
filing XBRLs for promoter-pledge/leverage checks that transparently adjust
only the deterministic fundamental score, computes the
authoritative Python fit, and may request a blind strict OpenRouter second
opinion. AI failure never changes a valid source/rules result. P7 never imports
or calls the execution engine, and any provider/model failure leaves chart
review and the manual trade checkpoint available.

Apply the P7 migration after the earlier migrations:

```bash
docker exec -i algo-trading-postgres \
  psql -v ON_ERROR_STOP=1 -U algo -d algo_trading \
  < server/db/migrations/003_p7_fundamental_pass.sql
```

Existing databases also require the additive reliability and trace migrations
in numeric order, including the NSE risk enrichment migration (whose historical
filename contains `scanner_v4`) and VCP vision hardening:

```bash
docker exec -i algo-trading-postgres \
  psql -v ON_ERROR_STOP=1 -U algo -d algo_trading \
  < server/db/migrations/008_p7_ai_trace.sql

docker exec -i algo-trading-postgres \
  psql -v ON_ERROR_STOP=1 -U algo -d algo_trading \
  < server/db/migrations/012_scanner_v4_nse_risk.sql

docker exec -i algo-trading-postgres \
  psql -v ON_ERROR_STOP=1 -U algo -d algo_trading \
  < server/db/migrations/013_vcp_vision.sql

docker exec -i algo-trading-postgres \
  psql -v ON_ERROR_STOP=1 -U algo -d algo_trading \
  < server/db/migrations/014_vcp_vision_hardening.sql
```

Configure the worker without committing credentials:

```env
P7_FUNDAMENTAL_PASS_ENABLED=true
UPSTOX_ANALYTICS_TOKEN=<one-year-read-only-analytics-token>
NSE_FUNDAMENTAL_RISK_ENABLED=true
OPENROUTER_API_KEY=<openrouter-key>
OPENROUTER_MODEL=deepseek/deepseek-v4-flash
OPENROUTER_REASONING_EFFORT=xhigh  # low | medium | high | xhigh
FUNDAMENTAL_RUN_TOKEN_BUDGET=150000
FUNDAMENTAL_PROMPT_MAX_CHARS=12000
```

Defaults lock the provider/model behavior:

- Known promoter-pledge warning/red/severe results adjust the deterministic
  fundamental score by `-3/-8/-15`; known non-financial leverage results use
  `-2/-5/-10`. Unknown, ambiguous, healthy, and not-applicable results have no
  score impact. These adjustments never alter technical rank or auto-reject.

- consolidated Upstox Company Fundamentals statements
- a 24-hour snapshot cache for new work; retry-incomplete reuses the exact
  result-linked snapshot regardless of TTL
- one survivor and one provider/model call at a time
- OpenRouter remains runtime-configurable: set `OPENROUTER_MODEL` and
  `OPENROUTER_REASONING_EFFORT` in `server/.env`. P7 records the exact model,
  reasoning effort, prompt version, and usage for each annotation.
- a packet-derived strict JSON Schema whose reference enums exactly match the
  blind request packet; reasoning is enabled but excluded from the response
- provider data collection denied

If P7 is disabled, existing scans retain `llm_status=not_requested`. Source and
AI state are independent: Upstox/normalization errors fail `fundamental_status`,
while OpenRouter errors affect only `ai_status`. Legacy `llm_status` remains a
compatibility mirror for journal readers.

## On-demand VCP vision validator (advisory)

This is a per-result, human-triggered chart-image second opinion. It never
changes technical rank, `vcp_detected`, `reviewer_status`, watchlists, trade
drafts, or execution state.

Flow:

1. The API persists the last 252 EOD sessions through the scan `as_of_date`
   (`vcp_visual_analyses.frozen_ohlcv` + `source_hash`) and creates an
   `awaiting_capture` analysis. Identical frozen source + renderer + model +
   reasoning/max-token settings + prompt/schema reuse the same analysis.
2. The frontend captures two standardized 1280×720 charts (252-session
   context, 126-session detail, log scale) with a fixed renderer version and
   uploads them as raw PNGs. When both are present the analysis is queued and
   the `run_vcp_vision_analysis` arq job starts.
3. The worker reads the immutable packet, verifies its source hash, and sends
   one blind OpenRouter request (prompt text + compact OHLCV table + both
   images, strict JSON schema, reasoning excluded, provider data collection
   denied). Every attempt is persisted in `vcp_visual_attempts`.
4. All returned dates must snap within 3 calendar days to frozen trading bars;
   contraction ranges/depth/sessions and pivot price are derived
   deterministically in Python. Two contraction-start peaks plus a later pivot
   close two windows. A response that invents dates is rejected and the
   analysis fails for audit; an underspecified `valid` verdict is stored as
   `uncertain` instead of failing.
5. Only explicit 429/5xx errors are retried once. `transport_unknown` and
   `invalid_response` outcomes are never auto-retried; the human can retry a
   failed analysis with the same stored charts.

Configure without committing credentials:

```env
VCP_VISION_ENABLED=true
OPENROUTER_API_KEY=<openrouter-key>
VCP_VISION_MODEL=google/gemini-3.7-flash
VCP_VISION_REASONING_EFFORT=high  # low | medium | high | xhigh
VCP_VISION_MAX_TOKENS=16384  # includes hidden reasoning + structured JSON
```

The API refuses new analyses while disabled. If configuration is disabled after
an analysis was queued, the worker marks that row failed with `VisionDisabled`
so it never remains stuck in `queued`.

## P4 execution modes

Paper mode remains the safe default:

```env
EXECUTION_MODE=paper
LIVE_ORDER_PLACEMENT_ENABLED=false
```

In paper mode, confirmation creates an idempotent entry intent, **immediately
fills the entry at the planned price**, opens the position, and never contacts
Fyers. The position monitor then enforces software SL/target/trailing on Redis
LTP ticks.

Live P4 is deliberately double-armed:

```env
EXECUTION_MODE=live
LIVE_ORDER_PLACEMENT_ENABLED=true
```

Both values are required. Live P4 supports buy-side CNC market/limit entries
only. It does not place exchange-held stop-loss or target orders; P5 uses a
software position monitor for market exits. Before enabling live placement,
activate a current Fyers trading app, whitelist the deployment's static IP,
and complete the required daily authentication.

Apply the migrations in order for a database created before P4:

```bash
docker exec -i algo-trading-postgres \
  psql -v ON_ERROR_STOP=1 -U algo -d algo_trading \
  < server/db/migrations/001_p3_paper_trading.sql

docker exec -i algo-trading-postgres \
  psql -v ON_ERROR_STOP=1 -U algo -d algo_trading \
  < server/db/migrations/002_p4_live_order_gateway.sql
```

Run the single order gateway as its own supervised process in **both paper and
live**. Paper drains Redis `paper_order_events` into the same fill processors;
it must not open a Fyers order WebSocket. Live owns the one Fyers order
WebSocket and subscribes only to `OnOrders,OnTrades`.

```bash
cd server
uv run python -m app.workers.order_gateway
```

A Redis singleton lock prevents two gateways from running at once. P10 paper
entries fail closed if this heartbeat is missing.

## P5 position monitor

Run the position monitor as its own supervised process in **both paper and live**
modes:

```bash
cd server
uv run python -m app.workers.position_monitor
```

The monitor:

- reloads non-closed positions from Postgres on start and every ~20 seconds
- subscribes to Redis `ticks` and `system_controls` (kill switch)
- ratchets `step_pct` trailing stops and writes `position_events`
- creates idempotent exit intents through the execution engine when SL/target
  rules trigger
- fills paper exits immediately at the observed LTP; submits live market exits
  via async Fyers REST when disarmed
- publishes `position_monitor:status` heartbeats to Redis (TTL 30s)

When the global kill switch is engaged, the monitor **does not** create new exit
intents or trailing-stop writes.

## P9 market context and stop-streak protection

Migration `018_p9_market_context.sql` installs P9 in shadow mode. The core EOD
chain is `candle sync → run_market_context → personal scan`; the SaaS scan is
queued independently and never consumes P9 ordering. P9 index history is
selected from instrument metadata and remains EOD-only.

Before policy enforcement, validate a freshly downloaded FYERS NSE symbol
master and produce the 2018-present replay report:

```bash
uv run python scripts/validate_p9_fyers_symbols.py --master /secure/NSE_symbol_master.csv
uv run python scripts/replay_p9_market_context.py \
  --start 2018-01-01 --end "$(date +%F)" --output /tmp/p9-replay.json
```

Review the report's 2018/2020/2022 windows, data failures, formula overlap, and
membership warning. The report command is transactionally rolled back and
cannot promote a policy. The personal proposal page exposes the exact snapshot,
sector tiers, policy sign-off fields, allocation gate evidence, breaker state,
and owner reset. An enforced policy fails closed for stale/incomplete context
or an unavailable sector; neither P9 nor the breaker changes existing-position
management.

## P10 proposal and entry workers

Run the P10 processes separately from the core arq worker:

```bash
uv run python -m app.workers.proposal_worker
uv run python -m app.workers.entry_supervisor
```

The proposal worker is serial and uses its dedicated Redis queue. The entry
supervisor reconstructs verified 5-minute triggers and add/correction state
from Postgres; approval requests never place orders inline. Apply migrations
`015`, `016`, `017`, and `018` in order on an existing database before starting
them.

### Live submission safety

- The human checkpoint, pending position, and deterministic intent commit
  before any broker request.
- Live submission fails closed unless the single order gateway has a fresh
  Redis heartbeat.
- A `submission_pending` claim commits immediately before the one HTTP call,
  blocking concurrent placement.
- HTTP timeouts, connection failures, malformed success responses, and server
  errors become `submission_unknown`. They are never automatically retried.
- Definite broker errors become `rejected`; an unfilled pending position is
  cancelled.
- Fyers `id_fyers`, order IDs, exchange IDs, and a compact order tag correlate
  WebSocket updates. Exact event replays and duplicate trade IDs are ignored.
- One Redis token bucket serializes the execution path to at most 10 OPS.

The API surface is under `/api/v1`:

- `GET /trading/execution-status`: public paper/live UI state, without
  credentials.
- `POST /trading/trade-instructions`: save a validated human draft.
- `POST /trading/trade-instructions/{id}/confirm`: explicit manual
  checkpoint. The required phrase is `CONFIRM_PAPER_TRADE` in paper mode and
  `CONFIRM_LIVE_ORDER` in live mode.
- `GET /trading/trade-instructions`, `/trading/positions`, and
  `/trading/order-intents`: read the audit state.
- `GET|PUT /system/kill-switch`: read or deliberately change the global
  automation control; changes are persisted and published to Redis.

## P6 reconciliation

The arq worker runs `run_reconciliation` every 15 minutes on weekdays during
market hours (09:00–15:59 IST). It compares live DB money-path state to Fyers
read APIs (`orderbook`, `tradebook`, `positions`, `holdings`) and never places
orders.

Heal policy (safe auto-heal only):

- Resolve matched `submission_unknown` / stuck intents via the existing order
  gateway processors (`process_order_message`, `process_trade_message`).
- Backfill missing fills/events for matched intents only.

Flag-only (no auto-import in v1):

- External Fyers-app orders/trades with no local intent.
- CNC quantity mismatches between broker books and open DB positions.

Ops API:

- `GET /system/reconciliation/runs`: recent reconciliation runs.
- `GET /system/reconciliation/runs/{id}/items`: discrepancies for a run.
- `POST /system/reconciliation/run`: enqueue a manual reconciliation job.
