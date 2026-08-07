# SwingTraderVCP Architecture, Security, and Reconciliation Audit

> Audit date: 2026-07-31
>
> Baseline: `main` at `e520307`, including uncommitted P8 journal, routing, chart, and related server changes present in the working tree
>
> Audit type: source review and local verification; no penetration test and no live broker order was placed

## 1. Executive assessment

The system has a strong high-level safety architecture: the manual checkpoint
is explicit, order placement has a single owner, intents are persisted before
broker calls, ambiguous submissions are not blindly retried, sockets are split
into dedicated processes, the global kill switch fails closed, and
reconciliation is broker-read-only.

However, the current application should be treated as **local/private paper
mode**, not production-ready live trading. The principal reasons are:

1. Every REST and browser-WebSocket operation is accessible without
   application authentication or authorization, including trade confirmation,
   kill-switch changes, token refresh, journal writes, and reconciliation.
2. SQLAlchemy parameter logging can disclose Fyers access/refresh tokens and
   the database encryption key.
3. A partial live exit can update quantity and realized P&L incorrectly.
4. A failed live-exit submission can leave a position in `exit_pending`, where
   the monitor no longer evaluates it.
5. Scheduled token refresh currently inserts an invalid `job_runs` record and
   can fail before refreshing.
6. Worker readiness and market-data freshness are not strong enough for a
   software-held stop-loss system.
7. Reconciliation has quantity, concurrency, ambiguous-submission, and
   evidence-retention weaknesses.

**Live-readiness decision:** do not enable live order placement until the
critical findings are closed, high findings affecting socket/monitor recovery
have been tested with real PostgreSQL and Redis, and the operational drills in
this report pass.

## 2. Scope and methodology

Reviewed areas:

- architecture and component-boundary compliance with `AGENTS.md`;
- FastAPI REST and browser WebSocket exposure;
- Fyers OAuth, token encryption, refresh, and shared-token usage;
- execution-engine idempotency, rate limiting, kill switch, and failure paths;
- market-data and order-WebSocket workers;
- position-monitor state and tick processing;
- reconciliation against orders, trades, positions, and holdings;
- screening, Upstox fundamentals, OpenRouter fundamental pass, and journal AI;
- journal fill outbox and chart-artifact upload;
- PostgreSQL schema/migrations and Redis coordination;
- Docker/local operational configuration;
- frontend network behavior and production build;
- Python and JavaScript dependency advisories;
- existing automated tests.

Methods used:

- static source and schema tracing;
- end-to-end data-flow and trust-boundary review;
- targeted inspection of state transitions and transaction boundaries;
- local backend tests, frontend lint, and production build;
- lockfile dependency audit and dependency-tree inspection.

Not performed:

- no live Fyers, Upstox, or OpenRouter calls;
- no live order, stop, or account mutation;
- no external network penetration test;
- no load, chaos, packet-loss, or process-kill test;
- no restoration test from PostgreSQL or Redis backup;
- no review of an actual reverse proxy, VPN, host firewall, or production
  process supervisor because none is checked into this repository.

## 3. Severity model

| Severity | Meaning in this system |
| --- | --- |
| Critical | Can directly permit unauthorized money-path control, leak broker credentials, duplicate/miss an order, or materially corrupt an open position |
| High | Can defeat a major safety layer, strand recovery, create false operational health, or expose sensitive account/trading data |
| Medium | Important defence-in-depth, availability, privacy, or operational weakness with narrower preconditions |
| Low | Maintainability or observability issue with limited immediate security impact |

All findings are open unless explicitly marked otherwise.

## 4. Controls that are working well

### Architectural boundaries

- The scanner and both LLM surfaces have no order-placement authority.
- The frontend talks only to the application backend, never directly to
  Fyers.
- Only the tick worker opens the Fyers market-data socket.
- Only the order gateway opens the Fyers order socket.
- Only the execution engine contains the Fyers async order REST call.
- The position monitor consumes Redis ticks and does not open another broker
  connection.
- Reconciliation uses a dedicated read-only client and explicitly rejects
  order/exit/convert paths.

### Money-path safeguards

- A human confirmation phrase and `manual_confirmed_at` are required before an
  entry.
- Live execution requires two configuration switches.
- The global kill switch is durable in PostgreSQL and propagated immediately
  through Redis.
- An `order_intent` and idempotency key exist before broker placement.
- The live submission claim commits before the HTTP request.
- A distributed rate limiter caps the execution path at 10 OPS.
- Timeouts and malformed/uncertain responses become `submission_unknown`
  instead of being blindly retried.
- Order and trade payload replays are deduplicated by event fingerprints and
  trade identifiers.
- The order gateway uses an atomic Redis singleton lease.

### Data and AI safeguards

