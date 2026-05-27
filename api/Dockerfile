FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

ENV GDAL_VERSION=3.8 \
    CPLUS_INCLUDE_PATH=/usr/include/gdal \
    C_INCLUDE_PATH=/usr/include/gdal \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---

FROM base AS development
CMD ["uvicorn", "geodeploy.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---

FROM base AS production
COPY . .
RUN alembic upgrade head || true
CMD ["uvicorn", "geodeploy.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
