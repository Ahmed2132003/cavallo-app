#!/usr/bin/env bash
# Part P-103: pre-migration database backup.
#
# Runs ON the target VPS, from the repository checkout directory.
#   1. pg_dump (custom format) of the app database into a timestamped file
#   2. integrity check of the dump with pg_restore --list
#   3. upload to object storage under backups/<environment>/ (P-013 bucket)
#
# Any failure exits non-zero. deploy.sh runs this BEFORE migrate and stops
# if it fails, so a migration never runs without a verified, uploaded backup.
#
# Usage: scripts/deploy/backup_db.sh <staging|production> <compose-file>
set -Eeuo pipefail

ENVIRONMENT="${1:-}"
COMPOSE_FILE="${2:-}"

if [[ "$ENVIRONMENT" != "staging" && "$ENVIRONMENT" != "production" ]]; then
  echo "usage: backup_db.sh <staging|production> <compose-file>" >&2
  exit 2
fi
if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "ERROR: compose file not found: $COMPOSE_FILE" >&2
  exit 2
fi

DC=(docker compose -f "$COMPOSE_FILE")
BACKUP_DIR="${BACKUP_DIR:-$PWD/backups}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE_NAME="${ENVIRONMENT}-${TIMESTAMP}.dump"
OBJECT_KEY="backups/${ENVIRONMENT}/${FILE_NAME}"
LOCAL_FILE="${BACKUP_DIR}/${FILE_NAME}"

mkdir -p "$BACKUP_DIR"

echo "[backup] dumping database to ${LOCAL_FILE}"
# POSTGRES_USER / POSTGRES_DB are expanded INSIDE the db container (single
# quotes), so this script never needs to read or source the .env file.
"${DC[@]}" exec -T db sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > "$LOCAL_FILE"

if [[ ! -s "$LOCAL_FILE" ]]; then
  echo "ERROR: dump file is empty" >&2
  rm -f "$LOCAL_FILE"
  exit 1
fi

echo "[backup] verifying dump integrity"
"${DC[@]}" exec -T db pg_restore --list < "$LOCAL_FILE" > /dev/null

echo "[backup] uploading to object storage as ${OBJECT_KEY}"
"${DC[@]}" run --rm -T -v "${BACKUP_DIR}:/backups:ro" web \
  python scripts/deploy/upload_backup.py "/backups/${FILE_NAME}" "$OBJECT_KEY"

# Keep local copies for 7 days only; the object storage copy is the record.
find "$BACKUP_DIR" -name '*.dump' -mtime +7 -delete

echo "BACKUP_OBJECT_KEY=${OBJECT_KEY}"
