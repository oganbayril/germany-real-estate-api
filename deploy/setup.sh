#!/usr/bin/env bash
#
# First-time provisioning for the real-estate API on a fresh Debian/Ubuntu host.
# Run as root:  bash deploy/setup.sh
#
# Idempotent enough to re-run. It will NOT overwrite an existing /opt/realestate/.env.
set -euo pipefail

APP_USER=realestate
APP_DIR=/opt/realestate
REPO=https://github.com/oganbayril/germany-real-estate-api.git
PY_VERSION=3.13

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

echo "==> packages"
apt-get update -qq
apt-get install -y -qq curl git ca-certificates gnupg debian-keyring debian-archive-keyring apt-transport-https postgresql

echo "==> caddy repo + install"
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy
fi

echo "==> app user"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR"
install -d -o caddy -g caddy /var/log/caddy

echo "==> uv (system-wide at /usr/local/bin, independent of any per-user install)"
if [[ ! -x /usr/local/bin/uv ]]; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi

echo "==> clone / update repo"
if [[ -d $APP_DIR/.git ]]; then
  sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only
else
  sudo -u "$APP_USER" git clone "$REPO" "$APP_DIR"
fi
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR"/{data,models,backups}

echo "==> .env"
if [[ ! -f $APP_DIR/.env ]]; then
  db_pass="$(openssl rand -hex 24)"
  sed "s#realestate:CHANGEME@#realestate:${db_pass}@#" \
    "$APP_DIR/deploy/.env.production.example" > "$APP_DIR/.env"
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo "    wrote $APP_DIR/.env  (set RE_PUBLIC_DOMAIN + RE_SMTP_PASSWORD by hand)"
else
  echo "    keeping existing $APP_DIR/.env"
fi
# shellcheck source=/dev/null
set -a; source "$APP_DIR/.env"; set +a
db_pass="$(printf '%s' "$RE_DATABASE_URL" | sed -E 's#.*realestate:([^@]+)@.*#\1#')"

echo "==> postgres role + database"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='realestate'" | grep -q 1 || \
  sudo -u postgres psql -qc "CREATE ROLE realestate LOGIN PASSWORD '${db_pass}'"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='realestate'" | grep -q 1 || \
  sudo -u postgres createdb -O realestate realestate

echo "==> python deps + migrations"
sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && /usr/local/bin/uv sync --frozen --python '$PY_VERSION'"
sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && set -a && . ./.env && set +a && ./.venv/bin/alembic upgrade head"

echo "==> systemd units"
cp "$APP_DIR"/deploy/realestate-*.service "$APP_DIR"/deploy/realestate-*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now realestate-scrape.timer realestate-train.timer realestate-backup.timer
systemctl enable --now realestate-api.service \
  || echo "  !! api did not start -- inspect: journalctl -u realestate-api -xe   (setup continues)"

echo "==> caddy"
sed "s/REALESTATE_DOMAIN/${RE_PUBLIC_DOMAIN}/" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

cat <<EOF

done.
  - point ${RE_PUBLIC_DOMAIN} at this host's IP (A record) if not already
  - edit $APP_DIR/.env: set RE_SMTP_PASSWORD, confirm RE_PUBLIC_DOMAIN
  - first data + model:
      sudo systemctl start realestate-scrape.service   # ~35 min
      sudo systemctl start realestate-train.service
  - check:  systemctl status realestate-api  &&  curl -s https://${RE_PUBLIC_DOMAIN}/health
EOF
