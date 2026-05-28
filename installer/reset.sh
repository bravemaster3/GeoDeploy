#!/usr/bin/env bash
# Wipes all GeoDeploy containers, images, network, and data for a clean reinstall.
set -euo pipefail

GEODEPLOY_DIR="${GEODEPLOY_DIR:-$HOME/geodeploy}"
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()  { echo -e "${GREEN}[geodeploy-reset]${NC} $*"; }
warn()  { echo -e "${YELLOW}[geodeploy-reset]${NC} $*"; }

warn "This will permanently delete all GeoDeploy data, containers, and configuration."
read -r -p "Are you sure? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
  echo "Aborted."
  exit 0
fi

info "Stopping and removing all GeoDeploy containers..."
sudo docker ps -a --filter "name=geodeploy" -q | xargs -r sudo docker rm -f

info "Removing GeoDeploy images..."
sudo docker rmi geodeploy/api:latest geodeploy/ui:latest 2>/dev/null || true

info "Removing GeoDeploy Docker network..."
sudo docker network rm geodeploy 2>/dev/null || true

info "Removing GeoDeploy directory ($GEODEPLOY_DIR)..."
cd "$HOME"
sudo rm -rf "$GEODEPLOY_DIR"

echo ""
echo -e "${GREEN}Reset complete. Run the installer to start fresh:${NC}"
echo ""
echo "  curl -fsSL https://raw.githubusercontent.com/bravemaster3/geodeploy/main/installer/install.sh | bash"
echo ""
