# SwingTraderVCP Personal App Security Audit

> Audit date: 2026-08-16
>
> Previous audit: 2026-07-31 (`SECURITY_AUDIT.md` at that date, baseline `main`
> `e520307`)
>
> Scope: **personal trading app only** (`client/`, `server/`, Compose, Caddy).
> Swyingify / SaaS is out of scope except where it shares Postgres/Redis with
> the money path.
>
> Audit type: source review of the current tree including uncommitted P10 paper
> broker / rollout work; no penetration test and no live broker order was placed

## 1. Executive assessment

The safety architecture is still the right one: human approve/reject, a single
execution engine, intents before broker calls, paper vs live books, kill
switch fail-closed, and Postgres as recovery source.

The **threat model has changed**. The July audit treated the app as
local/private paper mode. It is now deployed on a public VPS:

- `https://app.edurel.xyz` — personal client
- `https://api.edurel.xyz` — FastAPI, including `wss://api.edurel.xyz/ws`

There is still **no application authentication**. CORS is not authorization.
Any client that can reach FastAPI can read the ledger and mutate the money-path
control plane: proposal approve/reject, P10 stage promotion, kill switch, risk
policy, paper-account reset, stop-streak reset, token refresh, and
reconciliation.

Paper mode does **not** make that safe. Paper still performs a real Fyers
OAuth login so the tick worker can receive market data. SQLAlchemy `echo=True`
can log those access/refresh tokens and the encryption key. An attacker who
reads container logs, or who completes OAuth against this API, can use the
Fyers account **outside** this app even while `EXECUTION_MODE=paper`.

`architecture.md` already states the personal app has no end-user
authentication and should remain on a trusted private interface until an
access-control layer exists. The current Caddy publish of `api.edurel.xyz`
contradicts that.

**Paper-trading decision:** do not start the §12.2 fifty-proposal paper gate
on the public API until either (a) single-user app auth is in place, or (b)
`api.edurel.xyz` is taken off the public internet (Caddy IP allowlist / VPN /
reverse-proxy basic auth). Also disable SQL echo before the next Fyers login
on that host, and rotate tokens if production logs already contain bind
parameters.

**Live-readiness decision:** unchanged from July, and stricter: do not enable
live order placement until the paper gate, P9 enforcement, **0.25× reduced-live
sizing (P10-006, currently unimplemented)**, and the remaining critical/high
findings in this file are closed.

## 2. What changed since 2026-07-31

Implemented since the previous audit (relevant to security):

- P9 deterministic market context and owner-gated shadow → enforced
- P10 proposal generation, immutable approval, entry supervisor, staged exits
- Durable paper broker (`paper_broker_*`) with a mutating cash ledger
- Owner-gated rollout `shadow → paper → reduced_live → full_live`
- Production Compose: API, core worker, proposal worker, tick, entry
  supervisor, position monitor, order gateway, client
- Upstash Redis over `rediss://`; Postgres bound to `127.0.0.1:5482`
- Shared Redis URL parser that preserves TLS and auth (`redis_pool.py`)
- Order-gateway Redis singleton lease
- Exit-fill accounting now applies the **new** fill quantity, not the
  cumulative total (TRD-001)
- Reconciliation positions/intents are filtered by `execution_mode`
  (REC-001)
- OAuth GET callback uses `FRONTEND_PUBLIC_URL` instead of hardcoded
  localhost
- P10 ATR trail (`p10_staged_atr`) is implemented; the old unused `atr`
  trailing type is still accepted on the manual trade form

Still open from July, and in several cases **worse** because the API is
public: SEC-001, SEC-002, SEC-003, SEC-004, AUTH-001, AUTH-002, AUTH-003,
TRD-002, MD-001–004, OG-001–002, most REC/JRN/OPS/DEP items.

## 3. Scope and methodology

Reviewed:

- `AGENTS.md` boundaries vs current routers and workers
- FastAPI REST + browser WebSocket (no auth dependency anywhere on `/api/v1`)
- Fyers OAuth, token encryption, refresh, Redis cache
- P10 approval, rollout, risk policy, automation controls, paper ledger
- Execution engine paper vs live branching and kill switch
- Paper broker (no Fyers `/funds` or `/orders/async`)
- Tick worker, order gateway, position monitor, entry supervisor
- Reconciliation execution-mode scoping
- Journal upload/outbox/AI
- `docker-compose.prod.yml`, `.env.prod.example`, Caddy example, deploy
  workflow
- Frontend network behavior (no direct Fyers calls)
- Lockfile versions for `fyers-apiv3` transitives

Not performed: live Fyers/Upstox/OpenRouter calls; live orders; external pentest;
chaos/process-kill drills; backup restore; inspection of the live VPS firewall,
Caddyfile actually installed on the host, or `.env.prod` contents.

## 4. Severity model

| Severity | Meaning in this system |
| --- | --- |
| Critical | Unauthorized money-path control, broker-credential leak, duplicate/missed order, or material corruption of an open (paper or live) position |
| High | Defeats a major safety layer, strands recovery, false operational health, or exposes account/trading data |
| Medium | Defence-in-depth, availability, privacy, or operational weakness with narrower preconditions |
| Low | Maintainability or observability with limited immediate security impact |

Paper findings are graded as if they can fire during the paper gate. Paper
still holds a real Fyers session; credential leaks are Critical even in paper.

