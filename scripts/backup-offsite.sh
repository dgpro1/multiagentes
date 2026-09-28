#!/bin/sh
# Copies the database backups off this machine, and checks that a recent one
# exists. Run it from a host cron, NOT from inside Coolify: the off-site copy has
# to survive a Coolify reinstall, and a dump that only exists on the database's
# own disk does not survive the disk.
#
#   # once: an rclone remote for the destination (a Hetzner Storage Box over
#   # SFTP or WebDAV works; see docs/en/deploy-coolify.md, "Backups")
#   rclone config create hetzner sftp host u123456.your-storagebox.de user u123456
#
#   # then, in `crontab -e` on the server:
#   30 4 * * * cd /opt/openlivery && BACKUP_REMOTE=hetzner:openlivery ./scripts/backup-offsite.sh >> /var/log/openlivery-offsite.log 2>&1
#    0 9 * * * cd /opt/openlivery && ./scripts/backup-offsite.sh --check >> /var/log/openlivery-offsite.log 2>&1
#
# Modes:
#   (default)  sync the backups volume to the remote
#   --check    exit non-zero when the newest local dump is older than
#              BACKUP_MAX_AGE_HOURS (26 by default), so a silent failure in the
#              backup service shows up as a failed cron job instead of nothing
set -eu

# Git Bash on Windows rewrites absolute /paths found in arguments; every path
# handed to docker here is a path inside the container, so turn that off.
MSYS_NO_PATHCONV=1
export MSYS_NO_PATHCONV

MODE="sync"
case "${1:-}" in
  --check|check) MODE="check" ;;
  --sync|sync|"") MODE="sync" ;;
  *) printf 'Usage: %s [--check]\n' "$0" >&2; exit 2 ;;
esac

REMOTE="${BACKUP_REMOTE:-}"
RSYNC_TARGET="${BACKUP_RSYNC_TARGET:-}"
SSH_PORT="${BACKUP_SSH_PORT:-23}"
MAX_AGE_HOURS="${BACKUP_MAX_AGE_HOURS:-26}"
RCLONE_IMAGE="${RCLONE_IMAGE:-rclone/rclone:latest}"
RCLONE_CONF_DIR="${RCLONE_CONF_DIR:-${HOME:-/root}/.config/rclone}"

say() { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail "docker is not installed or not on PATH."
docker info >/dev/null 2>&1 || fail "the Docker daemon is not reachable; start Docker (or the stack) and try again."

volume="$(docker volume ls --filter name=db_backups --format '{{.Name}}' | head -n 1)"
[ -n "$volume" ] || fail "no db_backups volume found: is the db-backup service deployed?"

# --- Freshness: the newest dump, and how old it is ---------------------------
newest="$(docker run --rm -v "$volume":/backups:ro alpine:3 sh -c 'ls -1t /backups/last/*.sql.gz 2>/dev/null | head -n 1')"
[ -n "$newest" ] || fail "the backups volume holds no dump yet"

age_hours="$(docker run --rm -v "$volume":/backups:ro alpine:3 sh -c '
  newest="'"$newest"'"
  now=$(date +%s)
  then=$(stat -c %Y "$newest")
  echo $(( (now - then) / 3600 ))
')"

if [ "$MODE" = "check" ]; then
  if [ "$age_hours" -ge "$MAX_AGE_HOURS" ]; then
    say "STALE: the newest dump ($newest) is ${age_hours}h old, over the ${MAX_AGE_HOURS}h limit."
    exit 1
  fi
  say "OK: the newest dump ($newest) is ${age_hours}h old."
  exit 0
fi

say "Newest dump: $newest (${age_hours}h old)"
if [ "$age_hours" -ge "$MAX_AGE_HOURS" ]; then
  say "WARNING: that is older than ${MAX_AGE_HOURS}h - the backup service may be failing."
  say "Syncing anyway; run '$0 --check' from monitoring to be told about it."
fi

# --- The copy itself ---------------------------------------------------------
if [ -n "$REMOTE" ]; then
  [ -f "$RCLONE_CONF_DIR/rclone.conf" ] || fail "no rclone.conf in $RCLONE_CONF_DIR (set RCLONE_CONF_DIR, or run 'rclone config')"
  say "Syncing to $REMOTE with rclone"
  docker run --rm \
    -v "$volume":/backups:ro \
    -v "$RCLONE_CONF_DIR":/config/rclone:ro \
    "$RCLONE_IMAGE" sync /backups "$REMOTE" --config /config/rclone/rclone.conf --fast-list --transfers 4
elif [ -n "$RSYNC_TARGET" ]; then
  command -v rsync >/dev/null 2>&1 || fail "rsync is not installed on this host"
  mountpoint="$(docker volume inspect -f '{{.Mountpoint}}' "$volume")"
  say "Syncing $mountpoint to $RSYNC_TARGET over SSH (port $SSH_PORT)"
  rsync -az --delete -e "ssh -p $SSH_PORT -o StrictHostKeyChecking=accept-new" \
    "$mountpoint/" "$RSYNC_TARGET"
else
  fail "set BACKUP_REMOTE=<rclone remote:path> or BACKUP_RSYNC_TARGET=<user@host:path>"
fi

# --- Prove it landed ---------------------------------------------------------
if [ -n "$REMOTE" ]; then
  landed="$(docker run --rm -v "$RCLONE_CONF_DIR":/config/rclone:ro "$RCLONE_IMAGE" \
    lsjson "$REMOTE/last" --config /config/rclone/rclone.conf 2>/dev/null | grep -c "$(basename "$newest")" || true)"
  if [ "${landed:-0}" -gt 0 ]; then
    say "Verified: $(basename "$newest") is on the remote."
  else
    say "WARNING: could not find $(basename "$newest") on the remote; check the destination by hand."
  fi
fi

say "Off-site copy done."
