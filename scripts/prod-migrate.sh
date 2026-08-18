#!/usr/bin/env bash
# Apply schema or ordered migrations against the prod Postgres container.
#
# Designed for a VPS that only has docker-compose.prod.yml + .env.prod
# (no git checkout). SQL is read from the running `api` image.
#
# Fresh empty volume (recommended for first VPS bring-up):
#   ./scripts/prod-migrate.sh schema
#   # or, if this script is not on the VPS, use the one-liners in DEPLOY.md
#
# Existing DB that already has older tables:
#   ./scripts/prod-migrate.sh migrations
#
# Requires docker compose prod stack to be up (postgres + api).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Allow running from a bare deploy dir: ENV_FILE=.env.prod ./prod-migrate.sh
ROOT_DIR="${DEPLOY_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
MODE="${1:-schema}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env.prod}"
COMPOSE=(docker compose -f "$ROOT_DIR/docker-compose.prod.yml" --env-file "$ENV_FILE")

# Prefer credentials from .env.prod when present.
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  # Only export the postgres identity vars we need (ignore the rest).
  POSTGRES_USER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | tail -n1 | cut -d= -f2-)"
  POSTGRES_DB="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | tail -n1 | cut -d= -f2-)"
  set +a
fi
POSTGRES_USER="${POSTGRES_USER:-algo}"
POSTGRES_DB="${POSTGRES_DB:-algo_trading}"

psql_apply() {
  "${COMPOSE[@]}" exec -T postgres \
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"
}

cd "$ROOT_DIR"

case "$MODE" in
  schema)
    echo "Applying db/schema.sql from api image ..."
    "${COMPOSE[@]}" run --rm --no-deps -T api cat db/schema.sql | psql_apply
    ;;
  migrations)
    echo "Applying db/migrations/*.sql from api image ..."
    mapfile -t files < <("${COMPOSE[@]}" run --rm --no-deps -T api sh -c 'ls db/migrations/*.sql | sort')
    for f in "${files[@]}"; do
      f="$(echo "$f" | tr -d '\r')"
      echo "→ $f"
      "${COMPOSE[@]}" run --rm --no-deps -T api cat "$f" | psql_apply
    done
    ;;
  *)
    echo "Usage: $0 [schema|migrations]" >&2
    exit 1
    ;;
esac

echo "Done."
