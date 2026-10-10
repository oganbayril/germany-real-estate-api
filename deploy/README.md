# Deployment

Single Hetzner VPS (Debian/Ubuntu). Everything runs as plain systemd units under
a dedicated `realestate` user at `/opt/realestate` — no Docker, matching the
other services already on the box.

```
                         ┌───────────────── VPS ─────────────────┐
   internet  ──TLS──►  Caddy (:443)  ──►  uvicorn (127.0.0.1:8000)
                         │                      │
              realestate-scrape.timer  ───►  Postgres (localhost)
              (Mon/Thu, gentle: 5 urls/city,  │      ▲
               45-90s delay, 14d URL cache)    │      │
              realestate-train.timer  ─────────┘      │
              (Sat, retrains → restarts API)           │
              realestate-backup.timer ─────────────────┘
              (nightly pg_dump, keep 7)
                         └────────────────────────────────────────┘
```

Everything, including the scraper, runs on the VPS. `deploy/scrape_local.ps1` +
`deploy/register_scrape_task.ps1` (a PC-side scraper over an SSH tunnel to the
VPS Postgres) still exist as a documented fallback — see "If the VPS ever gets
blocked" below — but aren't the default anymore.

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
installs this site into Caddy (see "Caddy layout" below).

### Caddy layout (shared box)

Other projects run on the same VPS, each with its own Caddy site, so this repo
owns **one file** and never the whole config:

```
/etc/caddy/Caddyfile                       import sites-enabled/*
/etc/caddy/sites-enabled/realestate.conf   this site (from deploy/Caddyfile)
/etc/caddy/sites-enabled/<other>.conf      other projects' sites, never touched
/etc/caddy/backups/                        backups made by the install script
```

`deploy/install-caddy-site.sh <domain>` (run by `setup.sh`, safe to re-run):

- stops, changing nothing, if another file in `sites-enabled/` already defines
  the same domain (Caddy refuses duplicate sites);
- if the main `Caddyfile` does not contain `import sites-enabled/*` (e.g. the
  default one apt installs), backs it up to `/etc/caddy/backups/` and writes the
  import-only version; if it does, leaves it alone;
- writes `sites-enabled/realestate.conf`; if nothing changed, does nothing more;
- makes sure `/var/log/caddy/realestate.log` exists, owned by `caddy`, mode 600,
  and runs `caddy validate` **as the caddy user**; on failure restores the
  previous files (and removes the log if it created it) and does not reload;
- if Caddy is running, reloads it; a failed reload restores the previous files
  and stops. It **never restarts** Caddy: a restart with a config that cannot
  load would stop Caddy and every site on the box, while a failed reload keeps
  the previous config running. If Caddy is not running (first install), it is
  started.

Backups go to `/etc/caddy/backups/`, not `sites-enabled/`: everything in
`sites-enabled/` is loaded, so a backup there would define the site twice.

Why validate as `caddy`, not root: validating opens the configured log files.
As root it would create a missing log owned by root, which the running Caddy
(user `caddy`) cannot open, so the reload would fail. (This happened when
another site was added to this box; see the food-housing-spending-share
deploy notes.) If a log exists but is not owned by `caddy`, the script stops
before changing anything.

To test changes to the script without Caddy or root:
`CADDY_DIR=/tmp/caddy-test NO_RELOAD=1 bash deploy/install-caddy-site.sh example.org`
(generation only), and `bash deploy/tests/test_install_caddy_site.sh` for the
failure paths (fake `caddy` / `runuser` / `systemctl`, 32 checks).

Then, by hand:

```bash
sudoedit /opt/realestate/.env               # set RE_PUBLIC_DOMAIN, RE_SMTP_PASSWORD
systemctl restart realestate-api caddy
curl -s https://<domain>/health
```

`realestate-scrape.timer` and `realestate-train.timer` are both enabled by
`setup.sh`. To get the first dataset + model without waiting for Monday:

```bash
systemctl start realestate-scrape.service   # ~15-25 min, gentle pacing
systemctl start realestate-train.service    # once the DB has >= RE_MIN_TRAIN_ROWS
```

## Operations