All findings are **open** unless marked **Fixed**.

## 5. Disposition of the 2026-07-31 findings

| ID | Title | Status now |
| --- | --- | --- |
| SEC-001 | No application auth | **Open, elevated** — API is on the public internet |
| SEC-002 | SQL echo leaks tokens | **Open** — `echo=True` still in production |
| TRD-001 | Partial-exit fills applied twice | **Fixed** — delta uses the new fill quantity |
| TRD-002 | Failed exit strands `exit_pending` | **Open** — still true in paper and live |
| AUTH-001 | Token refresh omits `triggered_by` | **Open** — insert still violates `job_runs` |
| SEC-003 | OAuth state not server-validated | **Open** — frontend checks sessionStorage only |
| SEC-004 | Browser WS unconstrained | **Open** |
| INF-001 | Dev Postgres/Redis exposed | **Partial** — prod loopback; `docker-compose.yml` / `docker-compose.dev.yml` still `0.0.0.0` |
| INF-002 | Redis URL/TLS parsed inconsistently | **Fixed** — `RedisSettings.from_dsn` |
| AUTH-002 | Login does not replace Redis token cache | **Open** |
| AUTH-003 | Some consumers bypass `get_valid_access_token` | **Open** — historical + data validator |
| MD-001 | Tick callback not thread-safe | **Open** |
| MD-002 | Tick worker singleton not atomic | **Open** |
| MD-003 | Monitor accepts stale ticks | **Open** — paper fills also use unvalidated LTP |
| MD-004 | Monitor has no singleton lease | **Open** |
| OG-001 | Gateway ready before socket ready | **Open** for live; paper sets `running` without a Fyers socket (acceptable in paper) |
| OG-002 | Rejected exit does not re-arm | **Open** |
| OG-003 | Queue overflow drops then stops | **Open** (live path) |
| OG-004 | Synthetic trade-ID fallback | **Open** |
| REC-001 | Paper positions in live recon | **Fixed** — `execution_mode` filter + paper books |
| REC-002 | Multiple positions per symbol overwritten | **Open** |
| REC-003 | Holdings/net collapsed with `max` | **Open** (live path; paper uses paper books) |
| REC-004 | `submission_pending` unresolved | **Open** — only `submission_unknown` is flagged |
| REC-005 | No recon run lease | **Open** |
| JRN-001 | Outbox stranded in `processing` | **Open** |
| DEP-001 | Vulnerable fyers transitives | **Open** — `aiohttp 3.9.3`, `requests 2.31.0`, `setuptools 68.0.0` |
| SEC-005 | Same secret for OAuth and encryption | **Open** |
| SEC-006 | Verbose errors / provider payloads | **Open** |
| SEC-007 | Refresh cooldown is process-local | **Open** |
| SEC-008 | No security headers / TLS contract | **Partial** — Caddy terminates TLS; no headers, rate limit, or IP allowlist |
| JRN-002 | Upload buffers whole body | **Open** |
| JRN-003 | Weak journal validation | **Open** |
| JRN-004 | Journal AI missing `data_collection=deny` | **Open** |
| JRN-005 | AI run queued after enqueue failure | **Open** |
| JRN-006 | Period summary charge fields | **Open** — summary uses `estimated_charges` only |
| HIST-001 | Historical validation in API process | **Open** |
| TRD-003 | ATR trailing accepted but unimplemented | **Partial** — P10 `p10_staged_atr` works; schema still allows unused `atr` |
| REC-006 | Broad broker snapshots | **Open** |
| REC-007 | Schedule not calendar-aware | **Open** (holidays env exists, not fully wired as a fail-closed calendar) |
| OPS-001 | `/health` is `SELECT 1` only | **Open** |
| OPS-002 | No backup/restore/supervision runbook | **Open** |
| OPS-003 | No startup schema version check | **Open** |
| DEP-002 | `shadcn` in production dependencies | **Open** |
| TEST-001 | Money-path tests mostly mocked | **Partial** — new paper unit/e2e-simulation tests; still no real PG/Redis multi-process suite |

## 6. Controls that are working well

### Architectural boundaries

- Frontend talks only to this backend, never to Fyers.
- Only the tick worker opens the Fyers market socket.
- Only the order gateway opens the Fyers order socket (and in paper it does
  not open one; it drains `paper_order_events`).
- Only the execution engine places/modifies/cancels. Paper uses
  `paper_broker.place_paper_order`; live uses Fyers async REST.
- Scanner, P7, Gemini, P9, and journal AI have no order-placement authority.
- Approval is a durable decision; the HTTP handler does not call Fyers.

### Money-path safeguards

- P10 Shadow hard-blocks approve (`409`) until owner promotion.
- Rollout cannot skip stages; confirmation phrases are required
  (`CONFIRM_P10_PAPER`, etc.). Those phrases are **not** secrets — they are
  in this repo.
- Live placement still requires `EXECUTION_MODE=live` **and**
  `LIVE_ORDER_PLACEMENT_ENABLED=true`.
- Reduced/full live promotion is blocked while paper positions/intents exist
  and until an enforced P9 policy has a replay-report hash.
- Kill switch is durable in Postgres and fail-closed in the execution engine.
- Intent + idempotency key before broker/paper place; no blind retry of
  `submission_unknown`.
- Risk-policy **percentage** caps cannot be enlarged past the locked Balanced
  policy via the API (`le=0.01` / `0.04` / …).
