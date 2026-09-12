#!/usr/bin/env bash
# Shared scaffolding for the installer suites: a throwaway "GeoDeploy" the REAL installer scripts
# run against, plus assertion and decoy helpers.
#
# The stack is a stub — every service but nginx is a sleeping alpine — because these suites are about
# WHERE THE INGRESS BINDS, not about GeoDeploy. What is not stubbed is anything under test: the
# compose file carries the same `ports:` line as the real one, and installer/*.sh are copied in
# unmodified.
set -uo pipefail

SRC="${SRC:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
H="${GD_TEST_HOME:-$HOME/gdh}"
REPO="$H/repo"
KEYS="$H/keys.txt"
O="$H/out.txt"
export GEODEPLOY_DIR="$REPO"

P=0; F=0
ok(){ echo "   PASS  $*"; P=$((P+1)); }
bad(){ echo "   FAIL  $*"; F=$((F+1)); }
chk(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }
hdr(){ echo ""; echo "== $* =="; }
summary(){ echo ""; echo "================  $P passed, $F failed  ================"; [ "$F" -eq 0 ]; }

harness_build() {
  # `sudo` wants a password on many dev machines and there is no terminal to type it into. Docker
  # here is usable unprivileged, and on a real server the operator is root or has sudo — so a
  # passthrough shim keeps the scripts under test completely unmodified.
  mkdir -p "$H/bin"
  printf '#!/bin/sh\nwhile [ "${1#-}" != "$1" ]; do shift; done\nexec "$@"\n' > "$H/bin/sudo"
  chmod +x "$H/bin/sudo"
  export PATH="$H/bin:$PATH"

  rm -rf "$REPO"; mkdir -p "$REPO/installer" "$REPO/nginx" "$REPO/data/temp"
  cp "$SRC/installer"/*.sh "$REPO/installer/"
  cp "$SRC/.env.example" "$REPO/.env.example"
  # A checkout on a Windows filesystem has CRLF in the working tree while git stores LF. Strip it, or
  # bash reads `set -euo pipefail\r`. A harness concern only — nothing that ships is affected.
  sed -i 's/\r$//' "$REPO/installer"/*.sh "$REPO/.env.example" 2>/dev/null || true

  cat > "$REPO/docker-compose.yml" <<'YML'
services:
  geodeploy-api: { image: alpine:3, command: ["sleep","infinity"], networks: [geodeploy] }
  geodeploy-ui:  { image: alpine:3, command: ["sleep","infinity"], networks: [geodeploy] }
  celery:        { image: alpine:3, command: ["sleep","infinity"], networks: [geodeploy] }
  martin:        { image: alpine:3, command: ["sleep","infinity"], networks: [geodeploy] }
  titiler:       { image: alpine:3, command: ["sleep","infinity"], networks: [geodeploy] }
  redis:         { image: alpine:3, command: ["sleep","infinity"], networks: [geodeploy] }
  nginx:
    image: nginx:alpine
    restart: unless-stopped
    ports:
      - "${GEODEPLOY_HTTP_BIND:-0.0.0.0}:${GEODEPLOY_HTTP_PORT:-80}:80"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    networks: [geodeploy]
networks:
  geodeploy: { name: geodeploy, external: true }
YML

  cat > "$REPO/nginx/nginx.conf" <<'CONF'
events {}
http {
  server {
    listen 80;
    location /health { return 200 "ok\n"; }
    location / { return 200 "GEODEPLOY-STUB\n"; }
  }
}
CONF

  # install.sh resolves a version against a git checkout; give it one, on `main`, resolvable offline.
  ( cd "$REPO" && git init -q . && git add -A \
    && git -c user.email=t@t -c user.name=t commit -qm init && git branch -M main ) >/dev/null 2>&1
  docker network create geodeploy >/dev/null 2>&1 || true
}

dc(){ ( cd "$REPO" && docker compose "$@" ); }
ev(){ grep -E "^$1=" "$REPO/.env" 2>/dev/null | tail -1 | cut -d= -f2-; }
binding(){ docker inspect -f '{{range $p,$b := .HostConfig.PortBindings}}{{range $b}}{{.HostIp}}:{{.HostPort}}{{end}}{{end}}' "$(dc ps -q nginx 2>/dev/null | head -1)" 2>/dev/null; }
reset_install(){ dc down --remove-orphans >/dev/null 2>&1; rm -f "$REPO/.env" "$REPO/data/temp/deploy-hints.json"; }
# Colour codes break `grep -w` on a number (the character before it becomes the `m` of an escape).
strip_o(){ sed -i $'s/\x1b\\[[0-9;]*m//g' "$O" 2>/dev/null || true; }

run_install(){ ( cd "$REPO" && bash installer/install.sh "$@" ) >"$O" 2>&1; strip_o; }
run_setport(){ ( cd "$REPO" && bash installer/set-port.sh "$@" ) >"$O" 2>&1; strip_o; }
run_preflight(){ ( cd "$REPO" && bash installer/preflight.sh "$@" ) >"$O" 2>&1; strip_o; }
# A piped install: bash's stdin IS the script, which is the case a bare `read` would corrupt.
run_piped(){ ( cd "$REPO" && cat installer/install.sh | bash ) >"$O" 2>&1; strip_o; }
# THE REDIRECT GOES ON `script`, which forwards its own stdin into the pty. On the inner command the
# prompt reads /dev/tty, nothing writes there, and it hangs forever.
run_tty(){ printf '%s' "$1" > "$KEYS"
  ( cd "$REPO" && script -qec "bash installer/install.sh ${2:-}" /dev/null < "$KEYS" ) >"$O" 2>&1; strip_o; }

DECOYS=()
decoy(){ docker run -d --rm -p "0.0.0.0:$1:80" --name "gddecoy$1" \
    -e P="${2:-SITE-$1}" nginx:alpine sh -c 'echo "$P" > /usr/share/nginx/html/index.html; nginx -g "daemon off;"' \
    >/dev/null 2>&1; DECOYS+=("gddecoy$1"); sleep 0.7; }
kill_decoys(){ for d in "${DECOYS[@]:-}"; do [ -n "$d" ] && docker rm -f "$d" >/dev/null 2>&1; done; DECOYS=(); }