- PostgreSQL is the durable trading ledger.
- Broker tokens are encrypted at rest with PostgreSQL `pgcrypto`.
- Upstox is isolated to read-only fundamentals for technical survivors.
- Fundamental facts are normalized in Python and stored with reproducibility
  metadata before model annotation.
- LLM outputs use strict structured schemas and reasoning details are excluded.
- The journal fill and outbox notification are inserted transactionally.
- Journal AI operates only on closed journal data and has no money-path tools.

These are meaningful foundations. The findings below are implementation gaps,
not a recommendation to replace the architecture.

## 5. Critical findings

### SEC-001 — No application authentication or authorization

**Evidence**

- `server/main.py` mounts every router without an authentication dependency.
- `server/app/routers/ws.py` accepts every WebSocket immediately.
- No application session, user principal, API key, or authorization middleware
  exists.

**Impact**

Any process that can reach FastAPI can create and confirm a trade instruction,
read account/trading state, engage or disengage the kill switch, trigger token
refresh or reconciliation, modify journal reviews/charges, and subscribe to
market data. CORS does not stop non-browser clients and is not authorization.

Because this is a single-user application, role-based access control is not
required, but positive single-user authentication is still required.

**Required remediation**

- Add an opaque Redis-backed application session in an `HttpOnly`,
  `SameSite=Strict` cookie.
- Compare an environment-provided password with a constant-time comparison.
- Require the session on all business REST routes and `/ws`.
- Bind a CSRF token to the session and require it on state-changing requests.
- Keep liveness public only on a private interface; protect detailed readiness.
- Add a frontend login gate and send cookies with all REST/WS requests.

### SEC-002 — SQL parameter logging can disclose broker secrets

**Evidence**

`server/app/database.py` creates the engine with:

```python
create_async_engine(settings.database_url, echo=True)
```

`server/app/security.py` supplies the Fyers access token, refresh token, and
`settings.fyers_secret_key` as SQL bind parameters to `pgp_sym_encrypt` and
`pgp_sym_decrypt`.

**Impact**

SQL logs can contain plaintext access tokens, refresh tokens, and the key used
to encrypt/decrypt those tokens. Anyone with log access may gain broker access
and the ability to decrypt stored credentials. This negates the at-rest
encryption boundary.

**Required remediation**

- Set SQL echo to false by default and make any SQL diagnostic mode explicit.
- Never enable parameter logging on secret-bearing queries.
- Rotate the Fyers access/refresh tokens after log exposure is ruled out or
  logs are securely removed.
- Introduce a dedicated token-encryption key separate from the Fyers client
  secret, with a documented rotation procedure.

### TRD-001 — Cumulative partial-exit fills are applied twice

**Evidence**

In `server/app/services/order_gateway.py`, `process_trade_message` calculates
`total_filled` by summing all fills for an intent. On every new exit trade it
then computes:

```text
exit_qty = min(total_filled, current position open_quantity)
```

and applies that quantity again to `open_quantity` and realized P&L.

**Impact**

For an exit of 10 shares filled as 4 then 6, the first event applies 4. The
second event sees cumulative 10 and current open quantity 6, applies 6 using
the aggregate price, and may appear correct in that exact split. Other replay,
partial, overfill, and fill-order combinations can reapply previously
accounted quantity and price basis. More importantly, the position delta is
not explicitly tied to the newly inserted fill, so the accounting is not
provably replay-safe even though fill insertion is deduplicated.

**Required remediation**

- Use the newly inserted fill's quantity and price for the position/P&L delta.
- Use cumulative fill quantity only to determine intent status and overfill.
- Lock the position and fill aggregate in one transaction.
- Add tests for 4+6, 6+4, 3+3+4, duplicate second event, out-of-order events,
  and an overfill payload.

### TRD-002 — Failed live-exit submission can strand an armed position

**Evidence**

- `process_position_tick` creates an exit intent and transitions the position
  to `exit_pending` before the broker call.
- `server/app/workers/position_monitor.py` commits that change, then calls
  `submit_live_exit_intent` in a new session.
- Submission exceptions are logged, but the state is not re-armed.
- Future monitor evaluation skips every state except `open` and
  `trailing_active`.

**Impact**

If authentication, gateway health, kill-switch timing, Redis, rate limiting,
or HTTP setup fails before a broker request is made, the position may remain
open at Fyers while local state is `exit_pending`. The monitor then stops
evaluating its stop/target, defeating the primary exit control.

**Required remediation**

- Distinguish “no broker request occurred” from ambiguous submission.
- Safely retry an existing `created` exit intent with the same idempotency key
  after pre-submission failure.
- Never retry `submission_pending` or `submission_unknown` automatically.
- On confirmed rejection/cancellation with residual quantity, restore the
  preceding armed state, emit a critical event, and permit a new attempt only
  after a fresh valid tick.
