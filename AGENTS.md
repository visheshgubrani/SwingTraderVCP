# AGENTS.md — Swing Trading System

This file is the source of truth for architecture. If you are an AI coding
agent working on this repo: **read this fully before writing any code.**
When a request conflicts with this document, stop and flag the conflict —
do not silently deviate, "improve," or reinterpret the architecture.

If something here is genuinely ambiguous or missing, ask, or propose an
addition to this file explicitly — don't invent structure and move on.

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

---

## 2. Locked technology decisions

Do not substitute these without an explicit instruction from the user.

| Layer                | Choice                                            | Notes                                                                                                                                               |
| -------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Frontend             | Vite + React, TanStack Query, shadcn/ui, Tailwind | REST via TanStack Query; live data via WebSocket to **our own backend**, never directly to Fyers                                                    |
| Charting             | lightweight-charts (TradingView OSS)              |                                                                                                                                                     |
| Backend              | FastAPI (Python)                                  | Async throughout for I/O-bound work (WS, HTTP calls to Fyers)                                                                                       |
| Database             | PostgreSQL                                        | Chosen over SQLite specifically because multiple processes (API, ingestion, monitor, screener) read/write concurrently — do not fall back to SQLite |
| Broker/queue         | Redis                                             | Two roles: (1) pub/sub for tick fan-out, (2) backing store for the async job queue (e.g. `arq`) used for the screening job                          |
| Market data & orders | Fyers API (REST + WebSocket)                      | Single upstream WS connection to Fyers per the whole system — never one per browser/user session                                                    |

---

## 3. Component map

- **Frontend** — consumes backend REST API + backend WebSocket. Never talks
  to Fyers directly. Never contains trading logic (no SL/target math, no
  screening logic) — it displays state and sends instructions/decisions.
- **FastAPI backend (API layer)** — request/response only. Thin. Delegates
  real work to background workers. Owns the WebSocket connection(s) to the
  frontend.
- **Tick ingestion worker** — the _only_ component holding the Fyers market
  data WebSocket connection. Publishes ticks to Redis pub/sub. Does not
  contain business logic.
- **Position monitor worker** — subscribes to tick data via Redis. Evaluates
  SL / target / trailing rules for every open position, continuously, tick
  by tick. Runs independent of whether the frontend UI is open. This is the
  component that removes emotion/reaction time from exits — treat its
  correctness as critical.
- **Execution engine** — the _only_ component allowed to place, modify, or
  cancel orders with Fyers. Called by the position monitor (for SL/target/
  trailing exits) and by the API layer (for the initial entry, on explicit
  human instruction). Order placement must be idempotent — track an internal
  order-intent record before calling Fyers; never blind-retry a live order
  call.
- **Screening worker** — runs the technical filter (VCP pattern, 50/200 SMA,
  min volume, % from 52-week high) over the Nifty 500 universe using
  historical candles, then passes technical-pass survivors through an LLM
  fundamental check. Triggered by the scheduler via the Redis job queue, not
  called synchronously from the API.
- **Scheduler** — triggers the screening job (EOD and/or intraday cadence),
  enqueues to Redis. Also responsible for Fyers auth token refresh — this
  must be a scheduled job, not an on-demand/lazy check.
- **Reconciliation job** — periodically compares internal DB state (orders,
  positions) against what Fyers actually reports. Manual trades placed
  directly in the Fyers app must be detected here, not fought against.
- **Database (Postgres)** — system of record for watchlists, screening
  results, positions, orders, and tick/candle history.

---

## 4. Data flow — screening pipeline

```
Nifty 500 universe (historical candles from Fyers)
        │
        ▼
Technical filter (VCP, 50/200 SMA, volume, % from 52w high)
        │  (only survivors proceed — do not run LLM pass on all 500)
        ▼
LLM fundamental pass (structured input → structured flag/verdict, not prose)
        │
        ▼
Shortlist stored in DB, surfaced to frontend for manual review
```

This entire pipeline **never places an order and never touches money.** Its
only output is data for the human to review.

## 5. Data flow — execution & monitoring

```
Human decision (stock, entry, size, SL, target, trailing rule)
        │  ← this is the only manual step in this half of the system
        ▼
Execution engine places entry order via Fyers (idempotent)
        │
        ▼
Position created in DB, state = pending_entry → open
        │
        ▼
Position monitor (tick-driven, continuous, Redis-subscribed)
   watches live price vs SL / target / trailing rules
        │
        ▼
Exit order fired automatically when a rule triggers → state = closed
```

Position state must be modeled as an explicit state machine, e.g.
`pending_entry → open → trailing_active → exit_pending → closed`, so that:

- Trailing behavior is unambiguous per state.
- On process restart, the monitor reloads all non-closed positions from
  Postgres and resumes watching them without any manual re-arming.

---

## 6. Non-negotiable architectural rules

1. **Single Fyers WebSocket connection**, owned only by the tick ingestion
   worker. Nothing else connects to Fyers WS directly.
2. **Frontend never talks to Fyers directly** — always through our backend.
3. **The manual decision checkpoint is not to be automated away.** No
   component should auto-select a stock and auto-enter a trade without an
   explicit human instruction, regardless of how confident a filter/LLM is.
4. **Order placement is idempotent and only ever issued by the execution
   engine.** No other component calls Fyers order endpoints.
5. **SL/target/trailing enforcement must not depend on the frontend being
   open.** It's a backend worker process, always running while positions are
   open.
6. **A global kill switch must exist**: a single toggle (reachable from the
   UI) that immediately halts the execution engine and position monitor from
   firing any new orders. Build this early, not as an afterthought.
7. **Reconciliation, not blind trust**: system state is periodically
   verified against Fyers' actual state, since manual intervention outside
   the app will happen.
8. **Screening runs as a background job (Redis queue), never inline in an
   API request.**
9. **No schema decisions in this file** — table structure, columns, etc. are
   defined separately and may evolve; this file governs component
   boundaries and data flow only.

---

## 7. Build order (do not reorder without asking)

The user has specified the starting point explicitly:

1. Fyers historical data fetch (auth, candle retrieval) — foundation for
   everything else.
2. Screening/scanner: technical filter (VCP/SMA/volume/52w-high) on top of
   (1).
3. Shortlist storage + manual review surface (frontend).
4. LLM fundamental pass integrated into the screening pipeline.
5. Execution engine (manual-instruction-triggered order placement).
6. Position monitor (SL/target/trailing, tick-driven).
7. Reconciliation job + kill switch.
8. Real-time tick ingestion + live chart wiring (can be pulled earlier if
   charting is needed sooner for manual review, but monitor/execution logic
   must not depend on the frontend being connected).

Do not start building the execution engine or position monitor before the
screening pipeline is working end-to-end, unless explicitly told otherwise.

---

## 8. When in doubt

If a task seems to require crossing one of the boundaries in Section 6, or
introducing a new service/dependency not listed in Section 2, treat that as
a stop-and-ask situation, not a judgment call to make silently.
