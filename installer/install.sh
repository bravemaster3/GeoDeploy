#!/usr/bin/env bash
set -euo pipefail

GEODEPLOY_DIR="${GEODEPLOY_DIR:-$HOME/geodeploy}"
REPO="https://github.com/bravemaster3/geodeploy"
VERSION="${GEODEPLOY_VERSION:-main}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; DIM='\033[2m'; NC='\033[0m'

info()  { echo -e "${GREEN}[geodeploy]${NC} $*"; }
warn()  { echo -e "${YELLOW}[geodeploy]${NC} $*"; }
error() { echo -e "${RED}[geodeploy]${NC} $*" >&2; exit 1; }

# ── Arguments ─────────────────────────────────────────────────────────────────
# Answering the port question up front, for people who already know what they want.
#
#   curl -fsSL …/install.sh | bash -s -- --port 8081
#   curl -fsSL …/install.sh | bash -s -- --dedicated
#
# `bash -s --` is how you pass arguments to a piped script; the environment variables below work
# too, and are the friendlier form for cloud-init and Ansible. Either way the port is CHECKED before
# anything starts — being told "8081 is taken, here are three that are free" beats an install that
# completes and leaves nginx dead.
GD_WANT_PORT="${GEODEPLOY_HTTP_PORT:-}"
GD_WANT_BIND="${GEODEPLOY_HTTP_BIND:-}"
GD_WANT_MODE="${GEODEPLOY_DEPLOY_MODE:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --port)         shift; GD_WANT_PORT="${1:-}" ;;
    --port=*)       GD_WANT_PORT="${1#*=}" ;;
    --bind)         shift; GD_WANT_BIND="${1:-}" ;;
    --bind=*)       GD_WANT_BIND="${1#*=}" ;;
    --dedicated)    GD_WANT_MODE=dedicated ;;
    --behind-proxy) GD_WANT_MODE=behind-proxy ;;
    -h|--help)
      echo "Usage: install.sh [--port N] [--bind ADDR] [--dedicated | --behind-proxy]"
      echo ""
      echo "  --port N         publish on host port N (checked for availability first)"
      echo "  --bind ADDR      0.0.0.0 (reachable from the network) or 127.0.0.1 (this machine only)"
      echo "  --dedicated      take port 80 — GeoDeploy becomes this machine's web server"
      echo "  --behind-proxy   a local port only, published by a reverse proxy you already run"
      echo ""
      echo "With none of these, and a terminal to ask with, the installer asks."
      echo "Environment equivalents: GEODEPLOY_HTTP_PORT, GEODEPLOY_HTTP_BIND, GEODEPLOY_DEPLOY_MODE,"
      echo "GEODEPLOY_PORT_CANDIDATES (the ports offered when choosing), GEODEPLOY_DIR, GEODEPLOY_VERSION."
      exit 0 ;;
    *) error "Unknown option: $1  (try --help)" ;;
  esac
  shift
done

# ── Checks ────────────────────────────────────────────────────────────────────

command -v curl >/dev/null 2>&1 || error "curl is required."

if ! command -v docker >/dev/null 2>&1; then
  info "Docker not found. Installing Docker…"
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  info "Docker installed."
fi

if ! sudo docker compose version >/dev/null 2>&1; then
  error "Docker Compose is not available. Try: sudo apt-get install docker-compose-plugin"
fi

info "Docker found: $(sudo docker --version)"

# ── Install ───────────────────────────────────────────────────────────────────

info "Installing GeoDeploy to $GEODEPLOY_DIR"
mkdir -p "$GEODEPLOY_DIR"
cd "$GEODEPLOY_DIR"