- Add restart tests for every failure boundary around the durable claim and
  HTTP call.

### AUTH-001 — Scheduled token refresh violates the live schema

**Evidence**

`server/app/services/token_refresh.py` inserts:

```sql
INSERT INTO job_runs (job_type, job_key, status, started_at)
```

`server/db/schema.sql` defines `job_runs.triggered_by text NOT NULL`.

**Impact**

Against the real schema, the scheduled token-refresh job can fail at its first
insert and never refresh the access token before market open. Mocked tests do
not exercise this database constraint.

**Required remediation**

- Insert `triggered_by='scheduler'` for cron and `manual` for explicit runs.
- Add a real PostgreSQL integration test that applies migrations and runs the
  job start/finish transaction.
- Alert if the last successful token refresh is not recent before market open.

## 6. High findings

### SEC-003 — OAuth state is not validated by the backend

**Evidence**

- `/auth/url` generates and returns a random `state` but does not store it
  server-side.
- GET and POST callbacks accept `state` but `_exchange_code_and_save` never
  validates it.
- The GET callback hardcodes `http://localhost:3000` and places raw exception
  details in a redirect query string.

**Risk**

This permits login-CSRF/account-confusion and state replay when the route is
reachable. Error details may leak through browser history, logs, or referrers.

**Remediation**

Store state in Redis with a short TTL, bind it to the application session, and
atomically consume it once. Reject missing/mismatched/replayed state. Use a
configured allowlisted frontend redirect and a generic error code.

### SEC-004 — Browser WebSocket has no trust or resource controls

**Evidence**

`server/app/routers/ws.py` has no session check, Origin validation, Pydantic
message schema, input-size cap, symbol-count cap, or instrument allowlist.

Subscribe requests are forwarded to `tick_subs`. Unsubscribe requests alter
only the browser session; disconnect cleanup does not remove tick-worker
subscriptions.

**Risk**

An arbitrary reachable client can cause broker subscription growth and consume
memory/bandwidth. Subscription demand leaks after disconnect. The current
global subscription channel also lacks source ownership, so a future or direct
publisher could remove a mandatory open-position subscription.

**Remediation**

- Authenticate and validate Origin before accepting.
- Limit frame size, symbols per request/session, rate, and total sessions.
- Accept only active instrument symbols.
- Represent chart-session demand as expiring session-specific Redis sets.
- Have the tick worker compute a union of mandatory DB symbols and active chart
  demand; a browser must never remove position/watchlist/benchmark demand.

### INF-001 — Development PostgreSQL and Redis are broadly exposed

**Evidence**

`docker-compose.yml` publishes PostgreSQL on `5480:5432` with password `algo`
and Redis on `6380:6379` without authentication.

**Risk**

On a host whose firewall permits access, an attacker can read/change the
trading ledger or publish forged ticks, controls, worker heartbeats, and jobs.
Forged Redis state can directly affect monitoring and execution safety checks.

**Remediation**

- Bind development ports to `127.0.0.1`.
- Supply database and Redis credentials through environment/secrets.
- Keep both services on a private production network and enable Redis ACL/TLS
  where traffic leaves one host.
- Use separate least-privilege database roles for schema migration and runtime.

### INF-002 — Redis credentials/TLS are parsed inconsistently

**Evidence**

`server/main.py` and `server/app/worker.py` manually extract only host, port,
and database for `RedisSettings`. Other workers use `redis.from_url` and would
honor password or TLS settings.

**Risk**

Enabling authenticated or TLS Redis can silently break API/arq connections
while workers behave differently, encouraging insecure production
configuration or causing partial outages.

**Remediation**

Create one shared Redis settings parser that preserves scheme, username,
password, database, and TLS options for arq and redis-py clients. Add tests for
`redis://user:pass@host/db` and `rediss://`.

### AUTH-002 — Initial OAuth login does not replace shared token cache

**Evidence**

`_exchange_code_and_save` persists the new token to PostgreSQL but does not
replace `auth:fyers:access_token`, its expiry key, or auth health in Redis.

**Risk**

Workers may continue using an old cached token until its TTL expires even
after a successful login, causing avoidable socket/order failures.

**Remediation**

Use one token-save service for login and refresh that atomically updates the
database, Redis cache, expiry, and health signal.

### AUTH-003 — Some Fyers consumers bypass the shared valid-token path

**Evidence**

Historical fetch/validation code reads and decrypts broker tokens directly
rather than consistently calling `get_valid_access_token`, despite the module
contract naming that function as the sole entry point.

**Risk**

Components can use expired tokens, skip refresh/health signalling, and behave
differently during authentication failure.

**Remediation**

Route every historical REST, tick socket, order socket, execution, and broker
read through the same valid-token service. Ban direct `get_fyers_token` imports
outside the auth/security layer with a test or static check.

