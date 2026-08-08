# AGENTS.md — Swing Trading System

This file is the source of truth for the **personal** swing trading system.
If you are an AI coding agent working on this repo: **read this fully before
writing any code** for the personal app / money path. When a request conflicts
with this document, stop and flag the conflict — do not silently deviate,
"improve," or reinterpret the architecture.

If something here is genuinely ambiguous or missing, ask, or propose an
addition to this file explicitly — don't invent structure and move on.

Schema (tables, columns) lives in `server/db/` — not here. This file governs
component boundaries, data flow, locked product decisions, and build order
for the personal trading system.

---

## 0. Dual product map (read first)

This monorepo contains **two products**. Do not conflate them.

| | Personal trading system (this file) | Swyingify SaaS |
| --- | --- | --- |
| Frontend | `client/` (Vite + React) | `swyingify/` (Next.js) |
| Agent source of truth | **This file** (`AGENTS.md`) | [`swyingify/AGENTS.md`](swyingify/AGENTS.md) |
| Audience | Single user (owner) | Multi-user SaaS |
| Core job | Screen → **confirm trade** → automate execution / SL / TP | Screen → study → watchlist; free + paid scanners |
| Money path | Yes — Fyers orders, position monitor, kill switch | **Never** — no orders, no positions, no broker execution |
| Auth | None / single-user | Better Auth + paywall |
| Markets (current) | Indian equities (Nifty 500) | V1: Indian equities only |

**Shared infrastructure:** Postgres, Redis / `arq`, and Python `server/` (EOD
candles, scan engine, workers). The server may serve both apps. Shared scan
logic may be reused and expanded for Swyingify templates — but SaaS API
surfaces must never expose or invoke the money path
(`trade_instructions` confirm → execution, order gateway, position monitor,
kill switch as an order blocker, reconciliation-as-trading-control, journal
fills).

**Agent routing rules:**

1. Task is Swyingify / SaaS / Better Auth / public scanners / paywall → follow
   [`swyingify/AGENTS.md`](swyingify/AGENTS.md). Do not add trade confirm,
   execution, or position features to Swyingify.
2. Task is personal trading / Fyers money path / P0–P9 personal phases →
   follow **this** file. Do not turn the personal app into a multi-tenant SaaS.
3. Task touches shared `server/` code used by both → state the impact on
   **both** products, keep API / table ownership boundaries clear, and do not
   silently widen either product's scope.

---

## 1. What this system is

A **hybrid, human-in-the-loop swing trading system** for Indian equities via
the Fyers API. It is built for a single user, not a multi-tenant product.

The core principle governing every design choice below:

> Screening is fully automated. The final trade decision (which stock, size,
> entry, SL, target, trailing rule) is made by the human. Once instructed,
> execution and trade management (SL / target / trailing) are fully
> automated.

That boundary — automated screening → **manual decision checkpoint** →
automated execution/management — is the single most important invariant in
this system. No component should blur it (e.g. auto-placing an entry order
without an explicit human instruction, or requiring manual action to enforce
an already-set stop loss).

Mental model:

```
        AUTOMATED                         HUMAN                      AUTOMATED
[EOD candles → Screener → LLM] → [Chart review → Confirm trade] → [Entry → Monitor → Exit]
        no money                         checkpoint                    money path
                                                                      + reconcile
                                                                      + journal/AI (read-only)
```

---

## 2. Locked technology decisions

Do not substitute these without an explicit instruction from the user.

| Layer                | Choice                                            | Notes                                                                                                                                               |
| -------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Frontend             | Vite + React, TanStack Query, shadcn/ui, Tailwind | REST via TanStack Query; live data via WebSocket to **our own backend**, never directly to Fyers                                                    |
| Charting             | lightweight-charts (TradingView OSS)              | Presentational only — no SL/target/screening math in the client                                                                                     |
| Backend              | FastAPI (Python)                                  | Async throughout for I/O-bound work (WS, HTTP calls to Fyers)                                                                                       |
| Database             | PostgreSQL                                        | Chosen over SQLite specifically because multiple processes (API, ingestion, monitor, screener) read/write concurrently — do not fall back to SQLite |
| Broker/queue         | Redis                                             | (1) pub/sub for LTP fan-out, (2) hot LTP cache, (3) backing store for async job queue (`arq`)                                                        |
| Market data & orders | Fyers API (REST + WebSocket)                      | See §2.1 for which Fyers surfaces we use                                                                                                            |
| Fundamentals         | Upstox Company Fundamentals API                   | Read-only Analytics Token; ISIN-keyed snapshots for technical survivors only; never prices, sockets, or orders                                     |
| LLM inference        | OpenRouter (`openai/gpt-5.6-luna-pro`)            | Blind structured second opinion over normalized snapshots; Python's deterministic fit remains authoritative. No tools or money-path access. Overridable via `OPENROUTER_MODEL` env (server/.env)       |

