#!/usr/bin/env bash
# lib-deploy.sh in isolation — no Docker, no containers, runs in about a second.
#
# Everything here is a PARSING or SELECTION rule, and every one of them decides where an instance
# gets published. A `.env` line this misreads is an instance on the wrong port; a candidate list it
# mis-sanitises is a port test run against the string "abc", which passes as free.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
. ./lib-deploy.sh

P=0; F=0
ok(){ echo "   PASS  $*"; P=$((P+1)); }
bad(){ echo "   FAIL  $*  ($2)"; F=$((F+1)); }
is(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "got '$2', wanted '$3'"; fi; }
hdr(){ echo ""; echo "== $* =="; }

W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
cd "$W" || exit 1

# ── .env reading ──────────────────────────────────────────────────────────────────────────────────
hdr "gd_env_get — the shapes a real .env actually contains"
cat > .env <<'EOF'
# a comment
PLAIN=8080
QUOTED="8081"
SINGLE='8082'
SPACED   =   8083
export EXPORTED=8084
EMPTY=
WITHHASH=value#notacomment
TRAILING=8085
DUPLICATE=first
DUPLICATE=second
LOOKALIKE_PORT=9999
EOF
is "plain"                      "$(gd_env_get PLAIN)"        "8080"
is "double-quoted"              "$(gd_env_get QUOTED)"       "8081"
is "single-quoted"              "$(gd_env_get SINGLE)"       "8082"
is "whitespace around ="        "$(gd_env_get SPACED)"       "8083"
is "export prefix"              "$(gd_env_get EXPORTED)"     "8084"
is "empty value"                "$(gd_env_get EMPTY)"        ""
is "# inside a value is kept"   "$(gd_env_get WITHHASH)"     "value#notacomment"
# LAST wins, because that is what Docker Compose does with a duplicated key — disagreeing with the
# thing that actually creates the container would be the worst possible answer.
is "duplicate key: last wins"   "$(gd_env_get DUPLICATE)"    "second"
is "missing key"                "$(gd_env_get NOPE)"         ""
# A prefix match would return LOOKALIKE_PORT's value for PORT and publish on 9999.
is "no prefix/suffix matching"  "$(gd_env_get PORT)"         ""
is "missing file is not an error" "$(gd_env_get ANY /nope/.env)" ""

hdr "gd_env_set — in place, and only the key asked for"
before_inode="$(stat -c %i .env)"
gd_env_set PLAIN 9090
gd_env_set BRAND_NEW 1234
is "replaced"                   "$(gd_env_get PLAIN)"        "9090"
is "appended"                   "$(gd_env_get BRAND_NEW)"    "1234"
is "neighbours untouched"       "$(gd_env_get QUOTED)"       "8081"
is "comments survive"           "$(grep -c '^# a comment' .env)" "1"
# The whole reason this is not `sed -i`: .env is a single-file bind mount, and a new inode leaves
# every running container reading a file nobody else can see.
is "SAME INODE"                 "$(stat -c %i .env)"         "$before_inode"
gd_env_set PLAIN 7070
is "no duplicate key added"     "$(grep -c '^PLAIN=' .env)"  "1"
: > empty.env
gd_env_set FIRST 1 empty.env
is "works on an empty file"     "$(gd_env_get FIRST empty.env)" "1"
gd_env_set NEW 1 /tmp/gd-created-$$.env
is "creates a missing file"     "$(gd_env_get NEW /tmp/gd-created-$$.env)" "1"
rm -f /tmp/gd-created-$$.env

# ── The publish spec, and its back-compatibility contract ─────────────────────────────────────────
hdr "defaults — an installation predating these keys must not move"
: > bare.env
is "bind"      "$(gd_publish_bind bare.env)" "0.0.0.0"
is "port"      "$(gd_publish_port bare.env)" "80"
is "mode"      "$(gd_publish_mode bare.env)" "dedicated"
is "local url" "$(gd_local_url bare.env)"    "http://127.0.0.1"
printf 'GEODEPLOY_HTTP_BIND=127.0.0.1\nGEODEPLOY_HTTP_PORT=8080\n' > proxy.env
is "loopback bind"    "$(gd_publish_bind proxy.env)" "127.0.0.1"
is "url carries port" "$(gd_local_url proxy.env)"    "http://127.0.0.1:8080"

