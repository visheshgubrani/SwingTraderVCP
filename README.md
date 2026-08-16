# SwingTraderVCP

A single-user, human-in-the-loop swing trading workstation for Indian equities.
It screens the Nifty 500, presents candidates for chart and fundamental review,
requires an explicit human trade decision, and then automates broker execution,
position monitoring, exits, reconciliation, and journaling.

> [!WARNING]
> This project can place real orders when live execution is enabled. It is not
> financial advice, it is not production-ready by default, and software-held
> stops do not provide the same protection as exchange-held orders. Start in
> paper mode, review the [security audit](SECURITY_AUDIT.md), and test every
> failure and restart path before risking capital.

## Why this project exists

SwingTraderVCP keeps research automation and trading authority deliberately
separate:

```text
AUTOMATED RESEARCH                 HUMAN CHECKPOINT              AUTOMATED MONEY PATH
EOD data -> scan -> annotation -> chart review + confirm -> entry -> monitor -> exit
```

The scanner and AI can rank or annotate a stock, but they cannot confirm a
trade or call the order execution path. Once a confirmed position is open,
stop-loss, target, and supported trailing rules run in a backend worker and do
not depend on the browser remaining open.

## Features

- Incremental Nifty 500 daily candle ingestion from Fyers
- Versioned technical screening with Stage-2/VCP-style gates and scoring
- Interactive candlestick workspace powered by TradingView
  `lightweight-charts`
- Optional survivor-only company fundamentals from the read-only Upstox API
- Optional structured OpenRouter second opinion; deterministic Python scoring
  remains authoritative
- Explicit draft-and-confirm trade workflow with a durable audit trail
- Safe-by-default paper execution and separately armed live CNC execution
- One Fyers market-data WebSocket for live LTP fan-out
- One Fyers order WebSocket for order and fill correlation
- Backend position monitor for stop-loss, target, and step-percentage trailing
- Global kill switch, worker heartbeats, and broker reconciliation
- Automated trade journal, chart snapshots, P&L/R-multiple calculations, and a
  read-only AI journal coach

## Architecture at a glance

```mermaid
flowchart LR
    UI[React workstation] <-->|REST + browser WebSocket| API[FastAPI API]

    API <--> PG[(PostgreSQL)]
    API <--> R[(Redis)]

    ARQ[arq worker + scheduler] <--> PG
    ARQ <--> R
    ARQ -->|historical data + account reads| FYREST[Fyers REST]
    ARQ -->|fundamentals| UP[Upstox]
    ARQ -->|structured analysis| OR[OpenRouter]

    TICK[Tick ingestion] -->|single market-data WS| FYMD[Fyers Market WS]
    TICK -->|LTP cache + pub/sub| R

    API -->|confirmed entry| EXEC[Execution engine]
    MON[Position monitor] -->|rule-triggered exit| EXEC
    MON <--> R
    MON <--> PG
    EXEC -->|async orders; max 10 OPS| FYREST
    EXEC --> PG

    GATE[Order gateway] -->|single order WS| FYOWS[Fyers Order WS]
    GATE -->|events + fills| PG
```

The application intentionally runs as separate processes:

| Process | Responsibility |
| --- | --- |
| React client | Review scans and charts, create trade plans, explicitly confirm trades, inspect positions and journals |
| FastAPI API | Thin REST layer, browser WebSocket fan-out, validation, control updates, and job enqueueing |
| arq worker | EOD sync, screening, fundamentals, token refresh, reconciliation, journal processing, and AI jobs |
| Tick worker | The only Fyers market-data socket; publishes normalized LTP updates through Redis |
| Position monitor | Reloads non-closed positions and evaluates software stop, target, and trailing rules tick by tick |
| Order gateway | The only Fyers order socket; correlates asynchronous orders, events, trades, and fills |
| Execution engine | The only module allowed to place, modify, or cancel Fyers orders |

PostgreSQL is the system of record. Redis is used for the background queue,
pub/sub, hot LTP values, rate limiting, singleton locks, and worker status.

For the full component boundaries, state machines, and data flows, read
[architecture.md](architecture.md). The locked engineering decisions are in
[AGENTS.md](AGENTS.md), and exact tables and constraints live in
[server/db](server/db/README.md).

## Technology stack

| Layer | Technology |
| --- | --- |
| Frontend | Vite, React 19, TypeScript, React Router, TanStack Query, shadcn/ui, Tailwind CSS |
| Charts | TradingView `lightweight-charts` v5 |
| API and workers | Python 3.13, FastAPI, SQLAlchemy, asyncpg, arq |
| Data | PostgreSQL 17, Redis 7 |
| Broker and market data | Fyers API v3 |
| Fundamentals | Upstox Company Fundamentals API, read-only |
| AI annotations | OpenRouter with strict structured output |

## Repository layout

