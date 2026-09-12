#!/usr/bin/env bash
# The reverse-proxy configuration the dashboard hands out: is it VALID, does it WORK, and does a
# naive config fail in exactly the ways the panel warns about?
#
# Two halves, and the second is the one that earns the first. Anyone can generate a config that
# looks right; these run it — `nginx -t` and `caddy validate` on the real binaries, then a live
# proxy in front of a stub GeoDeploy — and then deliberately break it, because the value of each
# directive we emit is only demonstrated by what happens without it.
#
#   SRC=/path/to/repo bash installer/tests/test-proxy.sh
set -uo pipefail
SRC="${SRC:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# Under $HOME, not /tmp: a bind mount whose SOURCE FILE does not exist yet makes Docker create it as
# a root-owned DIRECTORY, and every later write to the work directory then fails with a permission
# error that looks nothing like its cause. Mounting the whole directory (below) is the actual fix;
# keeping it out of /tmp just makes the wreckage easy to clear.
W="$(mktemp -d "${TMPDIR:-$HOME}/gd-proxytest.XXXXXX")"
DOMAIN=maps.example.org
P=0; F=0
ok(){ echo "   PASS  $*"; P=$((P+1)); }
bad(){ echo "   FAIL  $*"; F=$((F+1)); }
chk(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }
hdr(){ echo ""; echo "== $* =="; }
cleanup(){ docker rm -f gdupstream gdouter >/dev/null 2>&1; rm -rf "$W"; }
trap cleanup EXIT
# Only the CONTAINERS at startup — calling `cleanup` here would delete the work directory that was
# just created, and every later write would then land in a path Docker had recreated as root.
docker rm -f gdupstream gdouter >/dev/null 2>&1

# ── Generate every flavour with the REAL service ──────────────────────────────────────────────────
# Imported without the FastAPI app: config needs pydantic-settings, and proxy_config touches none of
# it. Keeping the import light is what lets this run anywhere Python does.
gen() { # flavour port -> config on stdout
  python3 - "$SRC" "$DOMAIN" "$1" "$2" <<'PY'
import sys, types, os, importlib
src, domain, flavor, port = sys.argv[1:5]
sys.path.insert(0, os.path.join(src, "api"))
pkg = types.ModuleType("geodeploy"); pkg.__path__ = [os.path.join(src, "api", "geodeploy")]
sys.modules["geodeploy"] = pkg
svcs = types.ModuleType("geodeploy.services"); svcs.__path__ = [os.path.join(src, "api", "geodeploy", "services")]
sys.modules["geodeploy.services"] = svcs
cfg = types.ModuleType("geodeploy.config")
cfg.get_settings = lambda: types.SimpleNamespace(data_dir="/data", secret_key="x")
sys.modules["geodeploy.config"] = cfg
env = types.ModuleType("geodeploy.services.envfile"); env.read_all = lambda path=None: {}
sys.modules["geodeploy.services.envfile"] = env
dep = importlib.import_module("geodeploy.services.deployment")
sys.stdout.write(dep.proxy_config(domain, flavor, {"mode": "behind-proxy", "bind": "127.0.0.1", "port": port})["config"])
PY
}

hdr "the generator produces something for every flavour it advertises"
for f in nginx caddy apache traefik; do
  gen "$f" 8080 > "$W/$f.conf"
  chk "$f rendered"           '[ -s "$W/'"$f"'.conf" ]'
  chk "$f names the domain"   'grep -q "'"$DOMAIN"'" "$W/'"$f"'.conf"'
done

hdr "the nginx block is VALID nginx (nginx -t on the real binary)"
{ echo 'events {}'; echo 'http {'; cat "$W/nginx.conf"; echo '}'; } > "$W/full-nginx.conf"
# The DIRECTORY is mounted, never the individual file — see the note on $W above.
docker run --rm -v "$W:/w:ro" nginx:alpine nginx -t -c /w/full-nginx.conf >"$W/nginxt.txt" 2>&1
chk "nginx -t accepts it"     'grep -q "syntax is ok" "$W/nginxt.txt"'
chk "nginx -t is satisfied"   'grep -q "test is successful" "$W/nginxt.txt"'

hdr "the Caddyfile is VALID Caddy (caddy validate on the real binary)"
docker run --rm -v "$W:/w:ro" caddy:alpine \
  caddy validate --config /w/caddy.conf --adapter caddyfile >"$W/caddyv.txt" 2>&1
CADDY_RC=$?
chk "caddy validate accepts it" '[ "$CADDY_RC" -eq 0 ]'

hdr "the Traefik snippet is valid YAML"
docker run --rm -v "$W:/w:ro" python:3.12-slim sh -c \
  "pip install -q pyyaml >/dev/null 2>&1; python -c \"import yaml,sys; d=yaml.safe_load(open('/w/traefik.conf')); sys.exit(0 if 'services' in d else 1)\"" >/dev/null 2>&1
YAML_RC=$?
chk "parses, and declares services" '[ "$YAML_RC" -eq 0 ]'