if [ -d ".git" ]; then
  warn "Existing installation found. Updating…"
  # Resolve VERSION the same way self-update.sh does — branch, then tag, then commit — and reset to
  # it. `git pull origin "$VERSION"` could not do this: a TAG is not a branch, so pulling one onto a
  # detached HEAD either merges or refuses, and re-running the installer pinned at a release was the
  # obvious way to reach one. Tags also have to be fetched explicitly.
  git fetch --tags --force origin >/dev/null 2>&1 || warn "Could not fetch from GitHub — continuing with what is here."
  GD_TARGET=""
  for candidate in "refs/remotes/origin/$VERSION" "refs/tags/$VERSION" "$VERSION"; do
    if GD_TARGET="$(git rev-parse -q --verify "${candidate}^{commit}" 2>/dev/null)" && [ -n "$GD_TARGET" ]; then break; fi
    GD_TARGET=""
  done
  [ -n "$GD_TARGET" ] || error "No such version: $VERSION (expected a release tag like v1.0, 'main', or a commit)."
  git reset --hard "$GD_TARGET" >/dev/null || error "Could not check out $VERSION."
else
  git clone --branch "$VERSION" --depth 1 "$REPO" .
fi

# ── Where should GeoDeploy be published? ─────────────────────────────────────
# Everything from here to "Ensure the geodeploy network exists" decides ONE thing: which host port
# nginx publishes, and on which address. It is asked, never assumed.
#
# Why the checks happen AFTER the clone: preflight.sh and lib-deploy.sh ship in the repository, and
# `curl … | bash` has no repository until now. Cloning is reversible and takes nothing from the
# machine; no container is started and no port is bound until the question below is answered.

# shellcheck source=lib-deploy.sh
. "$GEODEPLOY_DIR/installer/lib-deploy.sh"
# shellcheck source=preflight.sh
. "$GEODEPLOY_DIR/installer/preflight.sh"

# The choice, once made, is a FACT about this installation — see the note at the top of
# lib-deploy.sh. These three are what get written; every path below sets them.
GD_MODE=""; GD_BIND=""; GD_PORT=""

# Resolve Docker access up front. `_gd_port_is_ours` needs it, and the port checks below have to be
# able to tell "in use by a stranger" from "in use by the GeoDeploy we are re-running over" — the
# second is the normal, healthy state of every re-run and must not read as a conflict. Cheap, and
# gd_preflight_run resets the findings list before it re-runs this, so nothing is double-reported.
check_docker

# Which port, once "behind a proxy" is chosen. Enter takes the suggestion; ANY port can be typed,
# because the candidate list is a convenience and not a menu — an operator who wants 3000 because
# that is what their proxy config already says should be able to say so. A typed port that is taken
# is refused with the reason and re-asked, never silently swapped for a working one.
ask_for_port() { # default → prints the chosen port, or fails if the operator gives up
  local default="$1" reply tries=0 held listed
  while [ "$tries" -lt 4 ]; do
    tries=$((tries+1))
    # RE-CHECKED on every pass, never a list captured earlier. The suggestions are only worth
    # anything if they are true right now: something can bind a port while this prompt is open, and
    # offering a port that has just been taken is worse than offering none. Each entry has had
    # gd_port_in_use run against it a moment ago.
    listed="$(gd_free_candidates 6)"
    if [ -z "$default" ] || gd_port_in_use "$default"; then
      default="${listed%% *}"
    fi
    echo "" >&2
    if [ -n "$listed" ]; then
      echo -e "  ${DIM}Free right now: ${listed}   (or type any other port)${NC}" >&2
    else
      echo -e "  ${YELLOW}None of the suggested ports is free ($(gd_candidates)).${NC}" >&2
      echo -e "  ${DIM}Type a port you know is free, or press Ctrl-C and set GEODEPLOY_PORT_CANDIDATES.${NC}" >&2
    fi
    printf "Which port? [%s]: " "$default" >&2
    read -r reply < /dev/tty || reply=""
    [ -n "$reply" ] || reply="$default"
    if ! gd_valid_port "$reply"; then
      warn "'$reply' is not a port number (1–65535)." >&2
      continue
    fi
    if [ "$reply" -lt 1024 ]; then
      warn "Port $reply is privileged. Ports below 1024 need root and are usually wanted by something else — pick a higher one, or choose option 1 for port 80." >&2
      continue
    fi
    if gd_port_in_use "$reply"; then
      held="$(gd_port_holder "$reply")"
      warn "Port $reply is already in use${held:+ by $held}. GeoDeploy will not take it." >&2
      continue
    fi
    printf '%s' "$reply"
    return 0
  done
  warn "No port chosen." >&2
  return 1
}

