# SwingTraderVCP

An automated swing trading workstation for Indian equities (Nifty 500) combining algorithmic screening, AI-powered pattern validation, and human-in-the-loop execution via the Fyers API.

![SwingTraderVCP Dashboard](docs/screenshots/dashboard.png)

---

## Overview

**SwingTraderVCP** is built for swing traders following Mark Minervini's **Volatility Contraction Pattern (VCP)** and Stage-2 breakout strategies. 

It bridges the gap between algorithmic automation and human judgment with a strict hybrid workflow:

```text
[ Automated Research ]            [ Human Checkpoint ]            [ Automated Execution ]
EOD Data -> Scan -> AI Audit  ->    Approve / Reject Plan   ->    Breakout Entry -> Trailing SL -> Exit
(Zero order authority)            (You retain final decision)     (Deterministic sub-second management)
```

1. **Automated Discovery & AI Audit**: The system scans the Nifty 500 at End-of-Day (EOD), identifies tightening consolidation bases, renders standardized charts, and runs multimodal AI (Gemini Vision) to audit contraction geometry.
2. **Human Approval Checkpoint**: Valid setups become immutable trade plans (entry trigger, stop-loss, targets, risk budget). You review the chart and proposal in the UI before market open and explicitly click **Approve** or **Reject**.
3. **Automated Execution & Management**: Once approved, backend workers monitor 5-minute bars for breakout confirmation and relative volume (RVOL), execute orders via the Fyers API, and manage trailing stops and profit targets tick by tick.

---

## Key Features

- 🔍 **Nifty 500 Technical Screener**: Scans 500 stocks for Stage-2 uptrend criteria, multi-contraction VCPs, pivot tightness, and volume dry-ups.
- 🤖 **Multimodal AI Pattern Audit**: Renders headless charts and uses Gemini 3.7 Flash to audit base symmetry, contraction depths, and volume signatures.
- 🖥️ **Interactive Workstation UI**: Fast, responsive dark-mode interface built with React and TradingView `lightweight-charts`, featuring real-time index breadth and live price streaming.
- 🎯 **Proposal Inbox & Risk Templates**: Automatically calculates exact pivot prices, stop levels, and multi-tier profit targets (T1, T2, T3) with customizable risk-budget allocation.
- ⏱️ **5-Minute Entry Supervisor**: Validates breakout-bar RVOL, candle close acceptance, and enforces a strict chase ceiling to prevent bad fills.
- 🛡️ **Tick-by-Tick Position Monitor**: Runs continuously in the background to enforce software stop-losses and step-trailing rules without requiring an open browser tab.
- 🧪 **Paper Trading & Live Execution**: Includes a built-in Paper Broker for realistic simulated execution, plus double-armed live CNC swing trading with rate limiting and an emergency **Global Kill Switch**.
- 📓 **Automated Trade Journal**: Logs executions, captures entry and exit charts, tracks P&L and R-multiples, and provides structured post-trade analytics.

---

## Daily Operating Workflow

```text
 18:30 IST       EOD Candle Sync & Technical Screen across Nifty 500
    │
 19:00 IST       AI Vision Audit & Immutable Trade Plan Generation
    │
 08:30 IST       Trader Reviews Proposal Inbox in UI -> Clicks [Approve] or [Reject]
    │
 09:15-15:30 IST Market Hours: Entry Supervisor watches 5m bars & Position Monitor tracks exits
    │
 15:40 IST       Post-Market: Trade Journal compiles fills and calculates R-multiples
```

---

## Tech Stack

| Layer | Technology |
| --- | --- |
| **Frontend** | React 19, TypeScript, Vite, Tailwind CSS, shadcn/ui, TanStack Query |
| **Charting** | TradingView `lightweight-charts` v5 (interactive), `mplfinance` (headless AI charts) |
| **Backend API** | FastAPI (Python 3.13), SQLAlchemy (asyncio), Pydantic v2 |
| **Database & Cache** | PostgreSQL 17, Redis 7 (`arq` background queue & pub/sub) |
| **Market Data & Broker** | Fyers API v3 (REST + Market Data WebSocket + Order WebSocket) |
| **AI / Vision** | Google Gemini 3.7 Flash via OpenRouter |

---

## Quick Start

### Prerequisites

- Linux, macOS, or WSL2 on Windows
- Docker with Docker Compose
- Python 3.13 and [`uv`](https://docs.astral.sh/uv/)
- Node.js (v20+) and [`pnpm`](https://pnpm.io/)
- A Fyers API v3 developer account

### 1. Clone & Setup

```bash
git clone https://github.com/visheshgubrani/SwingTraderVCP.git
cd SwingTraderVCP

# Install backend dependencies
cd server && uv sync --dev && cd ..

# Install frontend dependencies
cd client && pnpm install && cd ..
```

### 2. Start Local Databases

```bash
docker compose -f docker-compose.dev.yml up -d --wait
```
- PostgreSQL runs on `localhost:5480`
- Redis runs on `127.0.0.1:6380`

### 3. Initialize Database & Instruments

```bash
# Apply schema
docker exec -i algo-trading-postgres psql -U algo -d algo_trading < server/db/schema.sql

# Import Nifty 500 stock universe
cd server
uv run python scripts/import_nifty500_instruments.py
cd ..
```

### 4. Configure Environment

Copy and configure your environment files:

```bash
cp server/.env.example server/.env
```

Key variables in `server/.env`:
```dotenv
APP_ENVIRONMENT=development
DATABASE_URL=postgresql+asyncpg://algo:algo@localhost:5480/algo_trading
REDIS_URL=redis://localhost:6380/0

# Fyers Credentials
FYERS_APP_ID=your_app_id
FYERS_SECRET_KEY=your_secret_key
FYERS_REDIRECT_URI=http://127.0.0.1:3000/callback

# Safe Paper Trading by default
EXECUTION_MODE=paper
LIVE_ORDER_PLACEMENT_ENABLED=false

# AI Pattern Vision
VCP_VISION_ENABLED=true
OPENROUTER_API_KEY=your_openrouter_key
```

### 5. Launch the Stack

Start all services (API, frontend, and background workers) with one command:

```bash
./start-dev.sh
```

- **Frontend UI**: [http://localhost:5173](http://localhost:5173)
- **FastAPI Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)

To view process status or stop:
```bash
./start-dev.sh status
./start-dev.sh stop
```

---

## Safety & Risk Controls

- **Safe Paper Mode by Default**: New setups run in simulated paper execution (`EXECUTION_MODE=paper`). Orders are filled virtually with real tick prices.
- **Double-Armed Live Trading**: Live order placement requires both `EXECUTION_MODE=live` and `LIVE_ORDER_PLACEMENT_ENABLED=true`.
- **Global Kill Switch**: Accessible instantly from the UI or API to halt all automated entries and exits while keeping existing positions safe for manual management.
- **No Inline Orders**: Web requests never place orders directly; all trades flow through durable, idempotent background supervisor workers.

---

## License & Disclaimer

This project is for personal use and educational purposes. It is not financial advice. Automated trading involves substantial risk of financial loss. Always test thoroughly in paper trading mode before committing capital.
