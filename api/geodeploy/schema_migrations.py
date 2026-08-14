"""Additive schema migrations for the state/PostGIS database.

Lives in its own module because TWO processes need it. The API applies it at startup, and the
Celery worker re-applies it after a RESTORE: `pg_restore --clean` drops and recreates every table
from the dump, so the schema becomes the SNAPSHOT'S schema and every column added since that
backup disappears. That is a real regression, not a theoretical one — restoring a backup taken
before `portals.thumbnail_url` existed removed the column from a running instance, and publishing
a portal then failed to record its thumbnail while appearing to succeed.

Importing `main` from a task would pull in the whole FastAPI app, so the list moved here instead.
"""

# `Base.metadata.create_all` creates missing TABLES but never adds a column to a table that already
# exists, so a new column would break every EXISTING install (queries select a column the database
# does not have) while working perfectly on a fresh one. This list closes that gap.
#
# Rules for adding to it:
#   * `ADD COLUMN IF NOT EXISTS` only — this runs on EVERY start and must be a no-op once applied.
#   * Additive and nullable (or with a default). Never drop, rename or retype here: those need a
#     real migration with a data step, and a destructive statement running unattended on every boot
#     is how databases get lost.
#   * Postgres dialect. The SQLite-era `_apply_schema_migrations` below is dead code kept for
#     reference; do not extend it.
PG_MIGRATIONS = [
    # Portal card thumbnail: a snapshot of the published map, captured in the browser at publish.
    "ALTER TABLE portals ADD COLUMN IF NOT EXISTS thumbnail_url VARCHAR(512)",
    # int4 → int8 on every byte count. The rule above says "never retype", and this is the exception
    # it allows for: WIDENING an integer is lossless, cannot fail on existing data, and the
    # alternative is a live bug. int4 stops at 2_147_483_647 — 2.1 GB — which a whole-instance
    # backup passes immediately and a single raster passes eventually. On an install that overflowed,
    # `backup_runs.size_bytes` raised "integer out of range" at the END of a backup that had already
    # been written correctly, so a GOOD backup was recorded as a failure.
    #
    # Guarded by information_schema so it is a true no-op once applied: a bare ALTER TYPE would take
    # an ACCESS EXCLUSIVE lock on every boot for no reason.
    """DO $$ BEGIN
         IF EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'backup_runs' AND column_name = 'size_bytes'
                      AND data_type = 'integer') THEN
           ALTER TABLE backup_runs ALTER COLUMN size_bytes TYPE BIGINT;
         END IF;
       END $$""",
    """DO $$ BEGIN
         IF EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'vector_layers' AND column_name = 'file_size'
                      AND data_type = 'integer') THEN
           ALTER TABLE vector_layers ALTER COLUMN file_size TYPE BIGINT;
         END IF;
       END $$""",
    """DO $$ BEGIN
         IF EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'raster_layers' AND column_name = 'file_size'
                      AND data_type = 'integer') THEN
           ALTER TABLE raster_layers ALTER COLUMN file_size TYPE BIGINT;
         END IF;
       END $$""",
    # The anonymous instance index (`GET /api/public`). Default TRUE: publishing a portal as
    # 'public' already says it may be seen, and a geoportal that cannot be browsed is a filing
    # cabinet. An operator who wants their public portal reachable-but-unlisted turns this off.
    "ALTER TABLE setup_config ADD COLUMN IF NOT EXISTS public_index_enabled BOOLEAN DEFAULT TRUE",
    # Raster low-zoom cost, read from the COG's overview pyramid at ingest (issue #17). Nullable with
    # NO default on purpose: NULL means "not measured", which is different from False and is what
    # keeps every existing layer on the extent heuristic until it is re-ingested.
    "ALTER TABLE raster_layers ADD COLUMN IF NOT EXISTS low_zoom_ok BOOLEAN",
]