decide_publish() {
  # 1. An existing installation. NEVER re-pick — an update or a re-run that moved the port would
  #    silently orphan the operator's reverse proxy, DNS record and bookmarks, and would look
  #    exactly like a broken install. This is the single most important branch in this function.
  if [ -f .env ] && [ -n "$(gd_env_get GEODEPLOY_HTTP_PORT)" ]; then
    GD_MODE="$(gd_publish_mode)"; GD_BIND="$(gd_publish_bind)"; GD_PORT="$(gd_publish_port)"
    info "Keeping this installation's port: ${GD_BIND}:${GD_PORT} (${GD_MODE}). To change it: sudo bash installer/set-port.sh <port>"
    return
  fi
  # 2. An installation from BEFORE these keys existed. It is on 0.0.0.0:80 and must stay there; we
  #    only write down what is already true, so the file describes the machine.
  if [ -f .env ]; then
    GD_MODE=dedicated; GD_BIND=0.0.0.0; GD_PORT=80
    info "Existing installation — recording its current publish address (0.0.0.0:80). Unchanged."
    return
  fi
  # 3. Told explicitly, by --port/--dedicated or the matching environment variables. The supported
  #    non-interactive path: cloud-init, Ansible, CI.
  if [ -n "${GD_WANT_MODE}${GD_WANT_PORT}${GD_WANT_BIND}" ]; then
    GD_MODE="$GD_WANT_MODE"; GD_PORT="$GD_WANT_PORT"; GD_BIND="$GD_WANT_BIND"
    # ONE rule for what "mode" means, shared with set-port.sh: PORT 80 IS DEDICATED, anything else
    # is behind-proxy. The mode is not a separate fact to guess at — it is the answer to "does
    # GeoDeploy own this machine's web-server role", and port 80 is exactly what that comes down to.
    #
    # It matters that `--port 8081` lands on behind-proxy: nobody publishes a high port to the whole
    # network on purpose, and inferring dedicated would default the bind to 0.0.0.0 and quietly
    # expose an instance the operator believed was local — which, per the note in docker-compose.yml,
    # ufw would not stop.
    if [ -z "$GD_MODE" ]; then
      if [ "${GD_PORT:-}" = 80 ] || { [ -z "${GD_PORT:-}" ] && [ "${GD_BIND:-}" != "127.0.0.1" ]; }; then
        GD_MODE=dedicated
      else
        GD_MODE=behind-proxy
      fi
    fi
    [ -n "$GD_PORT" ] || { [ "$GD_MODE" = dedicated ] && GD_PORT=80 || GD_PORT="$(gd_first_free_candidate || echo 8080)"; }
    [ -n "$GD_BIND" ] || { [ "$GD_MODE" = dedicated ] && GD_BIND=0.0.0.0 || GD_BIND=127.0.0.1; }
    gd_valid_port "$GD_PORT" || error "'$GD_PORT' is not a port number (1–65535)."

    # CHECK IT. A requested port that is already taken used to be accepted here, and the install
    # then completed with nginx dead — the failure appears one layer away from its cause, which is
    # the worst place for it. Asking is better than guessing, and guessing is better than lying:
    # with a terminal we say who holds it and offer what is free; without one we stop.
    # No `_gd_port_is_ours` guard needed: branches 1 and 2 have already returned for any existing
    # installation, so nothing here can be holding the port on our behalf. (Another GeoDeploy in a
    # different directory would be holding it on ITS behalf, and refusing is right for that too.)
    if gd_port_in_use "$GD_PORT"; then
      local held; held="$(gd_port_holder "$GD_PORT")"
      if [ -r /dev/tty ] && [ -t 1 ]; then
        echo ""
        warn "You asked for port ${GD_PORT}, but it is already in use${held:+ by $held}."
        warn "GeoDeploy will not take it from them, and will not silently pick a different one."
        GD_MODE=""; GD_PORT=""; GD_BIND=""     # fall through to the chooser below
      else
        error "Port ${GD_PORT} is already in use${held:+ by $held}, and there is no terminal to ask with. Free it, or pass a different --port. Free right now: $(gd_free_candidates 5)"
      fi
    fi
    if [ -n "$GD_MODE" ]; then
      info "Publishing on ${GD_BIND}:${GD_PORT} (${GD_MODE}) — as requested."
      return
    fi
  fi

  # 4. Ask. First, what is actually on this machine.
  echo ""
  info "Checking this machine…"
  gd_preflight_run .env
  gd_preflight_print
  if [ "$GD_BLOCKERS" -gt 0 ]; then
    error "$(printf '%s conflict(s) above would stop this install. Nothing has been started. Fix them and run the installer again.' "$GD_BLOCKERS")"
  fi

  local free_port="$GD_FREE_CANDIDATE"
  # The candidate list is a SUGGESTION, so show several rather than one: an operator very often has
  # a port in mind already, or a reason to avoid the obvious one, and a single take-it-or-leave-it
  # number hides that this is a free choice. `gd_candidates` reads GEODEPLOY_PORT_CANDIDATES from
  # the environment or .env before falling back to the built-in list.
  local free_ports; free_ports="$(gd_free_candidates 6)"
  local GD_IP_HINT; GD_IP_HINT="$(gd_public_ip)"
  local ports_free=0
  [ "$GD_PORT80_FREE" = 1 ] && [ "$GD_PORT443_FREE" = 1 ] && [ -z "$GD_WEBSERVER" ] && ports_free=1

  # No terminal to ask with. We must not assume dedicated — taking the machine's web-server role is
  # never something to infer from silence — so take the option that claims nothing.
  if [ ! -r /dev/tty ] || [ ! -t 1 ]; then
    [ -n "$free_port" ] || error "No free port among $(gd_candidates), and no terminal to ask. Set GEODEPLOY_HTTP_PORT to a port you know is free and run again."
    GD_MODE=behind-proxy; GD_BIND=127.0.0.1; GD_PORT="$free_port"
    echo ""
    warn "GeoDeploy could not ask you a question (no terminal attached), so it chose the"
    warn "option that takes nothing from this machine: 127.0.0.1:${GD_PORT}, reachable only"
    warn "from this server."
    echo ""
    echo "  If this server is dedicated to GeoDeploy, make it the web server with:"
    echo "      cd $GEODEPLOY_DIR && sudo bash installer/set-port.sh --dedicated"
    echo ""
    return
  fi

  # Three genuinely different situations, and saying the wrong one costs trust immediately: an
  # operator who is told their machine is "already serving something on port 80" when it is not
  # stops believing the rest of the screen.
  if [ "$ports_free" = 1 ]; then
    echo "  Ports 80 and 443 are free and nothing else here is serving the web."
  elif [ "$GD_PORT80_FREE" != 1 ] || [ "$GD_PORT443_FREE" != 1 ]; then
    echo -e "  ${YELLOW}This machine is already serving something on port 80 or 443.${NC}"
    echo "  GeoDeploy will not touch it, whichever option you choose."
  else
    echo -e "  ${YELLOW}Ports 80 and 443 are free right now, but ${GD_WEBSERVER} is installed here.${NC}"
    echo "  It will most likely want them back the next time it starts, and GeoDeploy would then"
    echo "  be the thing that fails. Option 2 avoids that; GeoDeploy will not touch it either way."
  fi
  echo ""
  echo "How should GeoDeploy be published?"
  echo ""
  # THREE options, not two, because "dedicated or not" and "which port" are different questions and
  # bundling them hid the second one. Option 2 is the whole answer for most people; option 3 exists
  # because plenty of operators already know the port their proxy config names.
  echo -e "  ${GREEN}1) Make this machine a dedicated GeoDeploy server.${NC}"
  echo "     GeoDeploy takes port 80 and answers at http://${GD_IP_HINT:-<this server>} — no port in"
  echo "     the address. It becomes the machine's web server: anything else that wants port 80"
  echo "     afterwards will fail to start. Nothing running now is stopped by this installer."
  echo "     Choose this on a server you bought for GeoDeploy."
  if [ "$GD_PORT80_FREE" != 1 ]; then
    echo -e "     ${YELLOW}Not available right now — port 80 is in use${GD_PORT80_HOLDER:+ by $GD_PORT80_HOLDER}.${NC}"
  fi
  echo ""
  if [ -n "$free_port" ]; then
    echo -e "  ${GREEN}2) Use the default port — 127.0.0.1:${free_port}${NC}"
    echo "     GeoDeploy shares the machine. It listens on port ${free_port}, reachable only from this"
    echo "     server, and a reverse proxy you already run publishes it on a domain — the dashboard"
    echo "     writes that configuration for you. Nothing else on the machine is affected."
  else
    echo -e "  ${GREEN}2) Use the default port${NC}"
    echo -e "     ${YELLOW}Unavailable — none of the suggested ports is free ($(gd_candidates)).${NC}"
  fi
  echo ""
  echo -e "  ${GREEN}3) Choose a different port.${NC}"
  echo "     Same as 2, on a port you pick."
  if [ -n "$free_ports" ]; then
    echo -e "     ${DIM}Free right now: ${free_ports}${NC}"
  fi
  echo ""
  local default_choice=2
  [ "$ports_free" = 1 ] && default_choice=1
  [ -n "$free_port" ] || default_choice=3
  [ "$ports_free" = 1 ] && [ -z "$free_port" ] && default_choice=1

  local tries=0 choice
  while [ "$tries" -lt 4 ]; do
    tries=$((tries+1))
    printf "Choose [%s]: " "$default_choice"
    read -r choice < /dev/tty || choice=""
    [ -n "$choice" ] || choice="$default_choice"
    case "$choice" in
      1)
        if [ "$GD_PORT80_FREE" != 1 ]; then
          echo ""
          warn "Port 80 is in use${GD_PORT80_HOLDER:+ by $GD_PORT80_HOLDER}, and this installer will not stop it for you."
          warn "Stop that service yourself and run the installer again, or choose 2 or 3."
          echo ""
          continue
        fi
        GD_MODE=dedicated; GD_BIND=0.0.0.0; GD_PORT=80; break ;;
      2)
        # Re-checked rather than trusted: `free_port` was measured when the menu was printed, and
        # something can bind a port while someone is reading it.
        if [ -z "$free_port" ] || gd_port_in_use "$free_port"; then
          warn "Port ${free_port:-—} is no longer free. Choose 3 and pick another."
          continue
        fi
        GD_MODE=behind-proxy; GD_BIND=127.0.0.1; GD_PORT="$free_port"; break ;;
      3)
        GD_PORT="$(ask_for_port "$free_port")" || continue
        GD_MODE=behind-proxy; GD_BIND=127.0.0.1; break ;;
      *) warn "Type 1, 2 or 3." ;;
    esac
  done
  [ -n "$GD_MODE" ] || error "No choice made. Nothing has been started."

  # Only worth asking in behind-proxy mode: it is the mode whose whole point is a domain in front,
  # and the answer lets the dashboard open on the right screen with the field already filled.
  if [ "$GD_MODE" = behind-proxy ]; then
    echo ""
    echo "Do you have a domain name for this? Leave blank to skip — the dashboard can do it later."
    printf "Domain: "
    read -r GD_DOMAIN_HINT < /dev/tty || GD_DOMAIN_HINT=""
  fi
}

