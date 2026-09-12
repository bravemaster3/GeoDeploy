#!/usr/bin/env bash
# Move GeoDeploy to a different host port — or onto (or off) port 80 — safely and reversibly.
#
#   sudo bash installer/set-port.sh 8081             # behind a reverse proxy, on 127.0.0.1:8081
#   sudo bash installer/set-port.sh --dedicated      # take port 80 on every interface
#   sudo bash installer/set-port.sh --port 8081 --bind 0.0.0.0    # 8081, reachable from the network
#   sudo bash installer/set-port.sh --show           # just say where it is now
#
# This is the ONLY supported way to change the port, and the reason is worth stating: editing .env by
# hand changes nothing until nginx is recreated, so the file says one thing and the running container
# does another — and `docker compose up -d` on its own will not always notice. That "the update looks
# applied and simply is not" failure has cost this project real debugging time in three other places
# (nginx.conf's single-file mount, .env's inode, the portals mount), so the port does not get to join
# them.
#
# WHAT IT GUARANTEES
#   * the target port is free BEFORE anything is written;
#   * .env is rewritten IN PLACE (same inode — .env is bind-mounted into the api container);
#   * only nginx is recreated, so the API, the worker and every job in flight are untouched;
#   * if nginx does not come back healthy, the OLD settings are restored and nginx recreated again.
#     A failed port change leaves you exactly where you started, still serving.
set -uo pipefail

_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-deploy.sh
. "$_here/lib-deploy.sh"
cd "$_here/.." || { echo "Cannot find the GeoDeploy directory." >&2; exit 1; }

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; DIM='\033[2m'; NC='\033[0m'
info()  { echo -e "${GREEN}[set-port]${NC} $*"; }
warn()  { echo -e "${YELLOW}[set-port]${NC} $*"; }
error() { echo -e "${RED}[set-port]${NC} $*" >&2; exit 1; }

[ -f .env ] || error "No .env here — is $(pwd) a GeoDeploy installation?"

DC() { if docker info >/dev/null 2>&1; then docker compose "$@"; else sudo docker compose "$@"; fi; }

# ── Where are we now ──────────────────────────────────────────────────────────────────────────────

OLD_MODE="$(gd_publish_mode)"; OLD_BIND="$(gd_publish_bind)"; OLD_PORT="$(gd_publish_port)"

show_current() {
  echo ""
  echo "  Mode              $OLD_MODE"
  echo "  Published on      ${OLD_BIND}:${OLD_PORT}"
  if [ "$OLD_BIND" = "127.0.0.1" ]; then
    echo -e "  ${DIM}Reachable only from this machine. A reverse proxy on this host publishes it."
    echo -e "  From your laptop:  ssh -L ${OLD_PORT}:127.0.0.1:${OLD_PORT} $(id -un)@<this-server>${NC}"
  else
    local ip; ip="$(gd_public_ip)"
    echo -e "  ${DIM}Reachable from the network${ip:+ at http://${ip}$([ "$OLD_PORT" = 80 ] || printf ':%s' "$OLD_PORT")}${NC}"
  fi
  echo ""
}

# ── Arguments ─────────────────────────────────────────────────────────────────────────────────────

NEW_PORT=""; NEW_BIND=""; NEW_MODE=""; ASSUME_YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --show) show_current; exit 0 ;;
    --dedicated) NEW_MODE=dedicated; NEW_PORT=80; NEW_BIND=0.0.0.0 ;;
    --behind-proxy) NEW_MODE=behind-proxy; NEW_BIND=127.0.0.1 ;;
    --port) shift; NEW_PORT="${1:-}" ;;
    --bind) shift; NEW_BIND="${1:-}" ;;
    -y|--yes) ASSUME_YES=1 ;;
    # The header comment IS the help, printed up to the first line that is not a comment — a fixed
    # line range silently starts leaking code into --help the moment the header grows.
    -h|--help) awk 'NR>1 && /^#/ { sub(/^# ?/, ""); print; next } NR>1 { exit }' "$0"; exit 0 ;;
    -*) error "Unknown option: $1" ;;
    *) NEW_PORT="$1" ;;
  esac
  shift
done

# `--behind-proxy` with no port is a complete instruction — "get off the public port" — so pick the
# first CANDIDATE THAT IS FREE rather than making the operator go and look one up.
if [ -z "$NEW_PORT" ] && [ "$NEW_MODE" = behind-proxy ]; then
  NEW_PORT="$(gd_first_free_candidate || true)"
  [ -n "$NEW_PORT" ] || error "None of $(gd_candidates) is free. Name a port: set-port.sh --port <n>."
  info "Using ${NEW_PORT} — the first free port of $(gd_candidates)."