### MD-001 — Tick SDK callback is not handed to asyncio thread-safely

**Evidence**

`server/app/workers/tick_worker.py` calls `asyncio.Queue.put_nowait` directly
inside the threaded Fyers SDK callback. The order gateway correctly uses
`loop.call_soon_threadsafe`; tick ingestion does not.

**Risk**

Cross-thread queue use is unsupported and can lose, reorder, or corrupt
wakeup behavior under load. Queue overflow is caught only as a generic error.

**Remediation**

Capture the running loop and use `loop.call_soon_threadsafe` to enqueue. Track
queue depth/drops and mark the feed unhealthy on overflow.

### MD-002 — Tick worker singleton and socket health are not authoritative

**Evidence**

- Tick singleton protection is a read-then-check heartbeat, not an atomic
  lease; two instances can race.
- The heartbeat continues to write `running` without checking the SDK socket's
  current connection state.
- `on_close` and `on_error` log but do not update readiness or force a fresh
  token/reconnect lifecycle.

**Risk**

Duplicate market sockets can violate the architecture and provider limits.
Operations/UI can see `running` while no usable market feed exists.

**Remediation**

Add an atomic Redis lease, refresh it only while the owner is healthy, publish
`connecting/ready/degraded/stopped`, and clear readiness immediately on close,
auth error, or queue overflow.

### MD-003 — Position monitor accepts stale or out-of-order ticks

**Evidence**

The tick envelope includes provider and receipt timestamps, but
`PositionMonitorRuntime.handle_tick` evaluates only symbol and LTP. It does not
check age, future skew, monotonicity, or positive numeric bounds.

**Risk**

A delayed or reordered tick can incorrectly trigger a software stop/target or
ratchet a trailing stop. Redis compromise could inject arbitrary LTPs.

**Remediation**

- Normalize timestamps in ingestion.
- Reject non-positive, future, regressing, or excessively delayed ticks.
- Default to a configurable 10-second receipt-age limit during trading hours.
- Suppress money-path actions and emit a critical stale-feed event when the
  feed is not trustworthy.

### MD-004 — Position monitor has no singleton lease

**Evidence**

The monitor writes `position_monitor:status` but does not acquire an atomic
ownership lock before subscribing and processing positions.

**Risk**

Two monitors can process the same tick, create duplicate trailing events, and
race on exits. Idempotency reduces double-placement risk but does not make
duplicate monitors safe.

**Remediation**

Add an owner-valued Redis lease with compare-and-expire renewal and
compare-and-delete release. Stop processing immediately if ownership is lost.

### OG-001 — Gateway heartbeat can claim readiness before socket readiness

**Evidence**

The gateway calls `socket.connect()` and `socket.subscribe()`, then sets status
to `running` without synchronizing on `on_connect`. Its heartbeat continues
through non-auth closes because `on_close` only logs.

**Risk**

The execution engine may allow a REST order while the only correlation socket
is disconnected, increasing ambiguous state and reliance on reconciliation.

**Remediation**

Set `ready` only from a confirmed connection/subscription callback. Mark
degraded immediately on close/error and make the execution engine require a
fresh `ready`, not process `running`.

### OG-002 — Live exit rejection/cancellation does not re-arm residual quantity

**Evidence**

The gateway closes an unfilled rejected entry but has no equivalent recovery
that moves a rejected/cancelled exit with remaining quantity from
`exit_pending` back to `open` or `trailing_active`.

**Risk**

The broker can reject/cancel an exit while the application permanently stops
monitoring the still-open position.

**Remediation**

Persist the pre-exit armed state in the trigger event/intent. On a confirmed
terminal rejection and residual quantity, restore it, emit a critical event,
and require a fresh tick before a bounded new attempt.

### REC-001 — Paper positions can create false live reconciliation mismatches

**Evidence**

Reconciliation loads open positions by state, while live intents are filtered
by `execution_mode='live'`. Paper positions can therefore be compared against
the real broker account.

**Risk**

Routine paper operation can generate critical/noisy quantity discrepancies,
masking genuine live divergence.

**Remediation**

Derive the reconciliation position set only from app-managed live entry/fill
lineage, not position state alone.

### REC-002 — Multiple local positions per symbol are overwritten

**Evidence**

The local position quantity map assigns one value per symbol instead of
aggregating every open live position for that symbol.

**Risk**

The result depends on row order and can report false matches or mismatches when
more than one app position exists for an instrument.

**Remediation**

Aggregate signed open quantity with SQL `SUM` using explicit side/product
semantics and test multiple positions in the same symbol.

### REC-003 — Holdings and net-position quantity are collapsed with `max`

**Evidence**

