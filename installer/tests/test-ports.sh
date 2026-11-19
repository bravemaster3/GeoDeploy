#!/usr/bin/env bash
# What happens to a port AFTER it is chosen: candidate fallback, the no-terminal path, re-run drift,
# moving it, and the wedged bind that Docker reports as healthy.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
harness_build
trap 'kill_decoys; resume_other_installs' EXIT
pause_other_installs

# ─────────────────────────────────────────────────────────────────────────────
hdr "P1  the first candidate is busy — take the next one, leave it alone"
reset_install; decoy 8080 "SITE-A"
run_piped
chk "picked 8081"                     '[ "$(ev GEODEPLOY_HTTP_PORT)" = 8081 ]'
chk "bound 127.0.0.1:8081"            '[ "$(binding)" = "127.0.0.1:8081" ]'
chk "the decoy still serves"          '[ "$(curl -s http://127.0.0.1:8080/)" = "SITE-A" ]'
kill_decoys

hdr "P2  a piped install has no terminal — it must not hang, or eat its own script"
reset_install
run_piped
chk "completed"                       'grep -q "GeoDeploy is running" "$O"'
chk "said why it chose for you"       'grep -qi "could not ask you a question" "$O"'
chk "chose the option that claims nothing" '[ "$(ev GEODEPLOY_DEPLOY_MODE)" = behind-proxy ]'
chk "told you how to make it dedicated" 'grep -q "set-port.sh --dedicated" "$O"'

hdr "P3  every candidate occupied — fail, and start NOTHING"
reset_install
for p in 8080 8081 8082 8090 8880 9080 9090 8008 7080 8888; do decoy "$p" "S$p"; done
run_piped
chk "did not install"                 '! grep -q "GeoDeploy is running" "$O"'
chk "said no port was free"           'grep -qi "no free port\|every candidate is taken" "$O"'
chk "started no nginx"                '[ -z "$(dc ps -q nginx 2>/dev/null)" ]'
chk "all ten decoys still serving"    'for p in 8080 8081 8082 8090 8880 9080 9090 8008 7080 8888; do [ "$(curl -s http://127.0.0.1:$p/)" = "S$p" ] || exit 1; done'
kill_decoys

# ─────────────────────────────────────────────────────────────────────────────
hdr "P4  RE-RUNNING the installer must never move the port"
reset_install
run_piped
before="$(ev GEODEPLOY_HTTP_PORT)"
run_piped
chk "unchanged ($before)"             '[ "$(ev GEODEPLOY_HTTP_PORT)" = "$before" ]'
chk "said it was keeping it"          'grep -qi "Keeping this installation.s port" "$O"'
# A naive implementation re-scans and follows the new list. The operator's proxy_pass does not.
GEODEPLOY_PORT_CANDIDATES=9099,9098 run_piped
chk "unchanged with a NEW candidate list" '[ "$(ev GEODEPLOY_HTTP_PORT)" = "$before" ]'
GEODEPLOY_HTTP_PORT=9097 run_piped
chk "unchanged even when the env asks"    '[ "$(ev GEODEPLOY_HTTP_PORT)" = "$before" ]'

hdr "P5  set-port.sh moves it, and releases the old port"
run_setport 8090 -y
chk "moved to 8090"                   '[ "$(ev GEODEPLOY_HTTP_PORT)" = 8090 ]'
chk "bound there"                     '[ "$(binding)" = "127.0.0.1:8090" ]'
chk "serving there"                   '[ "$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8090/health)" = 200 ]'
chk "old port released"               '! curl -sf --max-time 2 "http://127.0.0.1:$before/health" >/dev/null 2>&1'

hdr "P6  a move that CANNOT come up is rolled back, and proved to be back"
run_setport --port 8085 --bind 10.99.99.99 -y
chk "reported the failure"            'grep -qi "restoring\|rolled back\|failed" "$O"'
chk "and that it is serving again"    'grep -qi "back on 127.0.0.1:8090 and serving" "$O"'
chk ".env restored"                   '[ "$(ev GEODEPLOY_HTTP_PORT)" = 8090 ]'
chk "bind restored"                   '[ "$(ev GEODEPLOY_HTTP_BIND)" = "127.0.0.1" ]'
chk "actually serving on 8090"        '[ "$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8090/health)" = 200 ]'

# ─────────────────────────────────────────────────────────────────────────────
hdr "P7  the configured port is taken at boot — fail loudly, do NOT relocate"
dc stop nginx >/dev/null 2>&1; dc rm -f nginx >/dev/null 2>&1
decoy 8090 "SQUATTER"
dc up -d nginx >/dev/null 2>&1
chk "nginx did not start"             '[ -z "$(dc ps --status running -q nginx 2>/dev/null)" ]'
chk ".env still says 8090"            '[ "$(ev GEODEPLOY_HTTP_PORT)" = 8090 ]'
chk "the squatter is unharmed"        '[ "$(curl -s http://127.0.0.1:8090/)" = "SQUATTER" ]'
run_preflight
chk "preflight names the conflict"    'grep -qi "Configured port" "$O" && grep -qi "in use by something else" "$O"'
chk "and points at set-port.sh"       'grep -q "set-port.sh" "$O"'

hdr "P8  the WEDGED bind — Docker says running, nothing is listening"
kill_decoys
# Measured behaviour, not a hypothetical: after a failed bind, neither `up -d` nor `restart`
# re-establishes the mapping. Only a recreate does. Everything that inspects Docker says healthy.
dc up -d nginx >/dev/null 2>&1; sleep 2
chk "plain up -d leaves it wedged"    '[ -n "$(dc ps --status running -q nginx)" ] && ! curl -sf --max-time 3 http://127.0.0.1:8090/health >/dev/null'
run_preflight
chk "preflight catches the dead port" 'grep -qi "but nothing answers there" "$O"'
chk "and names --force-recreate"      'grep -q "force-recreate nginx" "$O"'
dc up -d --force-recreate nginx >/dev/null 2>&1; sleep 2
chk "--force-recreate DOES recover"   '[ "$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8090/health)" = 200 ]'

hdr "P9  preflight is safe to run at any time and writes nothing"
before_env="$(md5sum "$REPO/.env" | cut -d' ' -f1)"
run_preflight
chk "left .env untouched"             '[ "$(md5sum "$REPO/.env" | cut -d" " -f1)" = "$before_env" ]'
chk "the instance is still serving"   '[ "$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8090/health)" = 200 ]'
run_preflight --json
chk "--json emits valid JSON"         'python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$O"'
chk "--json carries the findings"     'python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d[\"checks\"] and \"blockers\" in d else 1)" "$O"'

dc down --remove-orphans >/dev/null 2>&1
summary
