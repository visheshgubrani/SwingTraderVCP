# Production deploy (Step 1 — shared server)

VPS: `80.225.207.109` (ARM). Images: `ghcr.io/visheshgubrani/swingtradervcp/{server,client,swyingify}`.

Public hostnames:

- `https://app.edurel.xyz` — personal client
- `https://api.edurel.xyz` — FastAPI (+ `wss://api.edurel.xyz/ws`)

Redis runs **on the VPS** in Compose (internal network, password, AOF). Postgres is also on the VPS. Local laptop deps stay in `docker-compose.dev.yml` (used by `./start-dev.sh`).

Host ports avoid other stacks on this VPS (open-webui `8080`, academy `3000`/`5050`, cramlify `3001`/`8001`/`5470`).

**The VPS does not need a git checkout.** It only needs `docker-compose.prod.yml` + `.env.prod`. Schema, migrations, and the Nifty 500 import script ship inside the `server` image (`/app/db`, `/app/scripts`).

## One-time VPS setup

1. Install nothing beyond Docker (already present).
2. Create a deploy directory, e.g. `/opt/swingtradervcp`, and copy into it:
   - `docker-compose.prod.yml`
   - `.env.prod` (from [`.env.prod.example`](.env.prod.example))
3. DNS **A** records for `app.edurel.xyz` and `api.edurel.xyz` → `80.225.207.109`.
4. Set a strong `REDIS_PASSWORD` in `.env.prod`. Do **not** set `REDIS_URL` —
   Compose sets `REDIS_HOST=redis` and the apps build the URL (so passwords
   with `@` stay safe). Redis is not published on a host port.
   Cutover from Upstash is not a data migration: app sessions must be
   re-created (log in again) and any in-flight arq jobs on Upstash are abandoned.
5. Set a strong `POSTGRES_PASSWORD` and Fyers credentials in `.env.prod`.
   Do **not** set `DATABASE_URL` in `.env.prod` — Compose sets `POSTGRES_HOST=postgres`
   and the apps build the URL (so passwords with `@` stay safe).
6. In the Fyers developer console, whitelist redirect URI:
   `https://app.edurel.xyz/callback`
7. Add Caddy site blocks from [deploy/Caddyfile.example](deploy/Caddyfile.example), then reload Caddy.
8. GitHub repo secrets for Actions:
   - `VPS_HOST` = `80.225.207.109`
   - `VPS_USER` = SSH user
   - `VPS_SSH_KEY` = private key
   - `VPS_DEPLOY_DIR` = `/opt/swingtradervcp` (or your path)
9. Keep the three GHCR packages **private**. Set repo secret `GHCR_READ_TOKEN`
   to a PAT with `read:packages` so the VPS can pull. Do not make the server
   image public.
10. On the VPS, ensure `.env.prod` exists before the first deploy (workflow only
    copies `docker-compose.prod.yml`). Update existing `.env.prod` to use the
    `https://app.edurel.xyz` / HTTPS CORS values from `.env.prod.example`.

## Deploy images

Push to `main` or run **Build and deploy** via `workflow_dispatch`. The arm64 runner builds and pushes to GHCR (client baked with `https://api.edurel.xyz`), then SSHs to the VPS:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
```

Step 1 services: `postgres`, `redis`, `api`, `worker`, `proposal-worker`, `tick-worker`,
`entry-supervisor`, `position-monitor`, `order-gateway`, `client`.
Swyingify is behind `--profile saas` (Step 2). `docker compose up -d` starts
the full personal money path.

## First-time data bootstrap (on the VPS, no repo)

SSH into the VPS and `cd` to the deploy dir (e.g. `/opt/swingtradervcp`). All SQL and import code comes from the running `api` container.

```bash
cd /opt/swingtradervcp   # or your VPS_DEPLOY_DIR
COMPOSE=(docker compose -f docker-compose.prod.yml --env-file .env.prod)

# Wait until api is healthy
"${COMPOSE[@]}" ps

# 1) Fresh DB — apply full schema from the image
"${COMPOSE[@]}" exec -T api cat db/schema.sql \
  | "${COMPOSE[@]}" exec -T postgres \
      psql -v ON_ERROR_STOP=1 -U algo -d algo_trading

# (Only if upgrading an older DB instead of a fresh volume — apply migrations in order)
# for f in $("${COMPOSE[@]}" exec -T api sh -c 'ls db/migrations/*.sql | sort'); do
#   echo "→ $f"
#   "${COMPOSE[@]}" exec -T api cat "$f" \
#     | "${COMPOSE[@]}" exec -T postgres \
#         psql -v ON_ERROR_STOP=1 -U algo -d algo_trading
# done

# 2) Nifty 500 instruments (CSV is baked into the image)
"${COMPOSE[@]}" exec api python scripts/import_nifty500_instruments.py

# 2b) Ensure RS / regime index symbols exist (required for vcp_score_v3).
# Fresh schema.sql now seeds these; this is safe to re-run on older DBs.
"${COMPOSE[@]}" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U algo -d algo_trading <<'SQL'
INSERT INTO instruments (
    exchange, segment, symbol, trading_symbol, fyers_symbol, name,
    lot_size, tick_size, active, metadata
) VALUES
    (
        'NSE', 'INDEX', 'NIFTY50', 'NIFTY50-INDEX', 'NSE:NIFTY50-INDEX',
        'Nifty 50 Index', 1, 0.05, true,
        '{"role": "benchmark", "p8_regime": true}'::jsonb
    ),
    (
        'NSE', 'INDEX', 'NIFTY500', 'NIFTY500-INDEX', 'NSE:NIFTY500-INDEX',
        'Nifty 500 Index', 1, 0.05, true,
        '{"role": "rs_benchmark", "pipeline": "vcp_score_v3"}'::jsonb
    )
