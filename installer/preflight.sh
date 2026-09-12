#!/usr/bin/env bash
# What is already on this machine, and what would GeoDeploy collide with?
#
#   bash installer/preflight.sh            # human-readable
#   bash installer/preflight.sh --json     # for install.sh, and later the dashboard
#
# WRITES NOTHING and STARTS NOTHING. It is safe to run on a production box at any time, and
# install.sh sources it so a conflict is reported BEFORE the first file is created rather than
# discovered halfway through by a Docker error.
#
# It reports EVERY problem it finds, not the first. An operator who has to run an installer six
# times to learn six facts about their own server has been failed by the installer.
set -uo pipefail

_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-deploy.sh
. "$_here/lib-deploy.sh"

GD_JSON=0
# NOT `[ … ] && GD_JSON=1`: that returns 1 when the test fails, and install.sh sources this file
# under `set -e`, where a non-zero status from the last command aborts the installer.
if [ "${1:-}" = "--json" ]; then GD_JSON=1; fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; DIM='\033[2m'; NC='\033[0m'

# Findings accumulate here; nothing is printed until everything has been checked.
GD_ROWS=()          # "state|label|detail"   state ∈ ok warn bad
GD_BLOCKERS=0       # things that WILL break an install
row() { GD_ROWS+=("$1|$2|$3"); [ "$1" = bad ] && GD_BLOCKERS=$((GD_BLOCKERS+1)); return 0; }

# Machine-readable facts, for --json and for install.sh to read back.
GD_PORT80_FREE=1; GD_PORT443_FREE=1; GD_PORT80_HOLDER=""; GD_PORT443_HOLDER=""
GD_WEBSERVER=""; GD_FREE_CANDIDATE=""; GD_DOCKER_OK=0; GD_DOCKER_SUDO=0
GD_NET_FOREIGN=0; GD_NAME_COLLISIONS=""; GD_SWAP_MB=0

# ── Docker ────────────────────────────────────────────────────────────────────────────────────────

_docker() { if [ "$GD_DOCKER_SUDO" = 1 ]; then sudo docker "$@"; else docker "$@"; fi; }

check_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    row warn "Docker" "not installed — the installer will install it (needs root)"
    return
  fi
  if docker info >/dev/null 2>&1; then
    GD_DOCKER_OK=1; GD_DOCKER_SUDO=0
    row ok "Docker" "$(docker --version 2>/dev/null | sed 's/,.*//'), usable without sudo"
  elif sudo -n docker info >/dev/null 2>&1 || sudo docker info >/dev/null 2>&1; then
    GD_DOCKER_OK=1; GD_DOCKER_SUDO=1
    row ok "Docker" "$(sudo docker --version 2>/dev/null | sed 's/,.*//'), usable with sudo"
  else
    row bad "Docker" "installed but not usable by $(id -un) — add yourself to the 'docker' group, or run with sudo"
    return
  fi
  if ! _docker compose version >/dev/null 2>&1; then
    row bad "Docker Compose" "the v2 plugin is missing — install docker-compose-plugin"
  fi
}

# ── Ports ─────────────────────────────────────────────────────────────────────────────────────────

# Is this port held by OUR OWN nginx? On a re-run of the installer that is the normal, healthy state,
# and reporting it as a conflict would be both wrong and alarming.
_gd_port_is_ours() { # PORT
  [ "$GD_DOCKER_OK" = 1 ] || return 1
  _docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null \
    | awk -F'\t' -v p=":$1->" '$1 ~ /geodeploy/ && $1 ~ /nginx/ && $2 ~ p { found=1 } END { exit !found }'
}

