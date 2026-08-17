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

A single-user session layer now gates personal REST (SEC-001). SQL echo is
off in production (SEC-002). OAuth state is session-bound and consume-once
(SEC-003). Browser `/ws` requires the session cookie (SEC-004). OpenAPI is
off in production (SEC-010).

**Paper-trading decision:** Batch A and Batch B paper-path integrity are
closed in code. Set a long `APP_PASSWORD` in `.env.prod` and deploy this
build before promoting Shadow → Paper. Remaining leftovers: MD-003 /
JRN-001, plus Batch C items 6–7 (deps / GHCR) which do not block paper.

**Live-readiness decision:** do not enable live order placement until the
paper gate, P9 enforcement, **P10-006 applied as `min(override, broker) *
0.25` (currently scales the override first)**, OG-001 requiring `ready`
only, and the remaining critical/high findings in this file are closed.
Batch C items 1–5 shipped with leftovers; items 6–7 were not implemented.

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
- OAuth GET callback removed; SPA POST `/auth/callback` is session-bound
- P10 ATR trail (`p10_staged_atr`) is implemented; the old unused `atr`
  trailing type is still accepted on the manual trade form

Still open from July: OG-003/004, most JRN-002+/OPS/DEP items. AUTH-003,
REC-002/003/005/006 are closed. OG-001, REC-004, SEC-005, SEC-008,
OPS-003, and P10-006 shipped in Batch C with leftovers below. MD-003 /
JRN-001 leftovers from Batch B are unchanged.

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
| SEC-001 | No application auth | **Fixed** (2026-08-16) — cookie-only session + CSRF; personal REST gated |
| SEC-002 | SQL echo leaks tokens | **Fixed** (2026-08-16) — `echo=False` default; production start fails closed if `sql_echo=True`. Rotate tokens if old logs exist. |
| TRD-001 | Partial-exit fills applied twice | **Fixed** — delta uses the new fill quantity |
| TRD-002 | Failed exit strands `exit_pending` | **Fixed** (2026-08-16) — Re-arms `exit_pending` positions back to `open` / `trailing_active` on confirmed rejection/cancellation; emits critical audit events |
| AUTH-001 | Token refresh omits `triggered_by` | **Fixed** (2026-08-16) — scheduled insert includes `triggered_by='scheduler'` |
| SEC-003 | OAuth state not server-validated | **Fixed** (2026-08-16) — Redis state bound to app session, consume-once; GET callback removed |
| SEC-004 | Browser WS unconstrained | **Fixed** (2026-08-16) — cookie auth, production Origin required, session caps, disconnect cleanup. Tick worker must still union mandatory DB symbols before honoring `tick_subs` unsubscribe. |
| INF-001 | Dev Postgres/Redis exposed | **Partial** — prod loopback; `docker-compose.yml` / `docker-compose.dev.yml` still `0.0.0.0` |
| INF-002 | Redis URL/TLS parsed inconsistently | **Fixed** — `RedisSettings.from_dsn` |
| AUTH-002 | Login does not replace Redis token cache | **Fixed** (2026-08-17) — `persist_and_cache_fyers_token` unified across login & refresh |
| AUTH-003 | Some consumers bypass `get_valid_access_token` | **Fixed** (2026-08-17) — historical/validator/fetcher/recon/workers use `get_valid_access_token`; `get_fyers_token` stays in `security.py` / `auth_service.py` only |
| MD-001 | Tick callback not thread-safe | **Fixed** (2026-08-17) — `loop.call_soon_threadsafe`, drop tracking, degraded state on overflow |
| MD-002 | Tick worker singleton not atomic | **Fixed** (2026-08-17) — atomic Redis lease + structured state + renewal shutdown |
| MD-003 | Monitor accepts stale ticks | **Fixed** (2026-08-17) — freshness gates (10s monitor, 15s paper submission) + non-positive filtering |
| MD-004 | Monitor has no singleton lease | **Fixed** (2026-08-17) — atomic Redis lease + compare-and-expire renewal |
| OG-001 | Gateway ready before socket ready | **Partial** (2026-08-17) — worker sets `connecting` then `ready` on `on_connect`; `ensure_order_gateway_ready` still accepts `status in {"ready", "running"}` |
| OG-002 | Rejected exit does not re-arm | **Fixed** (2026-08-16) — Order gateway restores position state on exit rejected/cancelled |
| OG-003 | Queue overflow drops then stops | **Open** (live path) |
| OG-004 | Synthetic trade-ID fallback | **Open** |
| REC-001 | Paper positions in live recon | **Fixed** — `execution_mode` filter + paper books |
| REC-002 | Multiple positions per symbol overwritten | **Fixed** (2026-08-17) — `local_qty[symbol] += qty` instead of last-row overwrite |
| REC-003 | Holdings/net collapsed with `max` | **Fixed** (2026-08-17) — CNC inventory accurately summed as `holdings + netQty` |
| REC-004 | `submission_pending` unresolved | **Partial** (2026-08-17) — flags `submission_pending` older than 60s; ages `created_at`, not `broker_requested_at` |
| REC-005 | No recon run lease | **Fixed** (2026-08-17) — distributed run lease with explicit skip on contention |
| JRN-001 | Outbox stranded in `processing` | **Fixed** (2026-08-17) — stranded processing rows reclaimed after 5 minutes |
| DEP-001 | Vulnerable fyers transitives | **Open** — `aiohttp 3.9.3`, `requests 2.31.0`, `setuptools 68.0.0` |
| SEC-005 | Same secret for OAuth and encryption | **Partial** (2026-08-17) — `TOKEN_ENCRYPTION_KEY` exists; still falls back to `FYERS_SECRET_KEY` then a hardcoded default; not in `.env.prod.example`; no production fail-closed; no re-encryption migration |
| SEC-006 | Verbose errors / provider payloads | **Open** |
| SEC-007 | Refresh cooldown is process-local | **Open** |
| SEC-008 | No security headers / TLS contract | **Partial** (2026-08-17) — FastAPI middleware sets `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`; Caddy still TLS-only (no HSTS/CSP) |
| JRN-002 | Upload buffers whole body | **Open** |
| JRN-003 | Weak journal validation | **Open** |
| JRN-004 | Journal AI missing `data_collection=deny` | **Open** |
| JRN-005 | AI run queued after enqueue failure | **Open** |
| JRN-006 | Period summary charge fields | **Open** — summary uses `estimated_charges` only |
| HIST-001 | Historical validation in API process | **Open** |
| TRD-003 | ATR trailing accepted but unimplemented | **Partial** — P10 `p10_staged_atr` works; schema still allows unused `atr` |
| REC-006 | Broad broker snapshots | **Fixed** (2026-08-17) — snapshots redacted to essential correlation fields |
| REC-007 | Schedule not calendar-aware | **Open** (holidays env exists, not fully wired as a fail-closed calendar) |
| OPS-001 | `/health` is `SELECT 1` only | **Open** |
| OPS-002 | No backup/restore/supervision runbook | **Open** |
| OPS-003 | No startup schema version check | **Partial** (2026-08-17) — lifespan counts 8 core tables and warns if `found < 7` (7/8 does not warn); does not fail closed; not a migration-version check |
| P10-006 | Reduced-live 0.25x capital unscaled | **Partial** (2026-08-17) — scales override first, then `min(ceiling, broker_funds)`. Small live accounts use full broker cash, not `0.25×`. Factor is hardcoded, not persisted |
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

