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
| Core job | Screen → AI pattern proposal → **approve/reject** → deterministic entry / SL / TP | Screen → study → watchlist; free + paid scanners |
| Money path | Yes — Fyers orders, position monitor, kill switch | **Never** — no orders, no positions, no broker execution |
| Auth | None / single-user | Better Auth + paywall |
| Markets (current) | Indian equities (Nifty 500) | V1: Indian equities only |

**Shared infrastructure:** Postgres, Redis / `arq`, and Python `server/` (EOD
candles, scan engine, workers). The server may serve both apps. Shared scan
logic may be reused and expanded for Swyingify templates — but SaaS API
surfaces must never expose or invoke the money path
(`trade_proposals` approval → entry supervisor → execution, order gateway,
position monitor, kill switch as an order blocker,
reconciliation-as-trading-control, journal fills).

**Agent routing rules:**

1. Task is Swyingify / SaaS / Better Auth / public scanners / paywall → follow
   [`swyingify/AGENTS.md`](swyingify/AGENTS.md). Do not add trade confirm,
   execution, or position features to Swyingify.
2. Task is personal trading / Fyers money path / P0–P10 personal phases →
   follow **this** file. Do not turn the personal app into a multi-tenant SaaS.
3. Task touches shared `server/` code used by both → state the impact on
   **both** products, keep API / table ownership boundaries clear, and do not
   silently widen either product's scope.

---

## 1. What this system is

A **hybrid, human-in-the-loop swing trading system** for Indian equities via
the Fyers API. It is built for a single user, not a multi-tenant product.

The core principle governing every design choice below:

> Screening and trade-plan generation are automated. Python swing-detects
> first and sends Gemini a chart plus a short candidate summary. Gemini
> returns only qualitative judgments and window pointers — never a price,
> stop, target, or template. Deterministic Python owns every money and risk
> decision. In live execution mode, a human must approve or reject the
> resulting immutable proposal before any entry can become eligible. In the
> owner-enabled `paper` rollout stage only, deterministic live-eligible
> proposals may instead be auto-approved for unattended end-to-end testing.
> Once approved, entry, scale-in, SL, targets, trailing, and reconciliation
> are automated.

That live-money boundary — automated proposal → **manual approval checkpoint**
→ deterministic execution/management — is the single most important
invariant. The paper-only auto-approval exception requires
`EXECUTION_MODE=paper`, durable rollout stage `paper`,
`PAPER_AUTO_ARM_PROPOSALS=true`, and a versioned, unexpired, live-eligible
proposal. The human does not hand-author or edit scanner-sourced live plans;
approval accepts a versioned plan and maximum risk budget, not a stale
quantity. Gemini never sizes, manages risk, confirms a proposal, or touches
the execution path.

Mental model:

```
             AUTOMATED / NO MONEY                     HUMAN               AUTOMATED MONEY PATH
[EOD → scanner → Python swings → chart+summary → Gemini audit → Python plan]
        → [approve/reject] → [entry supervisor]
                                      │
                                      ▼
                        [execute → monitor → exit]
                             + reconcile + journal
```

---

## 2. Locked technology decisions

Do not substitute these without an explicit instruction from the user.

| Layer                | Choice                                            | Notes                                                                                                                                               |
| -------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Frontend             | Vite + React, TanStack Query, shadcn/ui, Tailwind | REST via TanStack Query; live data via WebSocket to **our own backend**, never directly to Fyers                                                    |
| Charting             | lightweight-charts (TradingView OSS)              | Presentational only — no SL/target/screening math in the client                                                                                     |
| Headless proposal charts | pinned `matplotlib` + `mplfinance` (Agg)      | Server-rendered, versioned 1280×720 PNGs; never browser screenshots for P10 proposal generation                                                     |
| Backend              | FastAPI (Python)                                  | Async throughout for I/O-bound work (WS, HTTP calls to Fyers)                                                                                       |
| Database             | PostgreSQL                                        | Chosen over SQLite specifically because multiple processes (API, ingestion, monitor, screener) read/write concurrently — do not fall back to SQLite |
| Broker/queue         | Redis                                             | (1) pub/sub for LTP fan-out, (2) hot LTP cache, (3) backing store for async job queue (`arq`)                                                        |
| Market data & orders | Fyers API (REST + WebSocket)                      | See §2.1 for which Fyers surfaces we use                                                                                                            |
| Fundamentals         | Upstox Company Fundamentals API + official NSE corporate filings | Upstox remains primary; official NSE shareholding and integrated-filing XBRL are read-only risk enrichment for technical survivors only; never prices, sockets, or orders |
| Fundamental LLM inference | OpenRouter (`openai/gpt-5.6-luna-pro`)      | Blind structured second opinion over normalized snapshots; Python's deterministic fit remains authoritative. No tools or money-path access. Overridable via `OPENROUTER_MODEL` env (server/.env)       |
| VCP vision inference | OpenRouter (`google/gemini-3.7-flash`)            | P10 proposal reader: serial audit of Python swing candidates over a standardized 126-session chart plus a short candidate summary (not a raw OHLCV table). Advisory screener VCP still sends frozen OHLCV. Strict JSON; no tools, broker/account context, or money-path access. Overridable via `VCP_VISION_MODEL` env (`server/.env`) |

### 2.1 Locked Fyers / trading product decisions

| Decision              | Choice                                         | Notes                                                                 |
| --------------------- | ---------------------------------------------- | --------------------------------------------------------------------- |
| Live market data      | **LTP / quote WebSocket primary**              | Not TBT 50-depth by default. Depth is optional/on-demand later only   |
| Exit enforcement      | **Software position monitor** + market/limit exits via execution engine | Not exchange CO/BO as the primary path                      |
| Default product type  | **CNC**                                        | Multi-day swing. Per-trade override may be added later; default CNC   |
| Order placement API   | **Async** (`/api/v3/orders/async`)             | Correlate via **Order WebSocket** (`id_fyers` → exchange order id)    |
| Order rate limit      | Internal ≤ **10 OPS** token bucket             | Align with Fyers; queue bursts inside the engine                      |
| AI / LLM              | **Pattern audit only**                        | May return classification, qualitative flags, candidate confirm/merge/reject actions, extra date-window pointers, evidence, and a display-only confidence score; never prices, stops, targets, templates, quantity/risk/exposure/trailing arithmetic, confirmation, or execution. Confidence may feed the Python template scorer only; it must not approve, arm, rank, or execute |
| P7 fundamental source | **Upstox primary + official NSE filings enrichment** | Consolidated Upstox statements by default; known NSE pledge/leverage risks transparently reduce only the deterministic fundamental score; neither source is used for trading |

**Explicit non-goals (v1 unless user reopens):**

