#!/usr/bin/env bash
# test-hostproxy.sh — does Settings → Deployment → Apply leave somebody else's web server alone?
#
# Every scenario boots a fresh FAKE SHARED MACHINE (hostproxy/Dockerfile): Debian, its own dockerd,
# and an nginx / Apache / Caddy — on the host or in a container — already serving made-up neighbour
# sites. The branch's `services/hostproxy.py` is then driven exactly as the admin router drives it,
# with DOCKER_HOST pointed at that machine, so the privileged helper's `/` is the fake machine's `/`.
# Real binaries do the testing and reloading; nothing is mocked; nothing on THIS machine is touched.
#
# What is asserted, every time, is what a neighbour could notice:
#   * the config trees, byte for byte (sha256 of every file, target of every symlink)
#   * what every neighbour site answers, over the scheme it is served on
#   * the proxy's master PID (same PID = reload, never restart)
#   * a 6 MB download from a neighbour, rate-limited so it is IN FLIGHT across the reload
# and, after Remove, that all of the above is identical to the photograph taken before Apply.
#
#   bash installer/tests/test-hostproxy.sh            # all scenarios, ~10 min
#   bash installer/tests/test-hostproxy.sh N1 C2      # just these
#
# Needs: Docker, the geodeploy/api image (any recent tag — the BRANCH's api/ is mounted over it),
# python3. Builds gdhp-fakehost:{bare,nginx,apache,caddy,mixed} and pulls caddy:2-alpine and
# traefik:v3.1 on first run.
set -uo pipefail

SRC="${SRC:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
W="${GD_HP_HOME:-$HOME/gdhp}"
API_IMAGE="${GD_API_IMAGE:-geodeploy/api:latest}"
NET=gdhp
D=maps.example.test
ONLY=" $* "

P=0; F=0; FAILS=""
ok(){ echo "   PASS  $*"; P=$((P+1)); }
bad(){ echo "   FAIL  $*"; F=$((F+1)); FAILS="$FAILS\n   [$SC] $*"; }
hdr(){ echo ""; echo "== $SC — $* =="; }
want(){ [ "$ONLY" = "  " ] || [[ "$ONLY" == *" $1 "* ]]; }