Reconciliation currently uses the maximum of broker positions and holdings for
a symbol when comparing CNC quantity.

**Risk**

Holdings and net positions describe different settlement/intraday views. An
undocumented `max` can hide or invent discrepancies around buy/sell activity
and settlement.

**Remediation**

Keep settled holdings and current net positions as separate domains. Normalize
their official Fyers fields with fixture tests and report domain-specific
issues rather than synthesizing an undocumented total.

### REC-004 — `submission_pending` can remain unresolved indefinitely

**Evidence**

Ambiguous `submission_unknown` intents are examined, but a process crash after
the durable `submission_pending` claim and before local response handling can
leave a stale pending intent that reconciliation does not prominently flag.

**Risk**

An order may exist at Fyers while the application retains a nonterminal local
state indefinitely.

**Remediation**

Age nonterminal live intents. Reconciliation must correlate stale
`submission_pending` records through order/trade books or create a critical
unresolved item. It must never automatically place them again.

### REC-005 — Reconciliation has no distributed run lease

**Evidence**

Cron and manual endpoints can enqueue the job, but `run_reconciliation` does
not acquire an owner lease covering the whole broker fetch and compare/write
cycle.

**Risk**

Concurrent runs can duplicate healing attempts/items, race status, and consume
broker rate budget. Job naming alone does not prove mutual exclusion across
manual and scheduled paths.

**Remediation**

Use an expiring owner-valued Redis lease and make “already running” an explicit
result. Healing remains idempotent through gateway persistence.

### JRN-001 — Journal outbox work can be stranded in `processing`

**Evidence**

The dispatcher commits `status='processing'`, but the claim query selects only
`pending`. `_count_pending` counts both, so a crash after claim leaves a record
that is counted and repeatedly re-enqueues work but can never be reclaimed.

**Risk**

The journal can permanently miss a fill even though the trading ledger is
complete.

**Remediation**

Add `claimed_at` and an owner/lease. Reclaim `processing` rows older than five
minutes with bounded attempts. Test process death immediately after claim.

### DEP-001 — Known vulnerable Python transitive dependencies

**Evidence**

The dependency audit reported 42 known advisories across:

- `aiohttp 3.9.3`
- `requests 2.31.0`
- `setuptools 68.0.0`

`uv tree --invert` traces all three to `fyers-apiv3 3.1.14`.

**Risk**

The exact exploitability varies, but `aiohttp` participates in broker-facing
network code and should not remain on a heavily vulnerable release without an
explicit compatibility decision.

**Remediation**

Upgrade/override to patched versions supported by the Fyers SDK and run socket,
OAuth, historical, and order-gateway fixture tests. If the vendor package
prevents a safe update, record the affected advisories, compensating controls,
and an expiry date for the exception.

## 7. Medium findings

### SEC-005 — Broker credential and encryption key are the same secret

`server/app/security.py` uses `FYERS_SECRET_KEY` as both the OAuth client secret
and the symmetric database encryption key. A leak or required broker-secret
rotation therefore affects both boundaries. Add a dedicated high-entropy
encryption secret and perform an audited re-encryption migration.

### SEC-006 — Error messages and provider payloads are too verbose

Auth callbacks and refresh routes can return raw exception strings, and refresh
rejection logs the provider response dictionary. Reconciliation/system events
also retain error strings. Map provider errors to stable internal codes,
redact token/account fields, and expose detailed diagnostics only in restricted
logs.

### SEC-007 — Manual refresh cooldown is process-local

The 30-second auth-refresh cooldown is a module global. Multiple API processes
or restarts bypass it. Move it to Redis with an atomic TTL key and apply a
separate per-session request rate limit.

### SEC-008 — Missing security headers and production TLS contract

The app sets CORS but not CSP, HSTS, `X-Content-Type-Options`, frame policy, or
a referrer policy. There is no checked-in TLS/reverse-proxy definition. Define
the same-origin HTTPS/WSS edge, trusted proxy behavior, and security headers
before network exposure.

### JRN-002 — Chart upload buffers the entire body before checking the limit

The artifact upload calls `await request.body()` before enforcing its 5 MiB
limit. An unauthenticated caller can force larger allocations. Stream with a
Content-Length precheck plus a hard incremental byte cap, validate MIME and
PNG signature, and bind the artifact claim to the authenticated session.

### JRN-003 — Journal review and actual-charge validation is weak

Notes/tags/lessons lack tight length/count normalization. Actual charge fields
permit negative or internally inconsistent values. Add maximum lengths, tag
count/format rules, non-negative monetary fields, and a computed/validated
total.

### JRN-004 — Journal AI privacy setting differs from fundamentals

The fundamental OpenRouter client denies provider data collection, while the
journal client does not apply the same setting. Journal notes can be more
sensitive than public fundamentals. Apply the same provider privacy option and
document precisely what closed-trade data leaves the system.