### SEC-001 — No application authentication; money-path API is on the public internet [FIXED: 2026-08-16]

**Status:** **Fixed.** Unauthenticated clients cannot approve trades, flip the
kill switch, or read the ledger.

- `APP_PASSWORD` with `secrets.compare_digest`; production refuses to start if
  the password is missing or shorter than 12 characters.
- Redis session + `HttpOnly` cookie + CSRF on mutating REST.
- Cookie-only: `session_id` is not in JSON, not in `sessionStorage`, and
  Bearer / `X-Session-ID` are rejected.
- Login lockout keys on the client IP, trusting `X-Forwarded-For` /
  `X-Real-IP` only when the peer is loopback (`127.0.0.1` / `::1`).
- Router-level `require_authenticated_user` on the personal money-path
  routers. `/health` stays public.

`P10-001` is reduced: those mutators now require a session. Confirmation
phrases remain non-secrets.

### SEC-002 — SQL parameter logging can disclose broker secrets [FIXED: 2026-08-16]

**Status:** **Fixed** in code. Engine `echo` defaults off and cannot be enabled when `APP_ENVIRONMENT=production` (startup `ValueError`).

Operational leftover: if the VPS already logged a Fyers login while `echo=True` was on, rotate access/refresh tokens. Dedicated encryption key remains SEC-005.