- Paper preflight never calls Fyers `/funds`.
- Order gateway has an atomic Redis singleton lease.
- Reconciliation in paper mode reads the paper ledger, not Fyers.

These are real foundations. The findings below are implementation and
deployment gaps, not a recommendation to replace the architecture.

---

## 7. Critical findings

### SEC-001 — No application authentication; money-path API is on the public internet

**Evidence**

- `server/main.py` mounts every personal router with no auth dependency.
- `server/app/routers/ws.py` accepts every WebSocket immediately.
- Production Caddy reverse-proxies `api.edurel.xyz` to FastAPI with no IP
  allowlist, basic auth, or mTLS (`deploy/Caddyfile.example`).
- SaaS routes under `/saas` have an HMAC access token;
  `/api/v1/*` does not.

Unauthenticated state-changing examples:

| Endpoint | Effect |
| --- | --- |
| `POST /api/v1/automation/proposals/{id}/decision` | Approve/reject; approval arms L1 |
| `POST /api/v1/automation/rollout/promote` | Shadow → paper → reduced_live → full_live |
| `PUT /api/v1/system/kill-switch` | Engage or **disengage** the kill switch |
| `PUT /api/v1/automation/controls/{key}` | Pause/resume proposal processing and new entries |
| `PUT /api/v1/automation/risk-policy` | Activate a new policy version |
| `POST /api/v1/automation/paper-portfolio/reset` | Wipe and re-seed paper cash |
| `POST /api/v1/automation/stop-streak/{mode}/reset` | Clear the three-stop breaker |
| `POST /api/v1/automation/market-context/policies/{v}/enforce` | Promote P9 to enforced |
| `POST /api/v1/trading/trade-instructions/{id}/confirm` | Confirm a trade (paper fill or live submit) |
| `POST /api/v1/auth/refresh` and `/auth/callback` | Refresh or replace Fyers tokens |
| `POST /api/v1/system/reconciliation/run` | Enqueue recon |
| `POST /api/v1/historical/sync` | Enqueue EOD sync |

Read surfaces dump proposals (including `proposal_hash` needed to approve),
positions, orders, journal notes, reconciliation evidence, and charts.

CORS (`allow_credentials=True`, origin `https://app.edurel.xyz`) only
restricts **browsers**. `curl`, scripts, and other origins' non-CORS clients
are unrestricted.

**Impact**

Anyone who discovers `api.edurel.xyz` can: approve pending live-eligible
proposals once P10 is in Paper; pollute or reset the paper ledger; turn the
kill switch off; promote rollout if env flags already allow live; exchange a
stolen Fyers auth code; and read the full trading book.

This is the blocker for starting paper on the current VPS.

**Required remediation**

- Add single-user application sessions: opaque Redis-backed session in an
  `HttpOnly; Secure; SameSite=Strict` cookie; constant-time password compare
  against an env secret; CSRF token bound to the session on all mutating
  REST; same session required on `/ws` (query is weaker than cookie).
- Keep `/health` as liveness only; do not put secrets in it.
- Until that ships, **compensating control**: Caddy `remote_ip` allowlist
  (your home/VPN IPs only) on `api.edurel.xyz`, or HTTP basic auth, or do not
  publish the API hostname at all (Tailscale / SSH tunnel).
- Do not treat confirmation phrases as authentication.

### SEC-002 — SQL parameter logging can disclose broker secrets

**Evidence**

`server/app/database.py`:

```python
create_async_engine(settings.database_url, echo=True)
```

`APP_ENVIRONMENT=production` does not disable this. `server/app/security.py`
binds `access_token`, `refresh_token`, and `settings.fyers_secret_key` into
`pgp_sym_encrypt` / `pgp_sym_decrypt`.

**Impact**

`docker logs` for `api`, `worker`, `tick-worker`, `order-gateway`, and
`entry-supervisor` can contain plaintext Fyers tokens and the key that
decrypts `broker_auth_tokens`. That is a real-account takeover, not a paper
quirk.

**Required remediation**

- Default `echo=False`; allow SQL echo only via an explicit non-production
  flag that refuses to start if `APP_ENVIRONMENT=production`.
- Never log bind parameters on secret-bearing queries.
- If this stack has already logged in to Fyers on the VPS, assume log
  exposure: rotate Fyers access/refresh tokens, and introduce a dedicated
  encryption secret (see SEC-005) with re-encryption.
- Restrict who can `docker logs` on the VPS.

### AUTH-001 — Scheduled token refresh still violates `job_runs`

**Evidence**

`server/app/services/token_refresh.py` inserts:

```sql
INSERT INTO job_runs (job_type, job_key, status, started_at)
```

`server/db/schema.sql` defines `job_runs.triggered_by text NOT NULL`.

**Impact**

Against the real schema the 08:50 IST refresh can fail on the first insert.
Paper still needs a valid Fyers market-data token every session. Tests that
mock the DB will not catch this.

**Required remediation**

- Insert `triggered_by='scheduler'` (and `manual` for the UI path).
- Add a migrated-Postgres test that runs the start/finish transaction.
- Alert if the last successful refresh is not recent before 09:15 IST.

### TRD-002 — Failed/rejected exit can leave a position unmonitored

**Evidence**

- `create_exit_intent` moves the position to `exit_pending` (full exits).
- `PositionMonitorRuntime.handle_tick` only evaluates `open` and
  `trailing_active`.
