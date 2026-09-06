"""Two ways a multi-layer GeoPackage upload died on a FRESH instance.

Both were reported from a real upload of a QGIS-packaged GeoPackage, and both are about the raw SQL
that creates a layer row per layer of the file — a path the ORM's defaults never touch.

1. `NotNullViolation: null value in column "visibility"`. `visibility` and `is_public` are
   `nullable=False` with defaults declared in PYTHON, which SQLAlchemy applies only to an ORM
   insert. `_create_sibling` writes raw SQL, so both arrived NULL. It bit only new installs: a
   database old enough to have got the column from `_apply_schema_migrations` got it as a plain
   nullable `ALTER TABLE ... ADD COLUMN`.

2. `relation "…_svg_marker_0645" already exists`. TWO separate identifier-length bugs, and the
   second is the one that actually threw:

   * table names were `f"{slug}_{uuid4().hex[:6]}"` with nothing keeping them inside Postgres's
     63-character limit — and Postgres truncates the END, which is exactly where the uniqueness
     lives, so a 58-character slug kept only FOUR of the six random characters;
   * the geometry index was named `table + "_geom_idx"`. For a table name at the limit that
     truncates back to THE TABLE'S OWN NAME, so `CREATE INDEX` collided with the table it was
     indexing — deterministically, on every attempt, which is why deleting the layers and
     retrying never helped.

These are cheap tests for an expensive failure: the arithmetic is a pure function, and the index
name is one query against a real Postgres.
"""
import pytest
from sqlalchemy import text

from geodeploy.services import postgis

#: The exact layer name from the report, slugified. 58 characters, which put the generated table
#: name two over the limit — near enough the boundary that no smaller example would have failed.
REPORTED = "QGIS packaged layers d/s canal - notable features - svg marker"


class TestTableNamesFitInAnIdentifier:
    def test_the_reported_name_no_longer_overflows(self):
        name = postgis.unique_table_name(REPORTED)
        assert len(name) <= postgis.MAX_IDENTIFIER, name

    def test_the_whole_random_suffix_survives(self):
        # The actual defect: the name was 65 characters, Postgres kept 63, and two of the six
        # random characters were thrown away — so uniqueness became a 1-in-65,536 coin flip.
        name = postgis.unique_table_name(REPORTED)
        head, _, suffix = name.rpartition("_")
        assert len(suffix) == postgis.TABLE_SUFFIX_LEN, name
        assert head, name

    def test_two_calls_for_the_same_long_name_differ(self):
        made = {postgis.unique_table_name(REPORTED) for _ in range(200)}
        assert len(made) == 200

    def test_a_prefix_is_counted_too(self):
        # `gpq_` and `csv_` are added by the callers, so the budget has to include them — the CSV
        # path was worse than the GeoPackage one: `safe_name` truncated to 60, then a 4-character
        # prefix and a 7-character suffix took it to 71.
        for prefix in ("gpq_", "csv_"):
            name = postgis.unique_table_name(REPORTED, prefix=prefix)
            assert name.startswith(prefix), name
            assert len(name) <= postgis.MAX_IDENTIFIER, name
            assert len(name.rpartition("_")[2]) == postgis.TABLE_SUFFIX_LEN, name

    def test_a_short_name_is_left_readable(self):
        name = postgis.unique_table_name("Rivers")
        assert name.startswith("rivers_")
        assert len(name) == len("rivers_") + postgis.TABLE_SUFFIX_LEN

    def test_a_name_with_nothing_usable_falls_back(self):
        name = postgis.unique_table_name("---", fallback="layer_3")
        assert name.startswith("layer_3_")

    def test_an_index_named_after_a_long_table_would_collide_with_it(self):
        # The arithmetic behind the reported error, stated plainly. This is what the code used to
        # do, and why `CREATE INDEX` had to be left unnamed.
        table = "q" * postgis.MAX_IDENTIFIER
        assert (table + "_geom_idx")[:postgis.MAX_IDENTIFIER] == table

    def test_no_doubled_underscore_where_the_cut_lands(self):
        # A cut landing on a word boundary used to leave `…features__ab12cd`.
        for candidate in (REPORTED, "a_" * 40, "x" * 80):
            assert "__" not in postgis.unique_table_name(candidate)


