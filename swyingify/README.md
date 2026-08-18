# Swyingify

Swyingify is the public scanner surface for India-only, Minervini-inspired
research. It is intentionally separate from the personal trading app: this
app has no broker connections, orders, positions, trade confirmation, or
execution controls.

## Run locally

```bash
pnpm install
cp .env.example .env.local
pnpm dev
```
The scanner and preview stock pages work without a database. Email/password
authentication requires the shared Postgres database and migrations through
`../server/db/migrations/011_saas_entitlements_and_strict.sql` to be applied.
Google sign-in additionally requires `GOOGLE_CLIENT_ID` and
`GOOGLE_CLIENT_SECRET` (and a matching callback URL in Google Cloud).

Required production environment values (see `.env.example`):

- `DATABASE_URL`, `BETTER_AUTH_SECRET`, `BETTER_AUTH_URL`,
  `NEXT_PUBLIC_BETTER_AUTH_URL`
- Optional Google OAuth: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
- FastAPI SaaS reads: `API_URL` and a matching `SAAS_INTERNAL_API_KEY`. Next
  uses the key to sign short-lived access assertions after resolving the
  Better Auth session; browsers never receive it.
- Production admin bypass: `SWYINGIFY_ADMIN_EMAILS` (comma-separated,
  server-only), or set `user.role = 'admin'` after migration 011.

Keep all secrets server-side.

## Entitlements and environment behavior

Entitlements are enforced only when the Next app runs with
`NODE_ENV=production`. Development and test builds receive all capabilities
without requiring a session or subscription. FastAPI mirrors this rule with
`APP_ENVIRONMENT=development|test|production`; production deployments must set
`APP_ENVIRONMENT=production` and use the same `SAAS_INTERNAL_API_KEY` as Next.
FastAPI defaults to production/fail-closed and should only be reachable from
the BFF or a private service network, never as a second public paywall.

Production admins bypass paid checks. Regular Pro access comes from an active
or trialing row in `saas_subscriptions`. For manual production testing:

```sql
UPDATE "user" SET role = 'admin' WHERE email = 'owner@example.com';

INSERT INTO saas_subscriptions (user_id, provider, plan_code, status)
SELECT id, 'manual', 'pro', 'active'
FROM "user"
WHERE email = 'subscriber@example.com';
```

The schema reserves `razorpay` as the locked production provider. Checkout and
webhooks stay disabled until launch price and billing details are confirmed.
Cashfree requires an explicit architecture update before it is added.

SEO / crawl controls (also in `.env.example`):

- `SITE_URL`: canonical origin for metadata, sitemap, and robots
- `SEO_INDEXING_ENABLED`: keep `false` until real scan data and the final
  domain are ready; when false, robots disallow all and the sitemap is empty
- `GOOGLE_SITE_VERIFICATION`: optional Search Console token

Canonical live scanner path: `/scanners/minervini-vcp` (legacy `/scanner` and
`/scanners/minervini` permanently redirect there).

## Preview / live scope

When `API_URL` points at the FastAPI server (and migration 011 is applied), the
Minervini Standard and Strict boards load their latest global top-25 results.
Without the API, fixtures remain labeled as preview. Scanner query state uses `q`
and allowlisted `sort` / filter URL parameters. Past scans require Better Auth.
Filtered scanner URLs and stock detail pages stay `noindex`.

Paid/test surfaces:

- `/scanners/minervini-vcp/strict`: paid Strict board; public result count.
- `/scanners/minervini-vcp/custom`: guided user variants, five per day.
- `/past-scans`: 20 sessions for signed-in free; complete for Pro/admin/dev.
- `/account`: effective tier, bypass reason, and enforced limits.

## Checks

```bash
pnpm lint
pnpm exec tsc --noEmit
pnpm seo:check
pnpm exec next build --webpack
```