### AUTH-001 — Scheduled token refresh still violates `job_runs` [FIXED: 2026-08-16]

**Status:** **Fixed** for the schema crash. `run_token_refresh` inserts
`triggered_by` (default `'scheduler'`), which matches `job_runs.triggered_by
text NOT NULL`. Cron does not pass `triggered_by` in arq ctx; the default is
the scheduled path.

Leftovers, not a reopen:
- Tests mock SQLAlchemy; there is still no migrated-Postgres start/finish
  assertion.
- `POST /auth/refresh` still calls `refresh_and_save` directly and does not
  write `job_runs`.
- No 09:15 IST “last successful refresh is stale” alert.

### TRD-002 — Failed/rejected exit can leave a position unmonitored [FIXED: 2026-08-16]

**Status:** **Fixed.** Confirmed rejection/cancellation and pre-claim blocked
submits restore residual `exit_pending` qty.

- Single helper `restore_rejected_exit_position` used by the execution engine
  and the order gateway.
- Restored state comes from the latest `position_events.from_state` into
  `exit_pending` (`open` vs `trailing_active`). Fallback is T2-complete +
  trailing stop, not `trailing_rule_type`.
- Pre-claim `ExecutionBlockedError` / `ExecutionSafetyError` on
  `submit_live_exit_intent` rejects the `created` intent, restores, then
  re-raises. Next tick can retry with `retry:N`.
- `submission_unknown` / `submission_pending` are not restored.
- `submit_live_entry_intent` does **not** reject on pre-claim block; the
  `created` entry stays retryable (entry idempotency has no `retry:N`).

### P10-001 — P10 control plane has no owner authentication

Covered in part by SEC-001. Called out because it is new since July.

Approve requires only a SHA-256 `proposal_hash` that `GET /proposals`
returns. Rollout promotion requires phrases that are public in this
repository. `changed_by` is a free-text field (`owner_api` / any string).

**Required remediation**

SEC-001 is closed; these mutators now require a session. Optionally add a
second owner confirmation cookie/step for rollout promote, kill-switch
**disengage**, P9 enforce, and stop-streak reset.

---

## 8. High findings

### SEC-003 — OAuth state is not validated by the backend [FIXED: 2026-08-16]

**Status:** **Fixed** (2026-08-16)
- `GET /auth/url` generates random OAuth `state` and records JSON `{"session_id": ...}` in Redis (`auth:oauth_state:<state>`) bound to the authenticated app session with 10-minute TTL.
- `POST /auth/callback` validates and atomically consumes the state on first use, verifying caller session matches state owner. Missing, expired, mismatched, or replayed states fail closed.
- Public `GET /auth/callback` handler removed.

### SEC-004 — Browser WebSocket has no auth, Origin, or caps [FIXED: 2026-08-16]

**Status:** **Fixed** for the original trust/caps finding.

- Cookie-only session before `accept()`. No `?token=`.
- Production requires `Origin` in `cors_origins`.
- Caps: 100 symbols/message, 200/session, 100 connections.
- Disconnect publishes `tick_subs` unsubscribe for symbols no other browser
  session still wants.

**Follow-up (not a reopen of the five residuals):** the tick worker still
honors `unsubscribe` without unioning open positions ∪ armed legs ∪
watchlist ∪ benchmark. A chart tab closing can drop Fyers demand for a
symbol the position monitor still needs. Fix in the tick worker: never
remove a DB-mandatory symbol because a browser asked.