- TBT 50-depth / protobuf incremental merge as the monitor feed
- Exchange Cover Order / Bracket Order as the primary SL/TP mechanism
- Frontend connecting to any Fyers endpoint or socket
- Live auto-entry without a versioned, unexpired human-approved proposal
- LLM-authored stop, quantity, monetary risk, exposure, daily-loss, or
  trailing calculations
- Writing every raw tick to Postgres (sample/debug only if needed)
- Upstox market quotes, WebSockets, portfolio APIs, or order APIs
- Persisting or displaying model `reasoning_details`

---

## 3. Process topology

Run as **separate processes** (same repo, different entrypoints). Do not fold
money-path workers into the API process.

```
┌─────────────┐    REST/WS    ┌──────────────────┐
│ React SPA   │◄─────────────►│ FastAPI (thin)   │
└─────────────┘               └──────────────────┘

┌─────────────────┐  top 20  ┌─────────────────┐  approved  ┌─────────────────┐
│ core arq worker │──────────►│ proposal worker │───────────►│ entry supervisor│
│ EOD/scan/P7/cron│           │ charts + Gemini │            │ bars + risk lock│
└─────────────────┘           │ + Python plan   │            └────────┬────────┘
                              └─────────────────┘                     │
                                                                      ▼
┌─────────────────┐  Redis LTP/5m bars  ┌─────────────────┐   ┌─────────────────┐
│ tick ingestion  │────────────────────►│ position monitor│──►│ execution engine│
│ 1× market WS    │────────────────────►│ SL/TP/trailing  │   │ Fyers async REST│
└─────────────────┘  entry supervisor   └─────────────────┘   └────────┬────────┘
                                                                       ▼
                                                              ┌─────────────────┐
                                                              │ order gateway   │
                                                              │ 1× order WS     │
                                                              └─────────────────┘

        Postgres = system of record; Redis = queue/pub-sub/hot cache only
```

---

## 4. Component map

- **Frontend** — backend REST + backend WebSocket only. Never talks to Fyers.
  Never contains trading logic. Displays proposal and position state and sends
  immutable approve/reject decisions. Scanner-sourced live proposals cannot be
  edited in the browser. Charting remains presentational.
- **FastAPI backend (API layer)** — request/response only. Thin. Creates
  and reads proposal decisions, risk-policy versions, and controls; owns
  **browser** WebSocket connections. It neither runs proposal/entry loops nor
  calls Fyers order APIs. Approval arms the durable entry supervisor; an HTTP
  request must not place an entry inline.
- **Tick ingestion worker** — the _only_ component holding the Fyers
  **market data** WebSocket (LTP/quote). Publishes ticks to Redis pub/sub and
  updates the hot LTP cache. It also aggregates/persists completed 5-minute
  bars and publishes bar events; this is data ingestion, not trade logic.
  Dynamic subscribe set = open positions ∪ armed proposal symbols ∪ active
  watchlist ∪ open chart sessions ∪ **`NSE:NIFTY50-INDEX` benchmark**. It
  periodically reconciles intraday bars with Fyers data. No entry/risk logic.
- **Order gateway** — the _only_ component holding the Fyers **order**
  WebSocket in live mode. In paper mode the same process never opens a Fyers
  order socket; it consumes paper order/trade events and runs the same
  `process_order_message` / `process_trade_message` fill processors. Receives
  async place/modify/cancel correlation (`id_fyers`, exchange ids, fills) and
  persists `order_events` / `order_fills`. Does not decide _when_ to trade.
- **Execution engine** — the _only_ module allowed to place, modify, or
  cancel orders with Fyers REST. Called by the entry supervisor for approved
  entries/adds/corrections and by the position monitor for exits. Must:
  - check global kill switch before any new order
  - insert `order_intent` with `idempotency_key` **before** calling Fyers
  - never blind-retry a live place; retry only with the same idempotency key
    when status is still unknown/created
  - enforce internal ≤10 OPS
- **Position monitor worker** — subscribes to LTP via Redis. Evaluates SL /
  T1/T2/T3/runner/trailing for every non-closed position, tick by tick. Runs
  whether or not the UI is open. On trigger, requests an idempotent exit via
  the execution engine. On restart, reconstructs every non-closed position
  from persisted intents/fills and re-arms with no manual step.
- **Screening worker** — technical filter over Nifty 500 from historical
  candles; optional LLM fundamental pass **only on technical survivors**.
  Triggered via Redis job queue (`arq`), never inline in an API request.
  Historical version-comparison shadow runs (now retired with the v4 score
  engine) remain persisted as personal technical-only runs and never replace
  the active production ranking.
  P7 fetches read-only Upstox data by ISIN plus best-effort official NSE
  shareholding/integrated-filing XBRL, persists reproducible snapshots,
  computes numeric facts and deterministic pledge/leverage risk adjustments in Python, and sends one
  blind, strict structured OpenRouter second-opinion request per survivor.
  AI failures never invalidate the Upstox snapshot or Python score. **Never places an
  order.**
- **Proposal / VCP vision worker** — a dedicated `arq` process with
  concurrency `1`, separate from core cron/reconciliation work. For the
  scanner's existing selected top 20 it freezes EOD OHLCV, runs Python
  swing detection on the same 126-session window the chart will show,
  renders a clean 126-session chart (plus a 252-session context chart for
  humans only), sends Gemini the 126-session image and a short candidate
  summary, then lets deterministic Python resolve survivors and build or
  reject an immutable proposal. Forming (`classification=forming`) names
  are persisted to `p10_forming_patterns` and rechecked after the new
  shortlist, at most 10 per batch. Every renderer, geometry, prompt,
  input, provider attempt, and policy version is audited. The worker
  has a **45-minute hard batch budget** from shortlist freeze, a 90-second
  per-attempt timeout, at most one retry when budget remains, and must not
  start an attempt after the deadline. Remaining candidates become
  `timed_out`, not silently deferred. Live-eligible output must also exist by
  **08:30 Asia/Kolkata** on the next NSE session; later output is review-only
  and cannot be approved for live entry.
- **Entry supervisor worker** — consumes completed 5-minute bars for approved
  proposals. It owns trigger evaluation, proposal expiry, add-leg gates,
  priority/capacity ordering, fresh broker-state preflight, and serialized
  deterministic sizing. It has no LLM and no Fyers socket. It persists trigger
  and allocation state before calling the execution engine and reconstructs
  nonterminal legs on restart.
- **Scheduler (core arq cron)** — EOD candle sync, optional EOD screen,
  deterministic P9 market-context computation, broker-auth readiness
  validation/alerts, and reconciliation cadence. Personal processing is
  ordered EOD sync → P9 context → personal scan; the independent SaaS scan
  does not consume P9 selection or money-path policy.
  Proposal jobs never share this worker's single execution slot.
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
  screening, proposal artifacts/decisions/legs, risk-policy and allocation
  events, watchlists, trade instructions, positions, order intents/events/
  fills, jobs, reconciliation, broker tokens, system controls/events.