ON CONFLICT (fyers_symbol) DO NOTHING;
SQL

# 2c) Validate every P9 index against the current FYERS NSE symbol master.
# Download the current CSV from FYERS Symbol Master Documentation, copy it into
# the API container, then run this. Enforcement is rejected until it succeeds.
"${COMPOSE[@]}" exec api python scripts/validate_p9_fyers_symbols.py \
  --master /app/private/NSE_symbol_master.csv

# 3) Fyers OAuth via personal client
#    Open https://app.edurel.xyz → Login Fyers

# 4) P9 replay requires 2018-present daily history for the three broad indices,
# all 16 sector indices, and point-in-time Nifty 500 constituents when available.
# A shorter normal sync is not sufficient for rollout sign-off.
curl -X POST http://127.0.0.1:8002/api/v1/historical/sync \
  -H 'Content-Type: application/json' \
  -d '{"backfill_years": 10}'

curl http://127.0.0.1:8002/api/v1/historical/status

# If status reports scanner_ready=false, fill missing historical prefixes.
# This is additive/upsert-only and also catches up any latest-date suffixes.
curl -X POST http://127.0.0.1:8002/api/v1/historical/sync \
  -H 'Content-Type: application/json' \
  -d '{"backfill_years":2,"repair_history":true}'

# 5) Personal scan (also enqueues SaaS Standard)
curl -X POST http://127.0.0.1:8002/api/v1/screening/scan

# 6) Confirm SaaS board data
curl http://127.0.0.1:8002/saas/scans/minervini/standard/latest

# 7) Generate the review-only replay report. It rolls back synthetic snapshots.
"${COMPOSE[@]}" exec api python scripts/replay_p9_market_context.py \
  --start 2018-01-01 --end "$(date +%F)" --output /tmp/p9-replay.json

# Review 2018/2020/2022, sector formula comparison, failures, and membership
# warning. Record the printed SHA-256 through the personal Operations UI only
# after owner sign-off. P9 starts shadow; no deployment stage self-promotes.
```

## P10 Shadow → Paper (₹1,00,000)

Keep `EXECUTION_MODE=paper` and `LIVE_ORDER_PLACEMENT_ENABLED=false`. Paper
uses Fyers for market data and daily login only. It must never call Fyers
`/funds` or `/orders/async`.

1. Apply `server/db/migrations/019_p10_paper_broker.sql` (or start from current
   `schema.sql`). Confirm the active Balanced policy has
   `deployable_capital_override = 100000`.
2. Set `PROPOSAL_AUTOMATION_ENABLED=true` when you want nightly proposal
   generation. P10 still starts at **Shadow**: review and reject are allowed;
   approve is a 409 until promotion.
3. Complete Fyers login so ticks and EOD history work.
4. Confirm `order-gateway` is running (Compose runs it without a live profile).
   Paper mode drains Redis `paper_order_events`; it does not open a Fyers
   order WebSocket.
5. On the personal dashboard, promote Shadow → Paper with
   `CONFIRM_P10_PAPER`, operator name, and reason. That seeds the fake ledger
   at ₹1,00,000. Account Ledger then shows cash, invested notional, and R stats.
6. Approve a live-eligible proposal only after that promotion. Entry, SL, T1–T3,
   journal, kill switch, and the three-stop breaker use the same processors as
   live against the paper books.

Reduced live is **not** flipping two env flags. It requires an owner-approved
P9 replay-report hash on an enforced policy, empty paper books, then
`CONFIRM_P10_REDUCED_LIVE` with `EXECUTION_MODE=live` and
`LIVE_ORDER_PLACEMENT_ENABLED=true`.

Before reduced-live P10, run paper restart/duplicate-fill/concurrent-close
drills until the paper three-stop breaker trips at exactly three qualifying
closures and its owner reset is verified not to clear a manual pause. Reduced
live remains blocked until P9 is enforced with its signed report hash and both
P9/breaker readiness have explicit owner approval.

If `POSTGRES_USER` / `POSTGRES_DB` in `.env.prod` are not `algo` / `algo_trading`, change the `psql -U … -d …` flags to match.

**Step 1 is done** when candles are filled, scans succeed, and weekday EOD (18:30 IST) keeps data fresh without your laptop.

## Ports (behind Caddy — loopback only)

| Host bind | Service |
| --- | --- |
| `127.0.0.1:8002` | FastAPI (`https://api.edurel.xyz`) |
| `127.0.0.1:8090` | Personal client (`https://app.edurel.xyz`) |
| `127.0.0.1:3002` | Swyingify later (`--profile saas`) |
| `127.0.0.1:5482` | Postgres |

### Caddy

Use the security-header snippet in [deploy/Caddyfile.example](deploy/Caddyfile.example)
(HSTS, nosniff, frame deny). Caddy handles TLS and WebSocket upgrades for `/ws`
automatically.

Server `.env.prod` (already in [`.env.prod.example`](.env.prod.example)):

- `FRONTEND_PUBLIC_URL=https://app.edurel.xyz`
- `FYERS_REDIRECT_URI=https://app.edurel.xyz/callback`
- `CORS_ORIGINS=["https://app.edurel.xyz"]`

Client image bake-in (CI):

- `VITE_API_BASE_URL=https://api.edurel.xyz/api/v1`
- `VITE_WS_URL=wss://api.edurel.xyz/ws`

## Local development

```bash
docker compose -f docker-compose.dev.yml up -d   # or ./start-dev.sh
```