check_ports() {
  local h p
  for p in 80 443; do
    if ! gd_port_in_use "$p"; then
      row ok "Port $p" "free"
      continue
    fi
    [ "$p" = 80 ] && GD_PORT80_FREE=0 || GD_PORT443_FREE=0
    if _gd_port_is_ours "$p"; then
      row ok "Port $p" "in use by this GeoDeploy"
      continue
    fi
    h="$(gd_port_holder "$p")"
    [ "$p" = 80 ] && GD_PORT80_HOLDER="$h" || GD_PORT443_HOLDER="$h"
    row warn "Port $p" "in use${h:+ — $h}"
  done

  # A web server that is INSTALLED but not currently listening still matters: it will very likely
  # want 80/443 back the next time it starts, and taking them now turns into a conflict at reboot.
  local s
  for s in nginx caddy apache2 httpd haproxy traefik; do
    if command -v "$s" >/dev/null 2>&1; then
      GD_WEBSERVER="${GD_WEBSERVER:+$GD_WEBSERVER, }$s"
    fi
  done
  [ -n "$GD_WEBSERVER" ] && row warn "Web server present" "$GD_WEBSERVER — GeoDeploy will not touch it"

  GD_FREE_CANDIDATE="$(gd_first_free_candidate || true)"
  if [ -n "$GD_FREE_CANDIDATE" ]; then
    row ok "A free port for GeoDeploy" "$GD_FREE_CANDIDATE"
  else
    row bad "A free port for GeoDeploy" "every candidate is taken ($(gd_candidates)) — free one, or set GEODEPLOY_HTTP_PORT to a port you know is free"
  fi
}

# The port this installation is ALREADY configured for, if there is one. Checked separately from the
# candidate scan because the two answer different questions: the candidates are for a first install,
# and this is "can the install that already exists still come up?". Never re-picks — see lib-deploy.
check_configured_port() {
  local env_file="$1" port bind holder
  [ -f "$env_file" ] || return 0
  port="$(gd_publish_port "$env_file")"; bind="$(gd_publish_bind "$env_file")"
  if gd_port_in_use "$port"; then
    # Ours, almost certainly — a running GeoDeploy holds its own port, and that is not a conflict.
    if _docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null | grep -q "nginx.*:${port}->"; then
      row ok "Configured port ($bind:$port)" "held by this GeoDeploy — nothing to do"
    else
      holder="$(gd_port_holder "$port")"
      row bad "Configured port ($bind:$port)" "in use by something else${holder:+ — $holder}. GeoDeploy will NOT move itself, because your reverse proxy, DNS or bookmarks point here. Free that port, or run: sudo bash installer/set-port.sh <other-port>"
    fi
  else
    row ok "Configured port ($bind:$port)" "free"
  fi
}

# nginx says it is running and Docker says it publishes the right port — and the port is dead.
#
# This is a real state, not a hypothetical (measured 2026-09-12). Start the container while
# something else holds the port and the bind fails; free the port and `docker compose up -d` or
# `restart` afterwards, and Docker brings the container back to `running` with the right
# PortBindings and NO host mapping. Everything that inspects Docker reports health; every request is
# refused. It is precisely the shape of failure an operator cannot diagnose, because the tools all
# say it is fine — so it gets a check of its own, and the fix named exactly.
#
# NOTE the detection deliberately does NOT go through `_gd_port_is_ours`. In the wedged state
# `docker ps` prints an EMPTY ports column — the mapping exists only in the container's config, not
# in the runtime — so keying off the published port would skip exactly the case this check is for.
# The trigger is "a running nginx of ours exists", and the evidence is a real request.
_gd_nginx_running() {
  [ "$GD_DOCKER_OK" = 1 ] || return 1
  _docker ps --format '{{.Names}}' 2>/dev/null | grep -q 'nginx'
}
check_ingress() {
  local env_file="$1" port bind
  [ -f "$env_file" ] || return 0
  [ "$GD_DOCKER_OK" = 1 ] || return 0
  port="$(gd_publish_port "$env_file")"; bind="$(gd_publish_bind "$env_file")"
  # Only meaningful if there is a running nginx to be wrong about.
  _gd_nginx_running || return 0
  if gd_ingress_reachable "$env_file"; then
    row ok "Answering on ${port}" "yes"
  else
    row bad "Answering on ${port}" "nginx is running and Docker says it publishes ${bind}:${port}, but nothing answers there. A failed port bind is not repaired by a restart — only by recreating the container: docker compose up -d --force-recreate nginx"
  fi
}