### 4.1 Suggested module ownership (server)

Keep packages aligned with write ownership. Do not invent parallel structures
without updating this file.

| Area                         | Owns writes / side effects                                      |
| ---------------------------- | --------------------------------------------------------------- |
| `routers/*`                  | Thin HTTP; no Fyers orders; no screening loop                   |
| Browser WS handler           | Fan-out of Redis LTP / position events to sessions              |
| Tick ingestion worker        | Fyers market WS → Redis/LTP cache; persisted completed 5m bars  |
| Order gateway worker         | Live: Fyers order WS → events/fills. Paper: paper-event drain into the same processors |
| Paper broker                 | `paper_broker_*` cash/books; never Fyers `/funds` or order APIs                         |
| P10 rollout                  | `p10_rollout_state` / `p10_rollout_events`; owner-gated stage lock                      |
| Execution engine service     | order_intents + paper-broker or Fyers async REST place/modify/cancel |
| Position monitor worker      | positions / position_events; calls execution engine for exits   |
| Screener / LLM jobs          | scan_runs, screening_results                                    |
| Proposal worker              | frozen charts, vision attempts, immutable trade proposals, `p10_forming_patterns` |
| Entry supervisor             | triggers, legs, risk snapshots, allocation ledger; calls execution engine |
| Reconcile / scheduler        | job_runs, reconciliation_*, broker-auth readiness, system_events |
| Journal processor (arq)      | journal_entries, journal_fill_outbox, market_regime_snapshots     |
| Journal router (API)         | journal review fields, actual_charges, chart artifact uploads   |
| Journal AI coach (arq)       | journal_ai_runs (read-only analysis, no money path)             |
| Domain pure modules          | geometry, triggers, risk/caps, rounding, trailing, state transitions |

Frontend should be split by feature (`screener`, `chart`, `proposals`,
`trade`, `positions`, `journal`, `admin`) rather than a single mega-page as
features grow.

---

## 5. Data flow — scan to immutable proposal

```
Nifty 500 → technical scan → existing selected top 20
        │
        ├── Upstox fundamentals + official NSE risk enrichment (P7)
        │
        ▼
freeze 252 EOD OHLCV + Python swing detection on the 126-session window
        │
        ▼
clean 126-session chart + short candidate summary (252 context is human-only)
        │
        ▼
Gemini qualitative audit (no broker/account/money context, no prices)
        │
        ▼
Python survivor resolution → prices/targets/template → numeric gates
        │
        ├── forming → p10_forming_patterns (no pivot/entry/target)
        ├── numeric-gate fail or not_vcp → audited non-proposal
        └── valid + gates pass → immutable proposal → human approve/reject
```

The proposal worker never places an order. Gemini output is one audited
qualitative input; Python is authoritative for survivor windows, proposal
validity, and every monetary rule. `classification=valid` is necessary but
not sufficient — independent numeric gates may still produce a non-proposal.

### 5.1 Standardized proposal charts and geometry

- Freeze the selected candidate's EOD OHLCV (252 sessions) and `source_hash`
  before rendering or inference. Reuse is allowed only when source, geometry,
  renderer, prompt, schema, model, and risk-policy version hashes all match.
  Frozen OHLCV is for charts, `source_hash`, ATR, SMA200, 52-week high, stop,
  and pivot snap. Gemini does **not** receive a raw OHLCV table.
- Render two fixed 1280×720, log-price PNGs using pinned
  `matplotlib`/`mplfinance` Agg versions. The **LLM payload is the 126-session
  chart only** (EMA21, SMA50/150/200 computed on the 252 freeze and plotted on
  the last 126; volume pane; **no contraction overlays**). The 252-session
  context chart is for the human review UI and is not sent to Gemini. Fix
  colors, fonts, DPI, candle/volume layout, MAs, axes, margins, and encoding.
- Python swing-detects first on the **same 126-session window** the chart
  shows (fractal k=2). Each candidate includes exact high/low dates, `%`
  depth, and volume vs ADV20/ADV50. Gemini receives that short text summary
  with the 126-session image and may confirm, merge, reject, or add extra
  date-window pointers. It may not rewrite the frozen OHLCV or emit prices.
- Missing/stale candles, failed survivor resolution, hash/render mismatch,
  invalid schema, provider failure, numeric-gate failure, or timeout produces
  an audited non-proposal state.

### 5.2 Gemini contract

Gemini must return strict JSON (`additionalProperties: false`) containing only:

- `classification`: `valid | forming | not_vcp`
- `forming_state`: `developing | breaking_down | null` (required when `forming`)
- `progressive_tightening`: `yes | no`
- `volume_dry_up`: `clearly | somewhat | not_really`
- `base_quality`: `price_action` (`orderly | choppy`),
  `climax_or_gap_violation` (`yes | no`), `stage2_context` (`yes | no`)
- `candidate_assessments`: exactly one row per Python candidate (`index`,
  `action` `confirm | merge | reject`, optional note). `merge_with_index` is
  **required** when `action=merge` and forbidden otherwise. Merging into a
  `reject` row is a validation failure, not a silent skip.
- `extra_windows`: optional date-range pointers for contractions Python
  missed (`high_start`/`high_end`, `low_start`/`low_end`) — no prices
- `confidence`: integer 0–100 (display + template-scorer input only)
- `red_flags` and a concise `evidence_summary`

The schema must not contain a pivot, stop, target, entry, quantity, capital,
monetary risk, position or sector exposure, daily-loss, add-size,
target-size, trailing, template, or a free `contraction_count`. Do not
persist or display provider `reasoning_details`. Python derives `llm_count`
from survivors after resolution; that derived count is the only LLM-side
count used by disagreement and template scoring.

Survivor resolution (authoritative, before any price):

1. Start from Python candidates indexed as sent to Gemini.
2. Drop every `reject`.
3. Fold `merge` rows transitively with `merge_with_index` into one window per
   group: snapped high = max high in the group, snapped low = min low in the
   group. The group counts as one survivor.
4. Each `confirm` is one survivor, snapped to exact high/low in its window.
5. Each `extra_windows` entry is one survivor, snapped inside the pointed
   date ranges.
6. `llm_count` = number of survivors. `python_count` remains the raw
   detector count.
7. Sort survivors by `high_date` then `low_date`.
8. **Final contraction** = the last survivor in that sort. Pivot and stop
   come from that window, including when Gemini rejected Python's last
   candidate.

`classification=valid` may proceed to price construction only when the
numeric gates in §5.3 also pass. `forming` writes `p10_forming_patterns`
and computes no pivot/entry/target. `not_vcp` is an audited non-proposal.

### 5.3 Deterministic proposal construction