```text
.
├── client/                 # Vite + React trading workstation
│   └── src/
│       ├── components/     # Shared layout and UI primitives
│       ├── features/       # Scanner, chart, trades, positions, journal, ops
│       └── lib/            # REST and browser WebSocket clients
├── server/
│   ├── app/
│   │   ├── domain/         # Pure trading, journal, and regime calculations
│   │   ├── routers/        # Thin FastAPI HTTP and browser WS routes
│   │   ├── schemas/        # Pydantic API contracts
│   │   ├── services/       # Screening, execution, reconciliation, journal
│   │   └── workers/        # Tick, order-gateway, and position processes
│   ├── db/                 # Canonical schema and ordered SQL migrations
│   ├── scripts/            # Instrument import and operational utilities
│   ├── tests/              # Backend test suite
│   ├── main.py             # FastAPI entrypoint
│   └── run_worker.py       # arq entrypoint
├── architecture.md         # Detailed personal-system architecture
├── DEPLOY.md               # VPS / GHCR / Upstash production bring-up
├── docker-compose.yml      # Alias of local Postgres + Redis
├── docker-compose.dev.yml  # Local Postgres + Redis (start-dev.sh)
├── docker-compose.prod.yml # VPS: Postgres + api + worker + client (+ saas)
└── start-dev.sh            # Local multi-process launcher
```

Production packaging lives in each app's `Dockerfile`. See [DEPLOY.md](DEPLOY.md)
for the ARM VPS + GHCR flow.
## Quick start

### Prerequisites

