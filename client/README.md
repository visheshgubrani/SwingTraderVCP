# SwingTraderVCP Client

The web frontend for the SwingTraderVCP trading workstation. Built with React 19, TypeScript, Vite, Tailwind CSS, shadcn/ui, TanStack Query, and TradingView `lightweight-charts`.

## Key Features

- **Dashboard**: Overview of current market status, active positions, open orders, and recent screening results.
- **Screener Workspace**: Stage-2 and VCP scan shortlists with interactive filtering, score breakdown, and VCP vision sheets.
- **Trading Chart**: High-performance candlestick charting powered by lightweight-charts v5 with EMA overlays, volume bars, and drawing tools.
- **Proposals Inbox**: Review automated trade proposals generated from shortlisted patterns. Inspect pivot levels, risk limits, chase ceilings, and multi-leg risk templates before issuing explicit approvals or rejections.
- **Positions & Orders**: Real-time position tracking with software stop-loss levels, profit targets (T1/T2/T3), step trailing indicators, and live order book synchronization.
- **Paper & Live Trading**: Safe paper mode simulation and double-armed live CNC execution controls.
- **Trade Journal**: Automated fill logging, entry/exit chart capture, P&L calculations, and structured AI coaching reviews.

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