- **Pivot** = snapped high of the final surviving contraction.
- **Planned entry** = pivot plus `0.10×ATR14`, tick-snapped.
- **Initial structural stop** = snapped low of that same final survivor
  minus `0.25×ATR14`, tick-snapped. Reject stop distance above 8%. Flag
  (do not reject) above 5%.
- **`base_high`** = the highest high in the 126-session chart window on or
  before the high date of the first surviving contraction, inclusive of
  that high. Measured-move height = `base_high − deepest surviving low`.
- Chase ceiling caps MPP overshoot from planned entry:
  `entry + min(2% of entry, 0.5 × (entry − stop))`, floored to tick.
  Order check: `entry <= chase_ceiling < T1 < T2 < T3`.
- **Targets are computed by Python from planned entry and frozen on the
  proposal.** The position monitor uses those frozen prices; do not
  recompute targets from fill VWAP. Accept realized-RR drift inside the
  chase band (a fill at `entry + 0.5R` yields 1.5R to locked T1). Invalid
  fill still fires if VWAP exceeds chase or T1 provides less than 1R from
  that VWAP.
  - `R = entry − stop` (planned entry)
  - `floor = entry + 2R`
  - `measured = pivot + (base_high − deepest surviving low)`
  - `stretch = entry + 3R`
  - Drop any level below 2R from the exit ladder; T1 must stay ≥ 2R.
  - Sort remaining unique tick-distinct prices ascending into T1/T2/T3.
  - If only two levels remain, synthesize T3 = `entry + 4R` (`synthetic_4r`).
  - Persist which formula landed in which slot.
- 52-week high is informational (`near_52w_high`, `fresh_high_breakout`,
  `level_to_watch`). It is never a rejection.
- Independent numeric gates (any failure → audited non-proposal):
  - `classification = valid`
  - `llm_count` ≥ 2 (survivors, including extras, after merges)
  - Survivor depths non-increasing with ≤ 0.5 percentage-point regression
    allowed between consecutive survivors (from snapped data, not Gemini)
  - `stage2_context = yes`
  - `volume_dry_up` in `{clearly, somewhat}`
  - last-survivor vol/ADV20 ≤ first-survivor vol/ADV20
- Python selects the template from a versioned rules table
  (`p10_template_score_v1`). Gemini does not pick a template. Static
  template configs still map to maximum approved-risk shares:

| Template | Risk by leg | Required relative volume |
| --- | --- | --- |
| `single` | 100% | 2.0× |
| `three_leg_front` | 50% / 30% / 20% | 2.0× |
| `two_leg` | 60% / 40% | 1.75× |
| `three_leg_balanced` | 40% / 30% / 30% | 1.5× |

V1 of the scorer emits only `single`, `two_leg`, or `three_leg_front`.
`|llm_count − python_count| > 1` forces `single` and a review-UI mismatch
banner; it does not auto-reject if survivors still pass the numeric gates.
Confidence may be an input to this scorer only.

These percentages divide the approved monetary risk budget, not raw shares or
notional. The relative-volume ladder deliberately follows capital committed:
template selection already prices pattern uncertainty, so raising the volume
bar again for looser templates would double-count the same signal. The largest
and earliest commitment therefore requires the strongest independent volume
proof. No template may create more than three entry legs.

### 5.4 Approval TTL and immutable decision

- Proposal generation session = the EOD source session (`D0`).
- A live-eligible proposal must have completed by 08:30 Asia/Kolkata on the
  immediately following NSE trading session (`D1`).
- Pending-approval deadline = **09:00 Asia/Kolkata on D1**. If no decision is
  recorded by then, the proposal becomes `expired_unapproved` and can never be
  reactivated. A new scan/analysis/proposal is required.
- Paper-only unattended testing may record a synthetic approved decision and
  arm the initial leg at proposal persistence when all four gates hold:
  `EXECUTION_MODE=paper`, rollout stage `paper`,
  `PAPER_AUTO_ARM_PROPOSALS=true`, and `live_eligible=true`. It must never run
  in `shadow`, `reduced_live`, `full_live`, or live execution mode. Disabling
  the flag restores manual approval in paper mode.
- Approval before that deadline arms the initial leg for **D1 only**; this is
  the separate entry-trigger window. If it does not trigger on D1, it becomes
  `entry_expired`. The D1 window is considered closed at **16:00 IST on D1**
  (after the final 15:45 intraday bar-reconciliation tick): the entry
  supervisor persists `expired` on untriggered `armed`/`trigger_observed`
  legs and cancels their higher-index `planned` siblings, and the API derives
  the same expired state for display even when the supervisor was not running
  at the close. The proposal decision itself remains `approved`; only the
  legs expire, and only a new scan/analysis/proposal can produce a fresh
  entry opportunity.
- Approval locks the proposal hash, source and policy versions, pivot, targets,
  stop/structure rules, template, risk budget, chase ceiling, add rules, exit
  rules, and expiries. It locks a maximum monetary risk budget, not quantity.
- Human action is approve or reject only; the paper-only synthetic approval
  above is the sole exception. A changed proposal requires a new immutable
  version and fresh approval. The active risk policy may tighten or block an
  approved proposal at execution, but may never enlarge it without reapproval.

### 5.5 Forming-pattern watch

`classification=forming` never produces a `trade_proposals` row and never
computes pivot, entry, stop, or targets. The proposal worker upserts
`p10_forming_patterns` (`watching | promoted | broken_down | expired`) and
does not write LTP `watchlists`.

- `promoted` — a later run produces `classification=valid` that passes
  numeric gates and inserts a `trade_proposals` row.
- `broken_down` — Gemini returns `forming_state=breaking_down` or
  `classification=not_vcp`.
- `expired` — 10 completed NSE sessions have elapsed since
  `first_seen_as_of` without promotion, or the instrument is no longer in
  the Nifty 500 screened universe at that EOD. Expired rows are terminal;
  a later shortlist hit may start a fresh watch.

Each proposal batch processes the new top-N shortlist first, then rechecks
at most **10** `watching` rows (oldest `next_check_date` first) if wall-clock
budget remains. Never start a forming attempt after the 45-minute deadline.

---

## 6. Data flow — approved proposal to execution and monitoring

```
approved immutable proposal
        ▼
entry supervisor: completed 5m price + volume confirmation
        ▼
fresh Fyers state → Postgres allocation lock → deterministic quantity
        ▼
order_intent persisted first → execution engine → Fyers async order
        ▼
order gateway correlates ack / partial / fill / terminal state
        ▼
post-fill risk recheck → tighten stop and/or trim when required
        ▼
position monitor: SL + T1/T2/T3 + ATR runner → reconcile → journal
```

### 6.1 Intraday entry confirmation

- The tick worker builds completed 5-minute bars from the single Fyers market
  WebSocket and reconciles them against Fyers every 15 minutes. The entry
  supervisor never creates another market-data connection.