- Worker catch around `submit_live_exit_intent` logs and does not restore
  state.
- `_record_definite_rejection` (used when paper LTP is missing) marks the
  intent `rejected` and only cancels `pending_entry` with zero quantity. An
  open position in `exit_pending` is left there.
- Gateway `_close_unfilled_entry` runs only for `intent_type == "entry"`.

**Impact**

A stop can fire, the paper/live submit can fail or be rejected, and the
monitor will never evaluate that position again. In paper this invalidates
the SL/T1–T3 evidence the rollout gate needs. In live it is an unprotected
position.

**Required remediation**

- Distinguish pre-broker failure from ambiguous submission.
- On confirmed rejection/cancellation with residual quantity, restore the
  pre-exit armed state, emit a critical event, and allow a bounded new
  attempt only after a fresh valid tick.
- Never automatically retry `submission_pending` / `submission_unknown`.
- Tests: missing LTP paper exit, HTTP timeout, broker reject, monitor
  restart.

### P10-001 — P10 control plane has no owner authentication

Covered in part by SEC-001. Called out because it is new since July.

Approve requires only a SHA-256 `proposal_hash` that `GET /proposals`
returns. Rollout promotion requires phrases that are public in this
repository. `changed_by` is a free-text field (`owner_api` / any string).

**Required remediation**

Ship SEC-001 first. Optionally add a second owner confirmation cookie/step
for rollout promote, kill-switch **disengage**, P9 enforce, and stop-streak
reset — after there is a real session.

---

## 8. High findings

### SEC-003 — OAuth state is not validated by the backend

**Evidence**

- `GET /auth/url` returns `state` and does not store it server-side.
- The SPA checks `sessionStorage` then `POST /auth/callback`.
- `_exchange_code_and_save` never looks at `state`.
- `GET /api/v1/auth/callback` still exchanges `auth_code` with no state
  check and redirects errors as raw `?error=` query text.

**Risk**

A stolen authorization code can be posted directly to the public API.
Login-CSRF / confused-deputy remains possible. Error strings leak into
browser history.

**Remediation**

Store state in Redis with a short TTL, bind it to the app session, consume
once. Reject missing/mismatched/replayed state. Disable or auth-gate the GET
callback. Use a generic error code on redirect.

### SEC-004 — Browser WebSocket has no auth, Origin, or caps

**Evidence**

`server/app/routers/ws.py`: no session, no Origin check, no Pydantic schema,
no frame/symbol/session caps. `subscribe` publishes to Redis `tick_subs`.
Disconnect does not remove tick-worker demand. The tick worker still honors
`unsubscribe` / `replace` from that channel.

**Risk**

A reachable client can grow Fyers subscriptions and consume Redis/API
memory. Anyone who can publish to Redis (stolen Upstash URL) can drop
mandatory position symbols or inject ticks (see MD-003).

**Remediation**

Authenticate and check Origin before `accept`. Cap frames, symbols, and
sessions. Allowlist instruments. Chart demand as expiring per-session Redis
sets. Tick worker union = DB mandatory symbols ∪ session demand; browsers
must never remove position/watchlist/benchmark demand.

### SEC-010 — FastAPI `/docs`, `/redoc`, and `/openapi.json` are public

**Evidence**

`FastAPI(...)` is constructed with default docs URLs. No `docs_url=None` in
production.

**Risk**

The public OpenAPI catalog is a map of every money-path route and schema,
including confirmation-phrase field names.

**Remediation**

Disable docs in production, or bind them to localhost / require the app
session.

### AUTH-002 — OAuth login does not replace the Redis token cache

**Evidence**

`_exchange_code_and_save` writes Postgres only. `refresh_and_save` updates
`auth:fyers:access_token`. Login does not.

**Risk**

Workers keep using a cached previous token until TTL expiry after a
successful re-login.

**Remediation**

One token-save helper for login and refresh that updates DB, Redis cache,
expiry, and health atomically.

### AUTH-003 — Historical path bypasses `get_valid_access_token`

**Evidence**

`historical.py`, `historical_fetcher.py`, and `data_validator.py` call
`get_fyers_token` directly.

**Risk**

Expired tokens, skipped refresh/health, divergent failure behavior. Manual
`POST /historical/sync` on the public API also decrypts tokens in-process
(and logs them via SEC-002).

**Remediation**

Route every Fyers REST consumer through `get_valid_access_token`. Ban
`get_fyers_token` imports outside `security.py` / `auth_service.py`.

### MD-001 — Tick SDK callback is not handed to asyncio safely

**Evidence**

`tick_worker.py` `_on_message_factory` calls `publish_queue.put_nowait`
directly from the Fyers SDK thread. Order gateway uses
`loop.call_soon_threadsafe`.

**Risk**

Unsupported cross-thread queue use can lose/reorder ticks under load. Paper
and live monitors both consume this feed.

**Remediation**

`call_soon_threadsafe`; track drops; mark the feed unhealthy on overflow.

### MD-002 — Tick worker singleton is a heartbeat race

**Evidence**

Read-then-check of `tick_worker:status`. Heartbeat writes `running` without
checking socket state. `on_close` / `on_error` only log.

**Risk**

Two market sockets; UI shows `running` while the feed is dead. Duplicate
sockets also violate Fyers limits.

