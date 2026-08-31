# SwingTraderVCP

A single-user swing trading workstation for Indian equities (Nifty 500) built on the Fyers API. It automates technical screening, chart pattern validation, and trade plan proposals, requires an explicit human approve/reject decision, and then automates intraday entry triggers, position monitoring, risk enforcement, broker reconciliation, and trade journaling.

> [!WARNING]
> This application can place real orders when live execution is armed. It is not financial advice, and software-held stops do not provide the same execution guarantees as exchange-held stop orders. Start in paper mode, review all risk policies, and test failure and restart procedures thoroughly before committing capital.

---

## The Core Concept

SwingTraderVCP separates automated research from trading authority:

```text
[ Automated Research ]            [ Human Checkpoint ]            [ Automated Money Path ]
EOD Data -> Scanner -> AI   ->    Approve / Reject Plan   ->    Entry -> Position Monitor -> Exit
(No money authority)              (Explicit human decision)     (Deterministic execution)
```

The system follows four core rules:

1. Scanners, market context calculations, and AI models only discover setups and annotate patterns. They cannot place, size, or confirm orders.
2. The human checkpoint is strictly approve or reject. Live proposals generated from scans cannot be arbitrarily edited in the browser; approving locks a versioned trade plan and risk budget.
3. API requests never place orders inline. Approving a proposal arms the entry supervisor worker to evaluate 5-minute bar breakouts and chase limits.
4. Position management runs continuously in backend workers and does not depend on an open browser session.

---

## System Architecture

### Trading Lifecycle Flow

```text
+------------------------------------------------------------------------------------+
| 1. SCANNING & RESEARCH (Automated)                                                 |
|                                                                                    |
|  [Fyers EOD Candles] -> [P9 Market Context] -> [Technical Scan] -> [Top 20 Ranked] |
|                                                                          |         |
|                                 [Upstox / NSE Filings Fundamentals] <----+         |
+--------------------------------------------------------------------------|---------+
                                                                           v
+------------------------------------------------------------------------------------+
| 2. PROPOSAL GENERATION (Automated, Serial Worker)                                  |
|                                                                                    |
|  [Freeze EOD OHLCV] -> [Render Standardized PNG Charts (1280x720)]                 |
|                                          |                                         |
|                                          v                                         |
|  [Python Risk Engine] <---- [Gemini 3.7 Flash Pattern Read]                        |
|   - Template Selection      (Strict JSON: pivot, valid/invalid, confidence, T1-T3) |
|   - Stop & Chase Limits                                                            |
|   - Target Validation                                                              |
+--------------------------------------------------------------------------|---------+
                                                                           v
+------------------------------------------------------------------------------------+
| 3. HUMAN CHECKPOINT (Decision Required by 08:30 IST)                              |
|                                                                                    |
|  [Proposals Inbox in UI] ----> [Human Decision] ----> [REJECT: Plan Archived]      |
|                                       |                                            |
|                                       +-------------> [APPROVE: Plan Locked]       |
+--------------------------------------------------------------------------|---------+
                                                                           v
+------------------------------------------------------------------------------------+
| 4. AUTOMATED EXECUTION & MONITORING (Armed & Durable)                              |
|                                                                                    |
|  [Entry Supervisor Worker]                                                         |
|   - Evaluates completed 5-minute bars from Tick Ingestion                          |
|   - Verifies breakout-bar RVOL, next-bar acceptance, and entry chase eligibility  |
|   - Sizing and allocation based on template (single, two-leg, three-leg)           |
|           |                                                                        |
|           v                                                                        |
|  [Execution Engine] ---> [Paper Broker (Simulated fills)]                          |
|           |         ---> [Live Fyers API (Idempotent async REST, max 10 OPS)]       |
|           v                                                                        |
|  [Order Gateway]   ---> Captures fill events and updates order ledger              |
|           v                                                                        |
|  [Position Monitor] -> Evaluates SL, targets (T1/T2/T3), and trailing per tick     |
|           v                                                                        |
|  [Journal & Coach]  -> Freezes entry charts, tracks P&L/R, runs AI coach           |
+------------------------------------------------------------------------------------+
```

### Process & Component Map