decide_publish

# ── Generate .env if it doesn't exist ────────────────────────────────────────

if [ ! -f .env ]; then
  cp .env.example .env
  # Generate a secure random secret key
  SECRET_KEY=$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)
  sed -i "s/change-this-to-a-long-random-string/$SECRET_KEY/" .env
  info ".env created with a generated secret key."
fi
# .env holds the JWT + DB + storage secrets — keep it owner-only.
chmod 600 .env 2>/dev/null || true

# Record the deployed commit in .env (env_file → the API reads GEODEPLOY_GIT_SHA at runtime) so the
# admin "Updates" panel can tell how far behind GitHub the running code is.
GD_SHA=$(git rev-parse HEAD 2>/dev/null || echo unknown)
if grep -q '^GEODEPLOY_GIT_SHA=' .env 2>/dev/null; then
  sed -i "s|^GEODEPLOY_GIT_SHA=.*|GEODEPLOY_GIT_SHA=${GD_SHA}|" .env
else
  echo "GEODEPLOY_GIT_SHA=${GD_SHA}" >> .env
fi
# …and the REF it came from, so the Updates panel knows whether this instance follows `main` or is
# pinned to a release, and offers the right next version. Same bind-mounted data/temp channel the
# updater writes; the API re-reads it per request.
mkdir -p data/temp
printf '%s' "$VERSION" > data/temp/deployed-ref 2>/dev/null || true