- Ignore the first 15 minutes of the session. There is no price-only fallback.
- Relative volume = current cumulative volume divided by
  `(robust ADV20 × expected cumulative-volume fraction)`.
- Robust ADV20 excludes daily-volume outliers with a MAD-based filter.
  Expected cumulative fraction is the median for that 5-minute bucket across
  the prior 30 completed sessions. Fewer than 15 valid profile sessions,
  stale/missing cumulative volume, or unresolved reconciliation drift blocks
  the trigger.
- A signal bar must close above its pivot/base-high trigger with the template's
  required relative volume. The immediately following completed 5-minute bar
  must remain above the trigger with relative volume still at or above the
  threshold. Recheck trigger freshness and chase ceiling before submission.
- A trigger that loses capacity is not held for a late entry. It needs a new
  qualifying two-bar trigger while its leg remains eligible.

### 6.2 Add-leg eligibility

Add eligibility begins only after the first fill and expires after 10 NSE
trading sessions. `single` has no adds.

- Leg reference = the preceding filled leg's trigger level.
- `Hold(N)` = N consecutive daily closes at or above the reference. A close
  below resets the count; an intraday breach that recovers by close does not.
- Hold and Base are sequential gates.
- `Base(M)` = M sessions where daily range is at most `1.5×ATR14`, the high is
  no more than `0.25×ATR14` above the preceding-leg high, and volume is below
  the 20-session average.
- L2 for `three_leg_front`: Hold(1), then Base(2).
- L2 for `two_leg` and `three_leg_balanced`: Hold(2), then Base(3).
- Every L3: Hold(2), then Base(3).
- Every add also requires `close > EMA21` and
  `EMA21 today > EMA21 five sessions ago`.
- After Base completes, its high becomes the add trigger and uses the same
  two-bar price/volume confirmation. Python may ratchet the common position
  stop to base low minus `0.25×ATR14`; it may never lower a stop.
- A stop trigger or the first T1 trigger permanently expires every unfilled add
  leg. Do not add after invalidation or after profit-taking has begun.

### 6.3 Deterministic risk policy

The initial **Balanced** policy is locked as:

- 1% maximum risk per trade
- 4% maximum total open risk
- 15% maximum single-name notional
- 30% maximum sector notional
- 30% maximum correlation-cluster notional
- 2% daily realized-loss stop
- three consecutive proposal-backed pure stop-loss closures pause every new
  initial/add leg until an explicit owner reset
- maximum 8 open positions
- long-only CNC, maximum three entry legs

Deployable capital is operator-configured but is bounded by a broker funds
snapshot no older than 15 seconds at execution. Broker-reported available
funds win when lower; do not optimistically recycle capital before the broker
reports it available.

In `EXECUTION_MODE=paper`, that snapshot is the durable paper-broker cash
ledger (seeded from `deployable_capital_override`, default ₹5,00,000), not
Fyers `/funds`. Fyers remains market-data and auth only. In live mode the
snapshot is Fyers Available Balance as before.

- Open risk uses remaining quantity and the current effective stop; risk is
  zero, not negative, when a stop has ratcheted above entry.
- Sector uses canonical Nifty metadata. Missing sectors share one conservative
  `unknown` bucket.
- Correlation uses 60-session daily returns. Instruments connected by Pearson
  `rho >= 0.80` form one exposure cluster. Insufficient usable history blocks
  automatic allocation instead of assuming zero correlation.
- Daily loss is the sum of realized loss-making exits including charges against
  that session's capital baseline. Profits may free broker-reported capital but
  do not offset this counter. At 2%, block new initial legs and remaining adds
  for the rest of the session; existing exits and reconciliation continue.

### 6.4 Serialized capacity and priority

Approval reserves neither cash nor shares. Immediately before **every** initial
entry, add, or correction:

1. Fetch a fresh broker preflight (funds, positions, orders, fills). Paper
   reads the paper-broker ledger; live reads Fyers. The two books never share
   one snapshot.
2. Acquire the Postgres allocation advisory lock.
3. Reject broker snapshots older than 15 seconds or superseded local allocation
   generations.
4. Recompute cash, per-trade and total open risk, position count, single-name,
   sector, the complete `rho >= 0.80` correlation-cluster exposure, daily loss,
   the consecutive-stop circuit breaker, current P9 market/sector gates, chase
   ceiling, and trigger validity.
5. Persist the sizing decision and allocation generation/event, then invoke the
   execution-engine service within the locked workflow so it persists the
   idempotent order intent before any Fyers call.
6. Recompute all constraints under the same lock from each actual fill.

The execution-time correlation check is mandatory: two independently approved
trades must not jointly breach a cluster cap when consuming recycled capital.

When multiple valid signals compete:

1. Remove invalid, contradictory, expired, stale, chase-blocked, or cap-blocked
   candidates.
2. Form descending scanner-score bands spanning two points from the current
   highest remaining score.
3. Within the band sort by conservative R:R, then trigger timestamp.
4. An exact remaining tie becomes `capacity_conflict`; never use symbol order
   or another hidden fallback. Human may select one winner or skip all, without
   editing either proposal. If unresolved before signal expiry, skip all.
5. Fill the highest-ranked candidate first, then independently size down lower
   candidates. Never pro-rate.

A leg is viable only at 50% or more of its approved leg-risk allocation and
when the resulting aggregate position contains at least four tradable shares/
lots for staged exits.

### 6.5 Whole-share/lot rounding

- For entries and adds, floor the maximum allowed quantity to the instrument's
  tradable whole-share/lot increment. Rounding may leave unused capacity; it
  may never intentionally exceed any cap.
- For a risk-reduction exit, compute the maximum allowed **remaining** quantity
  and floor it to the tradable increment; equivalently, round the exit quantity
  upward. This is always toward less remaining risk.
- Price movement, partial execution, or broker lot granularity after submission
  may still leave a residual overage no larger than one tradable lot's current
  risk/notional quantum. Persist it as `rounding_residual` and treat the
  correction as successful to prevent an order loop. Any larger residual
  requires one new idempotent correction cycle or, if unresolved, pauses the
  leg and emits a critical event. This tolerance is post-fill only and must
  never be used to size a new order above a cap.

### 6.6 MPP slippage and fill correction

- Use market/MPP for entries. Refuse submission when the fresh reference price
  is above the approved chase ceiling.
- If actual entry VWAP exceeds the chase ceiling or makes T1 provide less
  than 1R from that VWAP, send one idempotent full `invalid_fill_exit`. This
  is a thesis-changing fill failure, not routine overshoot handling. T2/T3
  R multiples do not invalidate a fill.
- Otherwise calculate actual combined position risk and every notional cap.
- First solve the common stop required to return risk to budget. For a long,
  the approved structural tightening corridor runs from the current stop up to
  one tick below the applicable contraction/base low. If the required stop is
  higher than the current stop, within that corridor, and valid at the broker,
  tighten the software stop without an order.