### JRN-005 — AI run can remain queued after enqueue failure

The API commits a `journal_ai_runs` row before calling Redis enqueue. A Redis
failure can leave it `queued` indefinitely. Add a periodic queued-run sweeper
or an outbox/transactional enqueue pattern.

### JRN-006 — Period summary charge aggregation may omit selected data

The period-summary path reads estimated charge fields from journal list rows,
but the shared list projection does not select every charge field it later
expects. Add a purpose-specific aggregate query and tests with estimated and
actual charges.

### HIST-001 — Historical validation runs in the API process

`server/app/routers/historical.py` uses FastAPI `BackgroundTasks` for
validation. This violates the intended thin API/process topology and work can
be lost on API restart. Move it to arq with durable job progress/cancellation.

### TRD-003 — ATR trailing is accepted but not implemented

`TrailingRule` allows `atr`, while the monitor logs that ATR is skipped until a
feed exists. A user can believe trailing protection is active when it is not.
Remove/disable ATR from API and UI until its server-side data and restart-safe
rule are implemented and tested.

### REC-006 — Full broker snapshots are retained/exposed more broadly than needed

Reconciliation stores broker snapshots/evidence that may include account and
order details beyond what a discrepancy requires. Normalize and redact to the
smallest fields needed for correlation and audit; apply a retention policy.

### REC-007 — Schedule is not exchange-calendar aware

Reconciliation starts at 09:00, includes 15:45, and runs every weekday,
including market holidays. EOD scheduling is also weekday-only. Introduce an
NSE trading-calendar check or explicitly document harmless off-market runs and
alert semantics.

### OG-003 — Order queue overflow drops an event before stopping

The gateway stops after a full 10,000-item queue but the triggering event is
already dropped. Reconciliation may recover it, but readiness should degrade
before exhaustion, queue depth should be observable, and a durable fallback or
immediate reconciliation request should follow any drop.

### OG-004 — Synthetic trade-ID fallback needs stronger normalization

When Fyers omits a trade number, locally derived identifiers can vary with
payload aliases or formatting. Canonicalize the exact broker fields and add
fixtures for missing IDs so the same trade cannot be stored twice.

### OPS-001 — Health endpoint is incomplete

`/health` checks only `SELECT 1`. It can report healthy while Redis, migrations,
tick ingestion, monitor, order gateway, or auth is unavailable. Split
liveness/readiness; keep the detailed endpoint private and avoid returning
secrets.

### OPS-002 — No production supervision, backup, or recovery definition

The repository contains a development launcher but no production supervisor,
TLS edge, backup retention, restore drill, or disaster-recovery runbook. For a
software-stop system, monitor/socket supervision and database restoration are
part of correctness, not optional hosting details.

### OPS-003 — Schema compatibility is not checked at startup

Migrations are manual SQL files and there is no version table/startup schema
check. A process can start against an incomplete schema and fail at runtime.
Track applied migrations and make money-path readiness fail closed on an
unsupported schema version.

### DEP-002 — Frontend tooling vulnerabilities are in production dependencies

The JavaScript audit reported three high and one moderate advisory under the
shadcn CLI dependency tree (`fast-uri`, `postcss`, `brace-expansion`, and
`@hono/node-server`). The CLI is declared in `dependencies` even though the SPA
does not import it at runtime. Move `shadcn` to `devDependencies`, update
patched transitives, and audit the resulting production tree.

### TEST-001 — Money-path tests do not exercise real infrastructure

The suite has good unit coverage but predominantly uses mocks/fakes. There is
no observed test that runs the money path against migrated PostgreSQL,
authenticated Redis, multiple real processes, or recorded SDK socket
callbacks. This is why the `triggered_by NOT NULL` defect passed the suite.

## 8. Low and maintainability findings

- Frontend lint passes with four Fast Refresh warnings caused by files that
  export both components and helpers.
- The frontend production build passes but produces a roughly 799 KB main
  JavaScript chunk (about 241 KB gzip), triggering the build's 500 KB warning.
  Route-level lazy loading would reduce initial loading and isolate feature
  failures.
- arq emits four deprecation warnings on Python 3.14 because it still calls
  `asyncio.iscoroutinefunction`; track upstream compatibility before Python
  3.16.
- Worker status data is spread across ad hoc Redis JSON documents. A shared
  health schema would make readiness and UI behavior consistent.
- `server/README.md` and `server/db/README.md` do not yet describe the P8
  migration even though the current working tree includes it.
- Browser chart capture is opportunistic: if no UI is open, a journal chart
  artifact may remain pending. This is acceptable only if documented as an
  optional artifact rather than a guaranteed journal invariant.

## 9. Reconciliation-specific assessment

### What reconciliation does correctly

