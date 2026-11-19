#!/usr/bin/env bash
# test-upgrade.sh — what happens to an EXISTING GeoDeploy when it updates, and to a NEW machine when
# somebody installs an older release or another branch.
#
# Every scenario runs on a fresh FAKE MACHINE (hostproxy/Dockerfile, stage `machine`): Debian with its
# own dockerd, the REAL installers and updaters, and a bare copy of this repository standing in for
# GitHub (git's `insteadOf`, so the scripts clone and fetch exactly the URL they always do). Nothing
# on the machine running the tests is touched.
#
#   F*  fresh installs with THIS installer: an old release (refused without consent, refused when 80
#       or 443 is taken, refused behind a proxy, handed to its own installer with consent), and this
#       branch in both modes
#   U*  an instance installed from `main` by main's installer, then updated the way "Update now"
#       does it (main's self-update.sh, which is the copy on disk), then recreated, downgraded,
#       moved behind a proxy, migrated, and refused the downgrades that would take 80/443
#   M*  the manual `installer/update.sh` path, with `main` fast-forwarded to this branch as a merge
#       would
#
#   bash installer/tests/test-upgrade.sh            # all, ~40 min (U and M build the images for real)
#   bash installer/tests/test-upgrade.sh F          # just the fresh installs, ~5 min
#
# Images are preloaded from this machine where building is not what is under test (installs); every
# UPDATE builds for real inside the fake machine, as it would on a server.
set -uo pipefail

SRC="${SRC:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
W="${GD_UP_HOME:-$HOME/gdup}"
BR="${GD_BRANCH:-$(git -C "$SRC" rev-parse --abbrev-ref HEAD)}"
OLD="${GD_OLD:-v1.6.4}"
PLUGIN="${GD_COMPOSE_PLUGIN:-/usr/libexec/docker/cli-plugins/docker-compose}"
# buildx too: api/Dockerfile uses `RUN --mount=type=cache`, which needs BuildKit, and Debian's own
# docker.io has no buildx — the build then fails (and the updater, correctly, rolls back).
BUILDX="${GD_BUILDX_PLUGIN:-/usr/libexec/docker/cli-plugins/docker-buildx}"
# Both the pinned tile servers (this branch) and `:latest` (what older versions name).
IMAGES="geodeploy/api:latest geodeploy/ui:latest nginx:alpine redis:7-alpine ghcr.io/maplibre/martin:latest ghcr.io/developmentseed/titiler:latest ghcr.io/maplibre/martin:1.16.1 ghcr.io/developmentseed/titiler:2.4.1"
ONLY=" $* "

