# Production deploy (Step 1 — shared server)

VPS: `80.225.207.109` (ARM). Images: `ghcr.io/visheshgubrani/swingtradervcp/{server,client,swyingify}`.

Public hostnames:

- `https://app.edurel.xyz` — personal client
- `https://api.edurel.xyz` — FastAPI (+ `wss://api.edurel.xyz/ws`)

Redis is **Upstash** (`rediss://`). Postgres runs **on the VPS** via Compose. Local laptop deps stay in `docker-compose.dev.yml` (used by `./start-dev.sh`).

Host ports avoid other stacks on this VPS (open-webui `8080`, academy `3000`/`5050`, cramlify `3001`/`8001`/`5470`).

**The VPS does not need a git checkout.** It only needs `docker-compose.prod.yml` + `.env.prod`. Schema, migrations, and the Nifty 500 import script ship inside the `server` image (`/app/db`, `/app/scripts`).

## One-time VPS setup

1. Install nothing beyond Docker (already present).
2. Create a deploy directory, e.g. `/opt/swingtradervcp`, and copy into it:
   - `docker-compose.prod.yml`
   - `.env.prod` (from [`.env.prod.example`](.env.prod.example))
3. DNS **A** records for `app.edurel.xyz` and `api.edurel.xyz` → `80.225.207.109`.
4. Create an Upstash Redis database → set `REDIS_URL=rediss://default:…@….upstash.io:6379`.
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
9. Package visibility: make the three GHCR packages **public**, or set repo secret
   `GHCR_READ_TOKEN` to a PAT with `read:packages` so the VPS can pull.
10. On the VPS, ensure `.env.prod` exists before the first deploy (workflow only
    copies `docker-compose.prod.yml`). Update existing `.env.prod` to use the
    `https://app.edurel.xyz` / HTTPS CORS values from `.env.prod.example`.

## Deploy images

Push to `main` or run **Build and deploy** via `workflow_dispatch`. The arm64 runner builds and pushes to GHCR (client baked with `https://api.edurel.xyz`), then SSHs to the VPS:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
```

Step 1 services: `postgres`, `api`, `worker`, `client`. Swyingify is behind `--profile saas` (Step 2).

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

# 3) Fyers OAuth via personal client
#    Open https://app.edurel.xyz → Login Fyers

# 4) Two-year candle backfill (arq worker must be running)
curl -X POST http://127.0.0.1:8002/api/v1/historical/sync \
  -H 'Content-Type: application/json' \
  -d '{"backfill_years": 2}'

curl http://127.0.0.1:8002/api/v1/historical/status

# 5) Personal scan (also enqueues SaaS Standard)
curl -X POST http://127.0.0.1:8002/api/v1/screening/scan

# 6) Confirm SaaS board data
curl http://127.0.0.1:8002/saas/scans/minervini/standard/latest
```

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

```caddy
app.edurel.xyz {
	encode gzip
	reverse_proxy 127.0.0.1:8090
}

api.edurel.xyz {
	encode gzip
	reverse_proxy 127.0.0.1:8002
}
```

Caddy handles TLS and WebSocket upgrades for `/ws` automatically.

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