**Remediation**

Atomic Redis lease (as the order gateway already does). Refresh only while
healthy. Publish `connecting/ready/degraded/stopped`. Clear readiness on
close, auth error, or overflow.

### MD-003 — Stale, future, or forged ticks drive exits and paper fills

**Evidence**

- Monitor `handle_tick` uses only `symbol` and `ltp`.
- `_complete_paper_submission` fills from `redis.get(f"ltp:{symbol}")` with
  no age/monotonicity/bounds check (TTL is 60s).
- The entry supervisor *does* reject LTP older than 15 seconds; exits and
  paper fills do not reuse that check.

**Risk**

Delayed ticks can fire a stop or ratchet a trail. A compromised Upstash
credential can inject LTPs and manufacture paper (or live) fills/exits.
Missing LTP rejects the paper order (and can strand via TRD-002).

**Remediation**

Normalize timestamps in ingestion. Reject non-positive, future, regressing,
or >10s-old ticks during session hours. Suppress money-path actions and emit
a critical stale-feed event.

### MD-004 — Position monitor has no singleton lease

**Evidence**

Writes `position_monitor:status`; never `SET NX` before processing.

**Risk**

Two monitors on the same tick: duplicate trailing events and exit races.
Idempotency reduces double-place risk; it does not make two monitors safe.

**Remediation**

Owner-valued Redis lease with compare-and-expire renewal. Stop immediately
on loss.

### P10-004 — Entry supervisor has no process singleton

**Evidence**

`run_entry_supervisor` subscribes to 5m bars and heartbeats
`entry_supervisor:status` with no `SET NX`. Allocation uses
`pg_advisory_xact_lock` only inside a sizing transaction.

**Risk**

Two supervisors can both observe a trigger and race into allocation. The
lock serializes sizing but duplicate trigger persistence / conflict rows are
still possible.

**Remediation**

Same Redis singleton pattern as the order gateway.

### P10-006 — Reduced-live `0.25×` capital is not enforced

**Evidence**

AGENTS.md §12.2: reduced live must size P10 against `0.25×` deployable
capital while keeping the same percentage policy. `p10_rollout.py`
`_assert_ready_for_live` checks env flags, empty paper books, and an
enforced P9 replay hash — it never records or applies a 0.25 multiplier.
Grep of `entry_supervisor.py`, `execution_engine.py`, and `p10_sizing.py`
finds no rollout-stage size factor.

**Impact**

Promoting to `reduced_live` would trade at full Balanced size against live
Fyers funds. This does not block paper, but it is a live-gate blocker: the
stage name currently provides no size reduction.

**Remediation**

On `reduced_live`, multiply the execution-time deployable-capital ceiling
by `0.25` (broker funds still win when lower). Persist the factor on the
rollout row or an immutable policy version. Fail closed if stage is
`reduced_live` and the factor is missing. Tests for paper vs reduced_live
vs full_live sizing. Do not rely on the operator manually lowering
`deployable_capital_override`.

### OG-001 — Live gateway claims `running` before a confirmed socket

**Evidence**

Live path: `socket.connect()`, `subscribe()`, then `_set_status(...,
"running")` without waiting for `on_connect`. `on_close` only logs.
`ensure_order_gateway_ready` treats `status=="running"` and a fresh
heartbeat as sufficient.

**Risk**

Live REST place while the correlation socket is down → more
`submission_unknown`. Paper mode does not open a Fyers order socket; this
finding is live-gated but should be closed before reduced live.

**Remediation**

`ready` only from a confirmed callback. Degrade immediately on close/error.
Execution must require `ready`, not process `running`.

### OG-002 — Confirmed exit rejection does not re-arm residual quantity

See TRD-002. Gateway closes unfilled **entries** only.

### REC-002 — Multiple local positions per symbol are overwritten

**Evidence**

```python
local_qty = {
    row["fyers_symbol"]: int(row["open_quantity"])
    for row in local_positions
    if int(row["open_quantity"]) > 0
}
```

**Risk**

Dict assignment keeps the last row. False match/mismatch when two app
positions share a symbol (adds, corrections, leftover manual paper).

**Remediation**

`SUM(open_quantity)` grouped by symbol with explicit side/product semantics.

### REC-004 — `submission_pending` can remain unresolved

**Evidence**

Reconciliation flags `submission_unknown` when no broker order matches. A
crash after the durable `submission_pending` claim is not equivalently
aged/alerted.

**Remediation**

Age nonterminal intents. Correlate or raise a critical unresolved item.
Never automatically place again.

### JRN-001 — Journal outbox can stick in `processing`

**Evidence**

`_claim_pending_events` selects `status = 'pending'` only, then sets
`processing`. Crash after commit leaves a row that `_count_pending` still
counts, so the dispatcher re-enqueues forever and never reclaims.

**Risk**

Paper-gate journal evidence can silently miss fills.

**Remediation**

Reclaim `processing` older than five minutes with bounded attempts. Test
death immediately after claim.

### DEP-001 — Known-vulnerable `fyers-apiv3` transitives

**Evidence**

`server/uv.lock`: `aiohttp==3.9.3`, `requests==2.31.0`,
`setuptools==68.0.0`, all via `fyers-apiv3`. `aiohttp` is on the broker
socket path.

**Remediation**

