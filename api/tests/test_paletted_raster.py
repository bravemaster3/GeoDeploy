"""A classified raster keeps its per-value colours.

`colormap` names a GRADIENT, and a gradient cannot describe a classification: interpolating between
class 3 and class 4 means nothing. Land cover, soil types and a QGIS paletted layer all give each
pixel VALUE its own colour, so the mapping itself has to travel — TiTiler takes it as JSON.

The cap matters as much as the feature: this mapping rides in the URL of every single tile request,
so an unbounded one would put kilobytes on the wire per tile and eventually exceed what a proxy
will accept.
"""
import json
from urllib.parse import parse_qs, urlparse

import pytest
from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import RasterLayer, User
from geodeploy.services.titiler import MAX_COLOR_CLASSES, get_tile_url

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
OWNER = 1
_next_id = [500]


@pytest.fixture
def auth():
    return {"Authorization": "Bearer " + jwt.encode({"sub": str(OWNER)},
                                                    get_settings().secret_key, algorithm="HS256")}


async def _seed_raster(db, style: dict, public: bool = False) -> RasterLayer:
    """One ready raster owned by OWNER, carrying `style` as its default style."""
    existing = await db.get(User, OWNER)
    if existing is None:
        db.add(User(id=OWNER, email="o@example.com", name="O", hashed_password=_pwd.hash("pw"),
                    is_admin=True, role="owner"))
        await db.flush()
    _next_id[0] += 1
    layer = RasterLayer(id=_next_id[0], user_id=OWNER, name="Land cover",
                        s3_key="rasters/1/x/landcover.tif", status="ready", band_count=1,
                        visibility="public" if public else "organization", is_public=public,
                        default_style=json.dumps(style))
    db.add(layer)
    await db.commit()
    await db.refresh(layer)
    return layer


class _S:
    storage_bucket = "geodeploy"


def _params(url):
    return parse_qs(urlparse(url).query)


CLASSES = [{"value": 1, "color": "#ff0000"},
           {"value": 2, "color": "#00ff00"},
           {"value": 3, "color": "#0000ff"}]


def test_classes_become_an_explicit_colormap():
    url = get_tile_url("r/x.tif", color_classes=CLASSES, settings=_S())
    cm = json.loads(_params(url)["colormap"][0])
    assert cm == {"1": [255, 0, 0, 255], "2": [0, 255, 0, 255], "3": [0, 0, 255, 255]}
    assert "colormap_name" not in _params(url)


def test_explicit_classes_beat_a_named_ramp():
    """Both set is not a conflict to resolve arbitrarily: the classes are the more specific
    statement, and a gradient over classified data is simply wrong."""
    url = get_tile_url("r/x.tif", colormap="viridis", color_classes=CLASSES, settings=_S())
    assert "colormap_name" not in _params(url)
    assert "colormap" in _params(url)


def test_a_named_ramp_still_works_when_there_are_no_classes():
    url = get_tile_url("r/x.tif", colormap="viridis", settings=_S())
    assert _params(url)["colormap_name"] == ["viridis"]


def test_alpha_is_preserved_and_defaulted():
    """A class can be transparent — "no data" in a land-cover raster usually is."""
    url = get_tile_url("r/x.tif", settings=_S(),
                       color_classes=[{"value": 0, "color": "#00000000"},
                                      {"value": 1, "color": "#abcdef"}])
    cm = json.loads(_params(url)["colormap"][0])
    assert cm["0"] == [0, 0, 0, 0]          # fully transparent, as written
    assert cm["1"] == [171, 205, 239, 255]  # opaque by default


def test_junk_entries_are_skipped_not_fatal():
    """One malformed class must not take the whole layer off the map."""
    url = get_tile_url("r/x.tif", settings=_S(), color_classes=[
        {"value": 1, "color": "#ff0000"},
        {"value": "not a number", "color": "#00ff00"},
        {"value": 3, "color": "nonsense"},
        {"color": "#0000ff"},               # no value
        "not even a dict",
    ])
    cm = json.loads(_params(url)["colormap"][0])
    assert cm == {"1": [255, 0, 0, 255]}


def test_no_usable_classes_falls_back_to_the_named_ramp():
    url = get_tile_url("r/x.tif", colormap="magma", settings=_S(),
                       color_classes=[{"value": None, "color": None}])
    assert _params(url)["colormap_name"] == ["magma"]


def test_the_mapping_is_capped():
    """It rides in EVERY tile URL. 256 covers a byte-valued classification, which is what this is
    for; past that the data is continuous in all but name."""
    many = [{"value": i, "color": "#010203"} for i in range(MAX_COLOR_CLASSES + 50)]
    cm = json.loads(_params(get_tile_url("r/x.tif", color_classes=many, settings=_S()))["colormap"][0])
    assert len(cm) == MAX_COLOR_CLASSES


def test_a_full_cap_url_stays_within_a_sane_request_line():
    """nginx's default large_client_header_buffers line limit is 8 kB. A tile URL at the cap has to
    fit inside it with room to spare, or every tile 414s and the layer silently never draws."""
    many = [{"value": i, "color": "#010203"} for i in range(MAX_COLOR_CLASSES)]
    url = get_tile_url("rasters/1/deadbeef/landcover.tif", color_classes=many, settings=_S())
    assert len(url) < 6000, f"tile URL is {len(url)} bytes"