### 2.1 Locked Fyers / trading product decisions

| Decision              | Choice                                         | Notes                                                                 |
| --------------------- | ---------------------------------------------- | --------------------------------------------------------------------- |
| Live market data      | **LTP / quote WebSocket primary**              | Not TBT 50-depth by default. Depth is optional/on-demand later only   |
| Exit enforcement      | **Software position monitor** + market/limit exits via execution engine | Not exchange CO/BO as the primary path                      |
| Default product type  | **CNC**                                        | Multi-day swing. Per-trade override may be added later; default CNC   |
| Order placement API   | **Async** (`/api/v3/orders/async`)             | Correlate via **Order WebSocket** (`id_fyers` → exchange order id)    |
| Order rate limit      | Internal ≤ **10 OPS** token bucket             | Align with Fyers; queue bursts inside the engine                      |
| AI / LLM              | Annotation only                                | May never confirm trades or call the execution engine                 |
| P7 fundamental source | **Upstox read-only fundamentals**              | Consolidated statements by default; never use Upstox for trading      |

**Explicit non-goals (v1 unless user reopens):**

- TBT 50-depth / protobuf incremental merge as the monitor feed
- Exchange Cover Order / Bracket Order as the primary SL/TP mechanism
- Frontend connecting to any Fyers endpoint or socket
- Auto-entry from screener score or LLM confidence
- Writing every raw tick to Postgres (sample/debug only if needed)
- Upstox market quotes, WebSockets, portfolio APIs, or order APIs
- Persisting or displaying model `reasoning_details`

---

## 3. Process topology

Run as **separate processes** (same repo, different entrypoints). Do not fold
money-path workers into the API process.

```
┌─────────────┐     REST/WS      ┌──────────────────┐
│  React SPA  │◄────────────────►│  FastAPI (API)   │  thin: auth, CRUD, browser WS
└─────────────┘                  └────────┬─────────┘
                                          │
         ┌────────────────────────────────┼──────────────────────────────┐
         ▼                                ▼                              ▼
┌─────────────────┐              ┌─────────────────┐            ┌─────────────────┐
│ arq worker      │              │ tick ingestion  │            │ order gateway   │
│ EOD, scan, LLM, │              │ 1× Fyers market │            │ 1× Fyers order  │
│ token refresh,  │              │ data WS (LTP)   │            │ WS + REST async │
│ reconcile cron  │              │ → Redis pub/sub │            │ → intents/fills │
└─────────────────┘              │ → LTP cache     │            └────────┬────────┘
                                 └────────┬────────┘                     │
                                          ▼                              │
                                 ┌─────────────────┐                     │
                                 │ position monitor│◄────────────────────┘
                                 │ Redis LTP sub   │  calls execution engine
                                 │ SL/TP/trailing  │  (never opens Fyers sockets)
                                 └─────────────────┘
                                          │
                     Postgres (system of record)  +  Redis (queue, pub/sub, LTP cache)
```

---

## 4. Component map

- **Frontend** — backend REST + backend WebSocket only. Never talks to Fyers.
  Never contains trading logic (no SL/target math, no screening logic). Displays
  state and sends human instructions/decisions. Charting is presentational
  (daily candles from API + live LTP overlay from our WS).
- **FastAPI backend (API layer)** — request/response only. Thin. Creates
  explicit human instructions, reads state, owns **browser** WebSocket
  connections. Delegates heavy work to workers. On confirmed trade
  instruction, calls the execution engine (does not call Fyers order APIs
  itself).
- **Tick ingestion worker** — the _only_ component holding the Fyers
  **market data** WebSocket (LTP/quote). Publishes ticks to Redis pub/sub and
  updates the hot LTP cache. Dynamic subscribe set = open positions ∪ active
  watchlist ∪ open chart sessions ∪ **`NSE:NIFTY50-INDEX` benchmark** (P8
  regime). No business/trading logic.
- **Order gateway** — the _only_ component holding the Fyers **order**
  WebSocket. Receives async place/modify/cancel correlation (`id_fyers`,
  exchange ids, fills) and persists `order_events` / `order_fills`. Does not
  decide _when_ to trade.