Override to patched versions compatible with the SDK and re-run socket/OAuth
fixtures. If the vendor pins prevent a safe bump, record advisories,
compensating controls (private network, no public API), and an expiry date.

### INF-004 — Caddy edge has TLS only

**Evidence**

`deploy/Caddyfile.example` is `encode gzip` + `reverse_proxy`. No
`header` (CSP, HSTS, `X-Content-Type-Options`, frame, referrer), no rate
limit, no `remote_ip` allowlist. Client `nginx.conf` likewise has no
security headers.

**Risk**

The public hostname is a raw FastAPI. TLS does not provide authorization.

**Remediation**

Until SEC-001: IP allowlist or basic auth on `api.edurel.xyz`. Then add
security headers at Caddy; document trusted-proxy behavior.

---

## 9. Medium findings

### SEC-005 — Broker client secret is also the DB encryption key

`FYERS_SECRET_KEY` keys both OAuth and `pgp_sym_encrypt`. Add a dedicated
high-entropy `TOKEN_ENCRYPTION_KEY` and an audited re-encryption migration.

### SEC-006 — Verbose errors

Auth callback/refresh can put exception strings in HTTP bodies and redirect
query strings. Map provider errors to stable codes; redact token/account
fields.

### SEC-007 — Manual refresh cooldown is process-local

`_last_refresh_ts` in `auth.py`. Move to Redis with TTL; add a per-session
rate limit after SEC-001.

### SEC-011 — Fyers access token cached in Redis in plaintext

`auth:fyers:access_token` is the hot path. Upstash is TLS-authenticated, but
a leaked `REDIS_URL` is equivalent to broker-token theft plus tick/order
forgery. Restrict Upstash ACL to this app; treat `REDIS_URL` like a password;
do not log it.

### P10-002 — Two paper execution paths

P10 entries/exits go through `paper_broker` and the gateway processors.
The retired manual form still calls `complete_paper_entry_fill`, which
opens a paper position **without** debiting `paper_broker_account`.

During the paper gate, using the manual trade form desynchronizes the cash
ledger the rollout evidence depends on.

**Remediation**

Disable or hard-block `complete_paper_entry_fill` while P10 paper is the
active book, or route the manual form through the same paper broker (only if
you explicitly reopen that architecture in `AGENTS.md`).

### P10-005 — `deployable_capital_override` has no upper bound

`RiskPolicyUpdateRequest` caps percentages but `deployable_capital_override:
Decimal = Field(gt=0)` is unbounded. Combined with unauthenticated
`/paper-portfolio/reset`, paper cash can be inflated and the 50-proposal
stats become meaningless. After SEC-001, still cap override to a documented
maximum (e.g. the Balanced seed).

### JRN-002 — Chart upload reads the entire body first

`await request.body()` then a 5 MiB check. Unauthenticated callers can force
large allocations. Stream with Content-Length precheck + incremental cap.

### JRN-003 — Journal review / actual-charge validation is weak

`JournalReviewUpdate` has no max length on notes/tags/lessons.
`ActualChargesUpdate` allows negative `Decimal` fields. Tighten lengths,
tag counts, and `ge=0` plus a validated total.

### JRN-004 — Journal OpenRouter client omits `data_collection: deny`

Fundamentals and VCP vision set `"provider": {..., "data_collection":
"deny"}`. `journal_llm.py` does not. Journal notes are more sensitive.
Apply the same privacy option.

### JRN-005 — AI run can remain `queued` after Redis enqueue failure

API commits `journal_ai_runs` then `enqueue_job`. Add a sweeper or
transactional outbox.

### JRN-006 — Period summary ignores actual charges

`get_period_summary` sets `total_charges` from `estimated_charges` only
(`journal_service.py`). Reconciled `actual_charges` never enter the
aggregate. Paper-gate P&L summaries can look better than the journal
detail. Use a purpose-specific query that prefers actual totals when
`charge_quality = reconciled`.

### HIST-001 — Historical validation still runs in the API process

`BackgroundTasks` in `historical.py`. Violates thin-API topology; work is
lost on API restart. Move to arq.

### TRD-003 — Manual `TrailingRule` still allows unimplemented `atr`

P10 uses `p10_staged_atr`. The trade-instruction schema still accepts
`atr`, and the monitor logs that it skips until a feed exists. Remove `atr`
from the API/UI.

### REC-003 — Holdings and net-position quantity still use `max`

Live reconciliation `_aggregate_broker_quantities` and the entry-supervisor
live preflight verify both collapse CNC net qty and holdings with `max`.
Keep domains separate.

### REC-005 — Reconciliation has no distributed run lease

Cron + manual enqueue can overlap. Owner-valued Redis lease; “already
running” as an explicit result.

### REC-006 — Broker snapshots retained too broadly

Redact to correlation fields; set retention.

### OG-003 / OG-004 — Live queue drop and synthetic trade IDs

Unchanged from July. Live-gated; close before reduced live.

### OPS-001 — `/health` is not readiness

Only `SELECT 1`. Compose uses it for `api` healthy; workers use
`raise SystemExit(0)` as a fake healthcheck (`OPS-004`). Split liveness vs
readiness (Redis, migrations, tick/monitor/gateway heartbeats, auth). Keep
detailed readiness off the public hostname.

### OPS-002 — No backup, restore, or DR runbook

Postgres volume has no documented dump/restore drill. For a software-stop
system, restore is part of correctness.

### OPS-003 — Schema compatibility is not checked at startup