### SEC-010 — FastAPI `/docs`, `/redoc`, and `/openapi.json` are public [FIXED: 2026-08-16]

**Status:** **Fixed** for default production (`docs_url` / `redoc_url` / `openapi_url` are `None`). Leave `ENABLE_DOCS_IN_PRODUCTION=false`. If that flag is ever true, docs are still unauthenticated — do not turn it on on the VPS.

### AUTH-002 — OAuth login does not replace the Redis token cache [FIXED: 2026-08-17]

**Status:** **Fixed** (2026-08-17).
- Added `persist_and_cache_fyers_token` unified helper in `auth_service.py`.
- Both OAuth login (`/auth/callback`) and token refresh (`refresh_and_save`) synchronously update Postgres, Redis token (`auth:fyers:access_token`), expiry (`auth:fyers:expires_at`), and health (`auth:fyers:healthy`).

### AUTH-003 — Historical path bypasses `get_valid_access_token` [FIXED: 2026-08-17]

**Status:** **Fixed** (2026-08-17).
- `historical.py`, `historical_fetcher.py`, `data_validator.py`, recon,
  tick, gateway, execution, and entry supervisor call
  `get_valid_access_token(redis)`.
- `get_fyers_token` remains the decrypt helper used only by
  `security.py` / `auth_service.py`.

### P10-006 — Reduced-live `0.25×` capital is not enforced [PARTIAL: 2026-08-17]

**Status:** **Partial** (2026-08-17).

`entry_supervisor.py` now does:

```python
capital_ceiling = policy.deployable_capital_override or Decimal("0")
if current_stage == "reduced_live":
    capital_ceiling = capital_ceiling * Decimal("0.25")
deployable = min(capital_ceiling, broker_snapshot.available_funds)
```

`proposal_worker.py` scales the override the same way for the approved
risk budget. The Batch C test only covers override ₹10L with broker ₹20L
→ ₹2.5L, which is the case where broker is *higher* than the scaled
ceiling.

AGENTS.md §12.2 is `0.25×` **deployable capital**, and §6.3 bounds
deployable as `min(override, broker)`. The live-safe formula is:

`min(override, broker_funds) * 0.25`

Current order of operations: `min(override * 0.25, broker_funds)`.

Example: override ₹10L, live Fyers cash ₹80k → deployable is ₹80k
(full account), not ₹20k. That is full Balanced size against live
funds — the original finding.

Leftovers before reduced live:
- Apply `0.25` to the already-bounded ceiling, not only the override.
- Persist the factor on the rollout row or an immutable policy version;
  fail closed if `reduced_live` and the factor is missing.
- Add a test where broker funds sit between `0.25× override` and
  `override`.

### OG-001 — Live gateway claims `running` before a confirmed socket [PARTIAL: 2026-08-17]

**Status:** **Partial** (2026-08-17).
- Live worker starts as `connecting`, sets `ready` in `on_connect`,
  `degraded` on close/error, `stopped` on auth-shaped errors.
- Paper mode is `ready` without a Fyers socket (correct).
- Heartbeat publishes `connecting` until `on_connect` (good).

Leftover: `ensure_order_gateway_ready` still accepts
`status in {"ready", "running"}`. The worker no longer writes
`running`, but any stale Redis value or future writer of `running`
still arms live REST. `test_live_execution.py` still asserts that
`running` is sufficient. Require `ready` only.

### MD-001 — Tick SDK callback is not handed to asyncio safely [FIXED: 2026-08-17]

**Status:** **Fixed** (2026-08-17).
- `_on_message_factory` uses `loop.call_soon_threadsafe(put)` to bridge from Fyers WebSocket SDK thread to asyncio.
- Non-positive LTPs (`ltp <= 0`) are filtered and logged before enqueuing.
- `asyncio.QueueFull` drops tick, logs critical error, and sets worker state to `degraded`.

### MD-002 — Tick worker singleton is a heartbeat race [FIXED: 2026-08-17]

