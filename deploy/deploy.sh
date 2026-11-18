#!/bin/bash
# Server-side deploy: update the code, install deps, migrate, restart, health-check.
# If anything fails, roll back to the previous commit and restart that.
#
# Runs ON THE SERVER. Called by .github/workflows/deploy.yml, or by hand:
#   bash deploy/deploy.sh                 # deploy origin/<BRANCH>
#   bash deploy/deploy.sh <commit-or-ref> # deploy a specific ref
#
# Note: database migrations are not rolled back. Keep migrations backward compatible.
set -uo pipefail

# ─── project settings ──────────────────────────────────────────────────────────
APP_DIR="/root/my_project/session_creator"
BRANCH="main"
SERVICE="session_creator"
REQUIREMENTS="requirements.txt"
# Tracked files that the app rewrites at runtime: kept as they are on the server.
RUNTIME_FILES=()
# Optional local URL that must answer 2xx after the restart (empty = skip).
HEALTH_URL="http://127.0.0.1:8000/health"
# Seconds the service must stay up (same PID) to count as healthy.
SETTLE=20

migrate() {
  # no database
  return 0
}
# ───────────────────────────────────────────────────────────────────────────────

log() { echo "==> $*"; }
fail() { echo "!!! $*" >&2; return 1; }

exec 9>"/run/lock/deploy-$SERVICE.lock"
flock -n 9 || { echo "another deploy of $SERVICE is running" >&2; exit 1; }

cd "$APP_DIR" || exit 1
PREV=$(git rev-parse HEAD)
TARGET="${1:-origin/$BRANCH}"
KEEP=$(mktemp -d)
trap 'rm -rf "$KEEP"' EXIT

save_runtime()    { for f in "${RUNTIME_FILES[@]}"; do [ -f "$f" ] && mkdir -p "$KEEP/$(dirname "$f")" && cp -p "$f" "$KEEP/$f"; done; return 0; }
restore_runtime() { for f in "${RUNTIME_FILES[@]}"; do [ -f "$KEEP/$f" ] && cp -p "$KEEP/$f" "$f"; done; return 0; }

checkout() {
  git fetch -q origin "$BRANCH" || return 1
  git reset -q --hard "$1" || return 1
  restore_runtime
}

health() {
  local pid
  sleep 3
  pid=$(systemctl show -p MainPID --value "$SERVICE")
  [ "$pid" != "0" ] || fail "$SERVICE did not start" || return 1
  sleep "$SETTLE"
  systemctl is-active --quiet "$SERVICE" || fail "$SERVICE is not active" || return 1
  [ "$(systemctl show -p MainPID --value "$SERVICE")" = "$pid" ] || fail "$SERVICE restarted (crash loop)" || return 1
  if [ -n "$HEALTH_URL" ]; then
    for _ in 1 2 3 4 5; do curl -fsS -o /dev/null --max-time 5 "$HEALTH_URL" && return 0; sleep 3; done
    fail "health URL $HEALTH_URL did not answer" || return 1
  fi
  return 0
}

install_and_restart() {
  if [ -n "$REQUIREMENTS" ]; then
    log "pip install -r $REQUIREMENTS"
    venv/bin/pip install -q --disable-pip-version-check -r "$REQUIREMENTS" || fail "pip install failed" || return 1
  fi
  log "migrations"
  migrate || fail "migration failed" || return 1
  log "restart $SERVICE"
  systemctl restart "$SERVICE" || fail "restart failed" || return 1
  log "health check (${SETTLE}s)"
  health
}

save_runtime
log "$SERVICE: $(git log -1 --format='%h %s' "$PREV") -> $TARGET"
if checkout "$TARGET" && install_and_restart; then
  log "deployed $(git log -1 --format='%h %s')"
  exit 0
fi

log "DEPLOY FAILED, rolling back to $(git log -1 --format='%h' "$PREV")"
journalctl -u "$SERVICE" -n 30 --no-pager -o cat || true
git reset -q --hard "$PREV"; restore_runtime
if install_and_restart; then
  log "rollback OK: running $(git log -1 --format='%h %s')"
else
  log "ROLLBACK ALSO FAILED: check the server by hand"
fi
exit 1