- If tightening is structurally invalid or insufficient, send the minimum
  whole-lot `risk_reduction_exit` required by §6.5. The correction solver must
  satisfy the strictest of monetary-risk, cash, name, sector, and correlation
  limits. Routine overshoot never causes a full exit when tighten/trim can
  restore compliance.

### 6.7 Exit policy

- Software stop, target, invalid-fill, and risk-reduction exits default to
  market/MPP for reliability. Limit exits remain opt-in and are not P10's
  primary path.
- T1 exits 25%; after its fill, move the stop to weighted-average entry.
- T2 exits 25%; after its fill, activate a `2×ATR14` high-water trail using the
  latest completed daily ATR.
- T3 exits 25%.
- The remaining 25% is the runner under the ATR trail.
- Stops and trails only ratchet upward. A stop exits all remaining quantity.
- Whole-share targets use deterministic largest-remainder apportionment.
- If a price gap crosses multiple targets, create one idempotent cumulative
  exit for the required shares, not duplicate target orders.
- Unknown trailing-rule types log a critical event and do not silently invent
  behavior. Exit enforcement never depends on the UI being open.

### 6.8 Durable state and crash recovery

Proposal decisions, triggers, legs, allocation events, order intents, broker
events, and fills live in Postgres. Redis events are never authoritative.

- Proposal decision states: `pending_approval`, `approved`, `rejected`, and
  `expired_unapproved`.
- Normal entry-leg progression: `planned → armed → trigger_observed →
  intent_created → submitted → partially_filled → filled`, plus explicit
  expired/cancelled/unknown terminal or recovery states.
- Position: `pending_entry → open → trailing_active → exit_pending → closed`,
  with `cancelled` only from pre-open paths.
- Use stable idempotency keys per proposal/leg/exit purpose. Persist an intent
  before every Fyers call. Never blind-retry a live place.
- On startup, reload every nonterminal proposal, leg, intent, event, fill, and
  position. Rebuild actual quantity, weighted price, stop, target allocations,
  remaining risk, and add eligibility.
- A persisted trigger without an intent may create its original intent under
  the allocation lock. An existing created/submitted/acknowledged/partial/
  unknown intent is never recreated.
- A lost broker response becomes `submission_unknown`; freeze the leg until
  the order gateway or reconciliation resolves it. Partial fills are managed
  from actual quantity and are never blindly topped up.
- Restart after a target, stop, or risk-correction trigger reconstructs the
  outstanding action from its persisted intent/fills instead of firing again.

### 6.9 Live data and traceability

```
Fyers market WS (tick worker only)
  → normalized tick + Redis LTP cache/pub-sub
  → completed/persisted 5m bars + Redis bar event
  → entry supervisor (approved proposals only)
  → position monitor (open positions only)
  → API browser WS (display only)
```

Daily/5-minute history comes from Postgres via REST; do not stream history over
WebSocket or use TBT depth for the monitor.

Scanner-sourced traceability is:

`positions → approved proposal → vision analysis → screening_results → scan_runs`

The existing free-form manual instruction path remains paper/log-only during
P10. Existing historical instructions and positions are not retroactively
converted to proposals.

### 6.10 Personal API/UI contract

The thin API exposes proposal-run status, proposal list/detail, one immutable
approve/reject decision endpoint with expected version/hash, capacity-conflict
list/decision, versioned risk-policy read/update, forming-watch list, and
entry-supervisor health.
The proposal UI shows both source charts (252 context for humans, 126-session
chart sent to Gemini), scanner rank, Gemini qualitative evidence, Python
candidate vs resolved-survivor lists, a mismatch banner when
`|llm_count − python_count| > 1`, 52-week tags, formula-labeled T1/T2/T3,
pivot/entry/stop/risk, template, TTL, and live leg/correction/recovery state.
It must not offer quantity, stop, target, or template edits for a
scanner-sourced live proposal. Confidence is display-only and must not
auto-approve.

### 6.11 P9 deterministic market context

P9 is a versioned deterministic EOD layer, not an LLM surface. It must never
change `technical_score` or `result_rank`. It computes:

- a green/yellow/red market light from two-of-three trend, Nifty 500 breadth,
  and constituent-turnover distribution evidence across Nifty 50, Nifty 500,
  and Nifty Midcap 150 context;
- cross-sectional sector strength for the checked-in 16-sector taxonomy versus
  Nifty 500; and
- a separate contextual P7 selection order only within inclusive two-point
  technical-score bands.

In enforced mode green/yellow/red multiply a new leg's deterministic risk
ceiling by `1.0/0.5/0.0`. A sector confirmed lagging on two consecutive EOD
snapshots, or unavailable context, blocks the new initial/add. The first
non-lagging EOD releases the sector block, but any confirmation observed while
blocked is consumed and a fresh two-bar trigger is required. P9 never exits,
trims, lowers a stop, or otherwise changes management of an existing position.

P9 starts in shadow mode and may become enforced only through an immutable
policy version with an owner-approved replay-report hash. It cannot
self-promote. Missing, stale, incomplete, or hash-invalid context fails closed
for new initials/adds only after enforcement.

The three-stop circuit breaker is also deterministic and has no LLM. It counts
future, same-execution-mode P10 proposal closures whose only exit fills are
stop-loss fills and whose versioned estimated net P&L is negative. Normal
target/trailing/mixed closures reset an untripped streak; manual, external,
invalid-fill, and risk-correction closures do not affect it. The third stop-out
atomically trips `new_entries_paused`; only an owner acknowledgement/reset may
re-arm entries, and that reset must not clear an independent manual pause.

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

**When disengaged:** normal approved-proposal entry supervision and monitor
exits resume.

UI copy must state clearly: kill switch = no automated orders, not a
substitute for being flat.

P10 also requires separate **proposal-processing pause** and **new-entry
pause** controls. These may stop inference or new initial/add legs while the
position monitor continues protecting existing positions. They do not replace
or weaken the global kill switch.

---

## 8. Auth / token lifecycle

- Fyers tokens are stored encrypted in Postgres (never in frontend or logs).
- All Fyers clients (historical REST, funds/broker reads, tick WS, execution,
  order gateway) must obtain tokens through one shared “valid access token”
  path.
- The scheduler validates broker-auth readiness and alerts before the live
  window. It must not pretend unattended refresh can create a new session when
  Fyers requires daily operator 2FA. Order-API deployment must use the
  registered static public IP required by the current Fyers retail-algo rules.
- Missing current-session auth or a static-IP/readiness failure blocks new
  entry/add orders and emits a critical event/banner. Existing positions remain
  visible; any inability to enforce exits must be shown as a money-path
  emergency, never hidden behind retries.
- On auth failure: pause money-path components, emit `system_events`, surface
  UI banner. Do not silently retry orders with a bad token.