**Status:** **Fixed** (2026-08-17).
- Atomic Redis singleton lease `tick_worker:singleton` via `SET NX EX 30`.
- Compare-and-expire renewal in `_heartbeat_loop` with immediate shutdown on lease loss.
- Heartbeat loop starts immediately after acquire to protect lease against auth/connect delays.
- Structured status (`connecting`, `ready`, `degraded`, `stopped`) including `worker_id`.
- Socket close/error updates state to `degraded` and stops on auth errors.
- Atomic compare-and-del lease release on shutdown.

### MD-003 — Stale, future, or forged ticks drive exits and paper fills [FIXED: 2026-08-17]

**Status:** **Fixed** (2026-08-17).
- Position monitor `handle_tick` validates positive LTP (`ltp > 0`) and timestamp freshness (`MAX_TICK_AGE_SECONDS = 10.0s`, drops age > 10s or future ticks < -5s).
- Paper execution `_complete_paper_submission` validates cached LTP is present, positive, and fresh (age ≤ 15.0s, future < -5s), cleanly rejecting stale or non-positive LTP.
- Entry supervisor already enforces ≤ 15.0s LTP freshness gate.

Leftovers, not a reopen:
- Monitor and paper-cache checks skip the age gate when `received_at` is
  missing (`if received_at_str`). Entry supervisor requires the field.
  Fail closed if it is absent.
- Exchange `last_traded_time` is stored but not validated; monotonicity is
  not checked. Ingest-time `received_at` is what gates exits.

### MD-004 — Position monitor has no singleton lease [FIXED: 2026-08-17]

**Status:** **Fixed** (2026-08-17).
- Atomic Redis singleton lease `position_monitor:singleton` via `SET NX EX 30`.
- Compare-and-expire renewal in `_heartbeat_loop` with immediate shutdown on lease loss.
- Atomic compare-and-del lease release on shutdown.

Leftover: `runtime.reload` runs after acquire and before the first heartbeat.
Start renew immediately after `SET NX`.

### P10-004 — Entry supervisor has no process singleton [FIXED: 2026-08-17]

**Status:** **Fixed** (2026-08-17).
- Atomic Redis singleton lease `entry_supervisor:singleton` via `SET NX EX 30`.
- Compare-and-expire renewal in `_heartbeat` with immediate shutdown on lease loss.
- Atomic compare-and-del lease release on shutdown.

### OG-002 — Confirmed exit rejection does not re-arm residual quantity [FIXED: 2026-08-16]

**Status:** **Fixed** (2026-08-16)
See TRD-002. `process_order_message` restores residual `exit_pending` qty on
`rejected` or `cancelled` for non-entry intents. Ambiguous statuses are not
re-armed. Same leftover as TRD-002: pre-claim worker exceptions never reach
this path.

### REC-002 — Multiple local positions per symbol are overwritten [FIXED: 2026-08-17]

**Status:** **Fixed** (2026-08-17).
`_reconcile_books` aggregates `local_qty[symbol] += qty` so two open
rows for the same symbol no longer overwrite.

### REC-004 — `submission_pending` can remain unresolved [PARTIAL: 2026-08-17]

**Status:** **Partial** (2026-08-17).
Unmatched `submission_pending` older than 60s is now a critical
`submission_pending_unresolved` item. Age is `(now - created_at)`.

Leftover: `order_intents.broker_requested_at` is the claim/submit time.
A row created long before the durable claim can false-positive as soon
as it is unmatched. Age from `broker_requested_at` (fall back to
`created_at` only if that is null).

### JRN-001 — Journal outbox can stick in `processing` [FIXED: 2026-08-17]

**Status:** **Fixed** (2026-08-17).
- `_claim_pending_events` and `_count_pending` query `WHERE status = 'pending' OR (status = 'processing' AND created_at < now() - interval '5 minutes')`.
- Stranded outbox events from crashed workers are automatically reclaimed on subsequent dispatcher runs up to `MAX_ATTEMPTS`.

