# SwingTraderVCP Server

## P7 survivor-only fundamental annotations

P7 runs as a separate arq job after a technical scan has persisted its
shortlist. It queries only those `screening_results` rows, fetches read-only
Upstox fundamentals by ISIN, stores a normalized snapshot, computes the
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
in numeric order, ending with:

```bash
docker exec -i algo-trading-postgres \
  psql -v ON_ERROR_STOP=1 -U algo -d algo_trading \
  < server/db/migrations/008_p7_ai_trace.sql
```

Configure the worker without committing credentials:

```env
P7_FUNDAMENTAL_PASS_ENABLED=true
UPSTOX_ANALYTICS_TOKEN=<one-year-read-only-analytics-token>
OPENROUTER_API_KEY=<openrouter-key>
OPENROUTER_MODEL=deepseek/deepseek-v4-flash
OPENROUTER_REASONING_EFFORT=xhigh  # low | medium | high | xhigh
FUNDAMENTAL_RUN_TOKEN_BUDGET=150000
FUNDAMENTAL_PROMPT_MAX_CHARS=12000
```

Defaults lock the provider/model behavior:

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

Run the single order gateway as its own supervised process whenever live mode
is armed:

```bash
cd server
uv run python -m app.workers.order_gateway
```

The gateway owns the one Fyers order WebSocket and subscribes only to
`OnOrders,OnTrades`. A Redis singleton lock prevents two gateways from running
at once.

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

- `GET /trading/execution-status` — public paper/live UI state, without
  credentials.
- `POST /trading/trade-instructions` — save a validated human draft.
- `POST /trading/trade-instructions/{id}/confirm` — explicit manual
  checkpoint. The required phrase is `CONFIRM_PAPER_TRADE` in paper mode and
  `CONFIRM_LIVE_ORDER` in live mode.
- `GET /trading/trade-instructions`, `/trading/positions`, and
  `/trading/order-intents` — read the audit state.
- `GET|PUT /system/kill-switch` — read or deliberately change the global
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

- `GET /system/reconciliation/runs` — recent reconciliation runs.
- `GET /system/reconciliation/runs/{id}/items` — discrepancies for a run.
- `POST /system/reconciliation/run` — enqueue a manual reconciliation job.