# ── Docker names and networks ─────────────────────────────────────────────────────────────────────

# The network is created `external`, and the installer's `docker network create geodeploy || true`
# swallows "already exists". If that network belongs to somebody ELSE, we silently join it — and our
# service aliases are `postgres`, `redis`, `minio`, `martin`, `titiler`, which are exactly the names
# an unrelated stack is likely to have used. Two stacks answering to the same DNS names on one
# network is a genuinely nasty failure, so it is a blocker, not a warning.
check_network() {
  [ "$GD_DOCKER_OK" = 1 ] || return 0
  if ! _docker network inspect geodeploy >/dev/null 2>&1; then
    row ok "Docker network" "'geodeploy' does not exist yet"
    return
  fi
  local attached foreign
  attached="$(_docker network inspect geodeploy --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null)" || attached=""
  foreign=""
  local c
  for c in $attached; do
    case "$c" in geodeploy*|*geodeploy*) ;; *) foreign="${foreign:+$foreign, }$c" ;; esac
  done
  if [ -n "$foreign" ]; then
    GD_NET_FOREIGN=1
    row bad "Docker network" "a network named 'geodeploy' already exists and unrelated containers are on it ($foreign). GeoDeploy would join their network and share DNS names like 'postgres' and 'redis' with them. Rename or remove that network first."
  else
    row ok "Docker network" "'geodeploy' exists and is ours"
  fi
}

# Our five fixed container_names. Docker refuses a duplicate, so this fails loudly on its own — but
# it fails HALFWAY THROUGH an install, which is a much worse place to learn it.
#
# "Is this container ours?" is subtler than it looks, and getting it wrong in the strict direction is
# the worse mistake: a false blocker refuses to install on a perfectly good machine. TWO signals,
# because neither alone is sufficient:
#   * a Compose project label — set on anything `docker compose up` created;
#   * the IMAGE — because postgres, martin, titiler and minio are provisioned by the SETUP WIZARD
#     through the Docker socket, OUTSIDE Compose (see the note in update.sh), so they legitimately
#     carry no Compose label at all. Checking only the label flags a healthy install as a collision,
#     which is exactly what the first run of this script did.
_gd_expected_image() { # container name → a substring its image must contain
  case "$1" in
    geodeploy-postgres) printf 'postgis' ;;
    geodeploy-martin)   printf 'martin' ;;
    geodeploy-titiler)  printf 'titiler' ;;
    geodeploy-minio)    printf 'minio' ;;
    geodeploy-redis)    printf 'redis' ;;
  esac
}
check_names() {
  [ "$GD_DOCKER_OK" = 1 ] || return 0
  local n id label image want
  for n in geodeploy-postgres geodeploy-martin geodeploy-titiler geodeploy-minio geodeploy-redis; do
    id="$(_docker ps -aq --filter "name=^/${n}$" 2>/dev/null | head -1)" || id=""
    [ -n "$id" ] || continue
    label="$(_docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$id" 2>/dev/null)" || label=""
    [ -n "$label" ] && continue
    image="$(_docker inspect -f '{{.Config.Image}}' "$id" 2>/dev/null | tr '[:upper:]' '[:lower:]')" || image=""
    want="$(_gd_expected_image "$n")"
    case "$image" in *"$want"*) continue ;; esac
    GD_NAME_COLLISIONS="${GD_NAME_COLLISIONS:+$GD_NAME_COLLISIONS, }$n (running ${image:-an unknown image})"
  done
  if [ -n "$GD_NAME_COLLISIONS" ]; then
    row bad "Container names" "already taken by containers GeoDeploy did not create: $GD_NAME_COLLISIONS"
  else
    row ok "Container names" "available"
  fi
}

# ── Memory ────────────────────────────────────────────────────────────────────────────────────────