# ── Candidates ────────────────────────────────────────────────────────────────────────────────────
hdr "gd_candidates — precedence, sanitising, deduplication"
: > .env
is "built-in default" "$(gd_candidates)" "8080 8081 8082 8090 8880 9080 9090 8008 7080 8888"
printf 'GEODEPLOY_PORT_CANDIDATES=9001,9002\n' > .env
is ".env is read"     "$(gd_candidates)" "9001 9002"
is "environment wins" "$(GEODEPLOY_PORT_CANDIDATES=7777 gd_candidates)" "7777"
printf 'GEODEPLOY_PORT_CANDIDATES=8080, 8081 ;8082\n' > .env
is "comma/space/semicolon" "$(gd_candidates)" "8080 8081 8082"
printf 'GEODEPLOY_PORT_CANDIDATES=8080,8080,8081,8080\n' > .env
is "deduplicated, order kept" "$(gd_candidates)" "8080 8081"
# A non-number reaching gd_port_in_use would be tested as a port and reported FREE.
printf 'GEODEPLOY_PORT_CANDIDATES=abc,8080,-1,0,65536,99999,8081\n' > .env
is "junk and out-of-range dropped" "$(gd_candidates)" "8080 8081"
printf 'GEODEPLOY_PORT_CANDIDATES=abc,xyz\n' > .env
is "all-junk yields nothing"       "$(gd_candidates)" ""
printf 'GEODEPLOY_PORT_CANDIDATES=1,2,3,4,5,6,7,8,9,10,11,12\n' > .env
is "a long list is kept whole"     "$(gd_candidates | wc -w)" "12"
: > .env

hdr "gd_valid_port"
for p in 1 80 8080 65535; do gd_valid_port "$p" && ok "accepts $p" || bad "accepts $p" "rejected"; done
for p in 0 65536 -1 abc 80.5 "" " " "8080 "; do
  gd_valid_port "$p" && bad "rejects '$p'" "accepted" || ok "rejects '$p'"
done

# ── Port detection ────────────────────────────────────────────────────────────────────────────────
hdr "gd_port_in_use — both address families"
python3 - <<'PY' &
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 19080)); s.listen(1); time.sleep(20)
PY
V4=$!
python3 - <<'PY' &
import socket, time
s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("::", 19081)); s.listen(1); time.sleep(20)
PY
V6=$!
python3 - <<'PY' &
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", 19082)); s.listen(1); time.sleep(20)
PY
LO=$!
sleep 1.5
gd_port_in_use 19080 && ok "sees an IPv4 listener"        || bad "sees an IPv4 listener" "missed"
# The one a v4-only check misses, and the reason a v6-only web server can silently collide with us.
gd_port_in_use 19081 && ok "sees an IPv6-ONLY listener"   || bad "sees an IPv6-ONLY listener" "missed"
gd_port_in_use 19082 && ok "sees a loopback-only listener" || bad "sees a loopback-only listener" "missed"
gd_port_in_use 19083 && bad "free port reads free" "said in use" || ok "free port reads free"

hdr "gd_free_candidates — filtered against those live listeners"
export GEODEPLOY_PORT_CANDIDATES=19080,19081,19082,19083,19084
free="$(gd_free_candidates)"
is "excludes every busy one"  "$free" "19083 19084"
is "respects the max"         "$(gd_free_candidates 1)" "19083"
is "first free"               "$(gd_first_free_candidate)" "19083"
export GEODEPLOY_PORT_CANDIDATES=19080,19081
is "none free yields nothing" "$(gd_free_candidates)" ""
gd_first_free_candidate >/dev/null 2>&1 && bad "fails when none free" "succeeded" || ok "fails when none free"
unset GEODEPLOY_PORT_CANDIDATES

kill $V4 $V6 $LO 2>/dev/null
wait $V4 $V6 $LO 2>/dev/null

echo ""
echo "================  $P passed, $F failed  ================"
[ "$F" -eq 0 ]