- The Upstox Analytics Token is a separate read-only environment secret used
  only by P7. It is never sent to the frontend or stored in fundamentals
  snapshots. Upstox auth failure marks annotations failed but does not pause
  or alter the Fyers money path.
- Official NSE corporate filing endpoints and archive XBRLs are public,
  read-only P7 enrichment. Fetch or parse failures remain visible `unknown`
  warnings and never pause or alter the Fyers money path.
- The OpenRouter key is an environment secret. Store model request IDs,
  prompt/input hashes, usage, and concise evidence-backed output; never store
  or display `reasoning_details`.

---

## 9. AI / LLM surfaces (three separate concerns)

Do not build one “AI god service.” Split:

| Surface            | Role                                              | Touches money? |
| ------------------ | ------------------------------------------------- | -------------- |
| Fundamental pass   | Python-authoritative fit plus blind AI pass/fail/uncertain second opinion | No          |
| VCP proposal reader | Serial audit of Python swing candidates: classification, qualitative flags, window pointers, and display-only confidence; Python resolves survivors and owns every price, template, and money rule | No |
| Journal coach      | Post-trade patterns/mistakes from closed trades   | No             |

P9 market context is deliberately absent from this table: it is pure,
versioned Python that may gate only new P10 initials/adds under §6.11. It has no
model, prompt, provider call, broker/account input, or order-side effect.

Hard rule: **LLM/AI never confirms a proposal, reads broker funds/account
state, computes money/risk/quantity/trailing rules, or calls the execution
engine.** Gemini does not select an `entry_template`; a versioned Python
rules table maps qualitative flags plus computed numbers onto the static
template configs. Deterministic Python owns the template's fixed risk
allocation and all execution decisions.
Frontend AI SDK streaming is fine only for read-only context.

---

## 10. Non-negotiable architectural rules

1. **Single Fyers market-data WebSocket**, owned only by the tick ingestion
   worker. Nothing else connects to Fyers market WS.
2. **Single Fyers order WebSocket**, owned only by the order gateway.
   Nothing else connects to Fyers order WS.
3. **Frontend never talks to Fyers directly** — always through our backend.
4. **The live manual approval checkpoint is not to be automated away.** No
   scanner-sourced live entry may be armed without one explicit, unexpired
   human approval of the exact immutable proposal version, regardless of any
   scanner score or LLM confidence. Paper auto-approval is allowed only under
   the four gates in §5.4 and never promotes the rollout stage itself.
5. **Order placement is idempotent and only ever issued by the execution
   engine.** No other component calls Fyers order REST endpoints.
6. **SL/target/trailing enforcement must not depend on the frontend being
   open.** Backend position monitor worker, always on while positions are open.
7. **Global kill switch** per §7 — build before or with first live order path.
8. **Reconciliation, not blind trust** — periodic verify against Fyers;
   external manual trades are imported/flagged, not fought.
9. **Screening, LLM fundamentals, and VCP proposal inference run as background
   jobs (Redis/`arq`), never inline in an API request.** Proposal inference has
   its own concurrency-1 worker and the §4 deadline; it cannot block core cron,
   token-readiness, or reconciliation jobs.
10. **Software residual risk is accepted and mitigated**: process supervision,
    monitor heartbeats/`system_events`, reconciliation, kill switch, small
    size until proven. Do not pretend software SL equals exchange-held SL.
11. **No schema decisions in this file** — see `server/db/`. Propose schema
    changes in `server/db/` explicitly; keep write ownership aligned with §4.
12. **Do not add dependencies or new long-running services** not listed in §2–§4
    without asking.
13. **Fundamental sources are read-only.** P7 may call documented
   fundamentals GET endpoints for persisted technical survivors. All market
   data, sockets, account state, and orders remain exclusively on the locked
   Fyers paths. Upstox is primary; official NSE shareholding and integrated
   filing XBRLs are limited to promoter-pledge and leverage risk enrichment.
   Known warning/red/severe results may reduce only the deterministic
   fundamental score and grade; they never change technical rank, reject a
   candidate automatically, confirm a trade, or invoke execution. Unknown or
   ambiguous filing data is not scored as healthy and receives no penalty.
14. **Deterministic Python owns money.** Stop placement/distance, targets'
    acceptance, risk budgets, quantities, whole-lot rounding, cash/funds,
    daily loss, position/name/sector/correlation caps, priority, add gates,
    target sizing, trailing, and fill correction must be pure/versioned Python
    rules. Prompt output is never authoritative for these fields.
15. **Execution-time revalidation is mandatory.** Proposal-time compliance
    does not reserve capacity. Every entry/add/correction rechecks fresh broker
    state and all caps—including the `rho >= 0.80` cluster—under one Postgres
    allocation lock, then rechecks actual fills.
16. **Postgres is the recovery source.** An in-flight leg/order is rebuilt from
    intents/events/fills; a Redis loss or worker restart must never cause a
    duplicate place or require the UI to re-arm protection.
17. **The live free-form trade form is retired for scanner trades.** Keep it
    paper/log-only unless the user explicitly reopens a separate manual-live
    architecture.

---

## 11. Build order and status

Do not reorder phases without asking. Status tags: `[done]`, `[next]`,
`[ ]`.

| Phase | Deliverable | Status |
| ----- | ----------- | ------ |
| 1 | Fyers historical data fetch (auth, candle retrieval) | `[done]` |
| 2 | Screening/scanner technical filter on top of (1) | `[done]` |
| 3 | Shortlist storage + manual review surface (frontend) | `[partial]` — results table + chart workspace (sample data); real candle endpoint added |
| **P0** | Shared valid-token path + scheduled refresh/readiness + auth failure signaling | `[done]` — P10 must adapt readiness to current static-IP/daily-2FA requirements |
| **P1** | Chart workspace on shortlist (daily candles from DB; no live required) | `[done]` — candle API endpoint live; chart component + workspace layout complete |
| **P2** | Tick ingestion worker + Redis LTP + backend→frontend WS overlay | `[done]` — tick_worker.py, ws.py router, useMarketWS hook, live LTP price line on chart |
| **P3** | Trade instruction API + confirm UI + execution engine in paper/log mode | `[done]` — draft/review/confirm API + UI, idempotent paper intents, pending positions, and kill switch control |
| **P4** | Order gateway (async REST + order WS) live CNC entry | `[done]` — durable live intent claim, `/orders/async` placement, single order WS, replay-safe order/fill correlation |
| **P5** | Position monitor SL/target/trail + kill switch wiring + heartbeats | `[done]` |
| **P6** | Reconciliation cron | `[done]` |
| **P7** | LLM fundamental pass on technical survivors | `[done]` — cached Upstox snapshots, deterministic normalization, strict OpenRouter verdicts, and manual-review UI |
| **P8** | Journal + AI coach (read-only) | `[done]` — future-fill outbox, frozen entry snapshots, regime tag at first fill, CNC charge estimates, chart PNG capture, period summaries, tradebook from journal, async AI coach with input-hash reuse |
| **VCP vision** | On-demand VCP validator (advisory screening/manual-review extension) | `[done]` — frozen EOD candle snapshot + reuse key, standardized 1280×720 capture, blind strict structured OpenRouter verdict with date-anchor validation, attempt audit trail, sheet UI with read-only overlay + source images, human review, and workstation overlay toggle |
| **P9** | Deterministic Nifty/sector context, shadow-first P10 entry/add gates | `[done]` — implementation is complete; replay review and owner-controlled shadow→enforced rollout remain operational gates |
| **P10** | Automated proposal generation → immutable approval → deterministic multi-leg entry/risk/exit automation | `[done]` — implementation and review hardening are complete; operational rollout remains gated and starts at Shadow |

