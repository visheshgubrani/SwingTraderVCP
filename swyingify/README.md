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
authentication requires the shared Postgres database and the Better Auth
migration at `../server/db/migrations/007_swyingify_auth.sql` to be applied.
Google sign-in additionally requires `GOOGLE_CLIENT_ID` and
`GOOGLE_CLIENT_SECRET` (and a matching callback URL in Google Cloud).

Required production environment values (see `.env.example`):

- `DATABASE_URL`, `BETTER_AUTH_SECRET`, `BETTER_AUTH_URL`,
  `NEXT_PUBLIC_BETTER_AUTH_URL`
- Optional Google OAuth: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
- FastAPI SaaS reads: `API_URL` (and matching `SAAS_INTERNAL_API_KEY` for
  authenticated past-scan dates)

Keep all secrets server-side.

SEO / crawl controls (also in `.env.example`):

- `SITE_URL` — canonical origin for metadata, sitemap, and robots
- `SEO_INDEXING_ENABLED` — keep `false` until real scan data and the final
  domain are ready; when false, robots disallow all and the sitemap is empty
- `GOOGLE_SITE_VERIFICATION` — optional Search Console token

Canonical live scanner path: `/scanners/minervini-vcp` (legacy `/scanner` and
`/scanners/minervini` permanently redirect there).

## Preview / live scope

When `API_URL` points at the FastAPI server (and migration `010_saas_scan_templates.sql`
is applied), the Minervini Standard board loads the latest global top-25 results.
Without the API, fixtures remain labeled as preview. Scanner query state uses `q`
and allowlisted `sort` / filter URL parameters. Past scans require Better Auth.
Filtered scanner URLs and stock detail pages stay `noindex`.

## Checks

```bash
pnpm lint
pnpm exec tsc --noEmit
pnpm seo:check
pnpm exec next build --webpack
```
