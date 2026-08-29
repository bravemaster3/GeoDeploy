"""V-16 Dashboard archetype: the config resolver and the layout defaults.

`services/dashboard.resolve_dashboard` is the archetype's single validator — the published runtime
(`templates/shared/dashboard.js`) does no checking of its own and is written against the invariants
this module guarantees. So these tests are that contract written down: unique DOM-safe ids, known
widget types, live action targets, in-grid layout, and normalisation rather than rejection.

Pure functions, no database — like `test_catalog_extra_layers.py`.
"""
from geodeploy.services.dashboard import (
    LINKED_FILTER_CAPS, DEFAULT_LINKED_FILTER_CAP,
    DEFAULT_MAP_TOOLS, DEFAULT_TOL_PX, GRID_COLS, MAX_TOL_PX, WIDGET_TYPES,
    dashboard_layer_refs, resolve_dashboard,
)
from geodeploy.services.portal_generator import resolve_layout


def _w(wid, wtype, **kw):
    out = {"id": wid, "type": wtype}
    out.update(kw)
    return out


# ── resolve_layout: the fourth archetype ────────────────────────────────────────────────────────

def test_dashboard_archetype_defaults():
    """A dashboard is a widget grid, not a map page — so the dashboard panel is on.

    The layer catalog is ALSO on, and that reversed a previous default. It carries the LEGEND: the
    legend has never been a panel of its own, it is drawn inside each layer card and opens from the
    toggle at the top of the control cluster. With the list off, a dashboard had `legend: true` and
    nowhere to draw one, and no way to read what the colours on its map meant. It starts collapsed,
    so the cost is one button in a cluster that already has several."""
    r = resolve_layout({"archetype": "dashboard"})
    assert r["archetype"] == "dashboard"
    assert r["panels"]["dashboard"] is True
    assert r["panels"]["layerCatalog"] is True
    assert r["panels"]["legend"] is True
    assert r["panels"]["story"] is False
    # The list is still available to an author who turns it on — and when they do it must FLOAT,
    # because the grid owns the page width.
    assert r["regions"]["layerList"]["mode"] == "floating"
    assert r["regions"]["dashboard"]["density"] == "comfortable"


def test_dashboard_overrides_merge_like_every_other_archetype():
    # `layerCatalog: False` is the override worth testing now that the DEFAULT is True — overriding
    # a flag to the value it already has proves nothing about the merge.
    r = resolve_layout({"archetype": "dashboard",
                        "regions": {"dashboard": {"density": "compact"}},
                        "panels": {"layerCatalog": False}})
    assert r["regions"]["dashboard"]["density"] == "compact"
    assert r["regions"]["dashboard"]["mapControls"] is True   # untouched key survives the merge
    assert r["panels"]["layerCatalog"] is False
    assert r["panels"]["dashboard"] is True                   # untouched panel survives too


# ── resolve_dashboard: nothing to render ────────────────────────────────────────────────────────

def test_no_config_is_none():
    """None rather than an empty object, so 'is this a dashboard' is ONE test in the runtime."""
    assert resolve_dashboard(None) is None
    assert resolve_dashboard({}) is None
    assert resolve_dashboard({"widgets": []}) is None
    assert resolve_dashboard({"widgets": "not a list"}) is None


def test_every_widget_dropped_is_none():
    """A config whose widgets are all unknown types renders nothing, so it bakes as nothing."""
    assert resolve_dashboard({"widgets": [_w("a", "hologram"), _w("b", "teleporter")]}) is None


# ── the invariants the runtime relies on ────────────────────────────────────────────────────────

def test_unknown_type_is_dropped_not_raised():
    """A dashboard published from a NEWER builder must still render what this server understands.
    Failing the whole publish over one unrecognised widget is the wrong trade."""
    out = resolve_dashboard({"widgets": [_w("a", "indicator"), _w("b", "hologram")]})
    assert [w["id"] for w in out["widgets"]] == ["a"]


def test_duplicate_and_unsafe_ids_are_replaced():
    """Widget ids land in DOM ids and CSS selectors in the published page."""
    out = resolve_dashboard({"widgets": [
        _w("a", "indicator"), _w("a", "indicator"), _w("has space", "indicator"),
        _w('"><script>', "indicator"),
    ]})
    ids = [w["id"] for w in out["widgets"]]
    assert len(set(ids)) == 4
    assert all(i.replace("-", "").replace("_", "").isalnum() for i in ids)