def test_rgb_composites_ignore_classes():
    """Three bands are already colours; a per-value mapping has no meaning there."""
    url = get_tile_url("r/x.tif", bidx=[3, 2, 1], color_classes=CLASSES, settings=_S())
    assert "colormap" not in _params(url)


def test_hillshade_ignores_classes():
    """A hillshade is a finished relief image — the same reason `colormap` is dropped for it."""
    url = get_tile_url("r/x.tif", algorithm="hillshade", color_classes=CLASSES, settings=_S())
    assert "colormap" not in _params(url)
    assert _params(url)["algorithm"] == ["hillshade"]


# ── The two API-side gaps a classified raster exposes ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_saving_a_style_does_not_wipe_fields_the_client_never_sent(client, db, auth):
    """The web UI's raster panel does not edit `color_classes`. A full-replace PUT therefore
    destroyed a paletted raster's palette as a side effect of changing the opacity — data loss
    caused by a client being older than the schema, which is the normal state of affairs."""
    layer = await _seed_raster(db, {"colormap": "viridis", "rescale": "0,100",
                                    "color_classes": [{"value": 1, "color": "#ff0000"}]})
    r = await client.put(f"/api/data/raster/{layer.id}/default-style",
                         json={"opacity": 0.5}, headers=auth)
    assert r.status_code == 200
    await db.refresh(layer)
    stored = json.loads(layer.default_style)
    assert stored["color_classes"] == [{"value": 1, "color": "#ff0000"}], "the palette was wiped"
    assert stored["colormap"] == "viridis"
    assert stored["opacity"] == 0.5


@pytest.mark.asyncio
async def test_a_field_can_still_be_cleared_on_purpose(client, db, auth):
    """Merging must not make a field unclearable — sending it as null is the way to mean it."""
    layer = await _seed_raster(db, {"color_classes": [{"value": 1, "color": "#ff0000"}]})
    r = await client.put(f"/api/data/raster/{layer.id}/default-style",
                         json={"color_classes": None}, headers=auth)
    assert r.status_code == 200
    await db.refresh(layer)
    assert json.loads(layer.default_style)["color_classes"] is None


@pytest.mark.asyncio
async def test_the_legend_says_a_classified_raster_is_not_a_ramp(client, db, auth):
    """Reporting `ramp: true` for classified data makes every renderer draw a gradient over land
    cover codes — a legend that disagrees with the map it describes."""
    layer = await _seed_raster(db, {"color_classes": [
        {"value": 1, "color": "#ff0000"}, {"value": 2, "color": "#00ff00"}]}, public=True)
    body = (await client.get(f"/api/data/raster/{layer.id}/legend")).json()
    assert body["ramp"] is False
    assert [e["value"] for e in body["entries"]] == [1, 2]
    assert body["color_classes"]


@pytest.mark.asyncio
async def test_a_continuous_raster_is_still_a_ramp(client, db, auth):
    layer = await _seed_raster(db, {"colormap": "viridis", "rescale": "0,100"}, public=True)
    body = (await client.get(f"/api/data/raster/{layer.id}/legend")).json()
    assert body["ramp"] is True
    assert body["entries"] == []
    assert body["colormap"] == "viridis"


# ── Reversing a palette ───────────────────────────────────────────────────────────────────────
# Not cosmetic: a ramp read the wrong way inverts the map's meaning. Depth, deprivation and error
# all conventionally run dark-for-high, which is the opposite of most sequential ramps.

def test_a_named_ramp_reverses_with_the_matplotlib_suffix():
    url = get_tile_url("r/x.tif", colormap="viridis", colormap_reverse=True, settings=_S())
    assert _params(url)["colormap_name"] == ["viridis_r"]


def test_reversing_is_idempotent_on_an_already_suffixed_name():
    """A stored `viridis_r` must not become `viridis_r_r`, which names no colormap at all."""
    url = get_tile_url("r/x.tif", colormap="viridis_r", colormap_reverse=True, settings=_S())
    assert _params(url)["colormap_name"] == ["viridis_r"]


def test_not_reversing_a_suffixed_name_unwinds_it():
    """The flag is the single source of truth, so `viridis_r` + reverse=False is forward."""
    url = get_tile_url("r/x.tif", colormap="viridis_r", colormap_reverse=False, settings=_S())
    assert _params(url)["colormap_name"] == ["viridis"]


def test_explicit_classes_reverse_by_re_pairing_the_colours():
    """There is no name to suffix, so the COLOURS move and the values stay put."""
    url = get_tile_url("r/x.tif", colormap_reverse=True, settings=_S(), color_classes=[
        {"value": 1, "color": "#ff0000"},
        {"value": 2, "color": "#00ff00"},
        {"value": 3, "color": "#0000ff"}])
    cm = json.loads(_params(url)["colormap"][0])
    assert cm["1"] == [0, 0, 255, 255]      # was blue at the top, now at the bottom
    assert cm["2"] == [0, 255, 0, 255]      # the middle is its own mirror
    assert cm["3"] == [255, 0, 0, 255]


def test_reverse_is_inert_without_a_palette():
    url = get_tile_url("r/x.tif", colormap_reverse=True, settings=_S())
    assert "colormap_name" not in _params(url) and "colormap" not in _params(url)