- Runs outside the API process through arq.
- Uses the shared valid-token path.
- Fetches broker books with GET only.
- Prohibits order async, exit, and convert paths in its client contract.
- Persists a run and individual discrepancy items.
- Reuses replay-safe gateway functions when a broker event matches a known
  local intent.
- Flags external broker trades instead of automatically fighting them.
- Emits a critical system event when critical items remain open.

### What it must never do

- Never place an order to “correct” a mismatch.
- Never flatten an external/manual position automatically.
- Never retry an ambiguous order just because it is absent from one broker
  response.
- Never collapse holdings and intraday/net positions using undocumented
  arithmetic.
- Never overwrite local audit history with the broker's latest snapshot.

### Required reconciliation decision model

For every run, evaluate independent domains:

1. **Order intent correlation:** match by `id_fyers`, broker order ID, exchange
   ID, and compact local tag.
2. **Trade/fill completeness:** insert only a missing, uniquely identified
   broker fill through the gateway processor.
3. **Submission ambiguity:** resolve stale pending/unknown intent when broker
   evidence is conclusive; otherwise preserve ambiguity and alert.
4. **Intraday/net position view:** compare normalized signed quantities using
   official Fyers position semantics.
5. **Settled holding view:** compare holdings separately from the intraday
   view.
6. **External activity:** flag broker orders/trades with no local intent and
   expose them for human classification/import.

Every item should contain normalized identifiers, expected/actual values,
severity, evidence timestamp, and resolution status. Raw account snapshots
should have a short, explicit retention period if retained at all.

## 10. WebSocket and webhook assessment

### Fyers market WebSocket

Correct owner: tick ingestion worker. Primary risks are duplicate workers,
false readiness, unsafe thread handoff, unbounded dynamic subscriptions, and
unvalidated tick freshness.

### Fyers order WebSocket

Correct owner: order gateway. Event persistence is replay-aware and separated
from decision-making. Primary risks are readiness before confirmed connection,
queue-drop recovery, and rejected-exit re-arming.

### Browser WebSocket

Correct owner: FastAPI. It fans out Redis ticks only and does not contact
Fyers. It needs application authentication, Origin checks, validated/capped
messages, and source-aware subscription cleanup.

### HTTP webhooks

No inbound provider webhooks exist. This is acceptable because Fyers order and
market events use WebSockets and REST reconciliation covers missed state. Do
not add a webhook merely for architectural symmetry.

If one is required later, minimum controls are:

- provider signature verification over the raw body;
- timestamp tolerance and nonce/event-ID replay rejection;
- strict body limit and media-type validation;
- durable event/fingerprint insertion before asynchronous processing;
- constant-time secret comparison and rotation;
- no direct execution-engine call from the ingress handler;
- the same gateway persistence/state-machine code used by socket and
  reconciliation events.

## 11. Dependency audit snapshot

### Python

| Package | Version | Relationship | Audit state |
| --- | --- | --- | --- |
| `aiohttp` | 3.9.3 | transitive from `fyers-apiv3` | multiple known advisories; update required |
| `requests` | 2.31.0 | transitive from `fyers-apiv3` | known advisories; update required |
| `setuptools` | 68.0.0 | transitive from `fyers-apiv3` | known advisory; update or remove runtime need |
| `fyers-apiv3` | 3.1.14 | direct broker SDK | compatibility owner for the three packages above |

The audit reported 42 vulnerability records across the three transitive
packages. Counts can change as advisory databases are updated; rerun the audit
before remediation and release.

### JavaScript production tree

| Advisory tree | Reported severity | Exposure note |
| --- | --- | --- |
| `fast-uri` under shadcn CLI | High | tooling, not imported SPA runtime |
| `postcss` under shadcn CLI | High | tooling/build path |
| `brace-expansion` under shadcn CLI | High | tooling path |
| `@hono/node-server` under shadcn CLI | Moderate | CLI server path |

Moving the CLI to development dependencies narrows production installation but
does not remove the need to patch developer/build environments.

## 12. Verification results

Commands executed against the current working tree:

```text
Backend pytest:        98 passed, 9 subtests passed, 4 deprecation warnings
Frontend TypeScript:   passed as part of pnpm build
Frontend Vite build:   passed, one large-chunk warning
Frontend oxlint:       passed, 4 Fast Refresh warnings
```

These results show that the current unit behavior and frontend compilation are
stable. They do not close the critical findings because the most important
failures occur at real transaction, socket, process, and network boundaries.

## 13. Prioritized remediation roadmap

### P0 — Before any network exposure or live order

1. Add single-user application sessions, CSRF protection, and authenticated
   WebSocket access.
