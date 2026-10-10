#!/usr/bin/env bash
#
# Behaviour tests for deploy/install-caddy-site.sh, off the server.
#
# Each case runs against a scratch "Caddy directory" with fake caddy /
# runuser / systemctl / install commands first on PATH. The fakes record
# their calls; FAIL=<step> makes one of them fail. Nothing here touches a
# real Caddy or server.
#
# Run from the repository root:  bash deploy/tests/test_install_caddy_site.sh
set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="${INSTALL_SCRIPT_UNDER_TEST:-$ROOT/deploy/install-caddy-site.sh}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
DOMAIN=germany-real-estate.duckdns.org
pass=0
fail=0

# --- fakes ---------------------------------------------------------------------
BIN="$WORK/bin"
mkdir -p "$BIN"
cat > "$BIN/caddy" <<'EOF'
#!/usr/bin/env bash
echo "caddy $*" >> "$CALLS"
if [ "${FAIL:-}" = validate ]; then echo "Error: fake validate failure" >&2; exit 1; fi
echo "Valid configuration"
EOF
cat > "$BIN/runuser" <<'EOF'
#!/usr/bin/env bash
echo "runuser $*" >> "$CALLS"
while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do shift; done
shift
exec "$@"
EOF
cat > "$BIN/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "$CALLS"
case "$1" in
  is-active) [ "${FAKE_ACTIVE:-1}" = 1 ] ;;
  reload)
    if [ "${FAKE_CADDY_WRITES_LOG:-0}" = 1 ]; then echo "caddy wrote a line" >> "$CADDY_LOG_DIR/realestate.log"; fi
    [ "${FAIL:-}" != reload ] ;;
  *) exit 0 ;;
