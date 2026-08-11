#!/usr/bin/env bash
# Apply schema or ordered migrations against the prod Postgres container.
#
# Fresh empty volume (recommended for first VPS bring-up):
#   ./scripts/prod-migrate.sh schema
#
# Existing DB that already has older tables:
#   ./scripts/prod-migrate.sh migrations
#
# Requires docker compose prod stack to be up (at least postgres).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-schema}"
COMPOSE=(docker compose -f "$ROOT_DIR/docker-compose.prod.yml" --env-file "${ENV_FILE:-$ROOT_DIR/.env.prod}")
PSQL=(exec -T postgres psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-algo}" -d "${POSTGRES_DB:-algo_trading}")

cd "$ROOT_DIR"

case "$MODE" in
  schema)
    echo "Applying server/db/schema.sql ..."
    "${COMPOSE[@]}" "${PSQL[@]}" < "$ROOT_DIR/server/db/schema.sql"
    ;;
  migrations)
    echo "Applying server/db/migrations/*.sql in order ..."
    for f in "$ROOT_DIR"/server/db/migrations/*.sql; do
      echo "→ $(basename "$f")"
      "${COMPOSE[@]}" "${PSQL[@]}" < "$f"
    done
    ;;
  *)
    echo "Usage: $0 [schema|migrations]" >&2
    exit 1
    ;;
esac

echo "Done."
