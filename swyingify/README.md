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

Required production environment values are documented in `.env.example`:
`DATABASE_URL`, `BETTER_AUTH_SECRET`, `BETTER_AUTH_URL`, and Google OAuth
credentials. Keep all secrets server-side.

## Preview scope

Fixtures are deterministic fictional NSE-style companies and are labeled
“Preview data” throughout the UI. Standard and Wide are public presets; the
scanner query state is stored in `preset`, `q`, and allowlisted `sort` URL
parameters. The next milestone replaces the fixture data source with the
global daily scanner API without changing the UI contracts.

## Checks

```bash
pnpm lint
pnpm exec tsc --noEmit
pnpm exec next build --webpack
```