- Docker with the Compose plugin
- Python 3.13 and [`uv`](https://docs.astral.sh/uv/)
- Node.js and [`pnpm`](https://pnpm.io/)
- `lsof` and `setsid` when using `start-dev.sh` (Linux or WSL is the simplest
  supported local environment)
- A Fyers API v3 application for real market data or broker integration
- Optional Upstox Analytics and OpenRouter credentials for fundamental
  annotations and AI journal analysis

### 1. Clone and install dependencies

```bash
git clone <repository-url>
cd SwingTraderVCP

cd server
uv sync --dev

cd ../client
pnpm install --frozen-lockfile

cd ..
```

### 2. Start PostgreSQL and Redis

```bash
docker compose up -d --wait
```

The development compose file exposes PostgreSQL on `localhost:5480` and Redis
on `localhost:6380`.

### 3. Initialize the database

For a new local database, apply the canonical schema from the repository root:

```bash
docker exec -i algo-trading-postgres \
  psql -v ON_ERROR_STOP=1 -U algo -d algo_trading \
  < server/db/schema.sql
```

Then import the bundled Nifty 500 instrument universe:

```bash
cd server
uv run python scripts/import_nifty500_instruments.py
cd ..
```

The import is repeatable: it updates existing instruments, creates current
memberships, and closes memberships absent from the supplied CSV.

For an existing database, apply only the unapplied SQL files in
`server/db/migrations/` in numeric order. The repository does not currently
include an automated migration runner.

### 4. Configure the backend

Create `server/.env`. The following is a safe paper-mode starting point:

```dotenv
DATABASE_URL=postgresql+asyncpg://algo:algo@localhost:5480/algo_trading
REDIS_URL=redis://localhost:6380/0
CORS_ORIGINS=["http://localhost:3000"]

FYERS_APP_ID=your_app_id
FYERS_SECRET_KEY=your_secret_key
FYERS_REDIRECT_URI=http://127.0.0.1:3000/callback

EXECUTION_MODE=paper
LIVE_ORDER_PLACEMENT_ENABLED=false

P7_FUNDAMENTAL_PASS_ENABLED=false
UPSTOX_ANALYTICS_TOKEN=
NSE_FUNDAMENTAL_RISK_ENABLED=true
OPENROUTER_API_KEY=
OPENROUTER_MODEL=openai/gpt-5.6-luna-pro
OPENROUTER_REASONING_EFFORT=medium
```

Never commit this file. Fyers tokens and provider keys must remain server-side.
The app stores Fyers access tokens encrypted in PostgreSQL and shares valid
tokens with workers through the backend token service.

The frontend defaults to `http://localhost:8000/api/v1` for REST and
`ws://localhost:8000/ws` for live prices. A different REST base can be set in
`client/.env.local`:

```dotenv
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

### 5. Start the development stack

```bash
./start-dev.sh
```

This starts the API, arq worker, tick worker, position monitor, and UI. The
order gateway starts only when live execution is double-armed.

Open:

- UI: <http://localhost:3000>
- API documentation: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health>

Runtime logs and PID files are written to `.dev-runtime/`.

```bash
./start-dev.sh status
./start-dev.sh stop
```

When `start-dev.sh` is attached to the terminal, `Ctrl-C` stops the application
processes but intentionally leaves PostgreSQL and Redis running.

## First-run workflow

1. Open the UI and complete the Fyers authorization flow.
2. Run the historical EOD sync to backfill daily candles.
3. Start a technical scan and wait for the arq worker to persist its results.
4. Review candidates in the scanner and chart workspace.
5. Optionally run the fundamental pass on selected technical survivors.
6. Create a trade instruction with quantity, entry, stop, target, and trailing
   rule.
7. Review the instruction and enter the explicit paper confirmation phrase.
8. Observe the resulting position, order intent, monitor status, and journal.

Nothing produced by screening or AI can skip step 7.

## Running processes individually

Run these from separate terminals when debugging or configuring a supervisor:

```bash
# API
cd server
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --reload

# Background jobs and schedules
cd server
uv run python run_worker.py

# Fyers market-data WebSocket -> Redis
cd server
uv run python -m app.workers.tick_worker

# Software SL/target/trailing enforcement
cd server
uv run python -m app.workers.position_monitor

# Serial P10 chart/Gemini proposal queue
cd server
uv run python -m app.workers.proposal_worker

# Approved-proposal 5-minute triggers, adds, allocation, and correction
cd server
uv run python -m app.workers.entry_supervisor

# Live mode only: Fyers order WebSocket -> order/fill ledger
cd server
uv run python -m app.workers.order_gateway

# Frontend
cd client
pnpm dev
```

Do not run multiple tick workers or order gateways against the same deployment.
The topology is intentionally one market-data socket and one order socket.

## Paper and live execution

Paper mode is the default:

```dotenv
EXECUTION_MODE=paper
LIVE_ORDER_PLACEMENT_ENABLED=false
```

Confirming a paper trade creates the full instruction, position, intent, fill,
monitoring, and journal trail without contacting the Fyers order API. Paper
entries fill at the planned price; software exits fill at the observed LTP.

Live placement requires both settings to be changed before startup:

```dotenv
EXECUTION_MODE=live
LIVE_ORDER_PLACEMENT_ENABLED=true
```

Live entry is deliberately double-armed and requires the explicit
`CONFIRM_LIVE_ORDER` phrase in the UI. The current live path is intended for
buy-side CNC entry orders. It persists an idempotent intent before calling the
Fyers asynchronous order API, relies on the order gateway for fills, and never
blind-retries an ambiguous placement.

> [!CAUTION]
> The global kill switch means **no new automated orders**. It blocks automated
> entries and exits and does not flatten existing positions. If it is enabled
> while a position is open, manage broker risk deliberately.

## Background schedules

The arq worker uses `Asia/Kolkata` by default:

| Job | Default schedule |
| --- | --- |
| Incremental EOD candle sync | Weekdays at 18:30 IST |
| Fyers token refresh | Weekdays at 08:50 IST |
| Broker reconciliation | Every 15 minutes, weekdays during configured market hours |
| Journal fill dispatcher | Every 30 seconds |

These are clock schedules and do not include an exchange holiday calendar.

## Verification

Run the backend tests:

```bash
cd server
UV_CACHE_DIR=/tmp/swingtrader-uv-cache uv run pytest -q
```

Check the frontend:

```bash
cd client
pnpm lint
pnpm build
```

Money-path changes should also be tested with broker mocks, replayed LTP
sequences, duplicate order events, kill-switch checks, and worker restart
drills. Never use meaningful live size as a substitute for a test suite.

## API overview

Business endpoints are under `/api/v1`; live browser prices use `/ws`.

| Area | Representative endpoints |
| --- | --- |
| Broker authorization | `/auth/url`, `/auth/status`, `/auth/refresh` |
| Historical data | `/historical/sync`, `/historical/status`, `/historical/candles` |
| Screening | `/screening/scan`, `/screening/runs`, `/screening/runs/{id}/results` |
| Trading | `/trading/execution-status`, `/trading/trade-instructions`, `/trading/positions`, `/trading/order-intents` |
| Operations | `/system/kill-switch`, `/system/reconciliation/run`, `/system/reconciliation/runs` |
| Journal | `/journal/entries`, `/journal/summary`, `/journal/ai/runs` |

FastAPI exposes the complete interactive OpenAPI reference at `/docs` while
the API process is running.

## Operational and security notes

- This is a single-user application and currently has no application-level
  login. Bind it to trusted interfaces and do not expose it publicly as-is.
- The included Compose services use development credentials and published
  ports. They are not a production deployment definition.
- Run the API, arq worker, tick worker, position monitor, and live order gateway
  under process supervision in any persistent environment.
- Keep PostgreSQL and Redis private, terminate TLS at a trusted edge, back up
  the database, and alert on stale worker heartbeats and critical
  `system_events`.
- PostgreSQL is the durable ledger. Redis pub/sub is ephemeral and must not be
  treated as an order or fill audit log.
- Upstox and OpenRouter failures affect annotations only; they must not pause or
  alter the Fyers money path.
- Reconciliation may heal broker events that match existing local intents. It
  flags unknown manual broker activity instead of fighting it automatically.

See [SECURITY_AUDIT.md](SECURITY_AUDIT.md) before deploying beyond a trusted
development machine.

## Contributing

Contributions should preserve the project's central safety boundaries:

- no automatic entry from scanner or AI output;
- no Fyers calls from the browser;
- one owner for each Fyers WebSocket;
- all order mutations go through the execution engine;
- persist an idempotent order intent before broker placement;
- position protection must not depend on the UI being open;
- screening and AI work belongs in the background queue.

Before opening a change, run the backend tests, frontend lint, and frontend
build. Changes to component ownership, providers, long-running services, or
money-path behavior should first update [AGENTS.md](AGENTS.md).

## License

No open-source license has been selected yet. Add a `LICENSE` file before
redistributing the project or accepting external contributions.