Migrations are manual SQL. A new image can boot against an old volume
(missing `019_p10_paper_broker.sql`) and fail mid-session. Track applied
migrations; money-path readiness must fail closed.

### OPS-004 — Worker Compose healthchecks always succeed

`python -c "raise SystemExit(0)"` on tick, monitor, gateway, supervisor,
proposal, and core worker. Docker will not restart a wedged-but-alive
process. Use heartbeat keys or a real process check.

### OPS-005 — VPS IP and confirmation phrases are in the public repo

`DEPLOY.md` / `.env.prod.example` publish `80.225.207.109`. Combined with
SEC-001 this is reconnaissance. Confirmation phrases are source-level.
Acceptable after SEC-001 + IP allowlist; until then assume the API is
scanned.

### OPS-006 — `DEPLOY.md` Step 1 service list is stale

The file still describes Step 1 as `postgres`, `api`, `worker`, `client`.
`docker-compose.prod.yml` starts proposal-worker, tick-worker,
entry-supervisor, position-monitor, and order-gateway with no profile.
`docker compose up -d` therefore runs the full money path. Update the
docs so operators are not surprised.

### INF-005 — GHCR images recommended public

`DEPLOY.md` offers making `ghcr.io/visheshgubrani/swingtradervcp/{server,client,swyingify}`
public. Public server images ship application source (`app/`, `db/`,
scripts) without runtime secrets, but they advertise the trading stack
and unauthenticated pull. Prefer private packages + `GHCR_READ_TOKEN`.

Prod Postgres on `127.0.0.1:5482` is also reachable from **other containers
on the same VPS** (open-webui, academy, cramlify per the example env).
Same `POSTGRES_*` credentials as the trading role. Treat co-hosted stacks
as part of the trust boundary until a dedicated DB role exists.

### INF-001 remainder — Dev compose still publishes on all interfaces

`docker-compose.yml` and `docker-compose.dev.yml`: `5480:5432` and
`6380:6379` with password `algo` and Redis with no AUTH. Bind to
`127.0.0.1` so a laptop on a shared network is not a second copy of SEC-001.

### DEP-002 — `shadcn` is in `client` `dependencies`

CLI tooling in the production install tree. Move to `devDependencies` and
re-audit.

### TEST-001 — Still no real multi-process money-path suite

New paper tests help. They do not replace migrated Postgres + Redis + two
copies of each singleton worker.

---

## 10. Low and maintainability

- FastAPI title is still `"Algo Trading"`; OpenAPI advertises the stack.
- Worker status documents are ad hoc Redis JSON; a shared health schema
  would make UI and `ensure_order_gateway_ready` consistent.
- arq / Python 3.14 `asyncio.iscoroutinefunction` deprecation remains.
- Frontend production bundle is still a large single chunk (from the July
  build warning; re-measure after current UI work).
- Chart capture for journal PNGs is still opportunistic if no UI is open.
- Shared Postgres with a future Swyingify container uses the same
  `POSTGRES_*` credentials; SaaS must never be granted DML on money-path
  tables (`broker_auth_tokens`, `order_intents`, `positions`,
  `p10_rollout_state`, …). Out of scope to fix in SaaS now; do not enable
  `--profile saas` until table privileges are split.
- `GET /auth/status` discloses whether a Fyers session exists; after
  SEC-001 this is fine for the owner, not for the internet.

---

## 11. WebSocket and webhook assessment

Unchanged in structure from July:

| Channel | Owner | Primary remaining risks |
| --- | --- | --- |
| Fyers market WS | tick worker | duplicate workers, unsafe thread handoff, false readiness, unvalidated ticks |
| Fyers order WS | order gateway (live only) | ready before connect, queue drop, rejected-exit re-arm |
| Paper order events | order gateway (paper) | Redis forgery of `paper_order_events` if `REDIS_URL` leaks |
| Browser `/ws` | FastAPI | no auth/Origin/caps; subscribe → `tick_subs` |

No inbound provider webhooks. Do not add one for symmetry.

---

## 12. Dependency snapshot (2026-08-16)

### Python (`server/uv.lock`)

| Package | Version | Relationship | State |
| --- | --- | --- | --- |
| `aiohttp` | 3.9.3 | transitive `fyers-apiv3` | known-vulnerable series; update required |
| `requests` | 2.31.0 | transitive `fyers-apiv3` | known advisories; update required |
| `setuptools` | 68.0.0 | transitive `fyers-apiv3` | known advisory |
| `fyers-apiv3` | ≥3.1.14 | direct SDK | compatibility owner |

Rerun `uv pip audit` / GitHub advisory DB at remediation time; counts move.

### JavaScript (`client/package.json`)

`shadcn` remains in `dependencies`. Treat CLI-tree advisories as
developer/build exposure until it is moved to `devDependencies`.

---

## 13. Paper-trading gate (do this before the 50-proposal run)

The AGENTS.md paper gate (complete path, zero duplicate orders, cap
discipline, three-stop breaker) is **not valid evidence** if an untrusted
party can approve, reset the ledger, inject ticks, or steal the Fyers
session.

### P0 — before promoting Shadow → Paper on the VPS

1. **Close SEC-001 or take the API private** (Caddy `remote_ip` / Tailscale
   / basic auth). This is non-negotiable on `api.edurel.xyz`.