```text
+-----------------------------------------------------------------------+
|                             BROWSER UI                                |
|  React 19 + TypeScript + Vite + TanStack Query + lightweight-charts   |
+-----------------------------------+-----------------------------------+
                                    | REST / Browser WebSocket
                                    v
+-----------------------------------------------------------------------+
|                           FASTAPI BACKEND                             |
|  - Proposal review & manual approval decisions                        |
|  - System controls & kill switch API                                  |
|  - Browser price fan-out via WebSockets                               |
|  - Read-only data endpoints (scanner, positions, journal, analytics)  |
+-------------------+-----------------------------------+---------------+
                    |                                   |
         Postgres SQL Reads/Writes           Redis Pub/Sub & Hot Cache
                    |                                   |
                    v                                   v
+-----------------------------------------------------------------------+
|                         BACKGROUND PROCESSES                          |
|                                                                       |
|  [Core ARQ Worker & Scheduler]                                        |
|   - EOD candle sync from Fyers                                        |
|   - P9 market regime context & sector breadth calculations            |
|   - Technical scanner runs & P7 Upstox/NSE fundamentals pass          |
|   - Daily token refresh & 15m broker reconciliation                   |
|                                                                       |
|  [Proposal Worker]                                                    |
|   - Serial worker with dedicated queue                                |
|   - Headless chart rendering (matplotlib/mplfinance Agg)              |
|   - Blind Gemini 3.7 Flash VCP pattern interpretation                 |
|   - Python deterministic proposal construction                        |
|                                                                       |
|  [Tick Ingestion Worker]                                              |
|   - Single market-data WebSocket connection to Fyers                  |
|   - Publishes live ticks to Redis and updates LTP cache               |
|   - Aggregates and persists completed 5-minute bars                   |
|                                                                       |
|  [Entry Supervisor]                                                   |
|   - Listens for completed 5-minute bar events                         |
|   - Evaluates breakout triggers against approved proposals           |
|   - Checks breakout-bar volume, reset state, chase eligibility, and add gates |
|   - Triggers order creation via Execution Engine                      |
|                                                                       |
|  [Execution Engine]                                                   |
|   - Idempotent order placement (token bucket <= 10 OPS)               |
|   - Kill switch verification before every order                       |
|   - Dispatches to Paper Broker or live Fyers async REST API           |
|                                                                       |
|  [Order Gateway]                                                      |
|   - Single order WebSocket connection to Fyers (live mode)            |
|   - Correlates order status, trades, and fill events                  |
|   - Updates DB order intents and fill ledger                          |
|                                                                       |
|  [Position Monitor Worker]                                            |
|   - Subscribes to live LTP ticks from Redis                           |
|   - Evaluates software SL, targets (T1, T2, T3), and trailing stops   |
|   - Calls Execution Engine for automated market/limit exits           |
+-----------------------------------------------------------------------+
```

---

## Process Responsibilities

| Process | Responsibility |
| --- | --- |
| **React Client** | Trading workstation for scanning, interactive charts, proposal inbox, position tables, order books, and journals. |
| **FastAPI Backend** | Thin REST API and browser WebSocket fan-out. Handles proposal approvals, risk configuration, and system control state. |
| **Core ARQ Worker** | Runs cron jobs: EOD candle sync, P9 market context, screener runs, P7 fundamentals, token refresh, and broker reconciliation. |
| **Proposal Worker** | Dedicated queue worker. Renders headless chart images, queries Gemini for pattern reads, and uses Python to construct immutable trade plans. |
| **Tick Ingestion** | The single connection to Fyers Market WebSocket. Updates Redis LTP cache and aggregates 5-minute bars. |
| **Entry Supervisor** | Monitors verified 5-minute bars for approved proposals. Evaluates breakout-bar RVOL, reset state, live price acceptance (trigger < LTP <= chase ceiling), and sizing before ordering entries. |
| **Execution Engine** | Routes orders to Paper Broker or Fyers REST. Enforces rate limits (10 OPS), idempotency checks, and the global kill switch. |
| **Order Gateway** | The single connection to Fyers Order WebSocket in live mode. Drains paper events in paper mode. Reconciles fills and order events in Postgres. |
| **Position Monitor** | Evaluates software stop-losses, partial targets (T1/T2/T3), and step-percentage trailing stops against live ticks. Dispatches exit orders. |