# ── Record where GeoDeploy is published ──────────────────────────────────────
# Written with gd_env_set, which rewrites .env IN PLACE. `sed -i` gives the file a new inode, and
# .env is a single-file bind mount into the api and celery containers — a running container would go
# on reading the old one. (The sed calls above run before any container exists, so they are safe.)
gd_env_set GEODEPLOY_DEPLOY_MODE "$GD_MODE"
gd_env_set GEODEPLOY_HTTP_BIND   "$GD_BIND"
gd_env_set GEODEPLOY_HTTP_PORT   "$GD_PORT"

# A domain the operator typed at install time. Only a HINT — the dashboard opens its Deployment
# screen with the field filled in rather than making them find it again. Same bind-mounted data/temp
# channel as deployed-ref, so the API can read it without a rebuild.
if [ -n "${GD_DOMAIN_HINT:-}" ]; then
  printf '{"domain":"%s","mode":"%s"}' \
    "$(printf '%s' "$GD_DOMAIN_HINT" | tr -d '"\\' )" "$GD_MODE" > data/temp/deploy-hints.json 2>/dev/null || true
fi

# ── Ensure the geodeploy network exists (persists across compose down/up) ────

sudo docker network create geodeploy 2>/dev/null || true

# ── Seed a minimal Martin config so the always-on tile server boots cleanly ──
# (Martin needs a config file to start; the API rewrites it once a DB is configured.)
mkdir -p data/martin
if [ ! -f data/martin/martin-config.yaml ]; then
  printf 'listen_addresses: 0.0.0.0:3000\n' > data/martin/martin-config.yaml