- **Execution engine** — the _only_ module allowed to place, modify, or
  cancel orders with Fyers REST. Called by the API layer (entry, after
  explicit human confirm) and by the position monitor (exits). Must:
  - check global kill switch before any new order
  - insert `order_intent` with `idempotency_key` **before** calling Fyers
  - never blind-retry a live place; retry only with the same idempotency key
    when status is still unknown/created
  - enforce internal ≤10 OPS
- **Position monitor worker** — subscribes to LTP via Redis. Evaluates SL /
  target / trailing for every non-closed position, tick by tick. Runs whether
  or not the UI is open. On trigger, requests an exit via the execution
  engine. On restart, reloads all non-closed positions from Postgres and
  re-arms with no manual step. Treat correctness as critical.
- **Screening worker** — technical filter over Nifty 500 from historical
  candles; optional LLM fundamental pass **only on technical survivors**.
  Triggered via Redis job queue (`arq`), never inline in an API request.
  P7 fetches read-only Upstox data by ISIN, persists a reproducible
  fundamentals snapshot, computes numeric facts in Python, and sends one
  blind, strict structured OpenRouter second-opinion request per survivor.
  AI failures never invalidate the Upstox snapshot or Python score. **Never places an
  order.**
- **Scheduler (arq cron)** — EOD candle sync, optional EOD screen, **Fyers
  auth token refresh** (scheduled, not lazy-only), reconciliation cadence.
- **Reconciliation job** — compares DB orders/positions/fills to Fyers.
  Manual trades placed in the Fyers app are detected and imported/flagged —
  never fought blindly.
- **Kill switch service** — reads/writes `system_controls` and publishes a
  Redis control channel so workers react immediately. See §7.
- **Journal / AI surfaces** — read-only over closed trades and notes. P8
  automates journal entries from future app-managed fills only (no backfill).
  First entry fill freezes chart, scanner, trade plan, and market regime;
  closure computes P&L, charges, R-multiples, and exit outcome. Human review
  fields (notes, tags, rating, actual charges) are journal-only writes.
  AI coach runs async over filtered closed trades with strict structured
  OpenRouter output — **must not** expose tools that place or confirm orders.
- **Database (Postgres)** — system of record for instruments, candles,
  screening, watchlists, trade instructions, positions, order intents/events/
  fills, jobs, reconciliation, broker tokens, system controls/events.

### 4.1 Suggested module ownership (server)

Keep packages aligned with write ownership. Do not invent parallel structures
without updating this file.

| Area                         | Owns writes / side effects                                      |
| ---------------------------- | --------------------------------------------------------------- |
| `routers/*`                  | Thin HTTP; no Fyers orders; no screening loop                   |
| Browser WS handler           | Fan-out of Redis LTP / position events to sessions              |
| Tick ingestion worker        | Fyers market WS → Redis; optional sampled ticks                 |
| Order gateway worker         | Fyers order WS → order_events / fills                           |
| Execution engine service     | order_intents + Fyers async REST place/modify/cancel            |
| Position monitor worker      | positions / position_events; calls execution engine for exits   |
| Screener / LLM jobs          | scan_runs, screening_results                                    |
| Reconcile / scheduler        | job_runs, reconciliation_*, broker token refresh, system_events |
| Journal processor (arq)      | journal_entries, journal_fill_outbox, market_regime_snapshots     |
| Journal router (API)         | journal review fields, actual_charges, chart artifact uploads   |
| Journal AI coach (arq)       | journal_ai_runs (read-only analysis, no money path)             |
| Domain pure modules          | trailing rules, position state transitions, tick-size snap      |

Frontend should be split by feature (`screener`, `chart`, `trade`,
`positions`, `journal`, `admin`) rather than a single mega-page as features
grow.

---

## 5. Data flow — screening pipeline

```
Nifty 500 universe (historical candles from Fyers → market_candles)
        │
        ▼
Technical filter (Stage-2 / VCP shortlist gates, volume, % from 52w high, etc.)
        │  (only survivors proceed — do not run LLM pass on all 500)
        ▼
Upstox fundamentals → durable normalized snapshot
        │
        ▼
LLM second opinion (blind pass/fail/uncertain + packet reference IDs)
        │
        ▼
Shortlist stored in DB, surfaced to frontend for manual review + charting
```

This entire pipeline **never places an order and never touches money.** Its
only output is data for the human to review.

Reviewer statuses on results (conceptual): pending → watchlisted | rejected |
trade_planned — driven by the human UI, not by the screener.

---

## 6. Data flow — execution & monitoring

