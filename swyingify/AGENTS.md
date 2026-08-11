<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# AGENTS.md — Swyingify (SaaS)

This file is the source of truth for **Swyingify**, the multi-user scanner
SaaS. If you are an AI coding agent working under `swyingify/` or on SaaS
scan/auth/billing surfaces: **read this fully before writing any code.**

When a request conflicts with this document, stop and flag the conflict —
do not silently deviate, "improve," or reinterpret the product.

The personal trading system (Vite `client/`, Fyers money path) is governed by
the **root** [`../AGENTS.md`](../AGENTS.md). Do not apply those money-path
rules here, and do not apply Swyingify multi-tenant/SaaS rules there.

Schema details live in `../server/db/` — propose table changes there
explicitly. This file governs product scope, component boundaries, and the
line between Swyingify and the personal app.

---

## 1. What Swyingify is

A **consumer SaaS for quality swing-trading scanners**, inspired by sites like
[fpidata.in](https://fpidata.in) in UX shape (browse daily scanner results),
but focused on **legend-trader strategy templates** — not FII/FPI data (out of
scope for now).

Mental model:

```
 AUTOMATED (shared server)              HUMAN (browser)
[EOD candles → template scans] → [Browse results → chart → watchlist]
        no money                         optional auth / paywall
```

There is **no** trade confirmation, **no** order placement, and **no**
position management in Swyingify. The product ends at screening, education,
and personal watchlists.

---

## 2. Dual product boundary (non-negotiable)

| | Personal app (`client/`) | Swyingify (`swyingify/`) |
| --- | --- | --- |
| Agent doc | Root `AGENTS.md` | **This file** |
| Users | Owner only | Many users |
| Auth | None / single-user | Better Auth |
| Paywall | No | Yes (advanced scans / stats) |
| Money path | Yes | **Never** |

**Shared:** Postgres, Redis/`arq`, Python `server/` (candles + scan engine).

**Hard rules for agents:**

1. Never add Fyers order placement, execution engine calls, position monitor
   behavior, kill-switch-as-trading-control, order WebSocket, or trade
   confirm flows to Swyingify.
2. Never expose personal money-path tables
   (`trade_instructions`, `positions`, `order_*`, journal fills, etc.)
   through Swyingify APIs or UI.
3. Shared scan code under `server/app/services/` may be reused and extended
   for SaaS templates. When changing shared scoring/indicators, note impact
   on the personal screener as well.
4. Prefer SaaS-specific HTTP surfaces (e.g. `/saas/...`) distinct from
   personal trading routers. Do not overload personal "Plan trade" UX into
   Swyingify.
5. If a request would blur the two products, **stop and ask**.

---

## 3. Locked technology decisions

Do not substitute these without an explicit instruction from the user.

| Layer | Choice | Notes |
| --- | --- | --- |
| Frontend | Next.js (this app) + TanStack Query + Tailwind | Already scaffolded; read Next 16 docs in `node_modules/next/dist/docs/` before coding |
| Auth | Better Auth | Sessions/users in shared Postgres; lives in this Next app — not FastAPI as source of truth |
| Paywall / billing | Razorpay | India-first; entitlements checked before gated templates / variant runs |
| Database | Same PostgreSQL as personal app | Extend with SaaS tables; isolate money-path access via API design (and DB roles when deploying) |
| Scan compute | Python FastAPI + `arq` workers in `server/` | Full-universe scans never inline in a user HTTP request |
| Market data (v1) | Existing EOD candle pipeline (Fyers historical sync) | UI shows neutral symbols/ISINs — not broker-vendor product concepts |
| Charting | Presentational only (e.g. lightweight-charts) | No SL/target/order math in the client |

---

## 4. Locked trader roster

Product families are named after well-known swing traders. They are
**rule-based approximations for screening**, not official endorsements.
Strategies are added **one by one after research** (documented rules →
versioned template → tests). Do not invent a full multi-legend catalog in
one PR.

| Legend | Code / family key (suggested) | Scope |
| --- | --- | --- |
| Mark Minervini (Stage 2 / VCP) | `minervini` | **V1 launch — only shipping family** |
| William O’Neil (CAN SLIM–style) | `oneil` | V2+ |
| Kristjan Kullamägi (Qullamaggie) | `qullamaggie` | V2+ |
| Nicolas Darvas | `darvas` | V2+ |
| Jesse Livermore | `livermore` | V2+ |

Do not add Weinstein or other legends unless the user explicitly expands this
roster.

---

## 5. Version scope

### V1 — Indian markets only, Minervini only

- Universe: Nifty 500 (India). **No S&P 500 / US data in v1.**
- Scanner family: Minervini only (reuse/expand existing `vcp_score_v2` score
  engine and config snapshot pattern).
- Delivery: **hybrid**
  - **Global daily:** after EOD candle sync, system runs predefined templates
    once; all users browse the same results.
  - **Paid advanced:** authenticated subscribers may run allowlisted
    tighten/variant scans (quota + rate limits).
- **Beta UI (shipping now):** Minervini **Standard only** — public top **25**
  by rank after each weekday EOD. **Wide is deferred** (not shown in the
  product UI until reintroduced). Strict remains paid / later (S3).
- Aggression presets (Minervini) — product roadmap:
  1. **Wide** — Stage-2 style pool (~150–200 of 500); deferred from beta UI.
  2. **Standard** — higher-quality shortlist; **beta ships top 25**, free/public
     for today’s EOD.
  3. **Strict** — tighter VCP/contraction/volume gates; **paid** (S3).
- Auth: Better Auth accounts work. Today’s Standard board needs **no** auth.
  **Past / as-of scan history** requires sign-in when that surface is built.
  Watchlists, Strict, and paid variant runs still require auth / paid later.
- No FII/FPI data, no broker integration, no auto-entry from scores.
- Entitlement enforcement is **production-only**. Development/test environments
  open all SaaS capabilities. Production accounts with `role=admin` (or the
  server-only bootstrap admin email allowlist) also bypass paid checks for QA.
  Protected mutations and reads must still pass through the Next BFF.

### V2 — later (do not build until asked)

- Add `oneil`, `qullamaggie`, `darvas`, `livermore` **one at a time** with
  research and versioned templates.
- Optional **S&P 500** (or other) universes and scanners.
- Additional paid packs / fundamentals tier / deeper archive as decided later.

---

## 6. Free vs auth vs paid

| Capability | Anonymous | Signed-in free | Paid |
| --- | --- | --- | --- |
| Browse Minervini Standard today (top 25) | Yes | Yes | Yes |
| Basic results table + chart / spark | Yes | Yes | Yes |
| Past / as-of scan history | No (sign-in) | Latest 20 sessions | Full |
| Wide preset (when reintroduced) | TBD | TBD | TBD |
| Strict / tight VCP, OBV / advanced presets | Public count only | Upgrade prompt | Yes |
| Deeper stats / component breakdown | Limited | Limited | Full |
| Personal watchlists | No | Yes | Yes |
| Custom tighten / allowlisted variant run | No | No | Yes (5/day) |
| Other legend families | — | — | V2+ |

Legal copy on public scanners: educational / not SEBI-registered / not
investment advice. Beta: no paywall on today’s Standard board.

---

## 7. Architecture & ownership

```
swyingify/ (Next.js)
  UI, Better Auth, entitlements, watchlists, marketing
        │
        ▼
server/ (FastAPI + arq)     ← shared with personal app
  EOD candles, template registry, global cron scans,
  paid variant enqueue, read APIs for results/charts
        │
        ▼
Postgres + Redis
```

| Area | Owns |
| --- | --- |
| `swyingify/` | Product UI, Better Auth, billing UX, entitlement checks in BFF/route handlers |
| `server/` scan jobs | Global template runs, variant jobs, persisting `scan_runs` / results |
| `server/` money path | Personal app only — Swyingify must not call it |
| Personal `client/` | Owner workstation — out of Swyingify scope |

**BFF preference:** browse/read can be served via FastAPI SaaS routers and/or
Next server reads; **job enqueue for full-universe or variant scans stays with
`arq` / FastAPI** so worker topology remains single and scans never run inline
in a request path for the whole universe.

---

## 8. Data direction (SaaS)

**Reuse:** `instruments`, `universe_memberships`, `market_candles`, and the
core shape of `scan_runs` / `screening_results` (extend, don’t fork candles).

**Add / extend (propose migrations in `server/db/`):**

- Better Auth tables (`user`, `session`, `account`, `verification`, …) — done
- `scan_templates` — legend, aggression, access tier, versioned config
- `scan_runs` extensions — `template_id`, `visibility`
  (`global` \| `user` \| `personal`), `owner_user_id`, `as_of_date`
- SaaS watchlists (do not silently overload personal `watchlists` without an
  app discriminant) — S4
- `subscriptions` / `entitlements` / Razorpay references — S3

**Never expose via Swyingify:** personal order/position/journal money-path
tables.

---

## 9. Non-negotiable product rules

1. **No money path in Swyingify** — screening and watchlists only.
2. **Manual trade decision stays in the personal app** — SaaS must not
   auto-enter or confirm broker orders.
3. **V1 = India + Minervini only** — no silent S&P 500 or other-legend
   launch scope.
4. **Legends after Minervini are research-gated** — one family at a time.
5. **Full-universe scans are background jobs** (`arq`), not request-inline.
6. **Template configs are versioned and snapshotted** per run for
   reproducibility.
7. **Secrets stay server-side** (Better Auth secrets, Razorpay, Fyers,
   OpenRouter if used later).
8. **Do not add dependencies / long-running services** beyond what’s locked
   in §3 without asking.
9. **Do not confuse agents:** SaaS work → this file; personal money path →
   root `AGENTS.md`.
10. **Paywalls are production-only.** Development/test and production admins
    have full feature access. This bypass is for product development and QA;
    it never grants access to personal money-path APIs.
11. **Cashfree is not yet an approved billing provider.** Adding it requires an
    explicit update to the locked billing decision in §3; keep current billing
    state provider-aware without activating Cashfree checkout/webhooks.
12. **FastAPI paid access is BFF-only.** Next resolves Better Auth and
    subscription state, then mints a short-lived HMAC assertion containing the
    subject and granted features. FastAPI must not trust caller-declared user
    IDs or accept the raw shared key as a paid entitlement.

---

## 10. Build order (Swyingify)

Status tags: `[done]`, `[next]`, `[ ]`.

| Phase | Deliverable | Status |
| --- | --- | --- |
| S0 | Dual-product agent docs (this file + root §0) | `[done]` |
| S1 | Better Auth + landing + scanner shell | `[done]` |
| S2 | Global daily Minervini Standard (top 25); public results + chart; Wide deferred | `[done]` |
| S3 | Razorpay entitlements + Strict template behind paywall | `[partial]` — production entitlement enforcement, admin/dev bypass, subscription state, Strict template/API/UI done; Razorpay checkout/webhooks pending |
| S4 | Watchlists + richer stats + paid allowlisted variants | `[partial]` — guided 5/day custom variants and 20-session/free archive done; watchlists, alerts, exports pending |
| V2 | O’Neil → Qullamaggie → Darvas → Livermore (one-by-one); optional S&P 500 | `[ ]` |

Do not reorder or pull V2 universes/legends into V1 without an explicit user
instruction and an update to this file.

---

## 11. When in doubt

If a task would:

- place or manage broker orders from Swyingify,
- ship a non-Minervini family or non-India universe before V2,
- or blur personal vs SaaS ownership in `server/`,

treat that as **stop-and-ask**. Propose an explicit edit to this file when
the architecture should change; do not silently drift.
