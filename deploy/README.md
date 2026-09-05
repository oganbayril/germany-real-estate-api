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
              realestate-train.timer  ──────────┤  (Sat, retrains → restarts API)
              realestate-backup.timer           │  (nightly pg_dump, keep 7)
                         └──────────────────────┼───────────────────────┘
                                                │  SSH tunnel :15432→:5432
   dev PC (residential IP) ──────────────────────┘
     GermanyRealEstate-Scrape task (Mon/Thu) → realestate-scrape run
```

**Why the scraper is on the PC, not the VPS:** Immowelt is behind DataDome, which
blocks Hetzner's datacenter ASN — from the VPS it serves stripped pages then 403s.
Search pages load fine from a residential connection, so the scrape runs on the
dev PC and writes into the VPS Postgres over an SSH tunnel. (Same pattern as the
`turkey-food-inflation` local scraper vs Şok.) Everything else stays on the VPS.

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
sudoedit /opt/realestate/.env               # set RE_PUBLIC_DOMAIN, RE_SMTP_PASSWORD
systemctl restart realestate-api caddy
systemctl disable --now realestate-scrape.timer   # scraper runs on the PC, not here
curl -s https://<domain>/health
```

Then set up the local scraper (below) and run it once for the first dataset;
`realestate-train` runs on its Saturday timer, or `systemctl start
realestate-train.service` to make the first model now.

## Local scraper (dev PC)

`realestate-scrape` runs from a residential connection because DataDome blocks the
VPS. On the PC, in the repo:

```powershell
Copy-Item deploy\.scrape_local.env.example deploy\.scrape_local.env
# edit it: RE_DB_PASSWORD from the VPS (grep RE_DATABASE_URL /opt/realestate/.env),
#          RE_SMTP_PASSWORD (the shared Gmail app password)

powershell -File deploy\scrape_local.ps1        # first run, ~35 min
powershell -File deploy\register_scrape_task.ps1   # then: Mon/Thu, "start when available"
```

`scrape_local.ps1` opens an SSH tunnel (`127.0.0.1:15432` → VPS `:5432`), runs
`realestate-scrape run --email` against it, and closes the tunnel. Needs your SSH
key already authorised on the VPS (it is). Logs to `deploy/scrape_local.log`.

## Operations

| Task | Command |
|------|---------|
| Deploy latest `main` (VPS) | `bash /opt/realestate/deploy/update.sh` |
| API logs | `journalctl -u realestate-api -f` |
| Retrain now | `systemctl start realestate-train.service` |
| Timer schedule | `systemctl list-timers 'realestate-*'` |
| Scrape now (PC) | `Start-ScheduledTask -TaskName 'GermanyRealEstate-Scrape'` or run `scrape_local.ps1` |
| Last scrape (PC) | tail `deploy/scrape_local.log` |
| Restore a backup | `pg_restore -d 'postgresql://realestate:…@localhost/realestate' -c /opt/realestate/backups/realestate-<stamp>.dump` |

## Schedules

- **Scrape** — PC scheduled task, `Mon,Thu 04:00`, "start when available" so it
  catches up whenever the PC is next on. Deliberately small: `≈5` search
  URLs/city, `25–45 s` between requests, and the sitemap-derived URL pool is
  cached for `RE_SCRAPE_DISCOVERY_CACHE_DAYS` (14) so a run is ~25 real requests
  total. DataDome scores IPs over time — raise the caps once a steady dataset
  exists and the IP has cooled. The discovery cache lives at
  `data/immowelt_search_urls.json` on the PC; delete it to force a rebuild.
- **Retrain** — VPS, `Sat 04:00`. `realestate-train` refuses if the DB has
  `< RE_MIN_TRAIN_ROWS` usable rows or the latest scrape run didn't succeed. On a
  successful retrain it restarts `realestate-api` (≈2 s blip) so the new artifact
  is picked up.
- **Backup** — VPS, nightly `02:30`, `pg_dump -Fc`, keeps the 7 newest.

`--email` sends a one-line summary via the shared Gmail app password
(`RE_SMTP_*`); a blank password disables it. A scrape that fetches pages but
parses zero listings is recorded as `blocked` (soft block), which also stops the
next retrain from running on empty data.

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
