#!/usr/bin/env bash
set -euo pipefail

GEODEPLOY_DIR="${GEODEPLOY_DIR:-$HOME/geodeploy}"
REPO="https://github.com/bravemaster3/geodeploy"
VERSION="${GEODEPLOY_VERSION:-main}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; DIM='\033[2m'; NC='\033[0m'

info()  { echo -e "${GREEN}[geodeploy]${NC} $*"; }
warn()  { echo -e "${YELLOW}[geodeploy]${NC} $*"; }
error() { echo -e "${RED}[geodeploy]${NC} $*" >&2; exit 1; }

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
  # 3. Told explicitly. The supported non-interactive path: cloud-init, Ansible, CI.
  if [ -n "${GEODEPLOY_DEPLOY_MODE:-}${GEODEPLOY_HTTP_PORT:-}${GEODEPLOY_HTTP_BIND:-}" ]; then
    GD_MODE="${GEODEPLOY_DEPLOY_MODE:-}"; GD_PORT="${GEODEPLOY_HTTP_PORT:-}"; GD_BIND="${GEODEPLOY_HTTP_BIND:-}"
    [ -n "$GD_MODE" ] || { [ "${GD_BIND:-}" = "127.0.0.1" ] && GD_MODE=behind-proxy || GD_MODE=dedicated; }
    [ -n "$GD_PORT" ] || { [ "$GD_MODE" = dedicated ] && GD_PORT=80 || GD_PORT="$(gd_first_free_candidate || echo 8080)"; }
    [ -n "$GD_BIND" ] || { [ "$GD_MODE" = dedicated ] && GD_BIND=0.0.0.0 || GD_BIND=127.0.0.1; }
    gd_valid_port "$GD_PORT" || error "GEODEPLOY_HTTP_PORT='$GD_PORT' is not a port number."
    info "Publishing on ${GD_BIND}:${GD_PORT} (${GD_MODE}) — set in the environment."
    return
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
  echo -e "  ${GREEN}1)${NC} Take over port 80 — GeoDeploy becomes this machine's web server."
  echo "     Open http://<this server> and it is there. Best on a server bought for GeoDeploy."
  if [ "$GD_PORT80_FREE" != 1 ]; then
    echo -e "     ${YELLOW}Not available right now: port 80 is in use${GD_PORT80_HOLDER:+ by $GD_PORT80_HOLDER}.${NC}"
  fi
  echo ""
  echo -e "  ${GREEN}2)${NC} Behind the web server you already run — GeoDeploy listens on"
  echo "     127.0.0.1:${free_port:-????}, reachable only from this machine. You then point a"
  echo "     domain at it, and the dashboard writes the reverse-proxy config for you."
  echo ""
  local default_choice=2
  [ "$ports_free" = 1 ] && default_choice=1
  [ -n "$free_port" ] || default_choice=1

  local tries=0 choice
  while [ "$tries" -lt 3 ]; do
    tries=$((tries+1))
    printf "Choose [%s]: " "$default_choice"
    read -r choice < /dev/tty || choice=""
    [ -n "$choice" ] || choice="$default_choice"
    case "$choice" in
      1)
        if [ "$GD_PORT80_FREE" != 1 ]; then
          echo ""
          warn "Port 80 is in use${GD_PORT80_HOLDER:+ by $GD_PORT80_HOLDER}, and this installer will not stop it for you."
          warn "Stop that service yourself and run the installer again, or choose 2."
          echo ""
          continue
        fi
        GD_MODE=dedicated; GD_BIND=0.0.0.0; GD_PORT=80; break ;;
      2)
        [ -n "$free_port" ] || { warn "No free port among $(gd_candidates). Free one, or choose 1."; continue; }
        GD_MODE=behind-proxy; GD_BIND=127.0.0.1; GD_PORT="$free_port"; break ;;
      *) warn "Type 1 or 2." ;;
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