def test_action_targets_must_exist_and_be_able_to_listen():
    out = resolve_dashboard({"widgets": [
        _w("src", "chart", actions={"filters": ["ind", "ghost", "src", "sel"]}),
        _w("ind", "indicator"),
        # A selector can never be a target — it is an input, and letting other widgets narrow its
        # options would make the control move under the hand using it.
        _w("sel", "selector"),
    ]})
    src = next(w for w in out["widgets"] if w["id"] == "src")
    assert src["actions"]["filters"] == ["ind"]


def test_a_target_that_opted_out_is_unwired():
    out = resolve_dashboard({"widgets": [
        _w("src", "chart", actions={"filters": ["ind"]}),
        _w("ind", "indicator", actions={"listens": False}),
    ]})
    src = next(w for w in out["widgets"] if w["id"] == "src")
    assert src["actions"]["filters"] == []


def test_target_only_widgets_never_publish():
    """Raster Stats has no attribute table of its own, so it can only ever be driven."""
    out = resolve_dashboard({"widgets": [
        _w("rs", "rasterstats", actions={"filters": ["ind"]}),
        _w("ind", "indicator"),
    ]})
    rs = next(w for w in out["widgets"] if w["id"] == "rs")
    assert rs["actions"]["filters"] == []
    assert WIDGET_TYPES["rasterstats"]["source"] is False
    assert WIDGET_TYPES["rasterstats"]["channel"] == "geom"


def test_source_only_widgets_never_listen():
    out = resolve_dashboard({"widgets": [_w("sel", "selector", actions={"listens": True})]})
    assert out["widgets"][0]["actions"]["listens"] is False


def test_layout_is_clamped_into_the_grid():
    out = resolve_dashboard({"widgets": [
        _w("a", "indicator", layout={"x": 10, "y": 3, "w": 9, "h": 99}),
        _w("b", "indicator", layout={"x": -4, "y": -2, "w": 0, "h": 0}),
        _w("c", "indicator"),          # no layout at all — stacked, not dropped
    ]})
    a, b, c = out["widgets"]
    assert a["layout"]["x"] + a["layout"]["w"] <= GRID_COLS
    assert b["layout"]["x"] >= 0 and b["layout"]["y"] >= 0 and b["layout"]["w"] >= 2
    assert c["layout"]["w"] > 0 and c["layout"]["h"] > 0


# ── data bindings ───────────────────────────────────────────────────────────────────────────────

def test_unbound_widget_is_legal():
    """Exactly what a preset template ships: a starting layout whose layer ids do not exist yet.
    The renderer draws a labelled placeholder, and the builder binds it."""
    out = resolve_dashboard({"widgets": [_w("a", "indicator", dataSource={"op": "sum"})]})
    assert out["widgets"][0]["dataSource"] is None


def test_aggregate_without_a_field_falls_back_to_count():
    """A sum with no column to sum cannot be answered. A true count beats a red box: the author
    sees a number where they expected a total and fixes the binding."""
    out = resolve_dashboard({"widgets": [
        _w("a", "indicator", dataSource={"layerType": "vector", "layerId": 3, "op": "sum"})]})
    assert out["widgets"][0]["dataSource"]["op"] == "count"


def test_map_keeps_its_tools_even_with_no_selection_layer():
    """The map is useful entirely unbound — polygon and box draw need no layer at all."""
    out = resolve_dashboard({"widgets": [_w("m", "map", dataSource={"tools": ["polygon", "nope"]})]})
    ds = out["widgets"][0]["dataSource"]
    assert ds["tools"] == ["polygon"]
    assert "layerId" not in ds


def test_the_click_radius_is_pixels_and_survives_an_unbound_map():
    """`tolPx` is the map's hit radius in SCREEN pixels, and it must exist even with no selection
    layer named — a click now falls through to the portal's other vector layers, so the radius
    applies to a map that binds nothing of its own.

    The bug this pins: the radius used to be `tol` in DEGREES, defaulting to 0, and it was written
    only inside the layer-bound branch. Zero degrees makes the server's pick an exact intersection
    with a zero-area point, which can only ever land on a polygon — so clicking a POINT layer
    resolved to nothing at every zoom, with no error to show for it."""
    out = resolve_dashboard({"widgets": [_w("m", "map", dataSource={"tools": ["click"]})]})
    ds = out["widgets"][0]["dataSource"]
    assert ds["tolPx"] == DEFAULT_TOL_PX
    assert ds["tolPx"] > 0