fi
[ -n "$NEW_PORT" ] || { show_current; error "Say which port. e.g. 'set-port.sh 8081', or 'set-port.sh --dedicated'."; }
gd_valid_port "$NEW_PORT" || error "'$NEW_PORT' is not a port number (1–65535)."

# The bind follows from the port unless the operator overrode it. Port 80 all but always means "be
# the machine's web server"; anything else all but always means "sit behind one". Both are
# overridable with --bind, because "publish 8080 to the whole network" is a legitimate, if unusual,
# thing to want (a proxy on a DIFFERENT machine).
if [ -z "$NEW_BIND" ]; then
  if [ "$NEW_PORT" = 80 ]; then NEW_BIND=0.0.0.0; else NEW_BIND=127.0.0.1; fi
fi
case "$NEW_BIND" in
  0.0.0.0|127.0.0.1|::|::1) ;;
  *[!0-9.]*) error "--bind must be an IP address on this machine (0.0.0.0 or 127.0.0.1 in almost every case)." ;;
esac
# The same one rule the installer uses: PORT 80 IS DEDICATED, anything else is behind-proxy. The
# mode answers "does GeoDeploy own this machine's web-server role", and port 80 is what that comes
# down to — so `--port 8081 --bind 0.0.0.0` (a proxy on a DIFFERENT machine) is still behind-proxy,
# and the two scripts cannot disagree about what an installation is.
if [ -z "$NEW_MODE" ]; then
  if [ "$NEW_PORT" = 80 ]; then NEW_MODE=dedicated; else NEW_MODE=behind-proxy; fi
fi

if [ "$NEW_PORT" = "$OLD_PORT" ] && [ "$NEW_BIND" = "$OLD_BIND" ]; then
  info "Already published on ${NEW_BIND}:${NEW_PORT} — nothing to do."
  exit 0
fi

# ── Is the target actually available? ─────────────────────────────────────────────────────────────

if gd_port_in_use "$NEW_PORT"; then
  # Our own nginx holding the port we are moving FROM is not a conflict; anything else is. (Moving
  # 8080 → 8080 exited above, so if we are here and we hold it, the operator is changing the BIND
  # only — 127.0.0.1:8080 → 0.0.0.0:8080, say — and Docker will release it when nginx is recreated.)
  if DC ps --format '{{.Service}}\t{{.Publishers}}' 2>/dev/null | grep -q "^nginx.*:${NEW_PORT}" \
     || [ "$NEW_PORT" = "$OLD_PORT" ]; then
    :
  else
    holder="$(gd_port_holder "$NEW_PORT")"
    # Offer somewhere to go. Every port named here has just had gd_port_in_use run against it, so
    # the list is what is free NOW rather than a static suggestion — being refused with no way
    # forward is the same dead end as not checking at all.
    alternatives="$(gd_free_candidates 6)"
    echo "" >&2
    warn "Port ${NEW_PORT} is already in use${holder:+ by $holder}."
    warn "Nothing has been changed — GeoDeploy is still on ${OLD_BIND}:${OLD_PORT}."
    if [ -n "$alternatives" ]; then
      echo "" >&2
      echo "  Free right now:  ${alternatives}" >&2
      echo "  For example:     sudo bash installer/set-port.sh ${alternatives%% *}" >&2
      echo "" >&2
    else
      echo "" >&2
      echo "  None of $(gd_candidates) is free either. Pick a port you know is available," >&2
      echo "  or set GEODEPLOY_PORT_CANDIDATES to a list that suits this machine." >&2
      echo "" >&2
    fi
    exit 1
  fi
fi

if [ "$NEW_PORT" -lt 1024 ] && [ "$(id -u)" != 0 ]; then
  warn "Port ${NEW_PORT} is privileged; Docker needs root to publish it. Re-run with sudo if this fails."
fi

# ── Confirm ───────────────────────────────────────────────────────────────────────────────────────

