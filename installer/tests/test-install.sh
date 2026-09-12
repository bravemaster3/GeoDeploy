#!/usr/bin/env bash
# Choosing a port: the question, the free list, and the rule that EVERY path checks its answer.
#
# The bug this suite exists to prevent shipped once already: `--port 8081` was validated as a number
# and then trusted, so an install onto a busy port completed and left nginx dead — the failure
# surfacing one layer away from its cause.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
harness_build
trap 'kill_decoys' EXIT

# ─────────────────────────────────────────────────────────────────────────────
hdr "C0  three options, and 'dedicated' is explained rather than merely named"
reset_install
run_tty $'2\n'
chk "1 = a dedicated GeoDeploy server"  'grep -q "Make this machine a dedicated GeoDeploy server" "$O"'
chk "  …says it takes port 80"          'grep -q "takes port 80" "$O"'
chk "  …says the address has no port"   'grep -q "no port in" "$O"'
chk "  …says what breaks later"         'grep -q "will fail to start" "$O"'
chk "  …says nothing is stopped now"    'grep -q "Nothing running now is stopped" "$O"'
chk "2 = use the default port"          'grep -q "Use the default port" "$O"'
chk "3 = choose a different port"       'grep -q "Choose a different port" "$O"'

hdr "C1  the ports offered are filtered to what is actually free"
reset_install; decoy 8080; decoy 8081
run_tty $'2\n'
chk "a free list was shown"             'grep -q "Free right now:" "$O"'
chk "excludes the busy 8080"            '! grep "Free right now:" "$O" | grep -qw 8080'
chk "excludes the busy 8081"            '! grep "Free right now:" "$O" | grep -qw 8081'
chk "offers the free 8082"              'grep "Free right now:" "$O" | grep -qw 8082'
chk "names 8082 as the default"         'grep -q "Use the default port — 127.0.0.1:8082" "$O"'
chk "option 2 took it"                  '[ "$(ev GEODEPLOY_HTTP_PORT)" = 8082 ]'
chk "bound 127.0.0.1:8082"              '[ "$(binding)" = "127.0.0.1:8082" ]'
chk "serving there"                     '[ "$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8082/health)" = 200 ]'
chk "both decoys untouched"             'curl -sf --max-time 3 http://127.0.0.1:8080/ >/dev/null && curl -sf --max-time 3 http://127.0.0.1:8081/ >/dev/null'
kill_decoys

hdr "C2  option 3 accepts any port, on the list or not"
reset_install
run_tty $'3\n3456\n'
chk "invites another"                   'grep -q "or type any other port" "$O"'
chk "took the typed 3456"               '[ "$(ev GEODEPLOY_HTTP_PORT)" = 3456 ]'
chk "bound 127.0.0.1:3456"              '[ "$(binding)" = "127.0.0.1:3456" ]'
chk "serving"                           '[ "$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:3456/health)" = 200 ]'

hdr "C3  a typed port that is TAKEN is refused, its holder named, and asked again"
reset_install; decoy 8085
run_tty $'3\n8085\n\n'
chk "refused"                           'grep -q "Port 8085 is already in use" "$O"'
chk "named the holder"                  'grep "Port 8085 is already in use" "$O" | grep -qi "docker container\|pid"'
chk "asked again"                       '[ "$(grep -c "Which port?" "$O")" -ge 2 ]'
chk "landed somewhere else"             '[ -n "$(ev GEODEPLOY_HTTP_PORT)" ] && [ "$(ev GEODEPLOY_HTTP_PORT)" != 8085 ]'
chk "the decoy is untouched"            'curl -sf --max-time 3 http://127.0.0.1:8085/ >/dev/null'
kill_decoys

hdr "C4  a privileged port is refused with a reason, not silently"
reset_install
run_tty $'3\n81\n8082\n'
chk "refused port 81"                   'grep -q "Port 81 is privileged" "$O"'
chk "accepted 8082 after"               '[ "$(ev GEODEPLOY_HTTP_PORT)" = 8082 ]'

# ─────────────────────────────────────────────────────────────────────────────
hdr "C5  --port is CHECKED — the regression that shipped once"
reset_install; decoy 8086
run_install --port 8086
chk "refused"                           'grep -qi "already in use" "$O"'
chk "offered free alternatives"         'grep -qi "Free right now" "$O"'
chk "wrote no .env"                     '[ ! -f "$REPO/.env" ]'
chk "started nothing"                   '[ -z "$(dc ps -q nginx 2>/dev/null)" ]'
chk "the decoy is untouched"            'curl -sf --max-time 3 http://127.0.0.1:8086/ >/dev/null'