def test_the_click_radius_is_clamped_and_zero_is_legal():
    """0 is exact and is all a polygon layer needs, so it must survive rather than being treated as
    "unset" and replaced by the default."""
    def tol(value):
        out = resolve_dashboard({"widgets": [_w("m", "map", dataSource={"tolPx": value})]})
        return out["widgets"][0]["dataSource"]["tolPx"]

    assert tol(0) == 0
    assert tol(9) == 9
    assert tol(9999) == MAX_TOL_PX
    assert tol(-4) == 0
    assert tol("wide") == DEFAULT_TOL_PX      # normalisation, not rejection


def test_following_a_linked_filter_is_opt_in_and_defaults_off():
    """The map can be told to follow a filter that reached it through a declared relation, and it is
    OFF unless an author asks for it.

    Why it has to default off: the widgets follow a relation as a subquery the engine runs, but the
    map filters in the browser against tiles it already holds, so the only way it can follow is to
    fetch the matching KEYS and test against those. That works for a narrow selection and is capped
    for a broad one — and a map that narrows for small selections and silently stops for large ones
    is a worse thing to hand someone unasked-for than a map that never claimed to narrow. Past the
    cap the runtime leaves the layer whole and says so on the map."""
    out = resolve_dashboard({"widgets": [_w("m", "map", dataSource={"tools": ["click"]})]})
    assert out["widgets"][0]["dataSource"]["linkedFilter"] is False

    on = resolve_dashboard({"widgets": [_w("m", "map", dataSource={"linkedFilter": True})]})
    assert on["widgets"][0]["dataSource"]["linkedFilter"] is True
    assert on["widgets"][0]["dataSource"]["linkedFilterCap"] == DEFAULT_LINKED_FILTER_CAP

    # Present even on a map that binds no selection layer: it governs what the map DRAWS, which is a
    # different question from what a click hit-tests against.
    unbound = resolve_dashboard({"widgets": [_w("m", "map", dataSource={"linkedFilter": True})]})
    assert "layerId" not in unbound["widgets"][0]["dataSource"]
    assert unbound["widgets"][0]["dataSource"]["linkedFilter"] is True


def test_the_linked_filter_cap_is_a_choice_not_a_free_number():
    """The author picks how many keys the map will fetch from a short list, and anything else falls
    back to the default rather than being honoured.

    A free number would be the wrong control here: too large a value fails as a sluggish map and a
    large response on every filter change, never as an error, so there is nothing to tell an author
    they have gone too far. The offered list is also bounded by what the server will actually serve
    (`aggregate.MAX_KEYS`) — a cap the backend silently clamps is a cap that lies."""
    def cap(value):
        out = resolve_dashboard({"widgets": [
            _w("m", "map", dataSource={"linkedFilter": True, "linkedFilterCap": value})]})
        return out["widgets"][0]["dataSource"]["linkedFilterCap"]

    for choice in LINKED_FILTER_CAPS:
        assert cap(choice) == choice
    assert cap(999999) == DEFAULT_LINKED_FILTER_CAP     # not offered
    assert cap(3000) == DEFAULT_LINKED_FILTER_CAP       # plausible, still not offered
    assert cap("lots") == DEFAULT_LINKED_FILTER_CAP     # normalisation, not rejection
    assert cap(None) == DEFAULT_LINKED_FILTER_CAP


def test_no_offered_cap_exceeds_what_the_server_will_serve():
    """The regression this pins cost a shipped release-note-worthy bug.

    The map asked for 5 000 keys; `postgis_aggregate` clamped the limit to `MAX_GROUPS` (200,
    correct for a CHART) and reported `truncated` against 200. So the map stopped narrowing at 201
    matching features while telling the visitor it had passed 5 000. Two unrelated questions were
    sharing one ceiling. `MAX_KEYS` is the keys-only ceiling; every choice offered has to fit under
    it or the same lie comes back."""
    from geodeploy.services.aggregate import MAX_KEYS
    assert max(LINKED_FILTER_CAPS) <= MAX_KEYS
    assert DEFAULT_LINKED_FILTER_CAP in LINKED_FILTER_CAPS


def test_map_extent_is_offered_but_never_assumed():
    """`extent` republishes the viewport as the geometry filter on every pan. It is a switch, not a
    gesture, so it must be selectable — but a map that never named its tools must NOT get it, or
    every existing dashboard would start silently narrowing its widgets to the current view."""
    picked = resolve_dashboard({"widgets": [
        _w("m", "map", dataSource={"tools": ["click", "extent"]})]})
    assert picked["widgets"][0]["dataSource"]["tools"] == ["click", "extent"]

    defaulted = resolve_dashboard({"widgets": [_w("m", "map", dataSource={})]})
    assert "extent" not in defaulted["widgets"][0]["dataSource"]["tools"]
    assert defaulted["widgets"][0]["dataSource"]["tools"] == list(DEFAULT_MAP_TOOLS)

    # An explicit empty list still falls back rather than leaving a map with no way to select.
    empty = resolve_dashboard({"widgets": [_w("m", "map", dataSource={"tools": []})]})
    assert empty["widgets"][0]["dataSource"]["tools"] == list(DEFAULT_MAP_TOOLS)


