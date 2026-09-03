# SwingTraderVCP Client

The web frontend for the SwingTraderVCP trading workstation, built as the **VCP Trader Core Terminal**
(design reference: `docs/design/vcp-trader-dashboard.html`). React 19, TypeScript, Vite, Tailwind CSS v4,
shadcn/ui, TanStack Query, and TradingView `lightweight-charts` v5.

## Key Features

- **Terminal frame**: top bar with global instrument search, scrolling ticker tape, left module rail,
  persistent **watchlist** sidebar (hearts in the scanner/search add/remove symbols; the backend keeps
  watchlist state in Postgres and the tick worker quotes active lists), NSE markets footer.
- **Chart module** (`/`): quote bar with live LTP/OHLC via the market WebSocket, timeframe × SMA
  20/50/200 × VOL toolbar, lightweight-charts with drawing tools + VCP vision overlays, and a
  traceable order ticket (manual instruction draft → review → confirm, paper/log only per AGENTS.md).
- **Scanner** (`/scanner`): VCP scoreboard over real EOD scan runs with filters, run history,
  VCP-vision analysis and proposal-generation row actions.
- **Positions / Order Intents / Tradebook** modules bound to `/trading/positions`,
  `/trading/order-intents` and journal closed trades.
- **Proposals**: P10 inbox, immutable proposal review/approval (hash-checked), attempt audits,
  capacity conflicts, forming patterns, P9 market context, paper ledger.
- **Fundamentals / Journal / Operations**: P7 manual review, journal + AI coach, EOD sync/operations.

## Local Development

### Prerequisites

- Node.js (v20 or higher)
- pnpm (v9 or higher)

### Setup

```bash
# Install dependencies
pnpm install --frozen-lockfile

# Start development server
pnpm dev
```

The frontend runs by default on `http://localhost:5173`.

### Environment Configuration

Create `client/.env.local` to customize the API base URL if not using defaults:

```dotenv
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

### Scripts

```bash
pnpm dev       # Start local Vite development server
pnpm build     # Type-check with TypeScript and build production bundle
pnpm lint      # Run ESLint / Oxlint checks
pnpm preview   # Preview production build locally
```