hdr "C5b  …and with a terminal it offers the chooser instead of only failing"
run_tty $'2\n' "--port 8086"
chk "named the port asked for"          'grep -q "You asked for port 8086" "$O"'
chk "fell through to the question"      'grep -q "How should GeoDeploy be published" "$O"'
chk "installed somewhere else"          '[ -n "$(ev GEODEPLOY_HTTP_PORT)" ] && [ "$(ev GEODEPLOY_HTTP_PORT)" != 8086 ]'
kill_decoys

hdr "C5c  --port with a free port is taken as given, no questions"
reset_install
run_install --port 8087
chk "used 8087"                         '[ "$(ev GEODEPLOY_HTTP_PORT)" = 8087 ]'
chk "behind-proxy, not dedicated"       '[ "$(ev GEODEPLOY_DEPLOY_MODE)" = behind-proxy ]'
chk "bound to the LOOPBACK"             '[ "$(binding)" = "127.0.0.1:8087" ]'
chk "did not ask"                       '! grep -q "How should GeoDeploy be published" "$O"'

hdr "C5d  --dedicated while :80 is busy is refused, not forced"
reset_install
run_install --dedicated
chk "refused"                           'grep -qi "already in use" "$O"'
chk "started nothing"                   '[ -z "$(dc ps -q nginx 2>/dev/null)" ]'

hdr "C5e  a nonsense --port is rejected before anything happens"
for p in 0 65536 -1 abc; do
  reset_install
  run_install --port "$p"
  chk "rejects --port $p"               'grep -qi "not a port number\|Unknown option" "$O" && [ ! -f "$REPO/.env" ]'
done
reset_install
run_install --nonsense
chk "rejects an unknown option"         'grep -qi "Unknown option" "$O"'
run_install --help
chk "--help explains itself"            'grep -q "\-\-dedicated" "$O" && grep -q "GEODEPLOY_PORT_CANDIDATES" "$O"'

# ─────────────────────────────────────────────────────────────────────────────
hdr "C6  candidates come from .env, and the environment wins over it"
reset_install
printf 'GEODEPLOY_PORT_CANDIDATES=9101,9102,9103\n' > "$REPO/.env"
run_preflight
chk "preflight used the .env list"      'grep "A free port for GeoDeploy" "$O" | grep -qw 9101'
rm -f "$REPO/.env"
reset_install
GEODEPLOY_PORT_CANDIDATES=9201,9202 run_tty $'2\n'
chk "the environment wins"              '[ "$(ev GEODEPLOY_HTTP_PORT)" = 9201 ]'
chk "and the menu says so"              'grep -q "Use the default port — 127.0.0.1:9201" "$O"'

hdr "C7  set-port.sh refuses a taken target and says where to go instead"
decoy 8088
run_setport 8088 -y
chk "refused"                           'grep -qi "already in use" "$O"'
chk "listed live alternatives"          'grep -q "Free right now:" "$O"'
chk "excluded the busy one"             '! grep "Free right now:" "$O" | grep -qw 8088'
chk "gave a copy-paste command"         'grep -q "sudo bash installer/set-port.sh " "$O"'
chk "changed nothing"                   '[ "$(ev GEODEPLOY_HTTP_PORT)" = 9201 ]'
kill_decoys

hdr "C7b set-port.sh argument handling"
run_setport --show
chk "--show reports where it is"        'grep -q "Published on" "$O"'
run_setport notaport -y
chk "rejects a non-port"                'grep -qi "not a port number" "$O"'
run_setport --port 8099 --bind nonsense -y
chk "rejects a bad --bind"              'grep -qi "must be an IP address" "$O"'
run_setport 9201 -y
chk "moving to the same port is a no-op" 'grep -qi "nothing to do" "$O"'
( cd "$REPO" && bash installer/set-port.sh 8099 </dev/null ) >"$O" 2>&1; strip_o
chk "refuses without -y and without a tty" 'grep -qi "Refusing to change the port without confirmation" "$O"'

dc down --remove-orphans >/dev/null 2>&1
summary
