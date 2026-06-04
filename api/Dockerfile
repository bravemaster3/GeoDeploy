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

# ---

FROM base AS development
CMD ["uvicorn", "geodeploy.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---

FROM base AS production
COPY . .
RUN alembic upgrade head || true
CMD ["uvicorn", "geodeploy.main:app", "--host", "0.0.0.0", "--port", "8000"]