# ── scaffolding ──────────────────────────────────────────────────────────────────────────────────
prepare() {
  mkdir -p "$W/logs"
  rm -rf "$W/ctx" "$W/api"
  cp -r "$SRC/installer/tests/hostproxy" "$W/ctx"
  cp -r "$SRC/api" "$W/api"
  find "$W/api" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
  sed -i 's/\r$//' "$W/ctx"/* 2>/dev/null
  for t in bare nginx apache caddy mixed; do
    docker build -q --target "$t" -t "gdhp-fakehost:$t" "$W/ctx" >/dev/null || { echo "build $t failed"; exit 2; }
  done
  docker image inspect caddy:2-alpine >/dev/null 2>&1 || docker pull -q caddy:2-alpine >/dev/null
  docker image inspect traefik:v3.1 >/dev/null 2>&1 || docker pull -q traefik:v3.1 >/dev/null
  docker image inspect nginx:alpine >/dev/null 2>&1 || docker pull -q nginx:alpine >/dev/null
  docker network create "$NET" >/dev/null 2>&1 || true
}

# boot SCENARIO FLAVOUR [extra images…] — a fresh machine, with nginx:alpine loaded as the helper image
boot() {
  FH="gdhp-$1"; SC="$1"
  docker rm -f "$FH" >/dev/null 2>&1
  # The driver writes the state file as root; clear it the same way.
  docker run --rm -v "$W/data:/d" nginx:alpine rm -rf "/d/$FH" >/dev/null 2>&1
  mkdir -p "$W/data/$FH"
  docker run -d --privileged --name "$FH" --hostname "$1" --network "$NET" "gdhp-fakehost:$2" >/dev/null
  for _ in $(seq 1 60); do docker exec "$FH" docker info >/dev/null 2>&1 && break; sleep 1; done
  shift 2
  docker save nginx:alpine "$@" | docker exec -i "$FH" docker load >/dev/null
}
down() { docker rm -f "$FH" >/dev/null 2>&1; }
fx() { docker exec "$FH" bash -c "$1"; }        # run on the fake machine
fsh() { docker exec -i "$FH" bash -s; }          # run a heredoc on the fake machine

# drv ACTION [DOMAIN] — call hostproxy; result in $W/out.json, kept under logs/ for the write-up
N=0
drv() {
  N=$((N+1))
  docker run --rm --network "$NET" -e DOCKER_HOST="tcp://$FH:2375" -e GEODEPLOY_DATA_DIR=/work/data \
    -v "$W/data/$FH:/work/data" -v "$W/api:/repo/api:ro" -v "$W/ctx/driver.py:/driver.py:ro" \
    "$API_IMAGE" python /driver.py "$@" 2>"$W/logs/$SC-$N-$1.err" \
    | sed -n 's/^@@JSON@@//p' > "$W/out.json"
  cp "$W/out.json" "$W/logs/$SC-$N-$1.json"
}
# j 'python expression over d' — print it
j() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2]))' "$W/out.json" "$1" 2>/dev/null; }
# jt DESC 'python boolean over d'
jt() {
  local show="$3"
  [ "$show" = steps ] && show="[(s['name'], s['ok'], (s['detail'] or '')[:160]) for s in d.get('steps', [])]"
  [ "$show" = codes ] && show="[b['code'] for b in d.get('blockers', [])]"
  if [ "$(j "bool($2)")" = "True" ]; then ok "$1"; else bad "$1  [got: $(j "$show" 2>/dev/null | head -c 400)]"; fi
}
steps() { j "[(s['name'], s['ok']) for s in d.get('steps', [])]"; }
codes() { j "[b['code'] for b in d.get('blockers', [])]"; }

snap() { fx "/opt/gdhp/snap.sh $1"; }
same() { # DESC A B KIND — KIND is files|http|pid
  if fx "diff -u /root/snap/$2.$4 /root/snap/$3.$4" >"$W/diff.txt" 2>&1; then ok "$1"
  else bad "$1"; sed 's/^/          /' "$W/diff.txt" | head -12; fi
}
only_ours() { # DESC A B — the only differences are GeoDeploy's own file and link
  local extra
  extra=$(fx "diff /root/snap/$2.files /root/snap/$3.files" | grep '^[<>]' | grep -v 'geodeploy\.\(conf\|caddy\)' || true)
  if [ -z "$extra" ]; then ok "$1"; else bad "$1"; echo "$extra" | sed 's/^/          /' | head -8; fi
}
ours_answers() { # DESC [scheme] — the domain reaches the stand-in GeoDeploy, Host intact
  local s="${2:-http}" port=80; [ "$s" = https ] && port=443
  local out; out=$(fx "curl -sk --max-time 5 --resolve $D:$port:127.0.0.1 $s://$D/api/public/whoami")
  if echo "$out" | grep -q '"instance": "fake-instance-0001"' && echo "$out" | grep -q "\"host\": \"$D\""; then
    ok "$1"; else bad "$1  [got: ${out:0:160}]"; fi
}
ours_absent() {
  local out; out=$(fx "curl -s --max-time 5 --resolve $D:80:127.0.0.1 http://$D/api/public/whoami")
  if echo "$out" | grep -q fake-instance; then bad "$1"; else ok "$1"; fi
}
no_leftovers() {
  local c; c=$(fx "docker ps -aq --filter name=geodeploy-hostproxy | wc -l")
  [ "$c" = 0 ] && ok "no helper container left behind" || bad "helper containers left: $c"
  local g; g=$(fx "find /etc /srv -name '*.gd-prev' 2>/dev/null | wc -l")
  [ "$g" = 0 ] && ok "no .gd-prev backup left behind" || bad ".gd-prev files left: $g"
}
# A download from a neighbour, rate-limited so it is still in flight when the proxy reloads.
slow_start() { fx "rm -f /root/slow.out; (curl -s --limit-rate 1M --resolve a.example.test:80:127.0.0.1 -o /root/slow.out -w '%{http_code} %{size_download}' http://a.example.test/big/blob > /root/slow.res 2>&1 &)"; }
slow_check() {
  for _ in $(seq 1 20); do [ -s "$W/slow" ] && break; fx "cat /root/slow.res 2>/dev/null" > "$W/slow"; sleep 1; done
  if grep -q '^200 6000000$' "$W/slow"; then ok "$1"; else bad "$1  [got: $(cat "$W/slow")]"; fi
}

# ── the neighbours ───────────────────────────────────────────────────────────────────────────────
# A Debian nginx the way it is found in the wild: the stock default site, one vhost in sites-enabled,
# one in conf.d, and an HTTPS catch-all (`listen 443 ssl default_server; server_name _`) — the exact
# shape that made the first field Apply look green while visitors got the other site.
nginx_neighbours() {
  fsh <<'EOF'
set -e
mkdir -p /var/www/a /var/www/b /var/www/big
echo SITE-A > /var/www/a/index.html; echo SITE-B > /var/www/b/index.html
head -c 6000000 /dev/urandom > /var/www/big/blob
cat > /etc/nginx/sites-available/site-a <<'C'
server {
    listen 80;
    server_name a.example.test;
    root /var/www/a;
    location /big/ { alias /var/www/big/; }
}
C
ln -s /etc/nginx/sites-available/site-a /etc/nginx/sites-enabled/site-a
cat > /etc/nginx/conf.d/site-b.conf <<'C'
server { listen 80; server_name b.example.test www.b.example.test; root /var/www/b; }
C
openssl req -x509 -nodes -newkey rsa:2048 -days 2 -subj /CN=catchall \
  -keyout /etc/ssl/private/catchall.key -out /etc/ssl/certs/catchall.crt 2>/dev/null
cat > /etc/nginx/sites-available/catchall-443 <<'C'
server {
    listen 443 ssl default_server;
    server_name _;
    ssl_certificate /etc/ssl/certs/catchall.crt;
    ssl_certificate_key /etc/ssl/private/catchall.key;
    return 200 "CATCHALL-443\n";
}
C
ln -s /etc/nginx/sites-available/catchall-443 /etc/nginx/sites-enabled/catchall-443
cat > /root/neighbours <<'C'
http a.example.test /
http b.example.test /
http www.b.example.test /
http unknown.example.test /
https a.example.test /
https whatever.example.test /
C
nginx -t 2>/dev/null && nginx
EOF
}

# ══════════════════════════════════════════════════════════════════════════════════════════════════
prepare

if want N1; then
  boot N1 nginx; nginx_neighbours
  hdr "Debian nginx with three neighbours and an HTTPS catch-all: the whole lifecycle"
  snap before
  drv plan $D
  jt "plan: can apply" "d['can_apply']" "d['blockers']"
  jt "plan: conf.d, no symlink" "d['adapter']['target']=='/etc/nginx/conf.d/geodeploy.conf' and not d['adapter']['symlink']" "d['adapter']"
  jt "plan: warns that https:// reaches the other site until a certificate" "any('https://' in w for w in d['warnings'])" "d['warnings']"
  jt "plan: the probe sees the 443 catch-all" "d['detected']['tls']['has_tls'] and d['detected']['tls']['catchall']" "d['detected']['tls']"
  snap planned
  same "plan wrote nothing" before planned files
  slow_start
  drv apply $D
  jt "apply: write / test / reload / route all pass" "[s['ok'] for s in d['steps']][:5]==[True]*5" "steps"
  jt "apply: names the HTTPS gap as its own failing step" "any(s['name'].startswith('HTTPS does NOT') and not s['ok'] for s in d['steps'])" "steps"
  snap applied
  only_ours "apply: the only change on disk is conf.d/geodeploy.conf" before applied
  same "apply: every neighbour answers exactly as before" before applied http
  same "apply: same master PID — reloaded, not restarted" before applied pid
  slow_check "apply: a download in flight across the reload completed intact"
  ours_answers "apply: $D reaches GeoDeploy with Host intact"
  out=$(fx "curl -s --resolve $D:80:127.0.0.1 http://$D/api/public/whoami")
  echo "$out" | grep -q '"xfp": "http"' && ok "apply: X-Forwarded-Proto is set from \$scheme" || bad "xfp: $out"
  out=$(fx "curl -s -H 'X-Forwarded-Proto: https' --resolve $D:80:127.0.0.1 http://$D/api/public/whoami")
  echo "$out" | grep -q '"xfp": "http"' && ok "apply: a client cannot forge https" || bad "forged xfp: $out"
  out=$(fx "head -c 3000000 /dev/zero > /root/up.bin; curl -s -o /dev/null -w '%{http_code}' --resolve $D:80:127.0.0.1 -X POST --data-binary @/root/up.bin http://$D/upload")
  [ "$out" = 200 ] && ok "apply: a 3 MB upload goes through (nginx's 1 MB default would 413)" || bad "upload got $out"
  fx "grep -q 'managed by GeoDeploy' /etc/nginx/conf.d/geodeploy.conf" && ok "apply: the file carries the marker" || bad "no marker"
  no_leftovers

  drv apply $D
  jt "apply again (same domain): passes, no name-taken refusal" "[s['ok'] for s in d['steps']][:5]==[True]*5" "[d.get('plan',{}).get('blockers'), d['steps']]"
  snap reapplied
  same "apply again: the file is byte-identical, nothing else moved" applied reapplied files
  same "apply again: neighbours unchanged" before reapplied http
  no_leftovers

  D2=tiles.example.test
  drv plan $D2
  jt "change domain: plan says the file will be replaced" "d['can_apply'] and any('will be replaced' in w for w in d['warnings'])" "[d['blockers'], d['warnings']]"
  drv apply $D2
  jt "change domain: applied" "[s['ok'] for s in d['steps']][:5]==[True]*5" "steps"
  ours_absent "change domain: the OLD name no longer reaches GeoDeploy"
  out=$(fx "curl -s --resolve $D2:80:127.0.0.1 http://$D2/api/public/whoami")
  echo "$out" | grep -q fake-instance && ok "change domain: the NEW name does" || bad "new name: $out"
  out=$(fx "curl -s --resolve $D:80:127.0.0.1 http://$D/" | head -c 400)
  echo "$out" | grep -qi 'welcome to nginx' && ok "change domain: the old name falls back to the machine's default site" || bad "old name now: ${out:0:80}"
  snap changed
  only_ours "change domain: still only our own file differs from the original machine" before changed
  same "change domain: neighbours unchanged" before changed http

  drv remove
  jt "remove: every step passes" "d['ok']" "steps"
  snap removed
  same "remove: the config tree is byte-identical to before Apply" before removed files
  same "remove: neighbours answer exactly as before" before removed http
  same "remove: same master PID throughout" before removed pid
  out=$(fx "curl -s --resolve $D2:80:127.0.0.1 http://$D2/api/public/whoami")
  echo "$out" | grep -q fake-instance && bad "remove: domain still served" || ok "remove: the domain is no longer served"
  drv state
  jt "remove: the state file is cleared" "d=={}" "d"
  drv remove
  jt "remove twice: says there is nothing to remove, touches nothing" "d['ok'] and d['steps'][0]['name']=='Nothing to remove'" "steps"
  no_leftovers
  down
fi

if want N2; then
  boot N2 nginx; nginx_neighbours
  hdr "nginx whose on-disk config is ALREADY broken (edited, never reloaded) — R2"
  fx "printf 'server { listen 80; server_name oops.example.test; bogus_directive on; }\n' > /etc/nginx/conf.d/half-finished.conf"
  snap before
  drv plan $D
  jt "plan: refuses with already-broken" "'already-broken' in [b['code'] for b in d['blockers']] and not d['can_apply']" "codes"
  jt "plan: quotes nginx's own error" "any('bogus_directive' in b['detail'] for b in d['blockers'])" "d['blockers']"
  drv apply $D
  jt "apply: refused at 'Checked this machine'" "not d['ok'] and d['steps'][0]['name']=='Checked this machine' and len(d['steps'])==1" "steps"
  snap after
  same "nothing written" before after files
  same "every site still served — the broken file was NOT loaded" before after http
  same "no reload happened (same PID)" before after pid
  no_leftovers
  down
fi

if want N3; then
  boot N3 nginx; nginx_neighbours
  hdr "the domain is already served here — exactly, by wildcard, or as a second name — R5"
  fsh <<'EOF'
cat > /etc/nginx/conf.d/theirs.conf <<'C'
server { listen 80; server_name legacy.example.test maps.example.test; return 200 "THEIR-MAPS\n"; }
server { listen 80; server_name *.corp.example.test; return 200 "CORP-WILDCARD\n"; }
C
echo "http maps.example.test /" >> /root/neighbours
echo "http x.corp.example.test /" >> /root/neighbours
nginx -s reload
EOF
  sleep 1; snap before
  drv apply $D
  jt "exact name (second token of a server_name line): refused" "not d['ok'] and 'name-taken' in [b['code'] for b in d['plan']['blockers']]" "d['plan']['blockers']"
  drv apply x.corp.example.test
  jt "name under a wildcard: refused" "not d['ok'] and 'name-taken' in [b['code'] for b in d['plan']['blockers']]" "d['plan']['blockers']"
  snap after
  same "nothing written" before after files
  same "their maps.example.test still answers THEIR site" before after http
  down
fi

if want N4; then
  boot N4 nginx; nginx_neighbours
  hdr "a geodeploy.conf exists that GeoDeploy did not write — R1"
  fx "printf '# hand-written by the admin\nserver { listen 80; server_name old-geo.example.test; return 200 \"HAND\";}\n' > /etc/nginx/conf.d/geodeploy.conf && nginx -s reload"
  sleep 1; snap before
  drv apply $D
  jt "refused with foreign-file" "not d['ok'] and 'foreign-file' in [b['code'] for b in d['plan']['blockers']]" "d['plan']['blockers']"
  snap after
  same "their geodeploy.conf is byte-identical" before after files
  drv remove
  jt "remove with no record: does nothing" "d['steps'][0]['name']=='Nothing to remove'" "steps"
  snap after2
  same "…and still byte-identical after Remove" before after2 files
  down
fi

if want N5; then
  boot N5 nginx
  hdr "hand-built nginx.conf that includes neither conf.d nor sites-enabled — R6"
  fsh <<'EOF'
cat > /etc/nginx/nginx.conf <<'C'
events {}
http {
    server { listen 80 default_server; server_name only.example.test; return 200 "ONLY-SITE\n"; }
}
C
echo "http only.example.test /" > /root/neighbours
nginx
EOF
  sleep 1; snap before
  drv plan $D
  jt "plan: no-adapter, and says why" "not d['can_apply'] and 'no-adapter' in [b['code'] for b in d['blockers']]" "d['blockers']"
  drv apply $D
  snap after
  same "nothing written (no file dropped where nothing reads it)" before after files
  same "the site is untouched" before after http
  down
fi

if want N6; then
  boot N6 nginx
  hdr "nginx that includes sites-enabled ONLY — the symlink route"
  fsh <<'EOF'
sed -i 's|^\(\s*\)include /etc/nginx/conf.d/\*.conf;|\1# conf.d disabled by the admin|' /etc/nginx/nginx.conf
grep -q 'conf.d disabled' /etc/nginx/nginx.conf || echo "SETUP FAILED: conf.d include still present"
mkdir -p /var/www/a && echo SITE-A > /var/www/a/index.html
printf 'server { listen 80; server_name a.example.test; root /var/www/a; }\n' > /etc/nginx/sites-available/site-a
ln -s /etc/nginx/sites-available/site-a /etc/nginx/sites-enabled/site-a
echo "http a.example.test /" > /root/neighbours
nginx
EOF
  sleep 1; snap before
  drv apply $D
  jt "apply: sites-available + sites-enabled link" "d['ok'] and d['target']=='/etc/nginx/sites-available/geodeploy.conf'" "steps"
  fx "[ -L /etc/nginx/sites-enabled/geodeploy.conf ]" && ok "the symlink exists" || bad "no symlink"
  ours_answers "the domain reaches GeoDeploy"
  snap applied; only_ours "only our file and our link changed" before applied
  drv remove
  jt "remove: ok" "d['ok']" "steps"
  snap removed
  same "remove: file AND link gone, tree byte-identical" before removed files
  same "neighbour unchanged" before removed http
  down
fi

if want N7; then
  boot N7 nginx; nginx_neighbours
  hdr "the new file fails nginx -t (a long name vs a small server_names_hash) — R4 rollback"
  # Real and common: a hand-tuned nginx.conf with a small hash bucket, and a long subdomain.
  fx "sed -i 's/^\(\s*\)# server_names_hash_bucket_size.*/\1server_names_hash_bucket_size 32;/' /etc/nginx/nginx.conf && grep -q 'server_names_hash_bucket_size 32' /etc/nginx/nginx.conf && nginx -s reload"
  sleep 1; snap before
  LONG=a-really-long-geoportal-name-for-the-department.example.test
  drv apply $LONG
  jt "fresh apply of the long name: test fails, file removed" "not d['ok'] and any(s['name']=='nginx accepted it' and not s['ok'] and 'removed' in s['detail'] for s in d['steps'])" "steps"
  jt "…and it says the machine is as it was, with the test passing again" "'passes again' in [s for s in d['steps'] if s['name']=='nginx accepted it'][0]['fix']" "steps"
  snap failed1
  same "fresh rollback: tree byte-identical" before failed1 files
  same "fresh rollback: neighbours unaffected, nothing reloaded" before failed1 pid
  drv apply $D
  jt "a short name applies fine" "d['steps'][4]['ok']" "steps"
  snap good
  drv apply $LONG
  jt "re-apply over a WORKING file with the long name: previous configuration restored" "any(s['name']=='nginx accepted it' and not s['ok'] and 'put back' in s['detail'] for s in d['steps'])" "steps"
  snap failed2
  same "the working file is back byte-for-byte" good failed2 files
  ours_answers "…and the previous domain still reaches GeoDeploy"
  drv state
  jt "state still records the WORKING domain, so Remove still knows the file" "d.get('domain')=='$D'" "d"
  no_leftovers
  drv remove; snap removed
  same "remove after all that: byte-identical to the original" before removed files
  down
fi

if want N8; then
  boot N8 nginx; nginx_neighbours
  hdr "somebody breaks the config AFTER Apply, then Remove is pressed"
  snap before
  drv apply $D
  fx "printf 'server { listen 80; server_name later.example.test; bogus_directive on; }\n' > /etc/nginx/conf.d/later.conf"
  snap broken
  drv remove
  jt "remove: reports the configuration does NOT pass without us" "not d['ok'] and any('still valid' in s['name'] and not s['ok'] for s in d['steps'])" "steps"
  jt "remove: did not reload" "not any(s['name']=='Reloaded' for s in d['steps'])" "steps"
  snap after
  same "remove: neighbours still served (the broken file was never loaded)" before after http
  same "remove: same PID — no reload" before after pid
  fx "[ ! -e /etc/nginx/conf.d/geodeploy.conf ] && [ -f /etc/nginx/conf.d/later.conf ]" \
    && ok "remove: only OUR file went; their broken file is left for them" || bad "files wrong"
  down
fi

if want N9; then
  boot N9 nginx; nginx_neighbours
  hdr "nginx installed and configured, but NOT RUNNING"
  fx "nginx -s quit; sleep 1; rm -f /run/nginx.pid"
  snap before
  drv plan $D
  jt "plan: refuses with not-serving" "not d['can_apply'] and 'not-serving' in [b['code'] for b in d['blockers']]" codes
  drv apply $D
  jt "apply: refused before writing" "not d['ok'] and len(d['steps'])==1" steps
  snap after
  same "nothing written to the idle nginx" before after files
  # Listeners, not pgrep: PID 1 here is `sleep`, so the stopped nginx lingers as <defunct> zombies.
  fx "ss -lnt | grep -q ':80 '" && bad "nginx was STARTED by Apply" || ok "nginx was not started"
  down
fi

if want N10; then
  boot N10 nginx
  hdr "IPv6 disabled on the machine: our block has 'listen [::]:80'"
  fsh <<'EOF'
sysctl -qw net.ipv6.conf.all.disable_ipv6=1; sysctl -qw net.ipv6.conf.default.disable_ipv6=1
sed -i '/listen \[::\]/d' /etc/nginx/sites-available/default
mkdir -p /var/www/a && echo SITE-A > /var/www/a/index.html
printf 'server { listen 80; server_name a.example.test; root /var/www/a; }\n' > /etc/nginx/conf.d/site-a.conf
echo "http a.example.test /" > /root/neighbours
nginx
EOF
  sleep 1; snap before
  drv apply $D
  echo "      apply steps: $(steps)"
  snap after
  same "neighbour unaffected" before after http
  if [ "$(j "d['ok']")" = True ]; then ours_answers "applied and reachable"
  else
    if fx "[ -e /etc/nginx/conf.d/geodeploy.conf ]"; then
      fx "nginx -s quit; sleep 1; nginx 2>&1 | tail -2; pgrep -x nginx >/dev/null" \
        && ok "the file left behind still lets nginx START" \
        || bad "the file left behind STOPS NGINX FROM STARTING at the next restart/reboot"
    else ok "nothing left behind"; fi
  fi
  down
fi

# ── Apache ───────────────────────────────────────────────────────────────────────────────────────
apache_neighbours() {
  fsh <<'EOF'
set -e
mkdir -p /var/www/a /var/www/big
echo SITE-A > /var/www/a/index.html
head -c 6000000 /dev/urandom > /var/www/big/blob
cat > /etc/apache2/sites-available/site-a.conf <<'C'
<VirtualHost *:80>
    ServerName a.example.test
    ServerAlias www.a.example.test
    DocumentRoot /var/www/a
    Alias /big/ /var/www/big/
    <Directory /var/www/big/>
        Require all granted
    </Directory>
</VirtualHost>
C
ln -s ../sites-available/site-a.conf /etc/apache2/sites-enabled/site-a.conf
cat > /root/neighbours <<'C'
http a.example.test /
http www.a.example.test /
http unknown.example.test /
C
apache2ctl configtest 2>/dev/null; apache2ctl start
EOF
  sleep 1
}

if want A1; then
  boot A1 apache; apache_neighbours
  hdr "stock Debian Apache — mod_proxy NOT enabled"
  snap before
  drv plan $D
  jt "plan: refuses with apache-modules, before writing anything" "not d['can_apply'] and 'apache-modules' in [b['code'] for b in d['blockers']]" codes
  jt "plan: gives the exact a2enmod command" "any('a2enmod proxy proxy_http headers' in (b['fix'] or '') for b in d['blockers'])" "d['blockers']"
  slow_start
  drv apply $D
  jt "apply: refused" "not d['ok']" steps
  snap after
  same "tree byte-identical" before after files
  same "neighbours unaffected" before after http
  same "no reload (same PID)" before after pid
  slow_check "download in flight completed"
  no_leftovers
  down
fi

if want A2; then
  boot A2 apache; apache_neighbours
  hdr "Debian Apache with proxy, proxy_http and headers enabled — the full lifecycle"
  fx "a2enmod -q proxy proxy_http headers >/dev/null && apache2ctl graceful"; sleep 1
  snap before
  slow_start
  drv apply $D
  jt "apply: write / test / reload / route all pass" "[s['ok'] for s in d['steps']][:5]==[True]*5" "steps"
  jt "apply: sites-available + sites-enabled link" "d.get('target')=='/etc/apache2/sites-available/geodeploy.conf'" "d.get('target')"
  snap applied
  only_ours "only our file and link changed" before applied
  same "neighbours answer as before" before applied http
  same "graceful: same parent PID" before applied pid
  slow_check "a download in flight across the graceful reload completed intact"
  ours_answers "the domain reaches GeoDeploy with Host intact"
  drv apply $D
  jt "apply again: passes" "[s['ok'] for s in d['steps']][:5]==[True]*5" "steps"
  drv remove
  jt "remove: ok" "d['ok']" "steps"
  snap removed
  same "remove: tree byte-identical" before removed files
  same "remove: neighbours as before" before removed http
  same "remove: same parent PID" before removed pid
  no_leftovers
  down
fi

if want A3; then
  boot A3 apache; apache_neighbours
  hdr "Apache already serves the name as a ServerAlias — R5"
  fx "a2enmod -q proxy proxy_http headers >/dev/null; sed -i 's/ServerAlias www.a.example.test/ServerAlias www.a.example.test $D/' /etc/apache2/sites-available/site-a.conf && apache2ctl graceful"
  sleep 1; snap before
  drv apply $D
  jt "refused with name-taken" "not d['ok'] and 'name-taken' in [b['code'] for b in d['plan']['blockers']]" "d['plan']['blockers']"
  snap after; same "nothing written" before after files
  down
fi

# ── Caddy ────────────────────────────────────────────────────────────────────────────────────────
caddy_start() {
  fsh <<'EOF'
mkdir -p /var/www/a && echo SITE-A > /var/www/a/index.html
echo "http a.example.test /" > /root/neighbours
caddy start --config /etc/caddy/Caddyfile --adapter caddyfile >/var/log/caddy.log 2>&1
EOF
  sleep 2
}

if want C1; then
  boot C1 caddy
  hdr "stock Caddy — the Caddyfile imports no drop-in directory"
  fsh <<'EOF'
cat > /etc/caddy/Caddyfile <<'C'
http://a.example.test {
	root * /var/www/a
	file_server
}
C
EOF
  caddy_start; snap before
  drv plan $D
  jt "plan: no-adapter" "not d['can_apply'] and 'no-adapter' in [b['code'] for b in d['blockers']]" "d['blockers']"
  jt "plan: gives the one import line to add" "any('import /etc/caddy/conf.d/*.caddy' in b['detail'] for b in d['blockers'])" "d['blockers']"
  drv apply $D; snap after
  same "the Caddyfile is untouched, nothing written" before after files
  same "the site is untouched" before after http
  down