echo ""
echo "  GeoDeploy will move:"
echo "      from   ${OLD_BIND}:${OLD_PORT}   (${OLD_MODE})"
echo "      to     ${NEW_BIND}:${NEW_PORT}   (${NEW_MODE})"
echo ""
if [ "$NEW_MODE" = dedicated ] && [ "$OLD_MODE" != dedicated ]; then
  echo -e "  ${YELLOW}This makes GeoDeploy this machine's web server on port ${NEW_PORT}.${NC}"
  echo "  Anything else that wants that port will fail to start. Nothing already running"
  echo "  will be stopped by this script."
  echo ""
fi
if [ "$NEW_BIND" = "127.0.0.1" ] && [ "$OLD_BIND" != "127.0.0.1" ]; then
  echo -e "  ${YELLOW}After this, GeoDeploy is reachable only from this machine.${NC}"
  echo "  Point your reverse proxy at http://127.0.0.1:${NEW_PORT} — Settings → Deployment"
  echo "  in the dashboard writes the configuration for you."
  echo ""
fi
if [ "$ASSUME_YES" != 1 ]; then
  # `< /dev/tty` for the same reason install.sh needs it: this may be running from a pipe.
  if [ -r /dev/tty ] && [ -t 1 ]; then
    printf "  Continue? [y/N]: "
    read -r reply < /dev/tty || reply=""
    case "$reply" in y|Y|yes|YES) ;; *) echo "  Aborted. Nothing changed."; exit 0 ;; esac
  else
    error "Refusing to change the port without confirmation and without a terminal to ask. Pass -y if you mean it."
  fi
fi

# ── Apply, with rollback ──────────────────────────────────────────────────────────────────────────

apply() { # mode bind port
  gd_env_set GEODEPLOY_DEPLOY_MODE "$1" || return 1
  gd_env_set GEODEPLOY_HTTP_BIND   "$2" || return 1
  gd_env_set GEODEPLOY_HTTP_PORT   "$3" || return 1
  # ONLY nginx. The API, the worker and anything they are in the middle of are none of this script's
  # business — and recreating them would abort running ingest jobs for a change they cannot observe.
  DC up -d --force-recreate nginx
}

wait_healthy() { # url
  local i
  for i in $(seq 1 20); do
    curl -fsS "$1/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

# Restore and PROVE it. Saying "GeoDeploy is back on the old port" without checking is the same
# unverified claim this project has been caught by before, and it is worse here than usual: the
# operator has just been told a change failed, and the one thing they need to be able to trust is
# that they are back where they started.
roll_back() { # reason
  warn "$1 — restoring ${OLD_BIND}:${OLD_PORT}."
  apply "$OLD_MODE" "$OLD_BIND" "$OLD_PORT" >/dev/null 2>&1
  if wait_healthy "$(gd_local_url)"; then
    error "Port change failed. GeoDeploy is back on ${OLD_BIND}:${OLD_PORT} and serving."
  fi
  error "Port change failed AND the rollback did not come back up. .env is restored to ${OLD_BIND}:${OLD_PORT}; recover with:  docker compose up -d --force-recreate nginx  (then check: docker compose logs nginx)"
}

info "Moving GeoDeploy to ${NEW_BIND}:${NEW_PORT}…"
if ! apply "$NEW_MODE" "$NEW_BIND" "$NEW_PORT"; then
  roll_back "Could not start nginx on the new port"
fi

NEW_URL="$(gd_local_url)"
if ! wait_healthy "$NEW_URL"; then
  roll_back "nginx started but ${NEW_URL}/health did not answer"
fi

echo ""
info "Done. GeoDeploy is published on ${NEW_BIND}:${NEW_PORT}."
if [ "$NEW_BIND" = "127.0.0.1" ]; then
  echo ""
  echo "  Locally:        ${NEW_URL}"
  echo "  From a laptop:  ssh -L ${NEW_PORT}:127.0.0.1:${NEW_PORT} $(id -un)@<this-server>"
  echo "                  then open http://localhost:${NEW_PORT}"
  echo ""
  echo "  To give it a domain, point your reverse proxy at ${NEW_URL} — or open"
  echo "  Settings → Deployment in the dashboard and it will write the config for you."
else
  ip="$(gd_public_ip)"
  echo ""
  echo "  ${NEW_URL}${ip:+   ·   http://${ip}$([ "$NEW_PORT" = 80 ] || printf ':%s' "$NEW_PORT")}"
fi
echo ""
if [ -n "$(gd_env_get GEODEPLOY_HTTPS_PORT)" ] || grep -q '^COMPOSE_FILE=.*tls' .env 2>/dev/null; then
  warn "This install also publishes 443 (docker-compose.tls.yml). That is unchanged by this script."
fi
