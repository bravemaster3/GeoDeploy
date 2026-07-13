#!/usr/bin/env bash
set -euo pipefail

GEODEPLOY_DIR="${GEODEPLOY_DIR:-$HOME/geodeploy}"
REPO="https://github.com/bravemaster3/geodeploy"
VERSION="${GEODEPLOY_VERSION:-main}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

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
  git pull origin "$VERSION"
else
  git clone --branch "$VERSION" --depth 1 "$REPO" .
fi

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

info "Waiting for API to be ready…"
for i in $(seq 1 30); do
  if curl -sf http://localhost/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if ! curl -sf http://localhost/health >/dev/null 2>&1; then
  warn "API did not respond in time. Check logs: sudo docker compose logs geodeploy-api"
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}┌──────────────────────────────────────────────────┐${NC}"
echo -e "${GREEN}│  GeoDeploy is running!                           │${NC}"
echo -e "${GREEN}│                                                  │${NC}"
echo -e "${GREEN}│  Open your browser: http://$(hostname -I | awk '{print $1}')          │${NC}"
echo -e "${GREEN}│                                                  │${NC}"
echo -e "${GREEN}│  The setup wizard will guide you through the     │${NC}"
echo -e "${GREEN}│  rest of the configuration.                      │${NC}"
echo -e "${GREEN}└──────────────────────────────────────────────────┘${NC}"
echo ""
