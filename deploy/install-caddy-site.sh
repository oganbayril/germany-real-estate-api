#!/usr/bin/env bash
#
# Install the real-estate site into Caddy without touching any other site.
# Called by setup.sh; safe to re-run on its own.
#
# Caddy layout on the host (shared with other projects on the same box):
#   /etc/caddy/Caddyfile               only the line: import sites-enabled/*
#   /etc/caddy/sites-enabled/<x>.conf  one file per site, each owned by its project
#   /etc/caddy/backups/                backups made by this script (NOT in
#                                      sites-enabled/: everything there is loaded,
#                                      so a backup there would define the site twice)
#
# Steps:
#   1. Stop, changing nothing, if another file in sites-enabled/ already
#      defines this domain (Caddy refuses two definitions of the same site).
#   2. Main Caddyfile: if it does not contain the import line (for example the
#      default one apt installs), back it up and write the import-only version.
#      If it does, leave it alone.
#   3. Write sites-enabled/realestate.conf from deploy/Caddyfile. If the file
#      is already identical and step 2 changed nothing, stop: nothing to reload.
#   4. Make sure the site's access log exists and is owned by the caddy user
#      (mode 600, like Caddy's own), then validate the whole config AS THE
#      CADDY USER. Validating as root would create a missing log file owned
#      by root, which the running Caddy (user caddy) then cannot open: its
#      reload fails. On failure, put the previous files back and stop.
#   5. If Caddy is running, reload it. A failed reload puts the previous files
#      back and stops; it never restarts Caddy (a restart with a config that
#      cannot load would stop Caddy, and with it every site on the box). Only
#      if Caddy is not running at all (first install) is it started.
#
# Usage (as root):  bash deploy/install-caddy-site.sh <domain>
#
# Testing without Caddy or root, against a scratch directory:
#   CADDY_DIR=/tmp/caddy-test NO_RELOAD=1 bash deploy/install-caddy-site.sh example.org
# NO_RELOAD=1 skips steps 4 and 5 (there is no Caddy to ask). The failure
# paths of steps 4-5 are tested with fake commands: deploy/tests/test_install_caddy_site.sh
set -euo pipefail

DOMAIN="${1:?usage: install-caddy-site.sh <domain>}"
CADDY_DIR="${CADDY_DIR:-/etc/caddy}"
NO_RELOAD="${NO_RELOAD:-0}"
CADDY_USER="${CADDY_USER:-caddy}"
LOG_FILE="${CADDY_LOG_DIR:-/var/log/caddy}/realestate.log"   # as in deploy/Caddyfile

TEMPLATE="$(cd "$(dirname "$0")" && pwd)/Caddyfile"
MAIN_FILE="$CADDY_DIR/Caddyfile"
SITES_DIR="$CADDY_DIR/sites-enabled"
SITE_FILE="$SITES_DIR/realestate.conf"
BACKUP_DIR="$CADDY_DIR/backups"
IMPORT_LINE='import sites-enabled/*'
STAMP="$(date +%Y%m%d-%H%M%S)"