P=0; F=0; FAILS=""
ok(){ echo "   PASS  $*"; P=$((P+1)); }
bad(){ echo "   FAIL  $*"; F=$((F+1)); FAILS="$FAILS\n   [$SC] $*"; }
hdr(){ echo ""; echo "== $SC — $* =="; }
want(){ [ "$ONLY" = "  " ] || [[ "$ONLY" == *" $1 "* ]]; }
chk(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

prepare() {
  mkdir -p "$W"
  rm -rf "$W/ctx"; cp -r "$SRC/installer/tests/hostproxy" "$W/ctx"; sed -i 's/\r$//' "$W/ctx"/*
  docker build -q --target machine -t gdhp-fakehost:machine "$W/ctx" >/dev/null || { echo "build failed"; exit 2; }
  # The stand-in for GitHub: every branch and tag of this checkout, including commits not pushed yet.
  rm -rf "$W/origin.git"; git clone -q --bare "$SRC" "$W/origin.git"
  # Keyed on the image LIST, so changing IMAGES cannot silently reuse a tar without the new ones.
  TAR="$W/images-$(echo "$IMAGES" | md5sum | cut -c1-12).tar"
  [ -f "$TAR" ] || docker save $IMAGES -o "$TAR"
  docker network create gdhp >/dev/null 2>&1 || true
}

boot() { # NAME
  SC="$1"; FH="gdup-$1"
  docker rm -f "$FH" >/dev/null 2>&1; docker volume rm -f "$FH-docker" >/dev/null 2>&1
  docker run -d --privileged --name "$FH" --hostname "$1" --network gdhp -e GDHP_STORAGE=overlay2 \
    -v "$FH-docker:/var/lib/docker" -v "$W/origin.git:/srv/origin.git:ro" \
    -v "$PLUGIN:/usr/libexec/docker/cli-plugins/docker-compose:ro"     -v "$BUILDX:/usr/libexec/docker/cli-plugins/docker-buildx:ro" -e DOCKER_BUILDKIT=1     gdhp-fakehost:machine >/dev/null
  for _ in $(seq 1 60); do docker exec "$FH" docker info >/dev/null 2>&1 && break; sleep 1; done
  docker exec -i "$FH" docker load < "$TAR" >/dev/null
  fx "git config --system url./srv/origin.git.insteadOf https://github.com/bravemaster3/geodeploy
      git config --system --add safe.directory '*'"
}
down() { docker rm -f "$FH" >/dev/null 2>&1; docker volume rm -f "$FH-docker" >/dev/null 2>&1; }
fx() { docker exec "$FH" bash -c "$1"; }
G=/root/geodeploy
gdc() { fx "cd $G && docker compose $*"; }
health() { fx "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1${1:+:$1}/health"; }
v6health() { fx "curl -s -o /dev/null -w '%{http_code}' --max-time 5 -g 'http://[::1]${1:+:$1}/health'"; }
listening() { fx "ss -Hlnt '( sport = :$1 )' | grep -q ."; }
# nginx's host bindings for 80 and 443, as created: `:80` is a bare `80:80` (IPv4 AND IPv6)
binding() { fx "docker inspect -f '{{range \$p,\$b := .HostConfig.PortBindings}}{{\$p}}={{range \$b}}{{.HostIp}}:{{.HostPort}}{{end}} {{end}}' \$(cd $G && docker compose ps -q nginx | head -1)" | tr -s ' ' | sed 's/ $//'; }
# What compose WOULD publish for nginx, from its own resolved config: [(host_ip, published, target)]
nginx_ports() { fx "cd $G && docker compose config --format json" | python3 -c 'import json,sys; print(" ".join("%s|%s|%s" % (p.get("host_ip") or "", p.get("published"), p.get("target")) for p in json.load(sys.stdin)["services"]["nginx"]["ports"]))'; }
nginx_id() { fx "cd $G && docker compose ps -q nginx | head -1"; }
head_is() { [ "$(fx "git -C $G rev-parse HEAD")" = "$(git -C "$W/origin.git" rev-parse "$1^{commit}")" ]; }
envget() { fx "grep -E '^$1=' $G/.env | tail -1 | cut -d= -f2-"; }
haskey() { fx "grep -qE '^$1=' $G/.env"; }
status_state() { fx "python3 -c 'import json; print(json.load(open(\"$G/data/temp/update-status.json\")).get(\"phase\"))'"; }
status_msg() { fx "python3 -c 'import json; print(json.load(open(\"$G/data/temp/update-status.json\")).get(\"message\"))'"; }
wait_health() { for _ in $(seq 1 60); do [ "$(health "${1:-}")" = 200 ] && return 0; sleep 3; done; return 1; }
teardown_install() { fx "cd $G 2>/dev/null && docker compose down >/dev/null 2>&1; cd / && rm -rf $G; docker network rm geodeploy >/dev/null 2>&1; true"; }

prepare
echo "branch under test: $BR    old release: $OLD"

# ══ Fresh installs with THIS installer ════════════════════════════════════════════════════════════
if want F; then
  boot F
  fx "git -C /srv/origin.git show $BR:installer/install.sh > /root/new-install.sh"
  newi() { fx "cd /root && $1 bash /root/new-install.sh $2 < /dev/null > /root/f.log 2>&1; echo \$?"; }
  log_has() { fx "sed 's/\x1b\[[0-9;]*m//g' /root/f.log | grep -qi -- \"$1\""; }

  hdr "F1  $OLD, piped, no consent → refused, nothing left behind"
  rc=$(newi "GEODEPLOY_VERSION=$OLD" "")
  chk "refused (exit $rc)"                         '[ "$rc" != 0 ]'
  chk "says it takes 80 AND 443"                   'log_has "takes ports 80 AND 443"'
  chk "says how to consent (--dedicated)"          'log_has "Pass --dedicated"'
  chk "the clone it made is removed"               '! fx "[ -e $G ]"'
  chk "nothing listens on 80"                      '! listening 80'

  hdr "F2  $OLD --behind-proxy → refused: that version cannot"
  rc=$(newi "GEODEPLOY_VERSION=$OLD" "--behind-proxy")
  chk "refused (exit $rc)"                         '[ "$rc" != 0 ]'
  chk "says it cannot be published behind a proxy" 'log_has "cannot be published behind a proxy"'
  chk "nothing left behind"                        '! fx "[ -e $G ]"'

  hdr "F3  $OLD --dedicated while somebody else holds port 80 → refused"
  fx "docker run -d --name decoy -p 80:80 nginx:alpine >/dev/null"; sleep 1
  rc=$(newi "GEODEPLOY_VERSION=$OLD" "--dedicated")
  chk "refused (exit $rc)"                         '[ "$rc" != 0 ]'
  chk "names port 80"                              'log_has "Port 80 is already in use"'
  chk "the other site still answers"               '[ "$(fx "curl -s -o /dev/null -w %{http_code} http://127.0.0.1/")" = 200 ]'
  chk "nothing left behind"                        '! fx "[ -e $G ]"'
  fx "docker rm -f decoy >/dev/null"

  hdr "F4  $OLD, interactive, answer 'n' → refused"
  fx "printf 'n\n' > /root/keys; cd /root && script -qec 'GEODEPLOY_VERSION=$OLD bash /root/new-install.sh' /dev/null < /root/keys > /root/f.log 2>&1; true"
  chk "asked before taking 80 and 443"             'log_has "let it take ports 80 and 443"'
  chk "stopped on 'n'"                             'log_has "Not confirmed"'
  chk "nothing left behind"                        '! fx "[ -e $G ]"'

  hdr "F5  $OLD --dedicated, ports free → handed to $OLD's own installer"
  rc=$(newi "GEODEPLOY_VERSION=$OLD" "--dedicated")
  chk "installed (exit $rc)"                       '[ "$rc" = 0 ]'
  chk "said it was handing over"                   'log_has "Handing over to the installer of"'
  chk "the checkout IS $OLD"                       'head_is $OLD'
  chk "serving on 80"                              'wait_health'
  chk "…and over IPv6, as $OLD always did"         '[ "$(v6health)" = 200 ]'
  teardown_install

  hdr "F6  $BR, piped, no flags → behind-proxy on a loopback port (never 80)"
  rc=$(newi "GEODEPLOY_VERSION=$BR" "")
  port=$(envget GEODEPLOY_HTTP_PORT)
  chk "installed (exit $rc)"                       '[ "$rc" = 0 ]'
  chk "mode behind-proxy, port $port"              '[ "$(envget GEODEPLOY_DEPLOY_MODE)" = behind-proxy ] && [ "$port" != 80 ]'
  chk "PUBLISH=127.0.0.1:$port"                    '[ "$(envget GEODEPLOY_HTTP_PUBLISH)" = "127.0.0.1:$port" ]'
  chk "nginx bound 127.0.0.1:$port only"           '[ "$(binding)" = "80/tcp=127.0.0.1:$port" ]'
  chk "serving on $port"                           'wait_health $port'
  chk "port 80 untouched"                          '! listening 80'
  teardown_install

  hdr "F7  $BR --dedicated → a bare 80:80, IPv4 AND IPv6, no 443"
  rc=$(newi "GEODEPLOY_VERSION=$BR" "--dedicated")
  chk "installed (exit $rc)"                       '[ "$rc" = 0 ]'
  chk "bind recorded empty, PUBLISH=80"            '[ -z "$(envget GEODEPLOY_HTTP_BIND)" ] && [ "$(envget GEODEPLOY_HTTP_PUBLISH)" = 80 ]'
  chk "nginx bound bare :80, nothing on 443"       '[ "$(binding)" = "80/tcp=:80" ]'
  chk "serving on 80"                              'wait_health'
  chk "serving over IPv6"                          '[ "$(v6health)" = 200 ]'
  teardown_install
  down
fi

# ══ An instance from main, updated the way "Update now" does it ═══════════════════════════════════
if want U; then
  boot U
  hdr "U1  install main with MAIN's installer"
  fx "git -C /srv/origin.git show main:installer/install.sh > /root/main-install.sh; cd /root && bash /root/main-install.sh < /dev/null > /root/u1.log 2>&1"
  chk "serving on 80"                              'wait_health'
  chk "serving over IPv6"                          '[ "$(v6health)" = 200 ]'
  chk "published 80:80 and 443:443, as main does"  '[ "$(binding)" = "443/tcp=:443 80/tcp=:80" ]'
  NG1=$(nginx_id); envsum=$(fx "grep -v '^GEODEPLOY_GIT_SHA=' $G/.env | sha256sum")

  hdr "U2  'Update now' to $BR — run by MAIN's self-update.sh, the copy on disk (builds for real)"
  fx "cd $G && bash installer/self-update.sh $BR > /root/u2.log 2>&1; true"
  chk "the update reports success ($(status_state))" '[ "$(status_state)" = success ]'
  chk "the checkout is $BR"                        'head_is $BR'
  chk "serving on 80"                              'wait_health'
  chk "serving over IPv6"                          '[ "$(v6health)" = 200 ]'
  chk "nginx was NOT recreated (zero downtime)"    '[ "$(nginx_id)" = "$NG1" ]'
  chk ".env untouched apart from the sha"          '[ "$(fx "grep -v ^GEODEPLOY_GIT_SHA= $G/.env | sha256sum")" = "$envsum" ]'
  chk "no publish keys invented for it"            '! haskey GEODEPLOY_HTTP_PUBLISH && ! haskey GEODEPLOY_HTTP_BIND'
  chk "compose now resolves a bare 80:80"          '[ "$(nginx_ports)" = "|80|80" ]'

  hdr "U3  nginx recreated later (any compose change does it) → still IPv4 AND IPv6"
  gdc "up -d --force-recreate nginx" >/dev/null 2>&1
  chk "bound bare :80; 443 released"               '[ "$(binding)" = "80/tcp=:80" ]'
  chk "serving on 80"                              'wait_health'
  chk "serving over IPv6"                          '[ "$(v6health)" = 200 ]'

  hdr "U4  downgrade to $OLD with THIS updater — allowed: the instance is on the default 80"
  fx "cd $G && bash installer/self-update.sh $OLD > /root/u4.log 2>&1; true"
  chk "success ($(status_state))"                  '[ "$(status_state)" = success ]'
  chk "the checkout is $OLD"                       'head_is $OLD'
  chk "serving on 80"                              'wait_health'

  hdr "U5  and forward again — by $OLD's updater this time"
  fx "cd $G && bash installer/self-update.sh $BR > /root/u5.log 2>&1; true"
  chk "success ($(status_state))"                  '[ "$(status_state)" = success ]'
  chk "the checkout is $BR"                        'head_is $BR'
  chk "serving on 80"                              'wait_health'

  hdr "U6  moved behind a proxy with set-port.sh"
  fx "cd $G && bash installer/set-port.sh 8080 -y > /root/u6.log 2>&1; true"
  chk "PUBLISH=127.0.0.1:8080"                     '[ "$(envget GEODEPLOY_HTTP_PUBLISH)" = 127.0.0.1:8080 ]'
  chk "bound 127.0.0.1:8080 only"                  '[ "$(binding)" = "80/tcp=127.0.0.1:8080" ]'
  chk "serving on 8080"                            'wait_health 8080'
  chk "port 80 released"                           '! listening 80'

  hdr "U7  a .env from before PUBLISH existed (BIND/PORT only) — the API adds it at startup"
  fx "cd $G && grep -v '^GEODEPLOY_HTTP_PUBLISH=' .env > /root/env.tmp && cat /root/env.tmp > .env"
  chk "(the key is really gone)"                   '! haskey GEODEPLOY_HTTP_PUBLISH'
  gdc "restart geodeploy-api" >/dev/null 2>&1; sleep 8
  chk "the API wrote PUBLISH=127.0.0.1:8080"       '[ "$(envget GEODEPLOY_HTTP_PUBLISH)" = 127.0.0.1:8080 ]'
  gdc "up -d --force-recreate nginx" >/dev/null 2>&1
  chk "so a recreate stays on 127.0.0.1:8080"      '[ "$(binding)" = "80/tcp=127.0.0.1:8080" ]'

  hdr "U8  …and the updater adds it too, before any compose command"
  fx "cd $G && grep -v '^GEODEPLOY_HTTP_PUBLISH=' .env > /root/env.tmp && cat /root/env.tmp > .env"
  fx "cd $G && bash installer/self-update.sh $BR > /root/u8.log 2>&1; true"
  chk "self-update wrote PUBLISH=127.0.0.1:8080"   '[ "$(envget GEODEPLOY_HTTP_PUBLISH)" = 127.0.0.1:8080 ]'
  chk "still on 127.0.0.1:8080, port 80 free"      '[ "$(binding)" = "80/tcp=127.0.0.1:8080" ] && ! listening 80'

  hdr "U9  downgrade to $OLD while behind a proxy → refused, nothing changed"
  before=$(fx "git -C $G rev-parse HEAD")
  fx "cd $G && bash installer/self-update.sh $OLD > /root/u9.log 2>&1; true"
  chk "refused ($(status_state))"                  '[ "$(status_state)" = error ]'
  chk "says why: it takes 80 and 443"              'status_msg | grep -q "predates"'
  chk "the checkout did not move"                  '[ "$(fx "git -C $G rev-parse HEAD")" = "$before" ]'
  chk "still serving on 8080, port 80 free"        '[ "$(health 8080)" = 200 ] && ! listening 80'

  hdr "U10 the installer re-run onto $OLD → refused BEFORE the reset"
  fx "cd /root && GEODEPLOY_VERSION=$OLD bash $G/installer/install.sh < /dev/null > /root/u10.log 2>&1; true"
  chk "refused, pointing at self-update.sh"        'fx "grep -q \"predates this installer\" /root/u10.log"'
  chk "the checkout did not move"                  '[ "$(fx "git -C $G rev-parse HEAD")" = "$before" ]'
  chk "still serving on 8080"                      '[ "$(health 8080)" = 200 ]'
  down
fi

# ══ The manual path: installer/update.sh after `main` gains this branch ═══════════════════════════
if want M; then
  boot M
  hdr "M1  install main, then fast-forward main to $BR in 'GitHub' (what the merge does)"
  fx "git -C /srv/origin.git show main:installer/install.sh > /root/main-install.sh; cd /root && bash /root/main-install.sh < /dev/null > /root/m1.log 2>&1"
  chk "serving on 80"                              'wait_health'
  # A writable copy of the stand-in, so `main` can move without touching the shared one.
  fx "git clone -q --bare /srv/origin.git /srv/merged.git && git -C /srv/merged.git update-ref refs/heads/main \$(git -C /srv/merged.git rev-parse $BR) && git config --system --remove-section url./srv/origin.git && git config --system url./srv/merged.git.insteadOf https://github.com/bravemaster3/geodeploy"

  hdr "M2  the operator runs installer/update.sh — MAIN's copy, which git pull rewrites as it runs"
  fx "cd $G && bash installer/update.sh > /root/m2.log 2>&1; echo \$? > /root/m2.rc"
  chk "update.sh finished (exit $(fx 'cat /root/m2.rc'))" '[ "$(fx "cat /root/m2.rc")" = 0 ]'
  chk "the checkout is $BR"                        'head_is $BR'
  chk "serving on 80"                              'wait_health'
  chk "serving over IPv6"                          '[ "$(v6health)" = 200 ]'
  chk "nginx recreated by up -d: bare :80, no 443" '[ "$(binding)" = "80/tcp=:80" ]'
  down
fi

echo ""
echo "================  $P passed, $F failed  ================"
[ "$F" -eq 0 ] || { printf "Failures:$FAILS\n"; exit 1; }