@pytest.mark.asyncio
async def test_a_sibling_row_inherits_the_parents_sharing(db):
    """The `visibility` NOT NULL violation, against the real Postgres schema."""
    from geodeploy.models import User, VectorLayer
    from geodeploy.tasks.vector_ingest import _create_sibling

    user = User(email="sibling@test.invalid", name="Sibling", hashed_password="x")
    db.add(user)
    await db.flush()

    parent = VectorLayer(user_id=user.id, name="multi.gpkg", table_name="multi_aa11bb",
                         schema_name="geodeploy_u{0}".format(user.id), file_size=123,
                         visibility="public", is_public=True, status="processing")
    db.add(parent)
    await db.commit()

    layer_id, job_id = _create_sibling(parent.id, "Second layer", parent.schema_name,
                                       "second_layer_cc22dd")
    assert layer_id and job_id

    row = (await db.execute(text(
        "SELECT name, visibility, is_public, user_id, file_size, status, storage_backend "
        "FROM vector_layers WHERE id = :i"), {"i": layer_id})).mappings().one()
    # Inherited, not defaulted: a user who published the file meant all of its layers, and quietly
    # making layer two organization-only would be a sharing decision nobody made.
    assert row["visibility"] == "public"
    assert row["is_public"] is True
    assert row["user_id"] == user.id
    assert row["status"] == "processing"
    assert row["storage_backend"] == "postgis"


@pytest.mark.asyncio
async def test_a_private_parent_gives_a_private_sibling(db):
    from geodeploy.models import User, VectorLayer
    from geodeploy.tasks.vector_ingest import _create_sibling

    user = User(email="sibling2@test.invalid", name="Sibling Two", hashed_password="x")
    db.add(user)
    await db.flush()
    parent = VectorLayer(user_id=user.id, name="private.gpkg", table_name="private_aa11bb",
                         schema_name="geodeploy_u{0}".format(user.id),
                         visibility="private", is_public=False, status="processing")
    db.add(parent)
    await db.commit()

    layer_id, _ = _create_sibling(parent.id, "Second", parent.schema_name, "second_ee33ff")
    row = (await db.execute(text("SELECT visibility, is_public FROM vector_layers WHERE id = :i"),
                            {"i": layer_id})).mappings().one()
    assert row["visibility"] == "private"
    assert row["is_public"] is False


@pytest.mark.asyncio
async def test_the_geometry_index_does_not_collide_with_its_own_table(db):
    """`CREATE INDEX` on a maximum-length table name, against real Postgres.

    A pure-function test cannot show this: the collision only exists because the SERVER truncates,
    and the whole point is that it does so without complaining. So this creates a table whose name
    fills the identifier and indexes it the way the ingest now does — unnamed.
    """
    schema = "geodeploy_idxtest"
    table = postgis.unique_table_name(REPORTED)
    await db.execute(text('CREATE SCHEMA IF NOT EXISTS "{0}"'.format(schema)))
    await db.execute(text('DROP TABLE IF EXISTS "{0}"."{1}"'.format(schema, table)))
    await db.execute(text('CREATE TABLE "{0}"."{1}" (id serial primary key, geom geometry)'
                          .format(schema, table)))
    # Unnamed. Naming it `table + "_geom_idx"` here raises
    # `relation "…" already exists`, because that truncates back to `table`.
    await db.execute(text('CREATE INDEX ON "{0}"."{1}" USING GIST (geom)'.format(schema, table)))

    made = (await db.execute(text(
        "SELECT indexname FROM pg_indexes WHERE schemaname = :s AND tablename = :t"),
        {"s": schema, "t": table})).scalars().all()
    # The primary key and the GIST index, and neither is the table's own name.
    assert len(made) == 2, made
    assert table not in made, made
    await db.execute(text('DROP SCHEMA "{0}" CASCADE'.format(schema)))
    await db.commit()

