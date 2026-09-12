#!/usr/bin/env bash
# Shared helpers for "where is GeoDeploy published on this machine" — sourced by preflight.sh,
# install.sh and set-port.sh. No side effects on source: every function is pure until called.
#
# THE ONE IDEA THIS FILE EXISTS TO PROTECT: the host port is chosen ONCE, with the operator's
# answer, and then it is a FACT about the installation — the same way the domain in someone's DNS
# and the `proxy_pass` in their reverse proxy are facts. Nothing here ever re-picks it. A port that
# moves on its own silently orphans whatever is pointing at it, and the operator has no way to tell
# a moved port from a broken install.

# ── .env access ───────────────────────────────────────────────────────────────────────────────────

gd_env_get() { # KEY [FILE] → the value, unquoted, or empty
  local key="$1" file="${2:-.env}" line
  [ -f "$file" ] || return 0
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$file" 2>/dev/null | tail -1)" || true
  [ -n "$line" ] || return 0
  line="${line#*=}"
  # Strip one matching pair of surrounding quotes, then surrounding whitespace.
  line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  case "$line" in
    \"*\") line="${line#\"}"; line="${line%\"}" ;;
    \'*\') line="${line#\'}"; line="${line%\'}" ;;
  esac
  printf '%s' "$line"
}

gd_env_set() { # KEY VALUE [FILE] — add or replace, IN PLACE
  # `sed -i` is WRONG here and it is not obvious why. GNU sed writes a temp file and renames it over
  # the original, which gives the file a NEW INODE — and `.env` is a SINGLE-FILE bind mount into the
  # api and celery containers (docker-compose.yml). A running container stays bound to the old inode
  # and goes on reading a file nobody else can see. That is the exact failure services/envfile.py
  # documents at length, and the reason it writes in place too.
  #
  # `> "$file"` truncates and rewrites the SAME inode, so every mount stays attached. The whole file
  # is read into memory first because the redirection truncates it before awk could read it.
  local key="$1" val="$2" file="${3:-.env}" body
  [ -f "$file" ] || : > "$file"
  body="$(awk -v k="$key" -v v="$val" '
    BEGIN { done = 0 }
    $0 ~ "^[[:space:]]*(export[[:space:]]+)?" k "[[:space:]]*=" { if (!done) { print k "=" v; done = 1 } ; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$file")" || return 1
  printf '%s\n' "$body" > "$file"
}

# ── Ports ─────────────────────────────────────────────────────────────────────────────────────────

# Is anything listening on this TCP port, on ANY address family? IPv6 matters and is easy to miss: a
# listener on `:::80` (v6 with v4-mapped addresses, the default for many servers) blocks a v4 bind on
# 0.0.0.0:80 just as surely as a v4 listener does, and a v4-only check calls the port free.
gd_port_in_use() { # PORT → 0 if in use
  local port="$1"
  # `-lnt` rather than `-lntH`: -H (suppress the header) is not in older iproute2. The header line's
  # last colon-field is "Port", which never matches, so leaving it in costs nothing.
  if command -v ss >/dev/null 2>&1; then
    ss -lnt 2>/dev/null | awk -v p=":$port" '{ n=split($4,a,":"); if (":" a[n] == p) found=1 } END { exit !found }' && return 0
    return 1
  fi
  if command -v netstat >/dev/null 2>&1; then
    netstat -lnt 2>/dev/null | awk -v p=":$port" 'NR>2 { n=split($4,a,":"); if (":" a[n] == p) found=1 } END { exit !found }' && return 0
    return 1
  fi
  # Last resort: try to BIND it. This is the most accurate test there is — it answers the question we
  # actually care about — but it needs python3, and on a privileged port it needs root, so it is the
  # fallback rather than the default.
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$port" <<'PY' && return 1 || return 0
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("0.0.0.0", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
  fi
  return 1   # cannot tell — treat as free rather than blocking an install on a missing tool
}

# Who holds it, in words an operator can act on. Needs root for the process name; without it we still
# say "in use", which is the part the decision turns on.
gd_port_holder() { # PORT → "nginx (pid 1234)" | "a Docker container" | ""
  local port="$1" out=""
  if command -v ss >/dev/null 2>&1; then
    # ss renders the owner as:  users:(("nginx",pid=1234,fd=6))
    out="$( { ss -lntp 2>/dev/null; sudo -n ss -lntp 2>/dev/null; } \
            | awk -v p=":$port" '{ n=split($4,a,":"); if (":" a[n] == p) print }' \
            | grep -oE 'users:\(\("[^"]+",pid=[0-9]+' | head -1 \
            | sed -e 's/users:(("//' -e 's/",pid=/ (pid /' )" || true
    [ -n "$out" ] && { printf '%s)' "$out"; return 0; }
  fi
  # docker-proxy holds the socket for a published container port; name the container instead, which
  # is what the operator needs to know.
  if command -v docker >/dev/null 2>&1; then
    out="$( { docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null || sudo -n docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null; } \
            | awk -F'\t' -v p=":$port->" '$2 ~ p { print $1; exit }')" || true
    [ -n "$out" ] && printf 'the Docker container %s' "$out" && return 0
  fi
  return 0
}

# TWO SETTINGS, and the difference between them is the whole design:
#
#   GEODEPLOY_PORT_CANDIDATES   a SUGGESTION list, read only while a port is being CHOSEN
#   GEODEPLOY_HTTP_PORT         the CHOSEN port — authoritative from then on, and never re-derived
#
# Keeping them apart is what stops the port drifting. If the installer re-scanned the candidates on
# every run, an install that landed on 8081 because 8080 was busy would silently move BACK to 8080
# the day that service was retired — and the operator's proxy_pass, DNS record and bookmarks would
# all still point at 8081.
#
# The defaults are deliberately boring, high, and unlikely to collide with something the operator
# cares about. 8080 first because it is the one people expect and recognise; the rest spread across
# the ranges different stacks favour, so a machine that is busy in one neighbourhood is usually free
# in another.
GD_DEFAULT_CANDIDATES="8080 8081 8082 8090 8880 9080 9090 8008 7080 8888"

gd_candidates() { # → the candidate list
  # Precedence: the environment (an explicit answer for THIS run) → .env (the operator's standing
  # preference for this installation) → the built-in list. The .env step matters: `.env.example`
  # advertises this key, and reading only the environment would have made that line decorative.
  local raw="${GEODEPLOY_PORT_CANDIDATES:-}"
  [ -n "$raw" ] || raw="$(gd_env_get GEODEPLOY_PORT_CANDIDATES "${1:-.env}")"
  [ -n "$raw" ] || raw="$GD_DEFAULT_CANDIDATES"
  # Comma or space separated, deduplicated in order, non-numbers dropped rather than carried into a
  # port test that would silently treat them as free.
  printf '%s' "$raw" | tr ',;' '  ' | tr -s ' ' | awk '{
    for (i = 1; i <= NF; i++)
      if ($i ~ /^[0-9]+$/ && $i+0 >= 1 && $i+0 <= 65535 && !seen[$i]++)
        printf "%s%s", (n++ ? " " : ""), $i
  }'
}

gd_free_candidates() { # [max] → every candidate that is free right now, in order
  local max="${1:-0}" n=0 p out=""
  for p in $(gd_candidates); do
    gd_port_in_use "$p" && continue
    out="${out:+$out }$p"
    n=$((n+1))
    if [ "$max" -gt 0 ] && [ "$n" -ge "$max" ]; then break; fi
  done
  printf '%s' "$out"
}

gd_first_free_candidate() { # → the first free candidate, or empty
  local first
  first="$(gd_free_candidates 1)"
  [ -n "$first" ] || return 1
  printf '%s' "$first"
}

gd_valid_port() { # PORT → 0 if a usable TCP port number
  case "$1" in ''|*[!0-9]*) return 1 ;; esac
  [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
}

# ── The publish spec ──────────────────────────────────────────────────────────────────────────────

# What docker-compose.yml will resolve `ports:` to, given a .env. Kept in ONE place so preflight,
# set-port and the drift check in the updaters cannot disagree with Compose about what is deployed.
# The defaults MUST match the `:-` defaults in docker-compose.yml — an install whose .env predates
# these keys is on 0.0.0.0:80, exactly as it always was.
gd_publish_bind() { local v; v="$(gd_env_get GEODEPLOY_HTTP_BIND "${1:-.env}")"; printf '%s' "${v:-0.0.0.0}"; }
gd_publish_port() { local v; v="$(gd_env_get GEODEPLOY_HTTP_PORT "${1:-.env}")"; printf '%s' "${v:-80}"; }
gd_publish_mode() { local v; v="$(gd_env_get GEODEPLOY_DEPLOY_MODE "${1:-.env}")"; printf '%s' "${v:-dedicated}"; }

# The URL to health-check and to print. ALWAYS via 127.0.0.1: in behind-proxy mode the bind address
# is 127.0.0.1 and nothing else can reach it, and in dedicated mode 127.0.0.1 works too. Using the
# configured BIND address here would break the loopback check on a bind of 0.0.0.0 only in exotic
# setups, but 127.0.0.1 is correct in every one of them.
gd_local_url() { # [FILE] → http://127.0.0.1[:port]
  local port; port="$(gd_publish_port "${1:-.env}")"
  if [ "$port" = 80 ]; then printf 'http://127.0.0.1'; else printf 'http://127.0.0.1:%s' "$port"; fi
}

# Is the ingress actually answering where it is supposed to?
#
# WHY THIS IS NOT THE SAME QUESTION AS "is nginx running" (measured, 2026-09-12): if the host port is
# occupied when the container starts, Docker fails the bind — and a later `docker compose up -d` or
# `docker compose restart`, once the port is free, brings the container back to `running` with the
# correct PortBindings in its config AND NOTHING LISTENING ON THE HOST. `docker compose ps` says Up,
# `docker inspect` says 127.0.0.1:8090, `ss` says nothing, every request is refused. Only
# `--force-recreate` re-establishes the mapping.
#
# So the reachable check has to be an actual request, never an inspection of Docker's own state.
gd_ingress_reachable() { # [FILE]
  curl -fsS --max-time 3 "$(gd_local_url "${1:-.env}")/health" >/dev/null 2>&1
}

# The address to SHOW someone. Behind a proxy the machine's public IP is meaningless (nothing is
# listening on it), so we never print it in that mode.
gd_public_ip() {
  local ip=""
  # The source address of the default route — the interface that actually reaches the internet.
  # `hostname -I | awk '{print $1}'` returns the FIRST address, which on a multi-homed box (almost
  # every cloud VM with a private network) is frequently the private one.
  if command -v ip >/dev/null 2>&1; then
    ip="$(ip route get 1.1.1.1 2>/dev/null | grep -oE 'src [0-9.]+' | awk '{print $2}' | head -1)" || true
  fi
  [ -z "$ip" ] && command -v hostname >/dev/null 2>&1 && ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  printf '%s' "$ip"
}
