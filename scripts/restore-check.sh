#!/bin/sh
# Rehearses a restore: loads the newest database backup into a throwaway
# PostgreSQL container and checks that it is complete and usable. Nothing it
# does touches the running installation.
#
#   ./scripts/restore-check.sh                        # newest dump in the backups volume
#   DUMP=backups/openlivery-2026-09-27.sql.gz ./scripts/restore-check.sh
#   KEEP=1 ./scripts/restore-check.sh                 # leave the scratch container for inspection
#
# Run it monthly and after every migration. A backup that was never restored is
# a hope, not a backup. See docs/en/self-hosting.md ("Backups").
set -eu

NAME="${RESTORE_CHECK_NAME:-openlivery-restore-check}"
IMAGE="${RESTORE_CHECK_IMAGE:-postgres:17.6-alpine}"
PORT="${RESTORE_CHECK_PORT:-55432}"
DB_USER="${POSTGRES_USER:-openlivery}"
DB_NAME="${POSTGRES_DB:-openlivery}"
KEEP="${KEEP:-0}"
DUMP="${DUMP:-}"
WAIT_SECONDS="${WAIT_SECONDS:-60}"

say() { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail "docker is not installed or not on PATH."

# --- 1. The dump to rehearse -------------------------------------------------
# The db-backup service writes plain SQL, gzipped, into BACKUP_DIR/last/ inside
# the db_backups volume; a dump copied off the machine can be passed instead.
fetch() { :; }
if [ -z "$DUMP" ]; then
  volume="$(docker volume ls --filter name=db_backups --format '{{.Name}}' | head -n 1)"
  [ -n "$volume" ] || fail "no db_backups volume found; pass DUMP=/path/to/file.sql.gz"
  say "Reading the newest backup from volume $volume"
  in_container="$(docker run --rm -v "$volume":/backups:ro alpine:3 \
    sh -c 'ls -1t /backups/last/*.sql.gz 2>/dev/null | head -n 1')"
  [ -n "$in_container" ] || fail "the backups volume holds no .sql.gz file: has the db-backup service ever run successfully?"
  say "Dump: $in_container"
  fetch() { docker run --rm -v "$volume":/backups:ro alpine:3 cat "$in_container"; }
else
  [ -f "$DUMP" ] || fail "$DUMP does not exist"
  say "Dump: $DUMP"
  fetch() { cat "$DUMP"; }
fi

# --- 2. A throwaway database -------------------------------------------------
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
cleanup
say "Starting $IMAGE as $NAME on 127.0.0.1:$PORT"
docker run -d --name "$NAME" \
  -e POSTGRES_PASSWORD=restore-check -e POSTGRES_USER="$DB_USER" -e POSTGRES_DB="$DB_NAME" \
  -p "127.0.0.1:$PORT:5432" "$IMAGE" >/dev/null

waited=0
until docker exec "$NAME" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; do
  waited=$((waited + 2))
  [ "$waited" -lt "$WAIT_SECONDS" ] || fail "the scratch database did not become ready in ${WAIT_SECONDS}s"
  sleep 2
done

# --- 3. Restore --------------------------------------------------------------
# Plain SQL, so psql and not pg_restore. ON_ERROR_STOP turns a truncated dump
# into a failure here instead of a half-restored database nobody notices.
say "Restoring..."
fetch | docker exec -i "$NAME" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -q >/dev/null

# --- 4. What a person would look for -----------------------------------------
revision="$(docker exec "$NAME" psql -U "$DB_USER" -d "$DB_NAME" -tAc 'SELECT version_num FROM alembic_version' 2>/dev/null || echo '(none)')"
counts="$(docker exec "$NAME" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT (SELECT count(*) FROM agencies) || ' agencies, ' || (SELECT count(*) FROM clients) || ' clients, ' || (SELECT count(*) FROM users) || ' users, ' || (SELECT count(*) FROM platform_admins) || ' platform admins, ' || (SELECT count(*) FROM conversations) || ' conversations'")"
say ""
say "  schema revision : $revision"
say "  contents        : $counts"

running="$(docker compose --env-file "${OPENLIVERY_COMPOSE_ENV:-.env.docker}" exec -T api alembic current 2>/dev/null | tail -n 1 || true)"
if [ -n "$running" ]; then
  say "  live install    : $running"
  case "$running" in
    *"$revision"*) say "  match           : yes" ;;
    *) say "  match           : NO - the dump predates the live schema, so a real restore would need 'alembic upgrade head'" ;;
  esac
fi

agencies="$(docker exec "$NAME" psql -U "$DB_USER" -d "$DB_NAME" -tAc 'SELECT count(*) FROM agencies')"
if [ "${ALLOW_EMPTY:-0}" != "1" ] && [ "$agencies" = "0" ]; then
  fail "the restored database has no agencies: this dump is empty or truncated (set ALLOW_EMPTY=1 on a fresh install)"
fi

say ""
say "OK - the newest backup restored cleanly."
trap - EXIT INT TERM
if [ "$KEEP" = "1" ]; then
  say "The scratch container '$NAME' is still running: docker exec -it $NAME psql -U $DB_USER -d $DB_NAME"
else
  cleanup
fi