```
Human decision (stock, entry, size, SL, target, trailing rule, product=CNC default)
        │  ← this is the only manual step in this half of the system
        │     trade_instruction: draft → confirmed (manual_confirmed_at required)
        ▼
API creates position (pending_entry) and calls execution engine
        │
        ▼
order_intent (idempotency_key) persisted FIRST
        │
        ▼
Fyers POST /orders/async → id_fyers
        │
        ▼
Order gateway (Order WS) correlates → fyers order id, partials, fills
        │
        ▼
Position: pending_entry → open (on fill); monitor arms SL/target/trail
        │
        ▼
Position monitor (LTP via Redis, continuous)
   watches live price vs SL / target / trailing rules
        │
        ▼
Exit order_intent + async place when rule triggers → exit_pending → closed
```

### 6.1 Position state machine

Explicit states (must stay unambiguous):

`pending_entry → open → trailing_active → exit_pending → closed`

Also: `cancelled` from pre-open paths as needed.

Rules:

- Trailing behavior is defined per state; unknown `trailing_rule.type` → log,
  do not trail.
- Default exit order type for software stops/targets: **market** (reliability).
  Limit exits are opt-in later.
- On process restart, monitor reloads all positions where
  `state NOT IN ('closed', 'cancelled')` and resumes without UI re-arming.

### 6.2 Live LTP path (charts + monitor)

```
Fyers market WS (single connection, tick ingestion worker)
        → normalize Tick { symbol, ltp, ts, ... }
        → Redis pub/sub + ltp:{symbol} cache
        → position monitor (rules)
        → API browser WS (only symbols each session cares about)
        → chart last-price overlay / watchlist
```

Chart history: daily candles from Postgres via REST. Do **not** stream full
history over WebSocket. Do **not** use TBT depth for the monitor feed.

### 6.3 Trade traceability

Scanner-sourced trades:

`positions.screening_result_id → screening_results → scan_runs`

Manual trades may leave `screening_result_id` null but still require a human
instruction and full order/position event trail.

---

## 7. Kill switch policy

A global kill switch must exist early (DB `system_controls` + UI toggle +
Redis control pub for instant worker pickup).

**When engaged:**

- Execution engine **refuses all new** place/modify orders (entries and
  automated exits).
- Position monitor **does not** fire new exit intents.
- Explicit human **panic flatten** (if implemented) is a separate, deliberate
  action — not implied by toggling kill.

**When disengaged:** normal automated entry-on-confirm and monitor exits
resume.

UI copy must state clearly: kill switch = no automated orders, not a
substitute for being flat.

---

## 8. Auth / token lifecycle

- Fyers tokens are stored encrypted in Postgres (never in frontend or logs).
- **Token refresh is a scheduled job** (arq cron). Do not rely only on
  on-demand/lazy refresh.
- All Fyers clients (historical REST, tick WS, order gateway) must obtain
  tokens through one shared “valid access token” path.
- On auth failure: pause money-path components, emit `system_events`, surface
  UI banner. Do not silently retry orders with a bad token.
- The Upstox Analytics Token is a separate read-only environment secret used
  only by P7. It is never sent to the frontend or stored in fundamentals
  snapshots. Upstox auth failure marks annotations failed but does not pause
  or alter the Fyers money path.
- The OpenRouter key is an environment secret. Store model request IDs,
  prompt/input hashes, usage, and concise evidence-backed output; never store
  or display `reasoning_details`.

---

## 9. AI / LLM surfaces (three separate concerns)

Do not build one “AI god service.” Split:

| Surface            | Role                                              | Touches money? |
| ------------------ | ------------------------------------------------- | -------------- |
| Fundamental pass   | Python-authoritative fit plus blind AI pass/fail/uncertain second opinion | No          |
| Journal coach      | Post-trade patterns/mistakes from closed trades   | No             |
| Nifty sentiment    | Optional regime tag / overlay                     | No             |

Hard rule: **LLM/AI never confirms `trade_instructions` and never calls the
execution engine.** Frontend AI SDK streaming is fine if the backend only
assembles read-only context.

---

## 10. Non-negotiable architectural rules

1. **Single Fyers market-data WebSocket**, owned only by the tick ingestion
   worker. Nothing else connects to Fyers market WS.
2. **Single Fyers order WebSocket**, owned only by the order gateway.
   Nothing else connects to Fyers order WS.
3. **Frontend never talks to Fyers directly** — always through our backend.
4. **The manual decision checkpoint is not to be automated away.** No
   component should auto-select a stock and auto-enter a trade without an
   explicit human instruction (`trade_instruction` confirmed), regardless of
   how confident a filter/LLM is.
