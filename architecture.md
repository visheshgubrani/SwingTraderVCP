  # SwingTraderVCP Technical Architecture

  > Last audited: 2026-07-31
  >
  > Repository baseline: `main` at `e520307`, including the uncommitted P8 journal and current frontend changes present during the audit.

  This document explains the application as it is currently implemented. It is
  intended to help a new engineer understand the system, trace data through it,
  and operate it safely.

  `AGENTS.md` remains the source of truth for locked architectural decisions and
  component boundaries. Database tables and columns remain authoritative in
  `server/db/`. If this document and either source disagree, follow `AGENTS.md`
  and the executable schema, then update this document.

  For security findings and the remediation backlog, see
  [`SECURITY_AUDIT.md`](SECURITY_AUDIT.md).

  ## 1. System purpose and safety boundary

  SwingTraderVCP is a single-user, human-in-the-loop swing trading system for
  Indian equities. It screens the Nifty 500, lets the user review candidates and
  construct a trade, then automates broker execution and position management
  only after explicit confirmation.

  The defining invariant is:

  ```text
  Automated research -> explicit human decision -> automated execution/management
        no money              checkpoint                 money path
  ```

  The scanner and LLM may annotate or rank candidates, but they cannot confirm a
  trade or invoke the execution engine. Conversely, once a confirmed trade is
  open, stop-loss, target, and supported trailing enforcement must continue even
  if the browser is closed.

  Other locked decisions:

  - Fyers is the only source for prices, market sockets, account state, and
    orders.
  - Upstox is used only for read-only company fundamentals.
  - OpenRouter AI is annotation/analysis only and receives no money-path tools.
  - The default trading product is CNC.
  - Software monitoring, not exchange CO/BO, is the primary exit mechanism.
  - Only the execution engine may call Fyers order REST endpoints.
  - There is one Fyers market-data WebSocket and one Fyers order WebSocket.
  - PostgreSQL is the durable system of record; Redis is coordination and hot
    state, not the authoritative trade ledger.

  ## 2. High-level topology

  ```mermaid
  flowchart LR
      U[Single user browser] -->|REST /api/v1| API[FastAPI API]
      U <-->|Browser WebSocket /ws| API

      API -->|SQL| PG[(PostgreSQL)]
      API -->|enqueue / pub-sub / cache| R[(Redis)]

      ARQ[arq worker and cron] --> PG
      ARQ --> R
      ARQ -->|historical REST and read-only books| FYREST[Fyers REST]
      ARQ -->|fundamentals GET| UP[Upstox]
      ARQ -->|structured inference| OR[OpenRouter]

      TICK[Tick ingestion worker] -->|one market-data WS| FYMD[Fyers Market WS]
      TICK -->|ticks and ltp cache| R

      OG[Order gateway] -->|one order WS| FYOWS[Fyers Order WS]
      OG --> PG

      PM[Position monitor] -->|subscribe ticks and controls| R
      PM --> PG
      PM --> EE[Execution engine]
      API -->|confirmed entry only| EE
      EE -->|async order REST, maximum 10 OPS| FYREST
      EE --> PG
      EE --> R

      R -->|tick fan-out| API
  ```

  The API, arq worker, tick worker, order gateway, and position monitor are
  separate processes. Keeping live-money workers outside the web process ensures
  that browser or API restarts do not intentionally disarm an open position.

  ## 3. Technology stack

  | Layer | Implementation | Responsibility |
  | --- | --- | --- |
  | Browser | Vite, React 19, React Router data-mode router, TanStack Query, shadcn/ui, Tailwind | Review, visualization, and explicit human instructions |
  | Charts | TradingView `lightweight-charts` v5 | Presentational candles, drawings, and live-price overlay |
  | API | FastAPI with async SQLAlchemy | Thin REST API, browser WebSocket, enqueueing jobs |
  | Workers | Python async processes and arq | Long-running sockets, scheduled work, screening, reconciliation, journal and AI jobs |
  | Durable data | PostgreSQL 17 with `pgcrypto` | Trading ledger, market history, scans, controls, audit state |
  | Coordination | Redis 7 | arq queue, pub/sub, LTP cache, rate limiter, worker status, singleton lock |
  | Broker | Fyers API v3 | Historical data, live LTP, orders, fills, portfolio truth |
  | Fundamentals | Upstox Company Fundamentals | ISIN-keyed read-only financial statements |
  | AI | OpenRouter, `xiaomi/mimo-v2.5-pro` | Strict fundamental verdicts and read-only journal coaching |

  ## 4. Processes and ownership

  ### 4.1 React SPA

  The frontend lives in `client/src` and is split by feature. `App.tsx` defines
  the browser routes with `createBrowserRouter`. The main routes are the trading
  workspace, fundamentals, positions, orders, tradebook, journal, operations,
  and the Fyers callback.

  TanStack Query owns server state. `MarketWSContext` and `useMarketWS` own the
  browser's live LTP connection. `TradingChart` obtains historical daily candles
  over REST and overlays the current LTP from the backend WebSocket.

  The frontend may calculate display-only values such as the currently visible
  unrealized P&L, but authoritative position state, stop/target decisions,
  screening rules, and order decisions remain on the server.

  Current network defaults are development-specific:

  - REST: `http://localhost:8000/api/v1`
  - WebSocket: `ws://localhost:8000/ws`
  - Vite: `http://localhost:3000`

  These endpoints are not yet configured as same-origin production paths. See
  the audit before exposing the application beyond a trusted local machine.

  ### 4.2 FastAPI API process

  Entrypoint: `server/main.py`

  The API process:

  - mounts the auth, historical, screening, trading, journal, system-control,
    and browser-WebSocket routers;
  - creates the Redis/arq pool used by request handlers to enqueue work;
  - owns one Redis pub/sub listener that fans ticks out to browser sessions;
  - performs request validation with Pydantic response/request models on the
    money and journal paths;
  - writes human instructions and controls, then delegates broker placement to
    the execution engine;
  - exposes `/health`, which currently checks PostgreSQL only.

  The API must never open either Fyers WebSocket. Heavy and scheduled work should
  be enqueued to arq. One current exception is the historical validation route,
  which uses a FastAPI `BackgroundTasks` task and is recorded in the audit.

  ### 4.3 arq worker and scheduler

  Entrypoints: `server/run_worker.py` and `server/app/worker.py`

  The arq process runs queued jobs and cron schedules in `Asia/Kolkata`:

  | Job | Current schedule | Purpose |
  | --- | --- | --- |
  | Incremental EOD sync | Weekdays at 18:30 | Fetch and persist completed daily candles |
  | Fyers token refresh | Weekdays at 08:50 | Refresh the shared Fyers access token before market open |
  | Reconciliation | Weekdays every 15 minutes for hours 09-15 | Compare local live state to Fyers REST books |
  | Journal dispatcher | Every 30 seconds | Process fill outbox records into journal entries |
  | Technical scan | On demand | Run the Nifty 500 screening pipeline |
  | Fundamental pass | Enqueued after eligible scans | Annotate persisted technical survivors only |
  | Journal AI coach | On demand | Analyze filtered closed journals using structured output |

  Cron expressions are clock-based and do not currently use an exchange holiday
  calendar.

  ### 4.4 Tick ingestion worker

  Entrypoint: `python -m app.workers.tick_worker`

  This is the only process allowed to create `FyersDataSocket`. On startup it:

  1. obtains a token through `get_valid_access_token`;
  2. loads the union of non-closed positions and active watchlist instruments;
  3. always includes `NSE:NIFTY50-INDEX` for regime calculations;
  4. connects and subscribes with `SymbolUpdate`;
  5. publishes normalized ticks to Redis `ticks`;
  6. writes each latest tick to `ltp:{symbol}` with a 60-second TTL;
  7. listens to `tick_subs` for dynamic browser or monitor subscriptions;
  8. publishes `tick_worker:status` and `tick_worker:symbols` heartbeats.

  The Fyers SDK callback runs in a thread and hands messages to an asyncio queue.
  The current handoff and singleton check have correctness gaps documented in
  the audit.

  Raw ticks are not normally persisted to PostgreSQL. The schema permits sampled
  or debug retention, but PostgreSQL is deliberately not a raw tick sink.

  ### 4.5 Order gateway

  Entrypoint: `python -m app.workers.order_gateway`

  The gateway is started only when live execution is double-armed. It:

  - obtains an atomic Redis singleton lock;
  - opens the only `FyersOrderSocket`;
  - subscribes to `OnOrders,OnTrades`;
  - moves SDK-thread callbacks into an asyncio queue safely;
  - correlates `id_fyers`, broker order IDs, exchange IDs, and the compact order
    tag back to a durable `order_intent`;
  - persists replay-safe `order_events` and `order_fills`;
  - updates entry/exit position state from fills;
  - creates journal outbox records for newly persisted fills.

  The gateway decides no trades. It records broker facts and advances durable
  state after correlation.

  ### 4.6 Execution engine

  Module: `server/app/services/execution_engine.py`

  This is the sole owner of Fyers order REST side effects. It is called by:

  - the trading router after explicit entry confirmation; and
  - the position monitor after an exit rule fires.

  For every live submission it enforces:

  - paper/live configuration and the separate live arming flag;
  - the global kill switch;
  - a fresh order-gateway heartbeat;
  - an internal Redis-backed limit of at most 10 operations per second;
  - a durable, unique idempotency key;
  - intent persistence before the broker request;
  - a committed `submission_pending` claim immediately before one HTTP call;
  - no blind retry after an ambiguous timeout or response.

  Fyers async REST returns `id_fyers`; the order gateway later correlates the
  exchange order and fills. The API response is therefore not the fill source of
  truth.

  ### 4.7 Position monitor

  Entrypoint: `python -m app.workers.position_monitor`

  The monitor:

  - reloads every position not in `closed` or `cancelled` on startup and roughly
    every 20 seconds;
  - subscribes to Redis `ticks` and `system_controls`;
  - evaluates stop loss, target, and supported trailing updates tick by tick;
  - persists position transitions and creates idempotent exit intents;
  - immediately completes paper exits at observed LTP;
  - invokes the execution engine for live market exits;
  - emits a Redis heartbeat with a 30-second TTL.

  `step_pct` trailing is implemented. The API currently also accepts `atr`, but
  the monitor explicitly skips ATR trailing because an ATR feed has not been
  implemented.

  The monitor never opens a broker WebSocket. Its continued operation, process
  supervision, data freshness, and Redis availability are safety-critical
  because stops are software-held.

  ### 4.8 Reconciliation

  Module: `server/app/services/reconciliation.py`

  Reconciliation is an arq job and uses a dedicated read-only client to fetch:

  - `/orders`
  - `/tradebook`
  - `/positions`
  - `/holdings`

  It compares these books with live `order_intents`, fills, and open positions.
  Safe healing is deliberately narrow: a broker order/trade that matches an
  existing local intent can be passed through the same order-gateway persistence
  functions to recover missing events or fills.

  It does not place, modify, cancel, exit, or convert an order. External manual
  activity and unresolved quantity differences are written as
  `reconciliation_items` for human review. Every run is represented by both a
  `job_runs` row and a `reconciliation_runs` row.

  Known aggregation, scheduling, locking, and ambiguous-submission gaps are
  listed in the audit. Reconciliation must be treated as a second line of
  defence, not a substitute for correct order correlation.

  ### 4.9 Journal processor and AI coach

  Order-fill persistence inserts a `journal_fill_outbox` record in the same
  database transaction. The journal dispatcher claims those records and:

  - creates a journal entry on the first app-managed entry fill;
  - freezes the scanner result, trade plan, entry chart context, and Nifty
    regime snapshot;
  - aggregates later entry and exit fills;
  - estimates CNC charges and computes gross/net P&L and R-multiples;
  - closes the journal when the corresponding position is closed.

  The browser may claim and upload a PNG chart artifact for a pending journal.
  Review fields, actual reconciled charges, notes, tags, lessons, and rating are
  the only journal data deliberately editable by the user.

  The AI coach is an arq job over filtered closed journals. Python calculates
  deterministic statistics before OpenRouter synthesizes a strict structured
  report. The coach has no execution tools, does not confirm trades, and stores
  request IDs, input hashes, usage, and concise output rather than model
  reasoning details.

  ## 5. External provider boundaries

  | Provider surface | Owner | Direction | Money side effect |
  | --- | --- | --- | --- |
  | Fyers historical REST | historical/arq services | Outbound | No |
  | Fyers order/trade/portfolio REST reads | reconciliation only | Outbound | No |
  | Fyers `/orders/async` | execution engine only | Outbound | Yes |
  | Fyers market WebSocket | tick worker only | Inbound socket stream | No |
  | Fyers order WebSocket | order gateway only | Inbound socket stream | Reflects broker side effects |
  | Fyers OAuth callback | auth router | Browser redirect then API exchange | Credentials only |
  | Upstox fundamentals GET | P7 job only | Outbound | No |
  | OpenRouter completions | P7 and journal AI clients | Outbound | No trading authority |

  ### Webhook policy

  There are currently **no inbound HTTP webhooks** from Fyers, Upstox, or
  OpenRouter. This is intentional, not a missing hidden route:

  - live prices arrive on the single Fyers market WebSocket;
  - order and trade events arrive on the single Fyers order WebSocket;
  - missed or ambiguous broker events are addressed by periodic REST
    reconciliation;
  - OAuth returns through `/api/v1/auth/callback` and is not a broker event
    webhook.

  If a provider webhook is introduced later, it requires an explicit
  architecture decision covering signature verification, replay protection,
  idempotent storage-before-processing, ingress limits, and ownership. It must
  not create a second order-decision path.

  ## 6. End-to-end data flows

  ### 6.1 Historical candles and screening

  ```mermaid
  sequenceDiagram
      participant UI as React UI
      participant API as FastAPI
      participant Q as Redis/arq
      participant W as arq worker
      participant FY as Fyers REST
      participant DB as PostgreSQL
      participant UP as Upstox
      participant OR as OpenRouter

      UI->>API: POST /historical/sync or /screening/scan
      API->>DB: create durable run metadata
      API->>Q: enqueue job
      W->>FY: fetch completed daily candles
      W->>DB: upsert market_candles
      W->>DB: load Nifty 500 and run technical gates
      W->>DB: persist scan_runs and screening_results
      opt P7 enabled, technical survivors only
          W->>UP: GET fundamentals by ISIN
          W->>DB: persist reproducible snapshot and Python facts
          W->>OR: strict structured annotation
          W->>DB: store verdict, evidence keys, request metadata
      end
      UI->>API: GET run/results/candles
  ```

  The pipeline's only output is review data. A `screening_result` cannot create
  an entry by itself.

  ### 6.2 Manual trade confirmation and entry

  ```mermaid
  sequenceDiagram
      participant U as User
      participant UI as React
      participant API as FastAPI
      participant DB as PostgreSQL
      participant EE as Execution engine
      participant R as Redis
      participant FY as Fyers async REST
      participant OG as Order gateway

      U->>UI: Enter symbol, qty, entry, SL, target, trail
      UI->>API: POST trade instruction draft
      API->>DB: trade_instruction = draft
      U->>UI: Explicit confirmation phrase
      UI->>API: POST /trade-instructions/{id}/confirm
      API->>DB: lock draft and record manual_confirmed_at
      API->>DB: create pending position and idempotent entry intent
      alt paper mode
          EE->>DB: synthetic fill and position=open
      else live mode, double-armed
          EE->>R: check kill switch, gateway, auth, rate limit
          EE->>DB: commit submission_pending claim
          EE->>FY: POST /orders/async once
          EE->>DB: record id_fyers or terminal/unknown outcome
          FY-->>OG: OnOrders / OnTrades
          OG->>DB: replay-safe events, fills, position=open
      end
  ```

  The explicit confirmation phrase is `CONFIRM_PAPER_TRADE` in paper mode and
  `CONFIRM_LIVE_ORDER` in live mode. Live placement requires both
  `EXECUTION_MODE=live` and `LIVE_ORDER_PLACEMENT_ENABLED=true`.

  ### 6.3 Live LTP, software stops, and exits

  ```mermaid
  sequenceDiagram
      participant FY as Fyers market WS
      participant TW as Tick worker
      participant R as Redis
      participant PM as Position monitor
      participant DB as PostgreSQL
      participant EE as Execution engine
      participant API as FastAPI WS
      participant UI as Browser

      FY-->>TW: SymbolUpdate
      TW->>R: SET ltp:symbol and PUBLISH ticks
      R-->>PM: normalized tick
      R-->>API: normalized tick
      API-->>UI: subscribed-symbol LTP
      PM->>DB: evaluate and persist trailing change
      alt stop/target fires and kill switch is off
          PM->>DB: create exit intent, position=exit_pending
          PM->>EE: submit live exit or complete paper exit
          EE->>DB: durable submission state
      end
  ```

  The chart is only a consumer. Closing the tab does not stop the monitor.

  ### 6.4 Reconciliation

  ```mermaid
  flowchart TD
      C[arq cron or manual trigger] --> JR[Create job and reconciliation run]
      JR --> T[Get shared valid Fyers token]
      T --> B[Fetch orders, trades, positions, holdings]
      B --> M{Matches existing local intent?}
      M -->|Yes| H[Replay through gateway persistence]
      M -->|No external activity| F[Create flag-only item]
      H --> Q[Compare quantities and terminal state]
      F --> Q
      Q --> S[Persist summary and system events]
  ```

  Reconciliation never creates a trade instruction and never calls the
  execution engine.

  ## 7. Durable state and state machines

  ### 7.1 Trade instruction

  ```text
  draft -> confirmed -> submitted
    |          |
    +------> cancelled / rejected (where allowed by the current path)
  ```

  `manual_confirmed_at` is required before an entry can reach the execution
  engine. A scanner reference is optional so manually discovered trades remain
  possible while preserving the same checkpoint.

  ### 7.2 Position

  ```text
  pending_entry -> open -> trailing_active -> exit_pending -> closed
        |
        +-------------------------------------------------> cancelled
  ```

  Partial entry fills may open a position before the entry intent is fully
  filled. Non-closed positions are reloaded after monitor restart. Unknown
  trailing rule types are logged and ignored rather than guessed.

  ### 7.3 Order intent

  ```text
  created
    -> submission_pending
        -> submitted -> acknowledged -> partially_filled -> filled
        -> rejected
        -> submission_unknown
    -> cancel_requested -> cancelled
  ```

  `submission_unknown` means the application cannot prove whether Fyers
  accepted the request. It is intentionally not blindly retried. Reconciliation
  and order-WebSocket correlation are responsible for resolving it.

  ### 7.4 Journal outbox and AI

  ```text
  journal_fill_outbox: pending -> processing -> completed
                                        \-> pending retry -> failed

  journal_ai_runs: queued -> running -> succeeded
                          \-> failed
  ```

  The fill and its outbox record are committed together, avoiding a gap between
  the trading ledger and journal notification. The current processing lease has
  a crash-recovery defect described in the audit.

  ## 8. Data model by domain

  The full schema is in `server/db/schema.sql`; upgrades are in
  `server/db/migrations`.

  | Domain | Main tables | Primary writer |
  | --- | --- | --- |
  | Reference | `instruments`, `universe_memberships` | import/backfill jobs |
  | Market data | `market_candles`, optional `market_ticks` | historical/tick ingestion |
  | Screening | `scan_runs`, `screening_results`, `fundamental_snapshots`, `watchlists`, `watchlist_items` | screening and P7 jobs; review API for statuses |
  | Human checkpoint | `trade_instructions` | trading API/service |
  | Money path | `positions`, `position_events`, `order_intents`, `order_events`, `order_fills` | execution engine, monitor, order gateway |
  | Operations | `job_runs`, `reconciliation_runs`, `reconciliation_items`, `broker_auth_tokens`, `system_controls`, `system_events` | scheduler and operational services |
  | Journal | `market_regime_snapshots`, `journal_entries`, `journal_chart_artifacts`, `journal_fill_outbox`, `journal_ai_runs` | journal processor, review API, AI job |

  Important traceability chains:

  ```text
  positions.screening_result_id
    -> screening_results.scan_run_id
    -> scan_runs

  screening_results.fundamental_snapshot_id
    -> fundamental_snapshots

  trade_instructions
    -> positions
    -> order_intents
    -> order_events / order_fills
    -> journal_fill_outbox
    -> journal_entries
  ```

  `market_candles` and `market_ticks` are time-partitioned with default
  partitions. Production operations must create future partitions and define a
  retention/downsampling policy for ticks.

  ## 9. Redis contracts

  Redis data is ephemeral coordination state. Losing it may interrupt live data,
  jobs, or safety checks, but PostgreSQL remains the ledger to rebuild from.

  | Key/channel | Purpose |
  | --- | --- |
  | `ticks` | normalized tick pub/sub fan-out |
  | `ltp:{symbol}` | latest tick cache, currently 60-second TTL |
  | `tick_subs` | dynamic subscribe/unsubscribe requests to tick worker |
  | `system_controls` | immediate kill-switch notifications |
  | `auth:fyers:access_token` | short-lived shared decrypted token cache |
  | `auth:fyers:expires_at` | cached token expiry |
  | `auth:fyers:healthy` | shared auth health flag |
  | `tick_worker:status` | tick worker heartbeat |
  | `tick_worker:symbols` | current tick-worker symbol snapshot |
  | `order_gateway:singleton` | atomic live gateway lease |
  | `order_gateway:status` | gateway heartbeat consumed by execution engine |
  | `position_monitor:status` | monitor heartbeat |
  | arq keys | queued jobs and uniqueness metadata |
  | execution rate-limit keys | distributed maximum-10-OPS token bucket |

  Redis pub/sub is at-most-once. Durable broker events must therefore come from
  the order gateway's PostgreSQL writes or be recovered through reconciliation;
  pub/sub messages themselves are not a durable audit trail.

  ## 10. API and browser WebSocket surface

  All REST business routes are under `/api/v1`.

  | Area | Representative routes |
  | --- | --- |
  | Fyers auth | `/auth/url`, `/auth/callback`, `/auth/status`, `/auth/events`, `/auth/refresh` |
  | Historical | `/historical/sync`, `/status`, `/cancel`, `/validate`, `/candles` |
  | Screening | `/screening/config`, `/scan`, `/runs`, `/runs/{id}/results` |
  | Trading | `/trading/execution-status`, `/trade-instructions`, `/{id}/confirm`, `/positions`, `/order-intents` |
  | Operations | `/system/kill-switch`, `/system/reconciliation/run`, `/system/reconciliation/runs` |
  | Journal | `/journal/entries`, `/summary`, artifact claim/upload, `/ai/runs` |
  | Health | `/health` |
  | Browser live data | `/ws` |

  Current browser WebSocket protocol:

  ```json
  {"action":"subscribe","symbols":["NSE:SBIN-EQ"]}
  {"action":"unsubscribe","symbols":["NSE:SBIN-EQ"]}
  {"action":"ping"}
  ```

  The server returns LTP ticks, subscription acknowledgements, `pong`, and a
  tick-worker status message. At present the WebSocket and REST API have no
  application-level session authentication; CORS is configured for development
  origins but is not an authorization control.

  ## 11. Authentication, secrets, and controls

  ### Fyers token lifecycle

  1. The UI requests a Fyers login URL.
  2. Fyers redirects with an authorization code.
  3. The backend exchanges the code for access and refresh tokens.
  4. Tokens are encrypted in PostgreSQL with `pgcrypto`.
  5. `get_valid_access_token` caches a valid access token in Redis.
  6. A scheduled job refreshes it before market open.
  7. Failed auth emits `system_events`; money-path callers fail closed when a
    valid token cannot be obtained.

  The current OAuth-state validation, logging, encryption-key separation, and
  cache invalidation have audit findings. Provider tokens must never be returned
  to the browser or logged.

  ### Global kill switch

  The kill switch is durable in `system_controls` and immediately published on
  the Redis `system_controls` channel.

  When engaged:

  - the execution engine refuses new place/modify operations;
  - the monitor does not create new exits or trailing writes;
  - existing positions remain at the broker;
  - toggling the control does not flatten the account.

  The switch therefore means **no automated orders**, not **account is flat**.
  A future panic-flatten operation must be a separate deliberate instruction.

  ### AI isolation

  The P7 fundamental pass and journal coach use purpose-specific clients and
  strict schemas. They do not import the execution engine or receive tools.
  Upstox and OpenRouter credentials are server-side environment secrets. Model
  reasoning details are deliberately excluded from storage and UI display.

  ## 12. Failure and restart behavior

  | Failure | Intended/current response |
  | --- | --- |
  | Browser closes | Backend monitor continues; only chart updates stop |
  | API restarts | Durable trading state remains in PostgreSQL; browser reconnects |
  | Position monitor restarts | Reloads non-closed positions and subscriptions |
  | Tick worker restarts | Reloads position/watchlist/benchmark subscriptions |
  | Order gateway restarts | Reconnects order socket; durable events remain; reconciliation covers missed events |
  | Fyers token invalid | Emit critical event, mark auth unhealthy, fail money path closed |
  | Kill switch engaged | Refuse new automated entries and exits; do not flatten |
  | Broker request timeout | Mark intent `submission_unknown`; do not blind retry |
  | Redis pub/sub loss | Live ticks/events may be missed; PostgreSQL plus reconciliation remain authoritative |
  | PostgreSQL unavailable | Money-path writes cannot be made safely; execution must stop rather than place first |
  | OpenRouter/Upstox failure | Annotation fails; scanning/manual review and Fyers money path continue |

  Some current heartbeats represent process liveness rather than confirmed
  socket readiness, and some recovery paths can strand work. Treat
  `SECURITY_AUDIT.md` as required reading before live deployment.

  ## 13. Local development and operation

  Prerequisites:

  - Docker with Compose
  - Python/`uv`
  - Node.js/`pnpm`
  - broker/provider credentials in `server/.env` when exercising integrations

  Start the full development stack from the repository root:

  ```bash
  ./start-dev.sh
  ```

  The script starts PostgreSQL, Redis, FastAPI, arq, tick ingestion, the position
  monitor, and Vite. It starts the order gateway only when live execution is
  double-armed. Logs and PID files are stored in `.dev-runtime/`.

  Useful checks:

  ```bash
  ./start-dev.sh status
  cd server && UV_CACHE_DIR=/tmp/swingtrader-uv-cache uv run pytest -q
  cd client && pnpm lint
  cd client && pnpm build
  ```

  The current Docker Compose file is development-only: it publishes PostgreSQL
  and Redis ports, uses a fixed database credential, and gives Redis no
  authentication. It must not be treated as a production deployment manifest.

  There are currently no checked-in production supervisor, reverse-proxy/TLS,
  backup, disaster-recovery, or CI definitions. Production deployment should run
  each worker under supervision, terminate TLS at a trusted private edge, keep
  PostgreSQL/Redis on a private network, and alert on stale worker heartbeats.

  ## 14. Configuration groups

  Configuration is loaded by `server/app/config.py` from environment variables
  and `server/.env`.

  | Group | Examples |
  | --- | --- |
  | Infrastructure | `DATABASE_URL`, `REDIS_URL`, `CORS_ORIGINS` |
  | Fyers OAuth | `FYERS_APP_ID`, `FYERS_SECRET_KEY`, `FYERS_REDIRECT_URI` |
  | Execution safety | `EXECUTION_MODE`, `LIVE_ORDER_PLACEMENT_ENABLED`, `ORDER_OPS_LIMIT` |
  | Scheduling | `SCHEDULER_TIMEZONE`, EOD sync, token refresh, reconciliation flags/times |
  | P7 | `P7_FUNDAMENTAL_PASS_ENABLED`, `UPSTOX_ANALYTICS_TOKEN`, snapshot/cache/concurrency settings |
  | OpenRouter | API key, model, prompt version, timeout/token/temperature settings |

  Safe defaults are paper execution and disabled live placement. Never commit
  real provider credentials. Any new long-running service or dependency requires
  an explicit architecture decision under `AGENTS.md`.

  ## 15. Observability and audit trail

  The system currently uses:

  - process logs;
  - expiring Redis worker status keys;
  - durable `system_events` with component, severity, event type, payload, and
    optional correlation IDs;
  - `job_runs` for background-job lifecycle;
  - `position_events`, `order_events`, and `order_fills` for the trade trail;
  - reconciliation runs/items for broker divergence;
  - OpenRouter request IDs, usage, prompt versions, and input hashes.

  There is no metrics backend or alert manager in the repository. Operationally
  critical conditions must presently be found through UI banners, status keys,
  database events, or logs. Secret-bearing provider responses should be reduced
  to normalized/redacted evidence before logging or persistence.

  ## 16. Verification baseline

  The following checks passed against the audited working tree on 2026-07-31:

  - Backend: `98 passed`, `9 subtests passed`; four arq/Python deprecation
    warnings.
  - Frontend lint: passed with four Fast Refresh warnings.
  - Frontend production build: passed; the main JavaScript chunk is about 799 KB
    before gzip and triggers the 500 KB chunk-size warning.

  Most backend tests use mocks or fake database/Redis/broker objects. They are
  valuable unit coverage but do not replace real PostgreSQL/Redis integration,
  multi-process restart drills, or tiny-size live operational testing.

  ## 17. New engineer reading order

  1. Read `AGENTS.md` completely for locked decisions and prohibited boundary
    changes.
  2. Read this document for the process and data-flow model.
  3. Read `SECURITY_AUDIT.md` before touching authentication, workers,
    reconciliation, or live execution.
  4. Inspect `server/db/schema.sql` and migrations for exact constraints.
  5. Trace a trade through `routers/trading.py`, `trade_service.py`,
    `execution_engine.py`, `workers/order_gateway.py`, and
    `workers/position_monitor.py`.
  6. Trace a scan through `routers/screening.py`, `screener.py`, and
    `fundamental_pass.py`.
  7. Run all checks in paper mode before changing the live-money path.

  When a requested change crosses a component boundary, adds a provider/service,
  or weakens the manual checkpoint, stop and propose an explicit `AGENTS.md`
  change instead of silently drifting the architecture.
