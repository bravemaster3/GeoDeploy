# Build tippecanoe (vector-tile / PMTiles generator) from the maintained felt fork — there is
# no apt package. Built on the same Debian base as python:3.12-slim so the binary is ABI-compatible;
# only the binary is copied into the final image (keeps it lean).
FROM debian:bookworm-slim AS tippecanoe
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libsqlite3-dev zlib1g-dev git ca-certificates \
    && git clone --depth 1 https://github.com/felt/tippecanoe.git /src \
    && make -C /src -j"$(nproc)" \
    && make -C /src install \
    && rm -rf /var/lib/apt/lists/*

FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev libpq-dev curl libsqlite3-0 \
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

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 120 --retries 5 -r requirements.txt

# Bake DuckDB's httpfs extension into the image so S3/GeoParquet reads work with NO runtime
# download. extensions.duckdb.org is often slow/unreachable, and a container recreate wipes
# DuckDB's ephemeral per-HOME cache — so a fresh `docker compose build` (e.g. on a new VPS) must
# ship the extension. We curl it (patient --retry, robust to a slow CDN) into a FIXED dir that the
# app points at via DUCKDB_EXTENSION_DIR; version is derived from the installed duckdb so it can't
# drift. The final LOAD verifies the baked extension at build time (fails the build if wrong).
ENV DUCKDB_EXTENSION_DIR=/opt/duckdb_ext
RUN V="v$(python -c 'import duckdb; print(duckdb.__version__)')" && P=linux_amd64_gcc4 && \
    mkdir -p "$DUCKDB_EXTENSION_DIR/$V/$P" && \
    curl --retry 10 --retry-delay 5 --retry-all-errors -fL --max-time 600 \
      "http://extensions.duckdb.org/$V/$P/httpfs.duckdb_extension.gz" \
      | gunzip > "$DUCKDB_EXTENSION_DIR/$V/$P/httpfs.duckdb_extension" && \
    python -c "import duckdb; c=duckdb.connect(); c.execute(\"SET extension_directory='$DUCKDB_EXTENSION_DIR'\"); c.execute('LOAD httpfs'); print('baked httpfs OK')"

# ---

FROM base AS development
CMD ["uvicorn", "geodeploy.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---

FROM base AS production
COPY . .
RUN alembic upgrade head || true
CMD ["uvicorn", "geodeploy.main:app", "--host", "0.0.0.0", "--port", "8000"]