hdr "every directive whose absence is a real failure is present"
chk "Host"                    'grep -q "proxy_set_header Host  *\$host;" "$W/nginx.conf"'
chk "X-Forwarded-Proto"       'grep -q "X-Forwarded-Proto \$scheme" "$W/nginx.conf"'
chk "X-Forwarded-For"         'grep -q "X-Forwarded-For" "$W/nginx.conf"'
chk "client_max_body_size"    'grep -q "client_max_body_size 11G;" "$W/nginx.conf"'
chk "request buffering off"   'grep -q "proxy_request_buffering off;" "$W/nginx.conf"'
chk "long timeouts"           'grep -q "proxy_read_timeout 600s;" "$W/nginx.conf"'
chk "websocket upgrade"       'grep -q "Upgrade" "$W/nginx.conf"'
# $scheme, never $http_x_forwarded_proto: an edge that echoes the client's header lets anyone claim
# https and be issued a Secure cookie over a plaintext connection.
chk "the edge sets the scheme, not the client" '! grep -q "http_x_forwarded_proto" "$W/nginx.conf"'
chk "the upstream follows the port" 'gen nginx 9099 | grep -q "proxy_pass http://127.0.0.1:9099;"'

# ── Live: a real proxy in front of a stub GeoDeploy ───────────────────────────────────────────────
cat > "$W/upstream.conf" <<'CONF'
events {}
http {
  server {
    listen 80;
    client_max_body_size 11G;
    location /health { return 200 "ok\n"; }
    location /whoami {
      default_type application/json;
      return 200 '{"host":"$http_host","proto":"$http_x_forwarded_proto","xff":"$http_x_forwarded_for"}';
    }
    location /upload { return 200 "received $content_length\n"; }
  }
}
CONF
docker run -d --name gdupstream -p 127.0.0.1:8080:80 \
  -v "$W:/w:ro" nginx:alpine nginx -c /w/upstream.conf -g 'daemon off;' >/dev/null
sleep 1
chk "stub upstream is up on 127.0.0.1:8080" '[ "$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8080/health)" = 200 ]'

# --network host, and it MATTERS: GeoDeploy is bound to 127.0.0.1, so a proxy in its own network
# namespace cannot reach it at all — that isolation is the point of the mode, and a bridged
# container would have tested a setup nobody runs. Only the listen port is rewritten.
outer() {
  docker rm -f gdouter >/dev/null 2>&1
  { echo 'events {}'; echo 'http {'
    sed -e 's/listen 80;/listen 18080;/' -e 's/listen \[::\]:80;/listen [::]:18080;/' "$1"
    echo '}'; } > "$W/outer.conf"
  docker run -d --name gdouter --network host -v "$W:/w:ro" nginx:alpine     nginx -c /w/outer.conf -g 'daemon off;' >/dev/null
  sleep 1.5
}

hdr "through the GENERATED config, everything GeoDeploy needs survives the hop"
outer "$W/nginx.conf"
chk "it answers"              '[ "$(curl -s -o /dev/null -w %{http_code} -H "Host: '$DOMAIN'" http://127.0.0.1:18080/health)" = 200 ]'
SEEN=$(curl -s -H "Host: $DOMAIN" http://127.0.0.1:18080/whoami)
echo "      upstream saw: $SEEN"
chk "hostname preserved"      'echo "$SEEN" | grep -q "\"host\":\"'$DOMAIN'\""'
chk "scheme forwarded"        'echo "$SEEN" | grep -q "\"proto\":\"http\""'
chk "client address forwarded" 'echo "$SEEN" | grep -q "\"xff\":\"1"'
SEEN=$(curl -s -H "Host: $DOMAIN" -H "X-Forwarded-Proto: https" http://127.0.0.1:18080/whoami)
chk "a client cannot forge the scheme" 'echo "$SEEN" | grep -q "\"proto\":\"http\""'
dd if=/dev/zero of="$W/blob.bin" bs=1024 count=2048 >/dev/null 2>&1
chk "a 2 MB upload is accepted" '[ "$(curl -s -o /dev/null -w %{http_code} -H "Host: '$DOMAIN'" --data-binary @"$W/blob.bin" http://127.0.0.1:18080/upload)" = 200 ]'

hdr "through a NAIVE config, it fails in exactly the documented ways"
cat > "$W/naive.conf" <<CONF
server {
  listen 80;
  server_name $DOMAIN;
  location / { proxy_pass http://127.0.0.1:8080; }
}
CONF
outer "$W/naive.conf"
SEEN=$(curl -s -H "Host: $DOMAIN" http://127.0.0.1:18080/whoami)
echo "      upstream saw: $SEEN"
chk "hostname LOST — every generated link would break" 'echo "$SEEN" | grep -q "\"host\":\"127.0.0.1:8080\""'
chk "X-Forwarded-Proto MISSING — cookies lose Secure"  'echo "$SEEN" | grep -q "\"proto\":\"\""'
chk "a 2 MB upload is REJECTED with 413"               '[ "$(curl -s -o /dev/null -w %{http_code} -H "Host: '$DOMAIN'" --data-binary @"$W/blob.bin" http://127.0.0.1:18080/upload)" = 413 ]'

echo ""
echo "================  $P passed, $F failed  ================"
[ "$F" -eq 0 ]