fi

caddy_with_import() {
  fsh <<'EOF'
mkdir -p /etc/caddy/conf.d
cat > /etc/caddy/Caddyfile <<'C'
{
	# Offline test machine: an internal CA instead of Let's Encrypt.
	local_certs
	skip_install_trust
}
http://a.example.test {
	root * /var/www/a
	file_server
}
import /etc/caddy/conf.d/*.caddy
C
EOF
}

if want C2; then
  boot C2 caddy; caddy_with_import; caddy_start
  hdr "Caddy that imports conf.d — the full lifecycle"
  snap before
  drv plan $D
  jt "plan: can apply, Caddy adapter, automatic TLS" "d['can_apply'] and d['adapter']['kind']=='caddy' and d['adapter']['tls']=='automatic'" "[d['blockers'], d['adapter']]"
  drv apply $D
  echo "      apply steps: $(steps)"
  jt "apply: every step passes (the loopback route check is skipped for Caddy, and says why)" "d['ok'] and 'Caddy redirects' in d['steps'][-1]['detail']" "steps"
  snap applied
  only_ours "only conf.d/geodeploy.caddy changed" before applied
  same "neighbour answers as before" before applied http
  same "same caddy PID (config reload via admin API)" before applied pid
  ours_answers "https://$D reaches GeoDeploy through Caddy" https
  drv remove
  jt "remove: ok" "d['ok']" "steps"
  snap removed
  same "remove: tree byte-identical" before removed files
  same "remove: neighbour as before" before removed http
  no_leftovers
  down
fi

if want C3; then
  boot C3 caddy; caddy_with_import
  hdr "Caddy already serves the name, written as  http://name, www.name {"
  fsh <<'EOF'
cat >> /etc/caddy/Caddyfile <<'C'
http://maps.example.test, http://www.maps.example.test {
	respond "THEIR-MAPS"
}
C
EOF
  caddy_start
  fx "echo 'http maps.example.test /' >> /root/neighbours"
  snap before
  drv plan $D
  jt "plan: name-taken, despite the scheme and the comma list" "not d['can_apply'] and 'name-taken' in [b['code'] for b in d['blockers']]" codes
  drv apply $D
  snap after
  same "nothing written" before after files
  same "their http://maps.example.test still answers THEIR site" before after http
  drv remove >/dev/null; snap removed
  same "after remove: byte-identical" before removed files
  down
fi

# ── mixed and bare machines ──────────────────────────────────────────────────────────────────────
if want M1; then
  boot M1 mixed
  hdr "Apache holds port 80; nginx is installed but idle (the reverse of the usual leftover)"
  fsh <<'EOF'
rm -f /etc/nginx/sites-enabled/default
a2enmod -q proxy proxy_http headers >/dev/null
mkdir -p /var/www/a && echo SITE-A > /var/www/a/index.html
printf '<VirtualHost *:80>\n ServerName a.example.test\n DocumentRoot /var/www/a\n</VirtualHost>\n' > /etc/apache2/sites-available/site-a.conf
ln -s ../sites-available/site-a.conf /etc/apache2/sites-enabled/site-a.conf
echo "http a.example.test /" > /root/neighbours
apache2ctl start
EOF
  sleep 1; snap before
  drv plan $D
  echo "      plan: adapter=$(j "(d['adapter'] or {}).get('kind')") listeners=$(j "d['detected']['listeners']")"
  jt "plan: picks APACHE, the one actually holding port 80" "(d['adapter'] or {}).get('kind')=='apache'" "d['adapter']"
  drv apply $D
  echo "      apply steps: $(steps)"
  snap after
  same "neighbour unaffected" before after http
  fx "[ -e /etc/nginx/conf.d/geodeploy.conf ]" && bad "a file was written into the IDLE nginx's conf.d" || ok "nothing written to the idle nginx"
  drv remove >/dev/null
  down
fi

if want M2; then
  boot M2 mixed; nginx_neighbours
  hdr "nginx holds port 80; an apache2 package is installed but idle"
  snap before
  drv plan $D
  jt "plan: picks nginx" "(d['adapter'] or {}).get('kind')=='nginx'" "d['adapter']"
  drv apply $D; snap after
  only_ours "only our file in nginx's conf.d; Apache's tree untouched" before after
  drv remove; snap removed; same "remove: byte-identical" before removed files
  down
fi

if want B1; then
  boot B1 bare
  hdr "a machine with no web server at all"
  drv plan $D
  jt "plan: no-adapter, says nothing was found" "not d['can_apply'] and 'No nginx, Caddy or Apache' in d['blockers'][0]['detail']" "d['blockers']"
  drv apply $D
  jt "apply: refused" "not d['ok']" "steps"
  fx "ls /etc/nginx /etc/caddy /etc/apache2 >/dev/null 2>&1" && bad "a proxy config dir appeared" || ok "nothing created"
  down
fi

# ── containerised proxies ────────────────────────────────────────────────────────────────────────
edge_nginx() { # a proxy CONTAINER publishing 80, its conf.d bind-mounted from the host
  fsh <<'EOF'
mkdir -p /srv/edge/conf.d
cat > /srv/edge/conf.d/site-a.conf <<'C'
server { listen 80; server_name a.example.test; return 200 "EDGE-SITE-A\n"; }
C
cat > /srv/edge/conf.d/default.conf <<'C'
server { listen 80 default_server; server_name _; return 200 "EDGE-DEFAULT\n"; }
C
echo "http a.example.test /" > /root/neighbours
echo "http unknown.example.test /" >> /root/neighbours
docker run -d --name edge-nginx -p 80:80 -v /srv/edge/conf.d:/etc/nginx/conf.d nginx:alpine >/dev/null
EOF
  sleep 2
}

if want D1; then
  boot D1 bare; edge_nginx
  hdr "nginx in a CONTAINER (publishes 80, conf.d bind-mounted from the host)"
  snap before
  drv plan $D
  jt "plan: a named refusal for a containerised proxy" "not d['can_apply'] and 'container-proxy' in [b['code'] for b in d['blockers']]" codes
  jt "plan: the fix names the container and the network step" "'docker network connect geodeploy edge-nginx' in d['blockers'][0]['fix']" "d['blockers']"
  drv apply $D
  jt "apply: refused" "not d['ok']" steps
  snap after
  same "nothing written into the container's mount" before after files
  same "neighbours answer as before" before after http
  no_leftovers
  down
fi

if want D2; then
  boot D2 bare; edge_nginx
  hdr "container proxy whose mount already has a geodeploy.conf GeoDeploy did not write — R1"
  fx "printf '# the admin wrote this\nserver { listen 80; server_name old.example.test; return 200 \"HAND\"; }\n' > /srv/edge/conf.d/geodeploy.conf && docker exec edge-nginx nginx -s reload"
  sleep 1; snap before
  drv apply $D
  jt "refused as a containerised proxy" "not d['ok'] and 'container-proxy' in [b['code'] for b in d['plan']['blockers']]" "d['plan']['blockers']"
  snap after
  same "their geodeploy.conf is byte-identical" before after files
  down
fi

if want D3; then
  boot D3 bare; edge_nginx
  hdr "container proxy already serves the name — R5"
  fx "printf 'server { listen 80; server_name $D; return 200 \"THEIR-MAPS\\\\n\"; }\n' > /srv/edge/conf.d/theirs.conf && docker exec edge-nginx nginx -s reload && echo 'http $D /' >> /root/neighbours"
  sleep 1; snap before
  drv apply $D
  jt "refused as a containerised proxy" "not d['ok'] and 'container-proxy' in [b['code'] for b in d['plan']['blockers']]" "d['plan']['blockers']"
  snap after
  same "their $D answers as before" before after http
  if fx "[ -e /srv/edge/conf.d/geodeploy.conf ]"; then bad "a second block for a name the container already serves was written"
  else ok "nothing written"; fi
  down
fi

if want D4; then
  boot D4 bare caddy:2-alpine
  hdr "Caddy in a CONTAINER that imports a mounted conf.d"
  fsh <<'EOF'
mkdir -p /srv/edge/caddy/conf.d
cat > /srv/edge/caddy/Caddyfile <<'C'
{
	local_certs
	skip_install_trust
}
http://a.example.test {
	respond "EDGE-CADDY-A"
}
import /etc/caddy/conf.d/*.caddy
C
echo "http a.example.test /" > /root/neighbours
docker run -d --name edge-caddy -p 80:80 -p 443:443 \
  -v /srv/edge/caddy/Caddyfile:/etc/caddy/Caddyfile -v /srv/edge/caddy/conf.d:/etc/caddy/conf.d caddy:2-alpine >/dev/null
EOF
  sleep 3; snap before
  drv plan $D
  jt "plan: refused" "not d['can_apply']" codes
  drv apply $D
  snap after
  same "nothing written" before after files
  same "neighbour answers as before" before after http
  down
fi

if want T1; then
  boot T1 bare traefik:v3.1
  hdr "Traefik in a container — a named refusal, never a guess"
  fx "docker run -d --name traefik -p 80:80 traefik:v3.1 --entrypoints.web.address=:80 >/dev/null"; sleep 2
  drv plan $D
  jt "plan: traefik blocker" "not d['can_apply'] and d['blockers'][0]['code']=='traefik'" "d['blockers']"
  drv apply $D
  jt "apply: refused" "not d['ok']" "steps"
  c=$(fx "docker ps -aq --filter name=geodeploy-hostproxy | wc -l"); [ "$c" = 0 ] && ok "no helper left" || bad "helper left"
  down
fi

echo ""
echo "================  $P passed, $F failed  ================"
[ "$F" -eq 0 ] || { printf "Failures:$FAILS\n"; exit 1; }