Notes:

- P10 implementation is complete; its next operational step is the Shadow
  gate in §12.2. P9 must complete its own replay-backed Shadow gate before any
  reduced-live P10 rollout. No rollout stage may be skipped or self-promote.
- P10 must reuse the single market WS, single order WS, execution-engine-only
  order mutation, reconciliation, journal, and kill-switch foundations. Do not
  replace them with a parallel broker path.
- P10 was implemented in this order: schema/migrations and pure domain rules →
  headless chart/geometry pipeline → strict Gemini contract and proposal worker
  → approval API/UI → 5-minute ingestion/profile → entry supervisor and
  allocation lock → multi-leg/fill correction/exit evolution → recovery and
  operational supervision. Staged rollout remains an operator-controlled
  operational activity under §12.2.
- The manual trade form stays paper/log-only. No scanner-sourced P10 order may
  reach live Fyers until all rollout gates in §12 pass.

Historical AGENTS list items 4–8 map onto P7, P3–P4, P5, P6, P2 respectively;
the phase table above is authoritative going forward.

---

## 12. Testing and P10 rollout expectations

### 12.1 Required automated coverage

- Golden charts/source hashes: identical frozen inputs and versions generate
  identical packets; changed renderer/geometry versions cannot silently reuse.
- Gemini schema: reject extra/missing fields, money fields, pivot/targets/
  template, and a free `contraction_count`; require `merge_with_index` on
  merge; reject merge-into-reject; allow confidence and date-window pointers.
- Survivor resolution: merge groups fold to one window; `llm_count` is
  derived; extras count toward the ≥2 gate; rejecting Python's last candidate
  shifts pivot/stop to the new latest survivor.
- Pure rules: geometry, ATR/tick snapping, planned-entry buffers, chase
  ceiling from entry (no AI-T1 shrink), T1–T3 built from floor/measured/
  stretch with 2R floor and optional synthetic 4R, 0.5pp depth tolerance,
  `base_high` inclusive definition, 52-week tags, template scorer v1
  (disagreement forces `single`), Hold/Base/EMA21, add expiry,
  25/25/25/25 apportionment, stop ratchets, daily-loss accounting, P9
  market/sector classification, and the consecutive-stop circuit breaker.
  Frozen proposal T1–T3 are unchanged by a chase fill.
- Forming watch: no `trade_proposals` row and no geometry prices; expiry at
  10 sessions or universe drop; batch cap of 10 rechecks.
- Five-minute replay: first-15-minute exclusion, robust profile, two-bar
  confirmation, stale volume, reconciliation drift, lost capacity, and fresh
  re-trigger behavior.
- Risk/allocation: fresh funds, every cap, priority bands, exact ties, minimum
  viability, conservative rounding, post-fill one-lot residual tolerance, and
  stricter active-policy versions.
- Concurrency: two independently approved highly correlated trades consume the
  same recycled capital; the allocation lock must resize/block the second and
  prove no cluster double-spend.
- MPP: valid stop tightening, tighten-plus-trim, trim fallback, name/sector/
  correlation correction, and the distinct full exit for a chase/R:R-invalid
  fill.
- Execution/gateway: stable idempotency, ≤10 OPS, no double-place, partial and
  out-of-order fills, unknown submission, cumulative multi-target gaps, and
  external broker trades handled by reconciliation rather than fought.
- Recovery: crash before intent, after intent, during unknown submission,
  during partial entry/add, between exit trigger and fill, and during risk
  correction. Restart must reconstruct state without a duplicate order.
- Boundaries: Gemini cannot see broker state or call execution; API approval
  never places inline; UI closure does not affect entry/exit supervision; all
  kill/pause controls retain their documented semantics.

### 12.2 Rollout gates

Durable `p10_rollout_state.stage` is `shadow → paper → reduced_live → full_live`.
Owner-only, never self-promotes, cannot skip. Default `shadow`.

1. **Shadow:** generate/audit proposals with no orders. Reject is allowed.
   Approve is hard-blocked (API 409); no leg may become `armed`. Validate
   batch deadline, schema reliability, target rejection, and human VCP
   agreement.
2. **Paper:** run the complete approval, trigger, allocation, multi-leg, exit,
   reconciliation, and restart path for at least 50 triggered proposals with
   zero duplicate orders, unexplained state transitions, or cap breaches beyond
   the explicit one-lot post-fill tolerance. P9 counterfactuals, fresh-trigger
   resets, and three-stop pause/reset recovery must pass here. Paper uses the
   same execution-engine claim/submit path and order-gateway fill processors as
   live; only the transport is a paper broker with a mutating ₹5,00,000 cash
   ledger. Owner-enabled paper auto-approval may be used for unattended testing
   at this stage; it does not waive any trigger, allocation, risk, kill-switch,
   or recovery gate. Positions, intents, daily-loss, stop-streak, and
   reconciliation are scoped by `execution_mode`.
3. **Reduced live:** explicit operator enablement; size P10 against `0.25×`
   deployable capital while retaining the same percentage policy. Blocked while
   any paper nonterminal position/intent exists. Use small CNC positions and
   complete live auth, restart, reconciliation, pause, and kill-switch drills.
   This stage is blocked until an enforced P9 policy references an
   owner-approved replay-report hash.
4. **Full live:** an explicit risk-policy version change to `1.0×` only after
   reviewing reduced-live fills, slippage, correction, and recovery evidence.
   No stage promotes itself.

Scanner score stays primary. Within a two-point scanner band, conservative R:R
then trigger timestamp break remaining ties. An exact remaining tie is still
`capacity_conflict`.

Never “test” live exits with size you cannot afford to lose.

---

## 13. When in doubt

If a task seems to require crossing one of the boundaries in §10, changing a
locked decision in §2 / §2.1, or introducing a new service/dependency not
listed in §2–§4, treat that as a **stop-and-ask** situation — not a judgment
call to make silently.

Propose an explicit edit to this file when the architecture should change;
do not silently drift.