esac
EOF
cat > "$BIN/install" <<'EOF'
#!/usr/bin/env bash
# Only the log-file form is faked (install -o U -g G -m 600 /dev/null FILE);
# everything else goes to the real install, without owner options.
if [ "$1" = "-o" ]; then for last in "$@"; do :; done; : > "$last"; exit 0; fi
exec /usr/bin/install "$@"
EOF
chmod +x "$BIN"/*

ME="$(stat -c %U "$SCRIPT")"   # owner of local files, stands in for "caddy"

OTHER_SITE='other.example.org {
	handle /api/* {
		reverse_proxy 127.0.0.1:8001
	}
	handle {
		file_server
	}
}'

fresh() {  # a scratch Caddy directory: apt-style default Caddyfile + another project's site
  T="$WORK/$1"
  mkdir -p "$T/caddy/sites-enabled" "$T/log"
  printf ':80 {\n\troot * /usr/share/caddy\n\tfile_server\n}\n' > "$T/caddy/Caddyfile"
  printf '%s\n' "$OTHER_SITE" > "$T/caddy/sites-enabled/other.conf"
  export CADDY_DIR="$T/caddy" CADDY_LOG_DIR="$T/log" CADDY_USER="$ME" CADDY_HOME="$T"
  export CALLS="$T/calls.log" FAIL="" FAKE_ACTIVE=1 FAKE_CADDY_WRITES_LOG=0
  : > "$CALLS"
}
run() { PATH="$BIN:$PATH" bash "$SCRIPT" "$DOMAIN" > "$T/out.log" 2>&1; echo $? > "$T/exit"; }
snapshot() { (cd "$T" && find caddy log -type f | sort | xargs md5sum); }
check() {
  if eval "$2"; then echo "  PASS  $1"; pass=$((pass + 1))
  else echo "  FAIL  $1"; fail=$((fail + 1)); sed 's/^/        | /' "$T/out.log"; fi
}
exit_is() { [ "$(cat "$T/exit")" = "$1" ]; }
called() { grep -q -- "$1" "$CALLS"; }
LOG() { echo "$CADDY_LOG_DIR/realestate.log"; }

echo "1. apt default Caddyfile: backed up, import-only written, site + log added, validated as caddy, reloaded"
fresh c1; other_before=$(md5sum < "$CADDY_DIR/sites-enabled/other.conf"); run
check "exit 0"                                  'exit_is 0'
check "main Caddyfile is import-only"           '[ "$(cat "$CADDY_DIR/Caddyfile")" = "import sites-enabled/*" ]'
check "apt default backed up (outside sites-enabled)" 'grep -q file_server "$CADDY_DIR"/backups/Caddyfile.*'
check "realestate.conf written"                 '[ -f "$CADDY_DIR/sites-enabled/realestate.conf" ]'
check "only realestate.conf + other.conf in sites-enabled" '[ "$(ls "$CADDY_DIR/sites-enabled" | tr "\n" " ")" = "other.conf realestate.conf " ]'
check "other project's site untouched"          '[ "$(md5sum < "$CADDY_DIR/sites-enabled/other.conf")" = "$other_before" ]'
check "log created before validating"           '[ -f "$(LOG)" ]'
check "validated as the caddy user, with its HOME" 'called "runuser -u $ME -- env HOME=$T caddy validate"'
check "reloaded, not restarted"                 'called "systemctl reload caddy" && ! called "restart"'

echo "2. re-run (idempotent): no changes, no validate, no reload"
before=$(snapshot); : > "$CALLS"; run
check "exit 0"                                  'exit_is 0'
check "no file changed or added"                '[ "$(snapshot)" = "$before" ]'
check "no caddy / systemctl calls"              '[ ! -s "$CALLS" ]'

echo "3. main Caddyfile already has the import line: left alone"
fresh c3; printf '# by hand\nimport sites-enabled/*\n' > "$CADDY_DIR/Caddyfile"; main_before=$(md5sum < "$CADDY_DIR/Caddyfile"); run
check "exit 0"                                  'exit_is 0'
check "main Caddyfile unchanged, no backup"     '[ "$(md5sum < "$CADDY_DIR/Caddyfile")" = "$main_before" ] && ! ls "$CADDY_DIR"/backups/Caddyfile.* >/dev/null 2>&1'

echo "4. another file already defines the domain: stop, change nothing"
fresh c4; printf '%s {\n\trespond "old"\n}\n' "$DOMAIN" > "$CADDY_DIR/sites-enabled/00-existing.conf"; before=$(snapshot); run
check "exit 1, names the file, nothing changed" 'exit_is 1 && grep -q 00-existing.conf "$T/out.log" && [ "$(snapshot)" = "$before" ] && [ ! -s "$CALLS" ]'

echo "5. domain only in a comment or a redirect: not a conflict"
fresh c5; printf '# %s {\nx.example.org {\n\tredir https://%s{uri}\n}\n' "$DOMAIN" "$DOMAIN" > "$CADDY_DIR/sites-enabled/x.conf"; run
check "exit 0"                                  'exit_is 0'

echo "6. domain as https:// address with port, in a list: a conflict"
fresh c6; printf 'a.example.org, https://%s:443 {\n\trespond "x"\n}\n' "$DOMAIN" > "$CADDY_DIR/sites-enabled/multi.conf"; run
check "exit 1"                                  'exit_is 1'

echo "7. validate fails on first install: files restored, created log removed, no reload"
fresh c7; main_before=$(md5sum < "$CADDY_DIR/Caddyfile"); FAIL=validate; run
check "exit 1"                                  'exit_is 1'
check "main Caddyfile restored"                 '[ "$(md5sum < "$CADDY_DIR/Caddyfile")" = "$main_before" ]'
check "new realestate.conf removed"             '[ ! -f "$CADDY_DIR/sites-enabled/realestate.conf" ]'
check "log created by this run removed"         '[ ! -e "$(LOG)" ]'
check "no reload or restart"                    '! called "systemctl reload" && ! called restart'

echo "8. validate fails on an update: the old site file is restored"
fresh c8; printf 'import sites-enabled/*\n' > "$CADDY_DIR/Caddyfile"
printf '%s {\n\trespond "old"\n}\n' "$DOMAIN" > "$CADDY_DIR/sites-enabled/realestate.conf"
site_before=$(md5sum < "$CADDY_DIR/sites-enabled/realestate.conf"); FAIL=validate; run
check "exit 1, old realestate.conf restored, no reload" 'exit_is 1 && [ "$(md5sum < "$CADDY_DIR/sites-enabled/realestate.conf")" = "$site_before" ] && ! called "systemctl reload"'

echo "9. missing main Caddyfile: created import-only, no backup"
fresh c9; rm "$CADDY_DIR/Caddyfile"; run
check "exit 0, import-only, no backup"          'exit_is 0 && [ "$(cat "$CADDY_DIR/Caddyfile")" = "import sites-enabled/*" ] && ! ls "$CADDY_DIR"/backups/Caddyfile.* >/dev/null 2>&1'

echo "10. reload fails (Caddy running): files restored, created log removed, NOT restarted"
fresh c10; main_before=$(md5sum < "$CADDY_DIR/Caddyfile"); FAIL=reload; run
check "exit 1"                                  'exit_is 1'
check "main Caddyfile restored, site file removed" '[ "$(md5sum < "$CADDY_DIR/Caddyfile")" = "$main_before" ] && [ ! -f "$CADDY_DIR/sites-enabled/realestate.conf" ]'
check "log created by this run removed"         '[ ! -e "$(LOG)" ]'
check "never restarted (old config keeps serving every site)" '! called restart && ! called "systemctl start"'

echo "11. reload fails, the log existed before (owned by caddy): log left alone"
fresh c11; : > "$(LOG)"; FAIL=reload; run
check "exit 1, pre-existing log left alone"     'exit_is 1 && [ -f "$(LOG)" ]'

echo "12. reload fails after Caddy wrote to the new log: non-empty log left alone"
fresh c12; FAIL=reload; FAKE_CADDY_WRITES_LOG=1; run
check "exit 1, non-empty log left alone"        'exit_is 1 && [ -s "$(LOG)" ]'

echo "13. Caddy not running (first install): started, not reloaded"
fresh c13; FAKE_ACTIVE=0; run
check "exit 0, started"                         'exit_is 0 && called "systemctl start caddy" && ! called "systemctl reload"'

echo "14. a log exists but is owned by another user: stop before changing anything"
fresh c14; echo old > "$(LOG)"; before=$(snapshot); CADDY_USER="someone-else"; run
check "exit 1, nothing changed, no calls"       'exit_is 1 && [ "$(snapshot)" = "$before" ] && [ ! -s "$CALLS" ]'

echo
echo "passed: $pass  failed: $fail"
[ "$fail" = 0 ]