fi

# ── Pull images ───────────────────────────────────────────────────────────────

info "Pulling Docker images…"
sudo docker compose pull geodeploy-ui nginx redis 2>/dev/null || true

# ── Start core services ───────────────────────────────────────────────────────
# martin (vector tiles) + titiler (raster tiles) are core — needed for both local and
# external PostGIS/S3 — so they start here rather than being profile-gated.

# LAST CHECK, immediately before anything binds. The port was verified when it was chosen, but the
# clone, the .env write, the network create and the image pull all happen in between — on a busy
# machine that is easily a minute, and a port can be taken in a minute. Failing here costs nothing;
# failing after `up -d` leaves a half-started stack and an error from Docker rather than from us.
if gd_port_in_use "$GD_PORT" && ! _gd_port_is_ours "$GD_PORT"; then
  GD_HELD="$(gd_port_holder "$GD_PORT")"
  error "Port ${GD_PORT} was free when you chose it and has been taken since${GD_HELD:+, by $GD_HELD}. Nothing has been started. Re-run the installer, or pick another port: sudo bash installer/set-port.sh <port> after installing. Free right now: $(gd_free_candidates 5)"
fi

info "Starting GeoDeploy…"
sudo docker compose up -d geodeploy-api geodeploy-ui nginx redis celery martin titiler