PostgreSQL is the durable system of record. Redis handles message queuing, pub/sub fan-out, the live price cache, and worker heartbeats.

---

## Features

- **Automated Technical Screener**: Scans the Nifty 500 for Stage-2 uptrends, Volatility Contraction Patterns (VCP), pivot tightness, and volume dry-up.
- **P9 Market Regime Context**: Computes market breadth, index moving averages, and sector rankings to dynamically adjust risk rules.
- **VCP Vision Pattern Validator**: Generates 1280x720 context and detail charts for candidate stocks and runs structured AI pattern verification (Gemini 3.7 Flash via OpenRouter).
- **Proposal Inbox & Templates**: Turns valid chart setups into immutable trade proposals with defined pivot prices, stop levels, targets, and multi-leg risk templates (Single, Two-Leg, Three-Leg Front, Three-Leg Balanced).
- **5-Minute Entry Supervisor**: Requires time-of-day-adjusted breakout-bar RVOL, validates live price acceptance (`trigger < LTP <= chase_ceiling`), and prevents execution beyond the immutable chase ceiling.
- **Sub-Second Position Monitor**: Evaluates software stop-losses, multi-tier profit targets, and step trailing rules against live tick streams without depending on open browser tabs.
- **Paper & Live Execution**: Built-in Paper Broker for zero-risk simulation and double-armed Live CNC trading with token-bucket rate limiting (10 OPS) and Fyers async order APIs.
- **P7 Company Fundamentals**: Optional survivor-only fundamental enrichment via Upstox API and official NSE corporate filings (promoter pledge and leverage checks).
- **Automated Trade Journal & AI Coach**: Automatically records trade fills, captures entry/exit charts, computes P&L and R-multiples, and provides structured post-trade AI coaching.
- **Global Kill Switch & Ops Controls**: Instant emergency breaker that pauses all automated entries and exits while leaving open positions intact for manual management.

---

## Technology Stack

| Layer | Technology |
| --- | --- |
| **Frontend** | React 19, TypeScript, Vite, TanStack Query, React Router, Tailwind CSS, shadcn/ui |
| **Charting** | TradingView `lightweight-charts` v5 (Presentational only) |
| **Server-Side Charts** | `matplotlib` and `mplfinance` (Agg headless rendering for proposals) |
| **Backend API** | Python 3.13, FastAPI, SQLAlchemy (asyncio), Pydantic v2 |
| **Databases & Cache** | PostgreSQL 17, Redis 7 (arq queue, pub/sub, hot cache) |
| **Broker Integration** | Fyers API v3 (REST + Market Data WebSocket + Order WebSocket) |
| **Fundamentals** | Upstox Company Fundamentals API + official NSE corporate filings |
| **AI Models** | OpenRouter (Gemini 3.7 Flash for VCP vision, GPT-5.6 / DeepSeek for fundamentals and journal coaching) |

---

## Dual Product Layout

This repository contains two products that share core backend scanning tools:

| Product | Directory | Purpose | Orders / Money Path |
| --- | --- | --- | --- |
| **SwingTraderVCP (Personal)** | `client/` + `server/` | Single-user swing trading workstation | **Yes** (Fyers live + paper execution) |
| **Swyingify (SaaS)** | `swyingify/` | Multi-tenant screening platform | **No** (Watchlists and scans only) |

---

## Repository Layout

```text
.
├── client/                 # Personal React trading workstation (Vite + TS)
│   └── src/
│       ├── components/     # UI primitives and shared layout
│       ├── features/       # Screener, chart, proposals, trades, positions, journal
│       └── lib/            # REST and WebSocket client connectors
├── server/                 # Python backend and workers
│   ├── app/
│   │   ├── domain/         # Pure trading math, risk calculations, and state machines
│   │   ├── routers/        # FastAPI REST endpoints and browser WS handlers
│   │   ├── schemas/        # Pydantic data contracts
│   │   ├── services/       # Screener, proposal, execution, reconciliation services
│   │   └── workers/        # Tick ingestion, proposal, entry supervisor, monitor, gateway
│   ├── db/                 # Postgres schema and numbered SQL migrations
│   ├── scripts/            # Database import tools and replay scripts
│   ├── tests/              # Backend test suite (pytest)
│   ├── main.py             # FastAPI entrypoint
│   └── run_worker.py       # Core arq background worker entrypoint
├── swyingify/              # Multi-tenant Next.js SaaS scanner application
├── architecture.md         # Detailed architectural documentation
├── AGENTS.md               # Source of truth for personal system boundaries
├── DEPLOY.md               # Production deployment guide (ARM VPS + Docker Compose)
├── docker-compose.dev.yml  # Local development Postgres and Redis containers
├── docker-compose.prod.yml # Production multi-container definition
└── start-dev.sh            # Local development process supervisor script
```

