#!/usr/bin/env bash
# Part P-103: deploy sequence, run ON the target VPS by the GitHub Actions
# deploy jobs (over SSH). Same script for staging and production.
#
# Order (the order is a safety requirement, do not rearrange):
#   1. fetch + check out the exact commit being deployed
#   2. build images
#   3. start db + redis
#   4. PRE-MIGRATION BACKUP (pg_dump -> object storage backups/ prefix)
#   5. migrate
#   6. restart all services
#
# Usage: scripts/deploy/deploy.sh <staging|production> <git-sha>
# Run from the repository checkout on the VPS (which holds the real .env).
set -Eeuo pipefail

ENVIRONMENT="${1:-}"
GIT_SHA="${2:-}"

case "$ENVIRONMENT" in
  staging) COMPOSE_FILE="docker-compose.staging.yml" ;;
  production) COMPOSE_FILE="docker-compose.prod.yml" ;;
  *)
    echo "usage: deploy.sh <staging|production> <git-sha>" >&2
    exit 2
    ;;
esac
if [[ ! "$GIT_SHA" =~ ^[0-9a-f]{7,40}$ ]]; then
  echo "ERROR: invalid git sha: '${GIT_SHA}'" >&2
  exit 2
fi

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

# One deploy at a time per environment.
exec 9> "/tmp/cavallo-deploy-${ENVIRONMENT}.lock"
if ! flock -n 9; then
  echo "ERROR: another ${ENVIRONMENT} deploy is already running" >&2
  exit 1
fi

DC=(docker compose -f "$COMPOSE_FILE")
BACKUP_KEY="(not created yet)"

on_error() {
  echo "ERROR: deploy of ${GIT_SHA} to ${ENVIRONMENT} failed (line $1)" >&2
  echo "Last backup object key: ${BACKUP_KEY}" >&2
  echo "Database restore uses that backup (pg_restore)." >&2
}
trap 'on_error $LINENO' ERR

echo "[deploy] 1/6 checking out ${GIT_SHA}"
git fetch --prune origin
git checkout --detach "$GIT_SHA"

echo "[deploy] 2/6 building images"
"${DC[@]}" build

echo "[deploy] 3/6 starting db and redis"
"${DC[@]}" up -d --wait db redis

echo "[deploy] 4/6 pre-migration backup"
BACKUP_OUTPUT="$(./scripts/deploy/backup_db.sh "$ENVIRONMENT" "$COMPOSE_FILE")"
echo "$BACKUP_OUTPUT"
BACKUP_KEY="$(sed -n 's/^BACKUP_OBJECT_KEY=//p' <<< "$BACKUP_OUTPUT")"
if [[ -z "$BACKUP_KEY" ]]; then
  echo "ERROR: backup did not report an object key; refusing to migrate" >&2
  exit 1
fi

echo "[deploy] 5/6 running migrations"
"${DC[@]}" run --rm -T web python manage.py migrate --noinput

echo "[deploy] 6/6 restarting services"
"${DC[@]}" up -d --remove-orphans
"${DC[@]}" ps

echo "[deploy] done: ${GIT_SHA} -> ${ENVIRONMENT} (backup: ${BACKUP_KEY})"