# ── Wait for API ──────────────────────────────────────────────────────────────

# Built from .env, never hard-coded: on a behind-proxy install nothing answers on port 80, and a
# hard-coded http://localhost/health reported a perfectly healthy stack as dead.
GD_HEALTH_URL="$(gd_local_url)/health"

info "Waiting for API to be ready…"
for i in $(seq 1 30); do
  if curl -sf "$GD_HEALTH_URL" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if ! curl -sf "$GD_HEALTH_URL" >/dev/null 2>&1; then
  warn "API did not respond at $GD_HEALTH_URL. Check logs: sudo docker compose logs geodeploy-api"
  # The commonest cause by far, and the one whose error message is otherwise inscrutable.
  if ! sudo docker compose ps nginx 2>/dev/null | grep -qi 'up\|running'; then
    warn "nginx is not running — usually the published port (${GD_BIND}:${GD_PORT}) was taken."
    warn "See: sudo docker compose logs nginx"
  fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
if [ "$GD_MODE" = behind-proxy ]; then
  echo -e "${GREEN}┌──────────────────────────────────────────────────────────────────┐${NC}"
  echo -e "${GREEN}│  GeoDeploy is running, behind whatever you put in front of it.   │${NC}"
  echo -e "${GREEN}└──────────────────────────────────────────────────────────────────┘${NC}"
  echo ""
  echo "  On this machine       $(gd_local_url)"
  echo ""
  echo -e "  ${YELLOW}Nothing outside this server can reach it yet — that is the point of${NC}"
  echo -e "  ${YELLOW}this mode. Two ways on:${NC}"
  echo ""
  echo "  To open the dashboard now, from your own computer:"
  echo "      ssh -L ${GD_PORT}:127.0.0.1:${GD_PORT} $(id -un)@$(gd_public_ip)"
  echo "      then open http://localhost:${GD_PORT}"
  echo ""
  echo "  To give it a domain, point your existing web server at $(gd_local_url)."
  echo "  Settings → Deployment in the dashboard writes that configuration for you."
  if [ -n "${GD_DOMAIN_HINT:-}" ]; then
    echo "  (It will start from the domain you gave: ${GD_DOMAIN_HINT})"
  fi
  echo ""
  echo -e "  ${DIM}Nothing already running on this machine was changed or stopped.${NC}"
else
  GD_IP="$(gd_public_ip)"
  echo -e "${GREEN}┌──────────────────────────────────────────────────────────────────┐${NC}"
  echo -e "${GREEN}│  GeoDeploy is running!                                           │${NC}"
  echo -e "${GREEN}└──────────────────────────────────────────────────────────────────┘${NC}"
  echo ""
  echo "  Open your browser:    http://${GD_IP:-<this server>}"
  echo ""
  echo "  The setup wizard will guide you through the rest of the configuration."
  echo ""
  echo -e "  ${DIM}GeoDeploy is this machine's web server on port ${GD_PORT}. To move it later:${NC}"
  echo -e "  ${DIM}    cd $GEODEPLOY_DIR && sudo bash installer/set-port.sh 8080${NC}"
fi
echo ""