def test_map_with_a_raster_selection_layer_is_rejected():
    """A click hit-tests features; a raster has none."""
    out = resolve_dashboard({"widgets": [
        _w("m", "map", dataSource={"layerType": "raster", "layerId": 2})]})
    assert "layerId" not in out["widgets"][0]["dataSource"]


def test_raster_stats_defaults_and_filters_unknown_statistics():
    out = resolve_dashboard({"widgets": [
        _w("a", "rasterstats", dataSource={"layerType": "raster", "layerId": 5,
                                           "stats": ["mean", "kurtosis"], "band": 900}),
        _w("b", "rasterstats", dataSource={"layerType": "raster", "layerId": 5}),
    ]})
    a, b = out["widgets"]
    assert a["dataSource"]["stats"] == ["mean"]
    assert a["dataSource"]["band"] == 64          # clamped, not passed through
    assert b["dataSource"]["stats"] == ["min", "max", "mean"]


def test_click_to_filter_needs_both_halves():
    """An indicator is only a source when the author says WHAT it stands for. Half the pair means
    a wired action that silently does nothing, so it is dropped."""
    out = resolve_dashboard({"widgets": [
        _w("a", "indicator", dataSource={"layerType": "vector", "layerId": 1,
                                         "filterField": "status", "filterValue": "open"}),
        _w("b", "indicator", dataSource={"layerType": "vector", "layerId": 1,
                                         "filterField": "status"}),
    ]})
    a, b = out["widgets"]
    assert a["dataSource"]["filterField"] == "status" and a["dataSource"]["filterValue"] == "open"
    assert "filterField" not in b["dataSource"]


def test_gauge_bands_are_sorted_and_clamped_into_the_dial():
    out = resolve_dashboard({"widgets": [
        _w("g", "gauge", dataSource={"layerType": "vector", "layerId": 1},
           style={"min": 0, "max": 10, "bands": [
               {"from": 8, "color": "#16a34a"},
               {"from": -5, "color": "#dc2626"},
               {"from": 99, "color": "#d97706"},
               {"from": 4, "color": "nonsense"},
           ]})]})
    bands = out["widgets"][0]["style"]["bands"]
    assert [b["from"] for b in bands] == [0, 4, 8, 10]
    assert bands[1]["color"] == "#3b82f6"        # a bad colour falls back, it does not drop the band


def test_gauge_range_cannot_be_inverted():
    out = resolve_dashboard({"widgets": [
        _w("g", "gauge", dataSource={"layerType": "vector", "layerId": 1},
           style={"min": 50, "max": 10})]})
    style = out["widgets"][0]["style"]
    assert style["max"] > style["min"]


def test_refresh_is_bounded():
    assert resolve_dashboard({"refresh": 999999, "widgets": [_w("a", "indicator")]})["refresh"] == 3600
    assert resolve_dashboard({"widgets": [_w("a", "indicator")]})["refresh"] == 0


# ── the layer set a dashboard exposes ───────────────────────────────────────────────────────────

def test_dashboard_layer_refs_finds_widget_only_layers():
    """This is what makes the public-read caches in routers/data/{vector,raster}.py correct: a
    Raster Stats widget can bind a COG the map never draws, and its endpoint has to answer for it."""
    out = resolve_dashboard({"widgets": [
        _w("m", "map", dataSource={"layerType": "vector", "layerId": 1}),
        _w("i", "indicator", dataSource={"layerType": "vector", "layerId": 7, "op": "count"}),
        _w("r", "rasterstats", dataSource={"layerType": "raster", "layerId": 9}),
        _w("d", "details"),
    ]})
    vectors, rasters = dashboard_layer_refs(out)
    assert vectors == {1, 7}
    assert rasters == {9}


def test_dashboard_layer_refs_ignores_an_unbound_map():
    out = resolve_dashboard({"widgets": [_w("m", "map", dataSource={"tools": ["bbox"]})]})
    assert dashboard_layer_refs(out) == (set(), set())


def test_dashboard_layer_refs_of_none_is_empty():
    assert dashboard_layer_refs(None) == (set(), set())


# ── the placeholder contract ────────────────────────────────────────────────────────────────────