---

## Quick Start

### Prerequisites

- Linux or macOS (or WSL2 on Windows)
- Docker with Docker Compose plugin
- Python 3.13 and [`uv`](https://docs.astral.sh/uv/)
- Node.js (v20+) and [`pnpm`](https://pnpm.io/)
- Fyers API v3 developer account credentials
- Optional: Upstox developer token and OpenRouter API key

---

### Step 1: Clone and Install Dependencies

```bash
git clone https://github.com/visheshgubrani/SwingTraderVCP.git
cd SwingTraderVCP

# Install server dependencies
cd server
uv sync --dev

# Install client dependencies
cd ../client
pnpm install --frozen-lockfile

cd ..
```

---

### Step 2: Start Postgres and Redis

Start local database containers in the background:

```bash
docker compose -f docker-compose.dev.yml up -d --wait
```

- PostgreSQL runs on `localhost:5480`
- Redis runs on `127.0.0.1:6380`

---

### Step 3: Initialize the Database

Apply the base schema to the database container:

```bash
docker exec -i algo-trading-postgres \
  psql -v ON_ERROR_STOP=1 -U algo -d algo_trading \
  < server/db/schema.sql
```

Import the Nifty 500 instrument master list:

```bash
cd server
uv run python scripts/import_nifty500_instruments.py
cd ..
```

---

### Step 4: Configure Environment Variables

Create `server/.env`:

```dotenv
APP_ENVIRONMENT=development
DATABASE_URL=postgresql+asyncpg://algo:algo@localhost:5480/algo_trading
REDIS_URL=redis://localhost:6380/0
CORS_ORIGINS=["http://localhost:5173","http://localhost:3000"]
APP_PASSWORD=dev_swing_password_2026

# Fyers API Configuration
FYERS_APP_ID=your_fyers_app_id
FYERS_SECRET_KEY=your_fyers_secret_key
FYERS_REDIRECT_URI=http://127.0.0.1:3000/callback

# Execution Safety Controls (Default: Safe Paper Trading)
EXECUTION_MODE=paper
LIVE_ORDER_PLACEMENT_ENABLED=false

# VCP Vision AI Proposal Engine
VCP_VISION_ENABLED=true
OPENROUTER_API_KEY=your_openrouter_key
VCP_VISION_MODEL=google/gemini-3.7-flash
VCP_VISION_REASONING_EFFORT=medium
PROPOSAL_AUTOMATION_ENABLED=true

# P7 Fundamentals & NSE Risk Enrichment
P7_FUNDAMENTAL_PASS_ENABLED=false
UPSTOX_ANALYTICS_TOKEN=
NSE_FUNDAMENTAL_RISK_ENABLED=true
OPENROUTER_MODEL=openai/gpt-5.6-luna-pro
```

Create `client/.env.local` (optional if using defaults):

```dotenv
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

---

### Step 5: Start the Development Stack

Launch all services with a single command:

```bash
./start-dev.sh
```

This starts:
- FastAPI backend on `http://localhost:8000`
- React UI on `http://localhost:5173`
- Background workers (ARQ, Tick worker, Position monitor, Proposal worker, Entry supervisor)

Useful commands:
```bash
./start-dev.sh status   # Check status of running processes
./start-dev.sh stop     # Stop all application processes
```

Interactive API documentation is accessible at `http://localhost:8000/docs`.

---

## Running Individual Processes

For debugging specific workers, launch them in dedicated terminals:

```bash
# 1. FastAPI API server
cd server
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --reload

# 2. Core ARQ scheduler & background jobs
cd server
uv run python run_worker.py

# 3. Fyers Market-Data WebSocket -> Redis LTP worker
cd server
uv run python -m app.workers.tick_worker

# 4. Software Stop-Loss and Trailing Position Monitor
cd server
uv run python -m app.workers.position_monitor

# 5. Serial Proposal Generator (Charts + Gemini Vision)
cd server
uv run python -m app.workers.proposal_worker

# 6. 5-Minute Entry Supervisor
cd server
uv run python -m app.workers.entry_supervisor

# 7. Live Order Gateway (Live mode only)
cd server
uv run python -m app.workers.order_gateway

# 8. React UI
cd client
pnpm dev
```

---

## Daily Operating Routine

```text
Time (IST)   Phase             Actions & Process
-----------------------------------------------------------------------------------------
08:50        Pre-Market        - ARQ worker runs token refresh
                               - System checks Fyers authorization status

09:15-15:30  Market Hours      - Tick Ingestion updates live prices and 5m bars
                               - Entry Supervisor watches approved proposals for triggers
                               - Position Monitor tracks active positions tick by tick
                               - Reconciliation runs every 15 minutes to verify broker state

15:40-18:30  Post-Market       - Journal dispatcher builds closed trade logs
                               - AI Coach runs async reviews on completed trades

18:30        EOD Sync & Scan   - ARQ worker fetches daily candles for Nifty 500
                               - P9 computes market regime and sector breadths
                               - Screener filters Stage-2 and VCP candidate shortlist

19:00-20:00  Proposals         - Proposal worker generates charts and Gemini pattern reads
                               - Python risk engine builds immutable trade plans

By 08:30     Human Review      - Trader opens Proposals Inbox in UI
(Next Day)                     - Reviews chart geometry, risk stops, and profit targets
                               - Explicitly clicks Approve or Reject on each proposal
```

---

## Execution Modes & Safety Controls

### Paper Trading Mode (Default)

```dotenv
EXECUTION_MODE=paper
LIVE_ORDER_PLACEMENT_ENABLED=false
```

In paper mode:
- Approved proposal triggers simulate instant fills via the internal Paper Broker.
- Software stops and targets trigger simulated exits at current market LTP.
- No network requests are sent to Fyers order endpoints.
- Full ledger, fill history, and journal trails are generated for inspection.

### Live Trading Mode (Double-Armed)

```dotenv
EXECUTION_MODE=live
LIVE_ORDER_PLACEMENT_ENABLED=true
```

Live execution requires both environment variables to be active:
- Orders use Fyers asynchronous REST APIs for buy-side CNC equity swing trades.
- Every order persists an idempotent intent key before dispatch.
- Rate limiting enforces a strict 10 orders per second limit.
- Live order events and trade fills are captured via the dedicated Order Gateway WebSocket.

### Global Kill Switch

The global kill switch provides an emergency stop:
- When activated, all automated entries and exits are immediately blocked.
- It does not dump or market-flatten existing positions. Open positions must be managed manually via the Fyers terminal or disarmed once safe.

---

## Background Job Schedules

Scheduled jobs run via the ARQ worker in the `Asia/Kolkata` time zone:

| Job | Schedule | Purpose |
| --- | --- | --- |
| **Token Refresh** | Weekdays at 08:50 IST | Verifies and refreshes Fyers access tokens before market open. |
| **Broker Reconciliation** | Every 15 minutes (09:00 to 16:00 IST) | Compares local DB positions and orders with Fyers broker books. |
| **Journal Fill Dispatcher** | Every 30 seconds | Ingests new order fills into the trade journal ledger. |
| **EOD Candle Sync** | Weekdays at 18:30 IST | Fetches daily OHLCV candles for all Nifty 500 stocks. |
| **Market Regime & Scan** | Follows EOD Sync | Computes P9 market regime metrics and runs technical screener. |

---

## Verification & Testing

Run backend tests:

```bash
cd server
uv run pytest -q
```

Run frontend linting and production build check:

```bash
cd client
pnpm lint
pnpm build
```

---

## Production Deployment

Production runs on an ARM64 Linux VPS using Docker Compose with Caddy as the reverse proxy. Container images are built and pushed via GitHub Actions to GitHub Container Registry (GHCR).

Refer to [DEPLOY.md](DEPLOY.md) for full deployment and secret management instructions.

---

## License

All rights reserved. Private trading software for personal use.