2. **Disable SQL echo** (SEC-002). If Fyers login already happened on that
   host, rotate tokens.
3. **Fix AUTH-001** so the morning refresh actually writes `job_runs`.
4. **Fix TRD-002** so a missing LTP / rejected paper exit cannot freeze a
   position out of the monitor.
5. Confirm `EXECUTION_MODE=paper`, `LIVE_ORDER_PLACEMENT_ENABLED=false`,
   kill switch policy understood (kill ≠ flatten).
6. Do not use the manual trade form during the P10 paper book (P10-002).

### P1 — before treating paper fills as rollout evidence

1. Tick + monitor + entry-supervisor Redis singleton leases (MD-002,
   MD-004, P10-004).
2. Tick thread-safety and freshness (MD-001, MD-003).
3. Login replaces Redis token cache (AUTH-002).
4. Journal outbox reclaim (JRN-001).
5. Bind local compose ports to loopback (INF-001 remainder).
6. Disable `/docs` in production (SEC-010).

### P2 — before reduced live (not this week)

Everything remaining in §8–§9 that is live-specific: OG-001/002/003,
REC-002/003/004/005, AUTH-003, DEP-001, OPS backup/schema, P9 enforced with
owner-approved replay hash, empty paper books, **P10-006 0.25× size
factor actually applied**, `CONFIRM_P10_REDUCED_LIVE`.

---

## 14. Prioritized remediation roadmap (clean sweep)

Suggested batching so you can fix in order without thrashing:

### Batch A — stop the bleeding (public VPS + paper)

1. Caddy IP allowlist **today** if auth will take more than a day.
2. `echo=False` in `database.py`; restart API/workers; rotate Fyers tokens
   if logs may already contain them.
3. AUTH-001 `triggered_by`.
4. Single-user session + CSRF + authenticated `/ws` (SEC-001, SEC-003,
   SEC-004, SEC-010).
5. TRD-002 / OG-002 exit re-arm.

### Batch B — paper-path integrity

1. Worker singleton leases (tick, monitor, entry supervisor, recon).
2. Tick `call_soon_threadsafe` + freshness gates; paper LTP age check.
3. Token-save unification (login + refresh + Redis).
4. Journal outbox reclaim.
5. Block manual `complete_paper_entry_fill` while P10 paper is active.

### Batch C — live-hardening

1. Gateway `ready` vs `running`; rejected-exit restore.
2. Reconciliation: SUM by symbol, split holdings, age `submission_pending`,
   run lease, redact snapshots.
3. `get_valid_access_token` everywhere; dedicated encryption key.
4. Security headers, Redis ACL review, schema version at startup, real
   worker healthchecks, backup/restore drill.
5. Dependency overrides; move `shadcn` to devDependencies.
6. Enforce reduced-live `0.25×` capital (P10-006) before any live promote.
7. Private GHCR; least-privilege DB role before `--profile saas`.

### Batch D — defence in depth

Journal validation/upload streaming, period-summary actual charges,
OpenRouter privacy parity, historical validation on arq, calendar-aware
jobs, frontend bundle split, no HTTP rate limit on personal routes
(only the execution-engine 10 OPS bucket exists today).

---

## 15. Required tests (updated)

Keep the July list. Add:

- Unauthenticated `401/403` on every `/api/v1` mutating route and `/ws`.
- OAuth state consume-once; GET callback rejected without session.
- SQL echo off in production settings; secret-bearing queries never appear
  in caplog.
- Token refresh insert against migrated `job_runs`.
- Paper exit with missing LTP restores monitor evaluation.
- Two entry-supervisor processes: only one owns the bar.
- Paper broker never calls Fyers funds/order URLs (monkeypatch assertion).
- Rollout cannot skip; approve blocked in Shadow; paper reset blocked with
  open positions.
- Manual trade confirm does not mutate `paper_broker_account` **or** is
  rejected during P10 paper.

### Paper operational drill (after Batch A)

- Kill switch engaged during setup; `EXECUTION_MODE=paper`.
- Promote Shadow → Paper from the authenticated UI only.
- Approve one live-eligible proposal; confirm L1 arms; kill the browser;
  confirm supervisor + monitor keep running.
- Restart position monitor; confirm re-arm from Postgres.
- Force a stop with a fresh tick; confirm paper fill, cash ledger, journal
  outbox.
- Repeat with a simulated missing LTP and confirm the position is not stuck
  in `exit_pending`.
- Trip the three-stop breaker at exactly three qualifying closures; owner
  reset must not clear an independent manual pause.
- Run paper reconciliation; no Fyers position book involved.

---

## 16. Acceptance criteria

### For starting the paper gate on this VPS

- Unauthenticated REST and WebSocket cannot approve, promote, reset paper,
  toggle kill/pauses, or refresh tokens.
- SQL logs cannot contain Fyers tokens or the encryption key.
- Scheduled token refresh succeeds against real `job_runs`.
- A failed paper exit cannot leave residual quantity unmonitored.
- Paper broker is the only paper cash book in use.

### For later reduced live (not claimed by this audit)

All of the July live criteria, plus: P9 enforced with owner-approved replay
hash; empty paper books; gateway `ready`; tick freshness; singleton workers;
reconciliation live-only and aggregation-correct; backups restored once.

Until Batch A is done, treat `https://api.edurel.xyz` as an unauthenticated
trading console that also holds a live Fyers session.
