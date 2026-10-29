#!/usr/bin/env bash
#
# Deploy the latest main: pull, sync deps, migrate, install changed systemd
# units, restart the API.
# Run as root:  bash /opt/realestate/deploy/update.sh
set -euo pipefail

APP_USER=realestate
APP_DIR=/opt/realestate

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

sudo -u "$APP_USER" bash -c "
  set -euo pipefail
  cd '$APP_DIR'
  git pull --ff-only
  /usr/local/bin/uv sync --frozen
  set -a && . ./.env && set +a
  ./.venv/bin/alembic upgrade head
"

# Unit files live in the repo; install any that changed (timeouts, schedules).
units_changed=0
for unit in "$APP_DIR"/deploy/realestate-*.service "$APP_DIR"/deploy/realestate-*.timer; do
  dest=/etc/systemd/system/$(basename "$unit")
  if ! cmp -s "$unit" "$dest"; then
    cp "$unit" "$dest"
    echo "installed $(basename "$unit")"
    units_changed=1
  fi
done
if [[ $units_changed -eq 1 ]]; then systemctl daemon-reload; fi

systemctl restart realestate-api.service
for _ in $(seq 1 15); do
  sleep 2
  curl -fsS http://127.0.0.1:8000/health && { echo; exit 0; }
done
echo "api did not become healthy -- check: journalctl -u realestate-api -xe" >&2
exit 1
