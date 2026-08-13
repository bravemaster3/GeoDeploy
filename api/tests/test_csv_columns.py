"""CSV header → Postgres column names (`tasks.csv_import.safe_columns`).

Found by uploading a 500-row CSV to a live instance with the CLI: the import ran to 45% and then
failed with *column "id" specified more than once*. The header was ordinary — `id,name,lon,lat,pop`
— and nothing in the message suggested that `id` is a name the destination table adds for itself.

The failure mode is what makes this worth pinning: the upload is ACCEPTED, the job is queued, and
the error arrives from Postgres minutes later naming a duplicate the user never wrote. A rename is
the right trade — a column called `id_1` is visible and explainable, a lost import is not.
"""
from geodeploy.tasks.csv_import import RESERVED_COLUMNS, safe_columns


class TestReservedNames:
    def test_an_id_column_is_renamed_rather_than_colliding(self):
        """`CREATE TABLE (id serial primary key, …)` — the table already has an `id`."""
        assert safe_columns(["id", "name"]) == {"id": "id_1", "name": "name"}

    def test_a_geom_column_too(self):
        """Same reason: the geometry column is injected, not read from the CSV."""
        assert safe_columns(["geom", "value"]) == {"geom": "geom_1", "value": "value"}

    def test_both_at_once(self):
        mapping = safe_columns(["id", "geom"])
        assert mapping == {"id": "id_1", "geom": "geom_1"}
        assert not set(mapping.values()) & set(RESERVED_COLUMNS)

    def test_case_and_punctuation_still_collide_after_sanitising(self):
        """`ID` and `Geom.` sanitise to the reserved names, so they must be caught too — the check
        has to happen AFTER `safe_name`, not on the raw header."""
        assert safe_columns(["ID"])["ID"] == "id_1"
        assert safe_columns(["Geom."])["Geom."] == "geom_1"

    def test_a_column_actually_called_id_1_does_not_then_collide(self):
        """Renaming must not create the very collision it is avoiding."""
        mapping = safe_columns(["id", "id_1"])
        assert len(set(mapping.values())) == 2
        assert "id" not in mapping.values()


class TestOrdinaryHeaders:
    def test_unremarkable_names_are_left_alone(self):
        assert safe_columns(["name", "lon", "lat", "pop"]) == {
            "name": "name", "lon": "lon", "lat": "lat", "pop": "pop"}

    def test_headers_that_sanitise_to_the_same_name_are_de_duplicated(self):
        """`Name` and `name!` both reduce to `name`; the second must not shadow the first."""
        mapping = safe_columns(["Name", "name!"])
        assert len(set(mapping.values())) == 2

    def test_sanitising_survives_punctuation_digits_and_blanks(self):
        mapping = safe_columns(["Population (2024)", "2024", "  "])
        assert mapping["Population (2024)"] == "population__2024"   # space and '(' each map to _
        assert mapping["2024"].startswith("_")          # a name may not start with a digit
        assert mapping["  "]                            # blank falls back rather than empty

    def test_every_name_is_unique(self):
        """The whole point of the mapping: these become one CREATE TABLE column list."""
        fields = ["id", "ID", "id_1", "geom", "geometry", "name", "name"]
        values = list(safe_columns(fields).values())
        assert len(values) == len(set(values))