2. Disable SQL parameter echo and rotate/review exposed token material.
3. Fix scheduled refresh's `triggered_by` insert and add a real DB test.
4. Fix exit-fill delta accounting.
5. Make pre-submission exit failure and confirmed rejection re-arm safely.
6. Bind/secure PostgreSQL and Redis; support authenticated/TLS Redis uniformly.
7. Disable ATR selection until implemented.

### P1 — Before relying on software exits

1. Add atomic tick-worker and monitor leases.
2. Make market/order status connection-aware and execution require `ready`.
3. Make the tick thread bridge safe and observable.
4. Enforce tick freshness/monotonicity and alert on stale feeds.
5. Make browser subscriptions session-owned and unable to remove mandatory
   symbols.
6. Reclaim journal outbox and AI queue work after crashes.
7. Resolve Python network dependency advisories or record time-limited vendor
   exceptions.

### P2 — Reconciliation and operational hardening

1. Add a reconciliation singleton lease.
2. Filter live-only positions and aggregate multiple positions correctly.
3. Separate holdings and net-position comparisons.
4. Surface stale pending/unknown submissions explicitly.
5. Minimize and expire broker evidence.
6. Add liveness/readiness, migration compatibility, process supervision,
   backup/restore, and alerting runbooks.
7. Add exchange-calendar awareness.

### P3 — Defence in depth and maintainability

1. Separate/rotate the token encryption key.
2. Add security headers and a documented HTTPS/WSS edge.
3. Tighten journal validation and upload handling.
4. Align OpenRouter privacy settings.
5. Move CLI-only frontend packages to development dependencies and split the
   large frontend bundle.

## 14. Required test and operations gates

### Automated tests

- Session expiry, logout, constant-time password validation, CSRF rejection,
  and WebSocket auth/Origin rejection.
- OAuth state success, mismatch, expiry, and replay.
- Token cache replacement after login and scheduled refresh against migrated
  PostgreSQL.
- Concurrent trade confirmation, kill-switch changes, and rate-limit calls.
- Tick-worker and monitor lease contention/loss.
- Stale, future, duplicate, and out-of-order tick sequences.
- Exit fills of 4+6, 6+4, 3+3+4, duplicates, overfills, and restart between
  fill persistence and position update.
- Pre-HTTP exit failure, HTTP timeout, malformed success, rejection,
  cancellation, and monitor restart.
- Reconciliation with paper and live positions, multiple same-symbol
  positions, settled holdings, intraday positions, external trades, and stale
  submission states.
- Journal crash after claim and AI enqueue failure.
- Authenticated Redis URL/TLS parsing in API, arq, and each worker.

### Real infrastructure integration

Run an opt-in suite against disposable PostgreSQL and Redis that:

1. applies every migration from an empty database;
2. verifies constraints and transaction isolation;
3. starts two copies of singleton workers and confirms only one owns work;
4. replays recorded Fyers market/order fixture payloads;
5. kills workers at each durable boundary and validates recovery;
6. confirms no duplicate order-intent transition or fill can occur.

### Paper-mode operational drill

- Keep `EXECUTION_MODE=paper` and the kill switch engaged during setup.
- Start all processes under the intended supervisor.
- Open a paper position, terminate the UI and API, and verify monitoring
  continues.
- Terminate/restart the position monitor and verify re-arm from PostgreSQL.
- Simulate stale ticks and confirm no exit is placed from stale data.
- Terminate/restart the order gateway fixture process and verify readiness
  blocks submission.
- Exercise kill-switch engage/disengage and verify it does not imply flatten.
- Run reconciliation and resolve each discrepancy type through the UI/audit
  trail.
- Restore PostgreSQL from backup into a clean environment and verify ledger
  consistency.

Only after these gates pass should live mode be double-armed, initially with a
quantity the operator can afford to lose. Software stops are never equivalent
to exchange-held stops.

## 15. Acceptance criteria for closing this audit

The application is ready for controlled private live use only when:

- unauthenticated REST and WebSocket requests are rejected;
- secrets and encryption keys cannot appear in SQL/application logs;
- OAuth state is server-validated and single-use;
- all Fyers consumers use one token lifecycle;
- only one healthy tick worker, order gateway, and monitor can own work;
- the execution engine requires confirmed order-socket readiness;
- stale/out-of-order ticks cannot create a money-path transition;
- partial exits update position quantity and P&L exactly once per fill;
- failed/rejected exits cannot leave an unmonitored residual position;
- reconciliation is single-run, live-only, aggregation-correct, read-only, and
  explicit about ambiguous state;
- journal/AI work is reclaimable after a crash;
- dependency audits have no unaccepted exploitable critical/high findings;
- real PostgreSQL/Redis integration and restart drills pass;
- production TLS, network isolation, supervision, backup, restore, and alert
  ownership are documented and tested.

Until then, paper mode and a private local network are the appropriate safety
posture.
