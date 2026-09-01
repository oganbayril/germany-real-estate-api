#!/usr/bin/env bash
#
# Deploy the latest main: pull, sync deps, migrate, restart the API.
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

systemctl restart realestate-api.service
sleep 1
systemctl --no-pager --lines=0 status realestate-api.service
curl -fsS http://127.0.0.1:8000/health && echo