def test_every_layout_placeholder_is_substituted():
    """Every `{{X}}` in the shared skeleton must be replaced by `build_portal_bundle`.

    The failure this prevents is specific and silent: a placeholder added to `layout.html` without a
    matching `.replace()` is written into the published page VERBATIM. Nothing errors, the portal
    still loads, and the literal text sits in the markup — which is exactly what would have happened
    to `{{DASHBOARD_JS}}` / `{{DASHBOARD_CSS}}` had the generator not been updated with the
    skeleton. Asserted over the repo's own files rather than by building a bundle, because
    `build_portal_bundle` reads the container's `/templates` mount.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    layout = (root / "templates" / "shared" / "layout.html").read_text(encoding="utf-8")
    generator = (root / "api" / "geodeploy" / "services" / "portal_generator.py").read_text(
        encoding="utf-8")

    placeholders = set(re.findall(r"\{\{([A-Z_]+)\}\}", layout))
    assert "DASHBOARD_JS" in placeholders and "DASHBOARD_CSS" in placeholders
    missing = [p for p in sorted(placeholders) if f'"{{{{{p}}}}}"' not in generator]
    assert not missing, f"layout.html placeholders never substituted: {missing}"


def test_the_dashboard_runtime_ships_beside_the_portal_runtime():
    """`portal_generator` reads `shared/dashboard.js` by name and portal.js calls `GD_DASHBOARD`.
    A rename on one side is a dashboard that silently renders nothing but a map."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    shared = root / "templates" / "shared"
    assert (shared / "dashboard.js").exists()
    assert (shared / "dashboard.css").exists()
    assert "window.GD_DASHBOARD" in (shared / "dashboard.js").read_text(encoding="utf-8")
    assert "GD_DASHBOARD" in (shared / "portal.js").read_text(encoding="utf-8")


def test_the_starter_templates_declare_the_dashboard_archetype():
    """A template is only listed when it has a `style.json`, and only offered for an experience it
    declares. A dashboard preset that forgot either is a template nobody can reach."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "templates" / "official"
    names = ["dashboard-monitoring", "dashboard-regional", "dashboard-assets", "dashboard-zonal"]
    for name in names:
        folder = root / name
        assert (folder / "style.json").exists(), f"{name} would be hidden from the gallery"
        meta = json.loads((folder / "template.json").read_text(encoding="utf-8"))
        assert meta["archetype"] == "dashboard"
        assert "dashboard" in meta["archetypes"]
        # The preset must survive the resolver — a preset that normalises to None ships a template
        # that opens on a blank grid.
        resolved = resolve_dashboard(meta["dashboard"])
        assert resolved and resolved["widgets"], name
        # …and it must be UNBOUND: the layers it would name do not exist when a template is written.
        assert dashboard_layer_refs(resolved) == (set(), set()), name


def test_every_preset_wires_a_source_to_something():
    """CROSS-FILTERING IS THE ARCHETYPE. A preset whose widgets are all mutually disconnected is a
    page of charts, and a visitor drawing a box on it watches nothing happen — which is exactly how
    the hand-built path failed (every widget was created with `filters: []`).

    So: every preset must have at least one live wire, and every preset that offers a raster-stats
    or details panel must have something pointed AT it. Those two are target-only, so an unwired one
    can never show anything but its own placeholder."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "templates" / "official"
    for folder in sorted(root.glob("dashboard-*")):
        meta = json.loads((folder / "template.json").read_text(encoding="utf-8"))
        resolved = resolve_dashboard(meta["dashboard"])
        widgets = resolved["widgets"]
        wired = {t for w in widgets for t in w["actions"]["filters"]}
        assert wired, f"{folder.name}: nothing filters anything"
        for w in widgets:
            if w["type"] in ("rasterstats", "details"):
                assert w["id"] in wired, f"{folder.name}: {w['id']} is target-only and unwired"


def test_every_preset_offers_a_chart():
    """A dashboard template with no chart on it reads as a broken dashboard, not a deliberate one —
    the Asset tracker shipped a gauge, a table and a details panel and nothing that plots."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "templates" / "official"
    for folder in sorted(root.glob("dashboard-*")):
        meta = json.loads((folder / "template.json").read_text(encoding="utf-8"))
        types = {w["type"] for w in resolve_dashboard(meta["dashboard"])["widgets"]}
        # The zonal preset plots a raster histogram instead of an attribute chart, which is the
        # same promise kept with the data it actually has.
        assert types & {"chart", "gauge", "rasterstats"}, folder.name
        assert "chart" in types or folder.name == "dashboard-zonal", folder.name