# True if a Caddy config file defines a site for $DOMAIN. Looks only at block
# openers (lines ending in "{") outside comments, and compares each address on
# the line, ignoring an http(s):// prefix and a :port suffix.
defines_domain() {
  awk -v domain="$DOMAIN" '
    /^[[:space:]]*#/ { next }
    /\{[[:space:]]*$/ {
      line = $0
      sub(/\{[[:space:]]*$/, "", line)
      count = split(line, addresses, /[[:space:],]+/)
      for (i = 1; i <= count; i++) {
        address = addresses[i]
        sub(/^https?:\/\//, "", address)
        sub(/:[0-9]+$/, "", address)
        if (address == domain) { found = 1 }
      }
    }
    END { exit (found ? 0 : 1) }
  ' "$1"
}

# --- 0. a log that Caddy could not open? Stop before changing anything. -------
if [[ $NO_RELOAD != 1 && -e $LOG_FILE && "$(stat -c %U "$LOG_FILE")" != "$CADDY_USER" ]]; then
  echo "!! $LOG_FILE exists but is not owned by $CADDY_USER: Caddy could not open it." >&2
  echo "!! Fix its owner first (chown $CADDY_USER:$CADDY_USER $LOG_FILE). Nothing was changed." >&2
  exit 1
fi

install -d "$SITES_DIR" "$BACKUP_DIR"

# --- 1. another file already defines this domain? -----------------------------
conflicts=()
for file in "$SITES_DIR"/*; do
  [[ -f $file && $file != "$SITE_FILE" ]] || continue
  if defines_domain "$file"; then
    conflicts+=("$file")
  fi
done
if (( ${#conflicts[@]} > 0 )); then
  echo "!! $DOMAIN is already defined in: ${conflicts[*]}" >&2
  echo "!! Caddy would refuse to load two definitions of the same site." >&2
  echo "!! Remove or rename that file first (e.g. an older copy of this site). Nothing was changed." >&2
  exit 1
fi

# --- 2. main Caddyfile ---------------------------------------------------------
main_changed=0
main_backup=""
if [[ -f $MAIN_FILE ]] && grep -qxF "$IMPORT_LINE" "$MAIN_FILE"; then
  echo "    $MAIN_FILE already imports sites-enabled/; leaving it alone"
else
  if [[ -f $MAIN_FILE ]]; then
    main_backup="$BACKUP_DIR/Caddyfile.$STAMP"
    cp -a "$MAIN_FILE" "$main_backup"
    echo "    backed up $MAIN_FILE -> $main_backup"
  fi
  printf '%s\n' "$IMPORT_LINE" > "$MAIN_FILE"
  main_changed=1
  echo "    wrote import-only $MAIN_FILE"
fi

# --- 3. this site's file -------------------------------------------------------
new_site="$(mktemp)"
sed "s/REALESTATE_DOMAIN/${DOMAIN}/" "$TEMPLATE" > "$new_site"

site_changed=0
site_backup=""
site_existed=0
if [[ -f $SITE_FILE ]] && cmp -s "$new_site" "$SITE_FILE"; then
  rm -f "$new_site"
  echo "    $SITE_FILE is already up to date"
else
  if [[ -f $SITE_FILE ]]; then
    site_existed=1
    site_backup="$BACKUP_DIR/realestate.conf.$STAMP"
    cp -a "$SITE_FILE" "$site_backup"
    echo "    backed up $SITE_FILE -> $site_backup"
  fi
  install -m 644 "$new_site" "$SITE_FILE"
  rm -f "$new_site"
  site_changed=1
  echo "    wrote $SITE_FILE"
fi

if (( main_changed == 0 && site_changed == 0 )); then
  echo "    caddy config unchanged; no reload needed"
  exit 0
fi

# --- 4. log owned by caddy, validate as caddy (or put everything back) --------
if [[ $NO_RELOAD == 1 ]]; then
  echo "    NO_RELOAD=1: skipping validate + reload"
  exit 0
fi

log_created=0
restore_previous() {
  if (( log_created == 1 )) && [[ -f $LOG_FILE && ! -s $LOG_FILE ]]; then
    rm -f "$LOG_FILE"
  fi
  if (( site_changed == 1 )); then
    if (( site_existed == 1 )); then
      cp -a "$site_backup" "$SITE_FILE"
    else
      rm -f "$SITE_FILE"
    fi
  fi
  if (( main_changed == 1 )); then
    if [[ -n $main_backup ]]; then
      cp -a "$main_backup" "$MAIN_FILE"
    else
      rm -f "$MAIN_FILE"
    fi
  fi
}

if [[ ! -e $LOG_FILE ]]; then
  install -o "$CADDY_USER" -g "$CADDY_USER" -m 600 /dev/null "$LOG_FILE"
  log_created=1
  echo "    created $LOG_FILE ($CADDY_USER:$CADDY_USER 600)"
fi

caddy_home="${CADDY_HOME:-$(getent passwd "$CADDY_USER" | cut -d: -f6 || true)}"
if ! runuser -u "$CADDY_USER" -- env HOME="$caddy_home" \
     caddy validate --config "$MAIN_FILE" --adapter caddyfile; then
  restore_previous
  echo "!! caddy validate (as $CADDY_USER) failed: previous config restored, caddy NOT reloaded" >&2
  exit 1
fi

# --- 5. reload; never restart (or put everything back) --------------------------
if systemctl is-active --quiet caddy; then
  if ! systemctl reload caddy; then
    restore_previous
    echo "!! caddy reload failed: previous config restored. Caddy keeps running its" >&2
    echo "!! previous config (it was NOT restarted). See: journalctl -u caddy" >&2
    exit 1
  fi
  echo "    caddy reloaded"
else
  systemctl start caddy
  echo "    caddy was not running: started"
fi