| Task | Command |
|------|---------|
| Deploy latest `main` | `bash /opt/realestate/deploy/update.sh` |
| API logs | `journalctl -u realestate-api -f` |
| Scrape now | `systemctl start realestate-scrape.service` |
| Retrain now | `systemctl start realestate-train.service` |
| Timer schedule | `systemctl list-timers 'realestate-*'` |
| Last scrape log | `journalctl -u realestate-scrape -n 100` |
| Restore a backup | `pg_restore -d 'postgresql://realestate:…@localhost/realestate' -c /opt/realestate/backups/realestate-<stamp>.dump` |

## Schedules

- **Scrape** — VPS timer, `Mon,Thu 03:00` + up to 6h jitter, `Persistent=true`
  (catches up a missed run on next boot/enable). Deliberately gentle:
  `RE_SCRAPE_MAX_SEARCH_URLS_PER_CITY=5`, `RE_SCRAPE_DELAY_MIN_S=45` /
  `_MAX_S=90`, and the sitemap-derived URL pool is cached for
  `RE_SCRAPE_DISCOVERY_CACHE_DAYS` (14) so a routine run is up to ~25 real
  requests, skipping the sitemap walk entirely. In practice almost every run
  since 2026-09-17 has been cut off after only 4-7 requests regardless of
  pacing (see below) — the delay was pushed from 25-45s to 45-90s on
  2026-10-03 to see whether a slower pace buys a longer session; raise the
  caps only once runs are coming back `success` rather than `partial`. The
  discovery cache lives at `data/immowelt_search_urls.json`; delete it to
  force a rebuild.
- **Retrain** — VPS, `Sat 04:00`. `realestate-train` refuses if the DB has
  `< RE_MIN_TRAIN_ROWS` usable rows or the latest scrape run didn't succeed
  (`blocked` status). A block that hits *after* real listings already landed is
  recorded as `partial`, which still counts as retrain-eligible. On a successful
  retrain it restarts `realestate-api` (≈2 s blip) so the new artifact is picked up.
- **Backup** — VPS, nightly `02:30`, `pg_dump -Fc`, keeps the 7 newest.

## If the VPS ever gets blocked

`deploy/scrape_local.ps1` + `deploy/register_scrape_task.ps1` run the scraper
from a PC instead, over an SSH tunnel to the VPS Postgres (`127.0.0.1:15432` →
`:5432`) — same idea as the `turkey-food-inflation` project's local scraper for
Şok. To switch to it: `systemctl disable --now realestate-scrape.timer` on the
VPS, then on the PC:

```powershell
Copy-Item deploy\.scrape_local.env.example deploy\.scrape_local.env
# edit it: RE_DB_PASSWORD from the VPS (grep RE_DATABASE_URL /opt/realestate/.env),
#          RE_SMTP_PASSWORD (the shared Gmail app password)
powershell -File deploy\scrape_local.ps1            # first run
powershell -File deploy\register_scrape_task.ps1    # Mon/Thu, "start when available"
```

This was actually the setup from 2026-09-05 to 2026-09-13, believed necessary
because of a DataDome block — that turned out to mostly be a schema bug
(`expose_id` too short for real IDs, invisible on SQLite, fatal on Postgres).
Fixed, and a full gentle scrape from the VPS then ran clean once. Real
throttling did show up afterward, though: every run since 2026-09-17 has come
back `partial`, blocked after only 4-7 of the ~25 requested pages. It's stayed
mild enough that the VPS keeps landing real data every run (947+ listings and
growing as of 2026-10-03) rather than needing this fallback, so it's still
documented but not in active use.

`--email` sends a one-line summary via the shared Gmail app password
(`RE_SMTP_*`); a blank password disables it. A scrape that fetches pages but
parses zero listings is recorded as `blocked` (soft block), which also stops the
next retrain from running on empty data.

## Why Caddy, not nginx

This site is one small API behind one TLS certificate. Caddy obtains and
renews the certificate itself — there's no certbot, no renewal timer to rot, no
post-renew reload hook. The whole reverse-proxy + TLS + security-header +
body-size config is [one short file](Caddyfile), installed as one site file next
to the other projects' sites (see "Caddy layout" above), and needs only the stock Caddy
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
