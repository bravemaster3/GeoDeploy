# Build tippecanoe (vector-tile / PMTiles generator) from the maintained felt fork — there is
# no apt package. Built on bookworm and run on trixie (the base below): a binary linked against an
# older glibc runs on a newer one. Only the binary is copied into the final image (keeps it lean).
#
# PINNED TO A COMMIT. This used to clone the default branch, so every build compiled whatever was
# committed that morning — the same drift that broke the worker through kombu (constraints.txt).
# felt tags releases rarely (2.79.0 was July 2025) while the branch moved on to v2.82.0 with real
# fixes, so the pin is a commit, not a tag. To upgrade: pick a newer commit, build, ingest a layer.
ARG TIPPECANOE_COMMIT=a888304b19cf1ed144341a8218a9421f47248d4e
FROM debian:bookworm-slim AS tippecanoe
ARG TIPPECANOE_COMMIT
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libsqlite3-dev zlib1g-dev git ca-certificates \
    && git init -q /src \
    && git -C /src fetch -q --depth 1 https://github.com/felt/tippecanoe.git "$TIPPECANOE_COMMIT" \
    && git -C /src checkout -q FETCH_HEAD \
    && make -C /src -j"$(nproc)" \
    && make -C /src install \
    && rm -rf /var/lib/apt/lists/*

# The Debian release named, not implied: plain `python:3.12-slim` follows whatever Debian is
# current, and moving to the next one would change the system GDAL every import runs on.
FROM python:3.12-slim-trixie AS base

# postgresql-client-16 is for BACKUPS (services/backup.dump_postgis). It must be pinned to the
# SERVER's major version: our PostGIS is postgis/postgis:16-3.4, and pg_dump REFUSES to dump a
# server newer than itself ("server version mismatch") — Debian bookworm ships client 15, which
# would fail every backup. Bump this together with services/postgis.IMAGE + docker-compose.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev libpq-dev curl libsqlite3-0 gnupg ca-certificates \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
    && apt-get purge -y gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# tippecanoe + tile-join from the build stage (runtime needs libsqlite3-0, installed above).
COPY --from=tippecanoe /usr/local/bin/tippecanoe /usr/local/bin/tippecanoe
COPY --from=tippecanoe /usr/local/bin/tile-join /usr/local/bin/tile-join

ENV GDAL_VERSION=3.8 \
    CPLUS_INCLUDE_PATH=/usr/include/gdal \
    C_INCLUDE_PATH=/usr/include/gdal \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# constraints.txt pins EVERY package, transitive ones included, so a build next month installs what
# was tested — not whatever is newest that day (see the header of constraints.txt for why).
COPY requirements.txt constraints.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 120 --retries 5 -r requirements.txt -c constraints.txt

# Bake DuckDB's httpfs + spatial extensions into the image so S3/GeoParquet reads AND native
# geometry conversion (GeoParquet → FlatGeobuf for PMTiles tiling) work with NO runtime download.
# extensions.duckdb.org is often slow/unreachable, and a container recreate wipes DuckDB's ephemeral
# per-HOME cache — so a fresh `docker compose build` (e.g. on a new VPS) must ship the extensions.
# `spatial` also lets the tiler drop the slow shapely GeoJSON funnel (it bundles GDAL, so DuckDB can
# COPY straight to FlatGeobuf). We curl each (patient --retry, robust to a slow CDN) into a FIXED dir
# the app points at via DUCKDB_EXTENSION_DIR; version is derived from the installed duckdb so it
# can't drift. The final LOAD verifies both baked extensions at build time (fails the build if wrong).
ENV DUCKDB_EXTENSION_DIR=/opt/duckdb_ext
RUN set -eux; \
    V="v$(python -c 'import duckdb; print(duckdb.__version__)')"; P=linux_amd64_gcc4; \
    DEST="$DUCKDB_EXTENSION_DIR/$V/$P"; mkdir -p "$DEST"; \
    for EXT in httpfs spatial; do \
      curl --retry 15 --retry-delay 5 --retry-all-errors -C - -fL --max-time 900 \
        -o "/tmp/$EXT.gz" "http://extensions.duckdb.org/$V/$P/$EXT.duckdb_extension.gz"; \
      gunzip -c "/tmp/$EXT.gz" > "$DEST/$EXT.duckdb_extension"; rm -f "/tmp/$EXT.gz"; \
    done; \
    python -c "import duckdb, os; c=duckdb.connect(config={'extension_directory': os.environ['DUCKDB_EXTENSION_DIR']}); c.execute('LOAD httpfs'); c.execute('LOAD spatial'); print('baked httpfs+spatial OK')"

# ---

FROM base AS development
CMD ["uvicorn", "geodeploy.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---

FROM base AS production
COPY . .
RUN alembic upgrade head || true
CMD ["uvicorn", "geodeploy.main:app", "--host", "0.0.0.0", "--port", "8000"]