# Not a port concern, but it is the single most common way an install or update dies, and preflight
# is where an operator will actually read it. Building the dashboard peaks far above what running it
# needs, and with no swap Linux kills the build rather than slowing it down.
check_swap() {
  command -v free >/dev/null 2>&1 || return 0
  GD_SWAP_MB="$(free -m 2>/dev/null | awk '/^Swap:/ {print $2}')" || GD_SWAP_MB=0
  [ -n "$GD_SWAP_MB" ] || GD_SWAP_MB=0
  local ram; ram="$(free -m 2>/dev/null | awk '/^Mem:/ {print $2}')" || ram=0
  if [ "$GD_SWAP_MB" -eq 0 ] 2>/dev/null; then
    row warn "Swap" "none — ${ram} MB RAM and no swap. Builds are killed rather than slowed; see docs/getting-started."
  else
    row ok "Swap" "${GD_SWAP_MB} MB (RAM ${ram} MB)"
  fi
}

# ── Run ───────────────────────────────────────────────────────────────────────────────────────────

gd_preflight_run() { # [env_file]
  GD_ROWS=(); GD_BLOCKERS=0
  check_docker
  check_ports
  check_configured_port "${1:-.env}"
  check_ingress "${1:-.env}"
  check_network
  check_names
  check_swap
}

gd_preflight_print() {
  local r state label detail sym col
  [ "${#GD_ROWS[@]}" -gt 0 ] || return 0
  echo ""
  for r in "${GD_ROWS[@]}"; do
    state="${r%%|*}"; r="${r#*|}"; label="${r%%|*}"; detail="${r#*|}"
    case "$state" in
      ok)   sym="✓"; col="$GREEN" ;;
      warn) sym="!"; col="$YELLOW" ;;
      *)    sym="✗"; col="$RED" ;;
    esac
    printf "  %b%s%b  %-26s %b%s%b\n" "$col" "$sym" "$NC" "$label" "$DIM" "$detail" "$NC"
  done
  echo ""
}

gd_preflight_json() {
  local r state label detail first=1
  [ "${#GD_ROWS[@]}" -gt 0 ] || GD_ROWS=()
  printf '{"blockers":%s,"port80_free":%s,"port443_free":%s,' "$GD_BLOCKERS" "$GD_PORT80_FREE" "$GD_PORT443_FREE"
  printf '"port80_holder":"%s","port443_holder":"%s",' "$(printf '%s' "$GD_PORT80_HOLDER" | sed 's/"/\\"/g')" "$(printf '%s' "$GD_PORT443_HOLDER" | sed 's/"/\\"/g')"
  printf '"web_server":"%s","free_candidate":"%s","docker_ok":%s,"docker_sudo":%s,' \
         "$GD_WEBSERVER" "$GD_FREE_CANDIDATE" "$GD_DOCKER_OK" "$GD_DOCKER_SUDO"
  printf '"network_foreign":%s,"name_collisions":"%s","swap_mb":%s,"checks":[' \
         "$GD_NET_FOREIGN" "$GD_NAME_COLLISIONS" "${GD_SWAP_MB:-0}"
  for r in "${GD_ROWS[@]}"; do
    state="${r%%|*}"; r="${r#*|}"; label="${r%%|*}"; detail="${r#*|}"
    [ "$first" = 1 ] || printf ','
    first=0
    printf '{"state":"%s","label":"%s","detail":"%s"}' \
           "$state" "$(printf '%s' "$label" | sed 's/"/\\"/g')" "$(printf '%s' "$detail" | sed 's/"/\\"/g')"
  done
  printf ']}\n'
}

# Only when RUN directly. When install.sh sources this file it wants the functions, not the report.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  cd "$_here/.." 2>/dev/null || true
  gd_preflight_run .env
  if [ "$GD_JSON" = 1 ]; then
    gd_preflight_json
  else
    echo ""
    echo "GeoDeploy preflight — what is already on this machine"
    gd_preflight_print
    if [ "$GD_BLOCKERS" -gt 0 ]; then
      printf "%b%s conflict(s) would stop an install. Nothing has been changed.%b\n\n" "$RED" "$GD_BLOCKERS" "$NC"
    else
      printf "%bNo conflicts. Nothing has been changed.%b\n\n" "$GREEN" "$NC"
    fi
  fi
  exit 0
fi
