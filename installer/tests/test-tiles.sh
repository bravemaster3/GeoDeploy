#!/usr/bin/env bash
# test-tiles.sh — do the tile servers GeoDeploy ships still serve what GeoDeploy asks of them?
#
# Martin and TiTiler are third-party images whose request contracts have changed under us before
# (a contour parameter retyped to integer blanked every contour layer). They are PINNED in
# docker-compose.yml, services/postgis.py and services/minio.py; this is the test to run before
# moving a pin, and the evidence for the pin that is there.
#
# A throwaway network with PostGIS, SILO, Martin, TiTiler and nginx running the repository's own
# nginx.conf. tiles/prepare.py builds the data and configures both servers with GeoDeploy's own code;
# tiles/check.py makes every request a portal, the editor and the API make, and DECODES the answer.
# Nothing of a live instance is touched (own network, own container names).
#
#   bash installer/tests/test-tiles.sh                        # the pinned images
#   GDT_MARTIN_IMAGE=ghcr.io/maplibre/martin:1.10.1 \
#   GDT_TITILER_IMAGE=ghcr.io/developmentseed/titiler:2.2.1 \
#     bash installer/tests/test-tiles.sh                      # any other pair, e.g. a candidate bump
#
# API_IMAGE must be built from this checkout's api/ (the checker imports its packages).
set -uo pipefail

SRC="${SRC:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
API_IMAGE="${API_IMAGE:-geodeploy/api:latest}"
# The constant as the code ships it (CR stripped: the checkout may have Windows line endings).
pinned() { tr -d '\r' < "$SRC/api/geodeploy/services/$2" | sed -n "s/^$1 = \"\(.*\)\"$/\1/p"; }
MARTIN="${GDT_MARTIN_IMAGE:-$(pinned MARTIN_IMAGE postgis.py)}"
TITILER="${GDT_TITILER_IMAGE:-$(pinned TITILER_IMAGE minio.py)}"
SILO="$(pinned IMAGE minio.py)"
POSTGIS="$(pinned IMAGE postgis.py)"
NET=gdtiles; PFX=gdtiles
W="$(mktemp -d)"; chmod 777 "$W"
ROOT_USER=gdtilesroot; ROOT_PASS=gdtiles-root-secret-0123456789
PG_PASS=gdtiles-pg

cleanup() {
  [ "${GDT_KEEP:-}" = 1 ] && [ "${1:-}" = exit ] && { echo "GDT_KEEP=1: left running on network $NET (data in $W)"; return; }
  docker rm -f $PFX-pg $PFX-minio $PFX-martin $PFX-titiler $PFX-nginx >/dev/null 2>&1
  docker network rm $NET >/dev/null 2>&1
  docker run --rm -v "$W:/w" alpine rm -rf /w/* >/dev/null 2>&1; rm -rf "$W"
}
trap 'cleanup exit' EXIT
cleanup >/dev/null 2>&1; W="$(mktemp -d)"; chmod 777 "$W"

echo "Martin $MARTIN   TiTiler $TITILER   SILO $SILO   PostGIS $POSTGIS   API $API_IMAGE"
docker network create $NET >/dev/null

docker run -d --name $PFX-pg --network $NET --network-alias postgres \
  -e POSTGRES_USER=geodeploy -e POSTGRES_PASSWORD=$PG_PASS -e POSTGRES_DB=geodeploy "$POSTGIS" >/dev/null
docker run -d --name $PFX-minio --network $NET --network-alias minio \
  -e MINIO_ROOT_USER=$ROOT_USER -e MINIO_ROOT_PASSWORD=$ROOT_PASS "$SILO" server /data >/dev/null

ENVS=(-e POSTGIS_HOST=postgres -e POSTGIS_PORT=5432 -e POSTGIS_USER=geodeploy -e POSTGIS_PASSWORD=$PG_PASS
      -e POSTGIS_DB=geodeploy -e STORAGE_BUCKET=geodeploy -e TITILER_URL=http://titiler:80
      -e GEODEPLOY_DATA_DIR=/tmp/gd -e GDT_NETWORK=$NET -e GDT_TITILER_NAME=$PFX-titiler
      -e GDT_TITILER_IMAGE="$TITILER" -e GDT_MARTIN_IMAGE="$MARTIN"
      -e GDT_ROOT_USER=$ROOT_USER -e GDT_ROOT_PASSWORD=$ROOT_PASS)
# pg_isready first: the PostGIS image restarts once while it initialises.
for _ in $(seq 1 60); do docker exec $PFX-pg pg_isready -U geodeploy -d geodeploy >/dev/null 2>&1 && break; sleep 1; done
sleep 3
docker run --rm --network $NET -v "$SRC:/repo:ro" -v "$W:/out" -v /var/run/docker.sock:/var/run/docker.sock \
  "${ENVS[@]}" "$API_IMAGE" python /repo/installer/tests/tiles/prepare.py || { echo "prepare failed"; exit 2; }

# Martin exactly as docker-compose.yml runs it.
docker run -d --name $PFX-martin --network $NET --network-alias martin -v "$W:/config" \
  "$MARTIN" --config /config/martin-config.yaml >/dev/null
docker run -d --name $PFX-nginx --network $NET --network-alias nginx \
  -v "$SRC/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" nginx:alpine >/dev/null
for _ in $(seq 1 60); do
  docker run --rm --network $NET "$API_IMAGE" python -c "import httpx,sys; sys.exit(0 if httpx.get('http://nginx/tiles/catalog',timeout=3).status_code==200 and httpx.get('http://titiler:80/healthz',timeout=3).status_code==200 else 1)" >/dev/null 2>&1 && break
  sleep 2
done

docker run --rm --network $NET -v "$SRC:/repo:ro" -v "$W:/out" "${ENVS[@]}" "$API_IMAGE" \
  bash -c "pip install -q mapbox-vector-tile==2.1.0 >/dev/null 2>&1; python /repo/installer/tests/tiles/check.py"
rc=$?
if [ $rc -ne 0 ]; then
  echo "--- martin log";  docker logs --tail 30 $PFX-martin 2>&1
  echo "--- titiler log"; docker logs --tail 30 $PFX-titiler 2>&1
fi
exit $rc