5. **Order placement is idempotent and only ever issued by the execution
   engine.** No other component calls Fyers order REST endpoints.
6. **SL/target/trailing enforcement must not depend on the frontend being
   open.** Backend position monitor worker, always on while positions are open.
7. **Global kill switch** per §7 — build before or with first live order path.
8. **Reconciliation, not blind trust** — periodic verify against Fyers;
   external manual trades are imported/flagged, not fought.
9. **Screening (and LLM funda) run as background jobs (Redis/`arq`), never
   inline in an API request.**
10. **Software residual risk is accepted and mitigated**: process supervision,
    monitor heartbeats/`system_events`, reconciliation, kill switch, small
    size until proven. Do not pretend software SL equals exchange-held SL.
11. **No schema decisions in this file** — see `server/db/`. Propose schema
    changes in `server/db/` explicitly; keep write ownership aligned with §4.
12. **Do not add dependencies or new long-running services** not listed in §2–§4
    without asking.
13. **Upstox is fundamentals-only and read-only.** P7 may call documented
    fundamentals GET endpoints for persisted technical survivors. All market
    data, sockets, account state, and orders remain exclusively on the locked
    Fyers paths.

---

## 11. Build order and status

Do not reorder phases without asking. Status tags: `[done]`, `[next]`,
`[ ]`.

| Phase | Deliverable | Status |
| ----- | ----------- | ------ |
| 1 | Fyers historical data fetch (auth, candle retrieval) | `[done]` |
| 2 | Screening/scanner technical filter on top of (1) | `[done]` |
| 3 | Shortlist storage + manual review surface (frontend) | `[partial]` — results table + chart workspace (sample data); real candle endpoint added |
| **P0** | Token refresh job + shared valid-token path + auth failure signaling | `[done]` |
| **P1** | Chart workspace on shortlist (daily candles from DB; no live required) | `[done]` — candle API endpoint live; chart component + workspace layout complete |
| **P2** | Tick ingestion worker + Redis LTP + backend→frontend WS overlay | `[done]` — tick_worker.py, ws.py router, useMarketWS hook, live LTP price line on chart |
| **P3** | Trade instruction API + confirm UI + execution engine in paper/log mode | `[done]` — draft/review/confirm API + UI, idempotent paper intents, pending positions, and kill switch control |
| **P4** | Order gateway (async REST + order WS) live CNC entry | `[done]` — durable live intent claim, `/orders/async` placement, single order WS, replay-safe order/fill correlation |
| **P5** | Position monitor SL/target/trail + kill switch wiring + heartbeats | `[done]` |
| **P6** | Reconciliation cron | `[done]` |
| **P7** | LLM fundamental pass on technical survivors | `[done]` — cached Upstox snapshots, deterministic normalization, strict OpenRouter verdicts, and manual-review UI |
| **P8** | Journal + AI coach (read-only) | `[done]` — future-fill outbox, frozen entry snapshots, regime tag at first fill, CNC charge estimates, chart PNG capture, period summaries, tradebook from journal, async AI coach with input-hash reuse |
| **P9** | Nifty sentiment overlay (optional) | `[ ]` |

Notes:

- P1 may ship before P2 so humans can review VCP on daily charts without live
  data. Monitor/execution must still never depend on the browser being
  connected (P5 is a worker).
- **Do not start P4/P5 until P0 and order-intent idempotency are solid.**
- Paper/log mode (P3) means full DB intent trail without live Fyers orders (or
  behind an explicit safe flag). Live money starts at P4 with tiny size.
- Kill switch implementation belongs with P3/P5 at latest — not as a late
  afterthought after live exits exist.

Historical AGENTS list items 4–8 map onto P7, P3–P4, P5, P6, P2 respectively;
the phase table above is authoritative going forward.

---

## 12. Testing expectations (money path)

Before enabling live exits:

- Pure unit tests: trailing math, state transitions, tick-size/qty validation
- Execution engine: mock Fyers — idempotency, kill switch, rate limit, no
  double-place on retry
- Monitor: replay recorded LTP sequences → expected exit intents
- Order gateway: fixture order-WS payloads → intent/fill state machine
- Ops drill: kill monitor mid-position → restart resumes; kill switch blocks
  new orders

Never “test” live exits with size you cannot afford to lose.

---

## 13. When in doubt

If a task seems to require crossing one of the boundaries in §10, changing a
locked decision in §2 / §2.1, or introducing a new service/dependency not
listed in §2–§4, treat that as a **stop-and-ask** situation — not a judgment
call to make silently.

Propose an explicit edit to this file when the architecture should change;
do not silently drift.
