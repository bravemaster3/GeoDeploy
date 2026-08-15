"""A 3D shapefile must import, not fail at the last step.

Uploading one produced:

    Geometry has Z dimension but column does not

`geometry(Geometry, srid)` is a TWO-dimensional type. The staging table is untyped and took the
features happily, so nothing looked wrong until the INSERT into the real table — which is most
shapefiles digitised from a DEM, and every GPS track.

Verified against PostGIS 16/3.4 as well as here: the old column type reproduces that exact message,
and the new one loads a file MIXING 2D and 3D features, keeping srid 4326 and reporting
coord_dimension 3 in `geometry_columns` (which Martin reads to serve tiles).
"""
from geodeploy.tasks.vector_ingest import _geom_column, _geom_value


def test_a_layer_with_z_gets_a_three_dimensional_column():
    assert _geom_column(4326, has_z=True) == "geometry(GeometryZ,4326)"
    assert _geom_column(3006, has_z=True) == "geometry(GeometryZ,3006)"


def test_a_flat_layer_is_unchanged():
    """The 2D path must stay exactly as it was — this fix is additive or it is a regression."""
    assert _geom_column(4326, has_z=False) == "geometry(Geometry,4326)"
    assert _geom_value("ST_SetSRID(geom, 4326)", has_z=False) == "ST_SetSRID(geom, 4326)"


def test_z_forces_every_row_to_three_dimensions():
    """One file can mix 2D and 3D features, and the typmod rejects the 2D ones just as firmly."""
    assert _geom_value("ST_SetSRID(geom, 4326)", has_z=True) == "ST_Force3D(ST_SetSRID(geom, 4326))"


def test_the_mercator_clamp_survives_the_wrapping():
    """The pole clamp is a CASE expression; Force3D must wrap it whole rather than mangle it."""
    from geodeploy.tasks.vector_ingest import _store_geom_sql

    clamped = _store_geom_sql(4326)
    wrapped = _geom_value(clamped, has_z=True)
    assert wrapped.startswith("ST_Force3D(CASE WHEN") and wrapped.endswith("END)")
    assert "ST_Intersection" in wrapped        # the clamp is still in there, not replaced


def test_srid_is_preserved_in_the_typmod():
    """Losing the srid here would leave Martin reading srid 0 and serving no tiles at all."""
    for srid in (4326, 3857, 3006, 32633):
        assert str(srid) in _geom_column(srid, has_z=True)
        assert str(srid) in _geom_column(srid, has_z=False)