Leftover: reclaim uses `created_at`, not last-claim time. The dispatcher
commits `processing` then works in a new session; cron is every 30s. An
event older than 5 minutes can be claimed by two runs at once. Set a claim
timestamp (or reuse `processed_at`) on claim and reclaim against that.

### DEP-001 — Known-vulnerable `fyers-apiv3` transitives

**Evidence**

`server/uv.lock`: `aiohttp==3.9.3`, `requests==2.31.0`,
`setuptools==68.0.0`, all via `fyers-apiv3`. `aiohttp` is on the broker
socket path.

**Remediation**

Override to patched versions compatible with the SDK and re-run socket/OAuth
fixtures. If the vendor pins prevent a safe bump, record advisories,
compensating controls (private network, no public API), and an expiry date.

### INF-004 / SEC-008 — Caddy edge has TLS only [PARTIAL: 2026-08-17]

**Status:** **Partial** (2026-08-17).
FastAPI middleware now sets `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, and
`Referrer-Policy: strict-origin-when-cross-origin`.

Leftover: `deploy/Caddyfile.example` is still `encode gzip` +
`reverse_proxy` only — no HSTS, CSP, rate limit, or `remote_ip`
allowlist. Headers on the origin are skipped if a future proxy caches
or strips them; HSTS belongs at Caddy.

---

## 9. Medium findings

### SEC-005 — Broker client secret is also the DB encryption key [PARTIAL: 2026-08-17]

**Status:** **Partial** (2026-08-17).
`Settings.token_encryption_key` exists. Passphrase is
`TOKEN_ENCRYPTION_KEY or FYERS_SECRET_KEY or
"antigravity-dev-token-encryption-key"`.

Leftovers:
- Production must fail closed if `TOKEN_ENCRYPTION_KEY` is empty
  (same pattern as `sql_echo`).
- Add it to `.env.prod.example` / `server/.env.example`.
- Audited re-encryption migration when rotating off `FYERS_SECRET_KEY`.
- Drop the hardcoded default outside local development.

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

### P10-002 — Two paper execution paths [FIXED: 2026-08-17]

**Status:** **Fixed** (2026-08-17).
- `complete_paper_entry_fill` checks `p10_rollout_state` and blocks execution with `ExecutionBlockedError("Manual paper entry fills are disabled while P10 automated paper trading is active. Use P10 proposal approvals.")` if stage is `paper`, `reduced_live`, or `full_live`.
- Prevents desynchronizing the `paper_broker_account` cash ledger during P10 paper rollout.

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

### REC-003 — Holdings and net-position quantity still use `max` [FIXED: 2026-08-17]

**Status:** **Fixed** (2026-08-17).
CNC inventory is `holdings.remainingQuantity + positions.netQty` (intraday
rows skipped). No longer `max(holdings, net)`.

### REC-005 — Reconciliation has no distributed run lease [FIXED: 2026-08-17]

**Status:** **Fixed** (2026-08-17).
- Acquired `reconciliation:run_lease` with 5-minute TTL at the start of `run_reconciliation`.
- If contested, skips execution cleanly returning `{"status": "skipped", "reason": "already_running"}`.
- Guaranteed release in `finally` block using atomic compare-and-delete.

### REC-006 — Broker snapshots retained too broadly [FIXED: 2026-08-17]

**Status:** **Fixed** (2026-08-17).
`_insert_item` persist an allowlisted subset (ids, qty, symbol, status,
product, prices). Retention policy is still unstated — not a reopen.

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

### OPS-003 — Schema compatibility is not checked at startup [PARTIAL: 2026-08-17]

**Status:** **Partial** (2026-08-17).
API lifespan counts eight core tables and logs a warning if `found < 7`.

Leftovers:
- Off-by-one: 7 of 8 tables does not warn.
- Warning only — the process still serves money-path routes.
- Presence of table names is not a migration version. Track applied
  files (or a `schema_migrations` row) and fail closed in production.

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
| Fyers order WS | order gateway (live only) | `running` still accepted by execution; queue drop; rejected-exit re-arm leftovers |
| Paper order events | order gateway (paper) | Redis forgery of `paper_order_events` if `REDIS_URL` leaks |
| Browser `/ws` | FastAPI | cookie auth + Origin in production; tick_subs unsubscribe leftover |

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
3. **Fix AUTH-001** so the morning refresh actually writes `job_runs`. — **[DONE: 2026-08-16]**
4. **Fix TRD-002** so a missing LTP / rejected paper exit cannot freeze a
   position out of the monitor. — **[DONE: 2026-08-16]**
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

Everything remaining in §8–§9 that is live-specific: OG-001 leftover
(`ready` only), OG-003, REC-004 leftover, DEP-001, OPS backup/schema
fail-closed, P9 enforced with owner-approved replay hash, empty paper
books, **P10-006 as `min(override, broker) * 0.25`**,
`CONFIRM_P10_REDUCED_LIVE`.

---

## 14. Prioritized remediation roadmap (clean sweep)

Suggested batching so you can fix in order without thrashing:

### Batch A — stop the bleeding (public VPS + paper) — **CLOSED 2026-08-17**

1. Caddy IP allowlist **today** if auth will take more than a day. — **[N/A: SEC-001 shipped]**
2. `echo=False` in `database.py`; restart API/workers; rotate Fyers tokens
   if logs may already contain them. — **[DONE: 2026-08-16, SEC-002]** (rotate tokens if old echo logs exist)
3. AUTH-001 `triggered_by`. — **[DONE: 2026-08-16, AUTH-001]**
4. Single-user session + CSRF + authenticated `/ws` (SEC-001, SEC-003,
   SEC-004, SEC-010). — **[DONE: 2026-08-16]**
5. TRD-002 / OG-002 exit re-arm. — **[DONE: 2026-08-17]** Exit pre-claim
   reject+restore is in place; entry pre-claim leaves `created`.

### Batch B — paper-path integrity — **CLOSED 2026-08-17**

1. Worker singleton leases (tick, monitor, entry supervisor, recon). — **[DONE: 2026-08-17, MD-002, MD-004, P10-004, REC-005]**
2. Tick `call_soon_threadsafe` + freshness gates; paper LTP age check. — **[DONE: 2026-08-17, MD-001, MD-003]**
3. Token-save unification (login + refresh + Redis). — **[DONE: 2026-08-17, AUTH-002]**
4. Journal outbox reclaim. — **[DONE: 2026-08-17, JRN-001]**
5. Block manual `complete_paper_entry_fill` while P10 paper is active. — **[DONE: 2026-08-17, P10-002]**

### Batch C — live-hardening — **UPDATED 2026-08-17**

1. Gateway `ready` vs `running` (OG-001). — **[DONE: 2026-08-17]** Worker
   sets `ready` on `on_connect`; `ensure_order_gateway_ready` strictly requires `ready` only.
2. Reconciliation: SUM by symbol (REC-002), CNC holdings+net (REC-003),
   age `submission_pending` by `broker_requested_at` (REC-004), run
   lease (REC-005), redact snapshots (REC-006). — **[DONE: 2026-08-17]**
3. `get_valid_access_token` everywhere (AUTH-003). Dedicated encryption
   key (SEC-005: production fail-closed if `TOKEN_ENCRYPTION_KEY` unset). — **[DONE: 2026-08-17]**
4. Security headers (INF-004 / SEC-008: FastAPI middleware + Caddy snippet with HSTS). Schema check at startup (OPS-003: strict 8/8 core tables, fails closed in production). — **[DONE: 2026-08-17]**
5. Enforce reduced-live `0.25×` capital (P10-006: strictly computes `min(override, broker) * 0.25`). — **[DONE: 2026-08-17]**
6. Dependency overrides (DEP-001); moved `shadcn` to devDependencies (`client/` and `swyingify/`).
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
hash; empty paper books; gateway `ready` only (not `running`); tick
freshness; singleton workers; reconciliation live-only and
aggregation-correct; **reduced-live size =
`min(override, broker) * 0.25`**; backups restored once.

Keep the session cookie and a long `APP_PASSWORD` on `api.edurel.xyz`; do
not treat confirmation phrases as authentication.
