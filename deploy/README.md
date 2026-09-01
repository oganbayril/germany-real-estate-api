# Deployment

Single Hetzner VPS (Debian/Ubuntu). Everything runs as plain systemd units under
a dedicated `realestate` user at `/opt/realestate` — no Docker, matching the
other services already on the box.

```
                         ┌───────────────── VPS ─────────────────┐
   internet  ──TLS──►  Caddy (:443)  ──►  uvicorn (127.0.0.1:8000)
                         │                      │
                         │                 Postgres (localhost)
                         │                      ▲
              realestate-scrape.timer  ─────────┤  (Mon/Thu, writes listings)
              realestate-train.timer   ─────────┘  (Sat, retrains → restarts API)
              realestate-backup.timer            (nightly pg_dump, keep 7)
```

## Prerequisites

1. A hostname pointed at the box. Free option: register a subdomain at
   <https://duckdns.org>, set its IP to the server's, e.g.
   `germany-real-estate.duckdns.org`.
2. Firewall: allow `22`, `80`, `443` only (`ufw allow OpenSSH && ufw allow 80,443/tcp && ufw enable`).

## First install

```bash
ssh root@<host>
git clone https://github.com/oganbayril/germany-real-estate-api.git /tmp/re && bash /tmp/re/deploy/setup.sh
```

`setup.sh` (idempotent): installs Caddy + Postgres + uv, creates the `realestate`
user and `/opt/realestate/{data,models,backups}`, clones the repo, generates a DB
password into `/opt/realestate/.env` (chmod 600), creates the Postgres role +
database, `uv sync`, `alembic upgrade head`, installs and enables all units, and
writes `/etc/caddy/Caddyfile` from the template.

Then, by hand:

```bash
sudoedit /opt/realestate/.env          # set RE_PUBLIC_DOMAIN, RE_SMTP_PASSWORD
systemctl restart realestate-api caddy
systemctl start realestate-scrape.service   # first data, ~35 min
systemctl start realestate-train.service    # first model
curl -s https://<domain>/health
```

## Operations

| Task | Command |
|------|---------|
| Deploy latest `main` | `bash /opt/realestate/deploy/update.sh` |
| API logs | `journalctl -u realestate-api -f` |
| Last scrape | `journalctl -u realestate-scrape -n 100` |
| Run a scrape now | `systemctl start realestate-scrape.service` |
| Retrain now | `systemctl start realestate-train.service` |
| Timer schedule | `systemctl list-timers 'realestate-*'` |
| Restore a backup | `pg_restore -d 'postgresql://realestate:…@localhost/realestate' -c /opt/realestate/backups/realestate-<stamp>.dump` |

DB (sqlite3 CLI isn't installed; use the venv):
`sudo -u realestate /opt/realestate/.venv/bin/python -c "..."` from `/opt/realestate`.

## Schedules

- **Scrape** — `Mon,Thu 03:00` + up to 6 h jitter. ~35 min/run at
  `RE_SCRAPE_MAX_SEARCH_URLS_PER_CITY=40` × 5 cities.
- **Retrain** — `Sat 04:00`. `realestate-train` refuses if the DB has
  `< RE_MIN_TRAIN_ROWS` usable rows or the latest scrape run didn't succeed. On a
  successful retrain it restarts `realestate-api` (≈2 s blip) so the new artifact
  is picked up.
- **Backup** — nightly `02:30`, `pg_dump -Fc`, keeps the 7 newest.

`--email` on the scrape/train units sends a one-line summary via the shared Gmail
app password (`RE_SMTP_*` in `.env`); blank `RE_SMTP_PASSWORD` disables it.

## Why Caddy, not nginx

This box serves one small API behind one TLS certificate. Caddy obtains and
renews the certificate itself — there's no certbot, no renewal timer to rot, no
post-renew reload hook. The whole reverse-proxy + TLS + security-header +
body-size config is [one short file](Caddyfile) and needs only the stock Caddy
binary (rate limiting is done in the app with `slowapi`, so no Caddy plugins).
nginx is the more common name on a CV, but here it would add certbot and its
timer for no functional gain at this scale.

## Security notes

- **API sandboxing** — `realestate-api.service` runs as an unprivileged user with
  `ProtectSystem=strict`, `ReadWritePaths=` limited to `data/` + `models/`,
  `NoNewPrivileges`, a syscall filter, etc. The model artifact is loaded via
  `joblib` (pickle), so the process is boxed in case an artifact is ever poisoned;
  `models/` is writable only by `realestate`.
- **`.env`** is `chmod 600`, loaded via systemd `EnvironmentFile=`, never baked
  into a unit or committed.
- **Rate limits** — `slowapi`: `/predict` 30/min, `/stats` 60/min, 240/min
  default, per client IP.
- **Postgres** listens on localhost only; the `realestate` role is a plain
  `LOGIN` role (no superuser) scoped to its own database.
- **Scraper** only fetches `www.immowelt.de` — every request URL and redirect hop
  is checked against an allowlist (no SSRF to link-local / metadata).
