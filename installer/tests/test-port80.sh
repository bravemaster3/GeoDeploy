#!/usr/bin/env bash
# The cases that genuinely need port 80: the back-compatibility contract, dedicated mode, and the
# proof that behind-proxy really is unreachable from the network.
#
# !! THIS STOPS ANY GEODEPLOY ALREADY RUNNING ON THIS MACHINE and restarts it from an EXIT trap.
#    Do not run it against something you care about. The trap is not decoration — an early version
#    died on a quoting error and left the machine's real instance down.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
harness_build

LIVE="${GD_LIVE_DIR:-$HOME/geodeploy}"
LANIP="$(hostname -I | awk '{print $1}')"

restore() {
  echo ""; echo "### restoring anything this suite stopped ###"
  dc down --remove-orphans >/dev/null 2>&1
  kill_decoys
  if [ -d "$LIVE" ]; then
    ( cd "$LIVE" && docker compose up -d nginx ) >/dev/null 2>&1
    sleep 2
    echo "pre-existing GeoDeploy on :80 -> $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1/)"
  fi
}
trap restore EXIT

if [ -d "$LIVE" ]; then
  echo "### stopping the GeoDeploy at $LIVE for the duration ###"
  ( cd "$LIVE" && docker compose stop nginx ) >/dev/null 2>&1
  sleep 1
fi

# ─────────────────────────────────────────────────────────────────────────────
hdr "B1  BACK-COMPAT: a .env with none of the publish keys still binds 0.0.0.0:80"
reset_install
cp "$REPO/.env.example" "$REPO/.env"
sed -i '/^GEODEPLOY_DEPLOY_MODE=/d;/^GEODEPLOY_HTTP_BIND=/d;/^GEODEPLOY_HTTP_PORT=/d' "$REPO/.env"
chk "the keys really are absent"      '! grep -q "^GEODEPLOY_HTTP_PORT" "$REPO/.env"'
dc up -d nginx >/dev/null 2>&1; sleep 2
chk "compose defaulted to 0.0.0.0:80" '[ "$(binding)" = "0.0.0.0:80" ]'
chk "serving on :80"                  '[ "$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1/health)" = 200 ]'

hdr "B1b the installer over that old .env records the truth and moves nothing"
run_piped
chk "recorded dedicated"              '[ "$(ev GEODEPLOY_DEPLOY_MODE)" = dedicated ]'
chk "recorded 0.0.0.0"                '[ "$(ev GEODEPLOY_HTTP_BIND)" = 0.0.0.0 ]'
chk "recorded 80"                     '[ "$(ev GEODEPLOY_HTTP_PORT)" = 80 ]'
chk "said it was an existing install" 'grep -qi "Existing installation" "$O"'
chk "still bound 0.0.0.0:80"          '[ "$(binding)" = "0.0.0.0:80" ]'
chk "still serving"                   '[ "$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1/health)" = 200 ]'

# ─────────────────────────────────────────────────────────────────────────────
hdr "B2  an empty machine, and the operator chooses DEDICATED"
reset_install
run_tty $'1\n'
chk "reported the ports free"         'grep -qi "Ports 80 and 443 are free" "$O"'
chk "dedicated was the default"       'grep -q "Choose \[1\]" "$O"'
chk "mode dedicated"                  '[ "$(ev GEODEPLOY_DEPLOY_MODE)" = dedicated ]'
chk "bound 0.0.0.0:80"                '[ "$(binding)" = "0.0.0.0:80" ]'
chk "reachable on the LAN address"    '[ "$(curl -s -o /dev/null -w %{http_code} "http://$LANIP/health")" = 200 ]'
chk "told them how to move it later"  'grep -q "set-port.sh 8080" "$O"'
chk "did NOT ask for a domain"        '! grep -qi "Do you have a domain name" "$O"'

hdr "B2b off port 80 and back again, both directions"
run_setport 8080 -y
chk "moved to 127.0.0.1:8080"         '[ "$(binding)" = "127.0.0.1:8080" ]'
chk "port 80 released"                '! curl -sf --max-time 3 http://127.0.0.1/health >/dev/null 2>&1'
run_setport --dedicated -y
chk "back on 0.0.0.0:80"              '[ "$(binding)" = "0.0.0.0:80" ]'
chk "serving on 80 again"             '[ "$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1/health)" = 200 ]'

# ─────────────────────────────────────────────────────────────────────────────
hdr "B3  an empty machine, and the operator chooses a LOCAL PORT"
reset_install
run_tty $'2\nmaps.example.org\n'
chk "mode behind-proxy"               '[ "$(ev GEODEPLOY_DEPLOY_MODE)" = behind-proxy ]'
chk "bound to the loopback"           '[ "$(binding)" = "127.0.0.1:8080" ]'
chk "answers on the loopback"         '[ "$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8080/health)" = 200 ]'
# The security property of the whole mode, and the reason it is 127.0.0.1 rather than a high port:
# a Docker publish on 0.0.0.0 is not covered by ufw.
chk "REFUSED from the LAN address"    '! curl -sf --max-time 3 "http://$LANIP:8080/health" >/dev/null 2>&1'
chk "port 80 left completely alone"   '! curl -sf --max-time 3 http://127.0.0.1/health >/dev/null 2>&1'
chk "printed the SSH tunnel"          'grep -q "ssh -L 8080:127.0.0.1:8080" "$O"'
chk "asked for a domain"              'grep -qi "Do you have a domain name" "$O"'
chk "stored the domain hint"          'grep -q "maps.example.org" "$REPO/data/temp/deploy-hints.json"'

summary
