# Production deploy (Step 1 — shared server)

VPS: `80.225.207.109` (ARM). Images: `ghcr.io/visheshgubrani/swingtradervcp/{server,client,swyingify}`.

Redis is **Upstash** (`rediss://`). Postgres runs **on the VPS** via Compose. Local laptop deps stay in `docker-compose.dev.yml` (used by `./start-dev.sh`).

Host ports avoid other stacks on this VPS (open-webui `8080`, academy `3000`/`5050`, cramlify `3001`/`8001`/`5470`).

## One-time VPS setup

1. Install nothing beyond Docker (already present).
2. Create a deploy directory, e.g. `/opt/swingtradervcp`, and copy into it:
   - `docker-compose.prod.yml`
   - `.env.prod` (from [`.env.prod.example`](.env.prod.example))
3. Create an Upstash Redis database → set `REDIS_URL=rediss://default:…@….upstash.io:6379`.
4. Set a strong `POSTGRES_PASSWORD` and Fyers credentials in `.env.prod`.
   Do **not** set `DATABASE_URL` in `.env.prod` — Compose sets `POSTGRES_HOST=postgres`
   and the apps build the URL (so passwords with `@` stay safe).
5. In the Fyers developer console, whitelist redirect URI:
   `http://80.225.207.109:8090/callback` (or your domain later).
6. GitHub repo secrets for Actions:
   - `VPS_HOST` = `80.225.207.109`
   - `VPS_USER` = SSH user
   - `VPS_SSH_KEY` = private key
   - `VPS_DEPLOY_DIR` = `/opt/swingtradervcp` (or your path)
7. Package visibility: make the three GHCR packages **public**, or set repo secret
   `GHCR_READ_TOKEN` to a PAT with `read:packages` so the VPS can pull.
8. On the VPS, ensure `.env.prod` exists before the first deploy (workflow only
   copies `docker-compose.prod.yml`).

## Deploy images

Push to `main` or run **Build and deploy** via `workflow_dispatch`. The arm64 runner builds and pushes to GHCR, then SSHs to the VPS:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
```

Step 1 services: `postgres`, `api`, `worker`, `client`. Swyingify is behind `--profile saas` (Step 2).

## First-time data bootstrap

From the VPS (or any host that can reach the published ports):

```bash
# 1) Schema (fresh DB)
./scripts/prod-migrate.sh schema
# or, if upgrading an older dump: ./scripts/prod-migrate.sh migrations

# 2) Nifty 500 instruments
docker compose -f docker-compose.prod.yml --env-file .env.prod \
  exec api python scripts/import_nifty500_instruments.py

# 3) Fyers OAuth via personal client
#    Open http://80.225.207.109:8090 → Login Fyers

# 4) Two-year candle backfill (arq worker must be running)
curl -X POST http://80.225.207.109:8002/api/v1/historical/sync \
  -H 'Content-Type: application/json' \
  -d '{"backfill_years": 2}'

curl http://80.225.207.109:8002/api/v1/historical/status

# 5) Personal scan (also enqueues SaaS Standard)
curl -X POST http://80.225.207.109:8002/api/v1/screening/scan

# 6) Confirm SaaS board data
curl http://80.225.207.109:8002/saas/scans/minervini/standard/latest
```

**Step 1 is done** when candles are filled, scans succeed, and weekday EOD (18:30 IST) keeps data fresh without your laptop.

## Ports (temporary IP exposure)

| Host port | Service |
| --- | --- |
| 8002 | FastAPI |
| 8090 | Personal client (nginx) |
| 3002 | Swyingify (profile `saas` only) |
| 127.0.0.1:5482 | Postgres (loopback only) |

Put a reverse proxy + TLS in front before a public marketing launch. Keep the personal API firewalled if possible — it has no app-level auth.

## Local development

```bash
docker compose -f docker-compose.dev.yml up -d   # or ./start-dev.sh
```
