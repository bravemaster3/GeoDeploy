"""Rule-based symbology: one render layer per rule, built by the ordinary single-symbol path.

A QGIS rule tree flattens to a list of render layers, each with its own filter, symbol and zoom
range — that is what `style.rules` holds, and `portal_generator._rule_layers` is what draws it. The
twin is `ui/src/lib/mapStyle.js` (the editor preview), which expands the same list before its draw
loop; the two must agree about ids, order, filters and the zoom clamp or a published portal and its
preview show different maps.

The rules themselves are produced by the QGIS plugin (`integrations/qgis-plugin/geodeploy_qgis/
rules.py`) and their filters by `cli/geodeploy/expressions.py`. Nothing here re-translates an
expression: a rule arrives with its MapLibre filter already built, deliberately, so there is one
translator rather than two that would drift.
"""
import pytest

from geodeploy.services import portal_generator as pg


class _Layer:
    """The handful of attributes `_vector_layer` reads off a layer row."""

    def __init__(self, geometry_type="LineString", layer_id=7):
        self.id = layer_id
        self.geometry_type = geometry_type
        self.schema_name = "public"
        self.table_name = "roads"
        self.storage_backend = "postgis"
        self.name = "roads"
        self.bbox = None


def _cfg(rules, **style):
    return {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
            "style": dict(style, rules=rules)}


RULES = [
    {"label": "A", "filter": ["==", ["get", "kind"], "a"],
     "expression": "\"kind\" = 'a'", "style": {"color": "#ff0000", "line_width": 2}},
    {"label": "B", "filter": ["==", ["get", "kind"], "b"],
     "expression": "\"kind\" = 'b'", "style": {"color": "#0000ff", "line_width": 4}},
]


class TestOneLayerPerRule:
    def test_each_rule_becomes_its_own_render_layer(self):
        out = pg._vector_layers("src", _Layer(), _cfg(RULES))
        assert len(out) == 2

    def test_ids_are_distinct_or_maplibre_sees_one_layer_defined_twice(self):
        out = pg._vector_layers("src", _Layer(), _cfg(RULES))
        assert [ml["id"] for ml in out] == ["vector-7-r0", "vector-7-r1"]

    def test_each_rule_carries_its_own_filter(self):
        out = pg._vector_layers("src", _Layer(), _cfg(RULES))
        assert [ml["filter"] for ml in out] == [
            ["==", ["get", "kind"], "a"], ["==", ["get", "kind"], "b"]]

    def test_each_rule_draws_its_own_symbol(self):
        out = pg._vector_layers("src", _Layer(), _cfg(RULES))
        assert out[0]["paint"]["line-color"] == "#ff0000"
        assert out[1]["paint"]["line-color"] == "#0000ff"
        assert out[0]["paint"]["line-width"] == 2
        assert out[1]["paint"]["line-width"] == 4

    def test_order_is_kept_because_rules_zero_draws_underneath(self):
        # QGIS's own rule order, and MapLibre draws in list order — so the list is emitted as-is.
        out = pg._vector_layers("src", _Layer(), _cfg(RULES))
        assert out[0]["paint"]["line-color"] == "#ff0000"

    def test_a_rule_with_no_filter_draws_everything(self):
        out = pg._vector_layers("src", _Layer(), _cfg([{"label": "all", "style": {"color": "#111"}}]))
        assert "filter" not in out[0]


class TestInheritance:
    def test_a_rule_inherits_the_layers_shape_where_it_says_nothing(self):
        # QGIS does the same: a rule's symbol starts as a copy of the layer's.
        cfg = _cfg([{"label": "A", "style": {"color": "#ff0000"}}], line_width=6, lineType="dashed")
        out = pg._vector_layers("src", _Layer(), cfg)
        assert out[0]["paint"]["line-width"] == 6
        assert out[0]["paint"]["line-dasharray"] == [2, 1.5]

    def test_a_rule_overrides_what_it_does_say(self):
        cfg = _cfg([{"label": "A", "style": {"line_width": 1}}], line_width=6)
        out = pg._vector_layers("src", _Layer(), cfg)
        assert out[0]["paint"]["line-width"] == 1

    def test_an_inherited_classification_is_dropped_inside_a_rule(self):
        """A rule is ONE symbol. A `color_mode` left over from the layer would classify inside the
        rule and paint it a colour the rule never named."""
        cfg = _cfg([{"label": "A", "style": {"color": "#ff0000"}}],
                   color_mode="categorized", color_field="kind",
                   categories=[{"value": "a", "color": "#00ff00"}])
        out = pg._vector_layers("src", _Layer(), cfg)
        assert out[0]["paint"]["line-color"] == "#ff0000"


class TestZoomRange:
    def test_a_zoom_range_is_written_through(self):
        cfg = _cfg([{"label": "A", "style": {"color": "#111"}, "minzoom": 8.5, "maxzoom": 14}])
        out = pg._vector_layers("src", _Layer(), cfg)
        assert out[0]["minzoom"] == 8.5 and out[0]["maxzoom"] == 14

    def test_the_defaults_are_not_written(self):
        # minzoom 0 and maxzoom 24 are MapLibre's own defaults; writing them is noise in the style.
        cfg = _cfg([{"label": "A", "style": {"color": "#111"}, "minzoom": 0, "maxzoom": 24}])
        out = pg._vector_layers("src", _Layer(), cfg)
        assert "minzoom" not in out[0] and "maxzoom" not in out[0]

    def test_a_zoom_outside_maplibres_range_is_clamped_not_passed_on(self):
        """QGIS stores scale thresholds far outside 0-24, and one of those makes MapLibre reject the
        WHOLE style rather than ignore the number — every layer disappears, not just this rule."""
        cfg = _cfg([{"label": "A", "style": {"color": "#111"}, "minzoom": -3, "maxzoom": 29.058}])
        out = pg._vector_layers("src", _Layer(), cfg)
        assert "minzoom" not in out[0]          # clamped to 0, which is the default
        assert "maxzoom" not in out[0]          # clamped to 24, which is the default

    def test_a_nonsense_zoom_is_ignored_rather_than_crashing_the_style(self):
        cfg = _cfg([{"label": "A", "style": {"color": "#111"}, "minzoom": "soon"}])
        out = pg._vector_layers("src", _Layer(), cfg)
        assert "minzoom" not in out[0]


class TestPolygons:
    def test_a_wide_outline_gets_its_own_layer_per_rule(self):
        rules = [{"label": "A", "filter": ["==", ["get", "k"], "a"],
                  "style": {"color": "#ff0000", "outline_color": "#000000", "outline_width": 3}}]
        out = pg._vector_layers("src", _Layer(geometry_type="Polygon"), _cfg(rules))
        assert [ml["id"] for ml in out] == ["vector-7-r0", "vector-7-r0-outline"]

    def test_the_outline_carries_the_same_filter_as_its_fill(self):
        rules = [{"label": "A", "filter": ["==", ["get", "k"], "a"],
                  "style": {"color": "#ff0000", "outline_color": "#000000", "outline_width": 3},
                  "minzoom": 9}]
        out = pg._vector_layers("src", _Layer(geometry_type="Polygon"), _cfg(rules))
        assert out[1]["filter"] == ["==", ["get", "k"], "a"]
        assert out[1]["minzoom"] == 9


class TestNotRuleBased:
    @pytest.mark.parametrize("style", [{}, {"rules": []}, {"rules": None}, {"rules": "yes"}])
    def test_a_layer_without_rules_takes_the_ordinary_path(self, style):
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
               "style": dict(style, color="#123456")}
        out = pg._vector_layers("src", _Layer(), cfg)
        assert [ml["id"] for ml in out] == ["vector-7"]
        assert out[0]["paint"]["line-color"] == "#123456"

    def test_rules_win_over_the_raw_paint_passthrough(self):
        """Both are ways of saying "this layer is more than one render layer". Rules are the
        friendly one and the editor can read them, so they take precedence."""
        cfg = _cfg(RULES)
        cfg["style"]["maplibre"] = {"layers": [{"type": "fill", "paint": {"fill-color": "#000"}}]}
        out = pg._vector_layers("src", _Layer(), cfg)
        assert [ml["id"] for ml in out] == ["vector-7-r0", "vector-7-r1"]

    def test_a_malformed_rule_entry_is_skipped_not_fatal(self):
        out = pg._vector_layers("src", _Layer(), _cfg(["nonsense", RULES[0]]))
        assert len(out) == 1 and out[0]["id"] == "vector-7-r1"


class TestLayerScope:
    """A QGIS layer's scale range and subset string apply to EVERYTHING it draws — the fill and its
    outline, every rule — so they are applied to each render layer rather than baked into one."""

    def test_a_zoom_range_reaches_every_render_layer(self):
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
               "style": {"color": "#ff0000", "outline_color": "#000", "outline_width": 3,
                         "minzoom": 9, "maxzoom": 15}}
        out = pg._vector_layers("src", _Layer(geometry_type="Polygon"), cfg)
        assert len(out) == 2
        assert all(ml["minzoom"] == 9 and ml["maxzoom"] == 15 for ml in out)

    def test_a_subset_filter_reaches_every_render_layer(self):
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
               "style": {"color": "#ff0000", "outline_color": "#000", "outline_width": 3,
                         "filter": [">", ["get", "pop"], 5]}}
        out = pg._vector_layers("src", _Layer(geometry_type="Polygon"), cfg)
        assert all(ml["filter"] == [">", ["get", "pop"], 5] for ml in out)

    def test_a_rules_filter_is_ANDED_with_the_layers_not_replaced(self):
        """In QGIS both are true at once: the subset decides which features the layer HAS, the rule
        which of those it draws. Replacing one with the other draws the wrong set."""
        cfg = _cfg(RULES)
        cfg["style"]["filter"] = [">", ["get", "pop"], 5]
        out = pg._vector_layers("src", _Layer(), cfg)
        assert out[0]["filter"] == ["all", ["==", ["get", "kind"], "a"], [">", ["get", "pop"], 5]]

    def test_a_rules_zoom_range_wins_because_it_is_already_the_narrower_one(self):
        # Rule ranges were intersected with their parents when the rules were read, so the layer's
        # must not overwrite them.
        cfg = _cfg([{"label": "A", "style": {"color": "#111"}, "minzoom": 12}])
        cfg["style"]["minzoom"] = 4
        out = pg._vector_layers("src", _Layer(), cfg)
        assert out[0]["minzoom"] == 12

    def test_the_zoom_range_is_clamped_to_maplibres_own(self):
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
               "style": {"color": "#111", "minzoom": -5, "maxzoom": 31}}
        out = pg._vector_layers("src", _Layer(), cfg)
        assert "minzoom" not in out[0] and "maxzoom" not in out[0]


class TestNoSymbol:
    def test_no_symbol_emits_nothing_at_all(self):
        """QGIS's "No symbols" renderer: the layer stays listed and identifiable and draws nothing.
        An empty list is exactly that — not a layer painted transparent."""
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
               "style": {"no_symbol": True, "color": "#ff0000"}}
        assert pg._vector_layers("src", _Layer(), cfg) == []


class TestLineVocabulary:
    def test_a_custom_dash_pattern_wins_over_the_named_presets(self):
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
               "style": {"color": "#111", "lineType": "dashed", "dash_pattern": [3, 2, 1, 2]}}
        out = pg._vector_layers("src", _Layer(), cfg)
        assert out[0]["paint"]["line-dasharray"] == [3.0, 2.0, 1.0, 2.0]

    def test_an_odd_length_pattern_is_made_even(self):
        """MapLibre wants dash/gap pairs; an odd-length pattern repeats inverted, which is not what
        QGIS drew."""
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
               "style": {"color": "#111", "dash_pattern": [3, 2, 1]}}
        out = pg._vector_layers("src", _Layer(), cfg)
        assert len(out[0]["paint"]["line-dasharray"]) % 2 == 0

    def test_cap_and_join_are_LAYOUT_not_paint(self):
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
               "style": {"color": "#111", "line_cap": "round", "line_join": "bevel"}}
        out = pg._vector_layers("src", _Layer(), cfg)
        assert out[0]["layout"] == {"line-cap": "round", "line-join": "bevel"}
        assert "line-cap" not in out[0]["paint"]

    def test_an_unknown_cap_is_ignored_rather_than_passed_through(self):
        # MapLibre rejects the whole style over an invalid enum value.
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
               "style": {"color": "#111", "line_cap": "flat"}}
        out = pg._vector_layers("src", _Layer(), cfg)
        assert "layout" not in out[0]

    def test_line_offset(self):
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
               "style": {"color": "#111", "line_offset": -2.5}}
        out = pg._vector_layers("src", _Layer(), cfg)
        assert out[0]["paint"]["line-offset"] == -2.5

    def test_a_polygon_outline_gets_the_line_vocabulary_too(self):
        """An outline IS a line — a dashed boundary is an ordinary thing to draw."""
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
               "style": {"color": "#111", "outline_color": "#000", "outline_width": 3,
                         "dash_pattern": [4, 2]}}
        out = pg._vector_layers("src", _Layer(geometry_type="Polygon"), cfg)
        assert out[1]["paint"]["line-dasharray"] == [4.0, 2.0]


class TestMarkerVocabulary:
    def test_rotation_and_offset_are_layout(self):
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
               "style": {"color": "#111", "marker_rotation": 45, "marker_offset": [2, -3]}}
        out = pg._vector_layers("src", _Layer(geometry_type="Point"), cfg)
        assert out[0]["layout"]["icon-rotate"] == 45
        assert out[0]["layout"]["icon-offset"] == [2.0, -3.0]

    def test_rotation_is_normalised_into_one_turn(self):
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
               "style": {"color": "#111", "marker_rotation": 405}}
        out = pg._vector_layers("src", _Layer(geometry_type="Point"), cfg)
        assert out[0]["layout"]["icon-rotate"] == 45

    def test_the_markers_own_opacity_multiplies_the_layers(self):
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 0.8,
               "style": {"color": "#111", "marker_opacity": 0.5}}
        out = pg._vector_layers("src", _Layer(geometry_type="Point"), cfg)
        assert out[0]["paint"]["icon-opacity"] == 0.4


def _labelled(**labels):
    return {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
            "style": {"color": "#111", "labels": dict({"enabled": True, "field": "name"}, **labels)}}


class TestLabels:
    """A label is a second thing drawn for the same feature, so it is its OWN symbol layer — it has
    its own zoom range, it must draw above every geometry, and a point layer's own layer is already
    a symbol layer carrying an icon."""

    def test_a_label_layer_is_emitted_beside_the_geometry(self):
        out = pg._vector_layers("src", _Layer(), _labelled())
        assert [ml["id"] for ml in out] == ["vector-7", "vector-7-labels"]
        assert out[1]["type"] == "symbol"

    def test_labels_draw_above_the_geometry(self):
        # MapLibre draws in list order, so "after" is "on top".
        out = pg._vector_layers("src", _Layer(geometry_type="Polygon"),
                                _labelled())
        assert out[-1]["id"].endswith("-labels")

    def test_a_plain_field_becomes_a_string_read(self):
        out = pg._vector_layers("src", _Layer(), _labelled())
        assert out[1]["layout"]["text-field"] == ["to-string", ["get", "name"]]

    def test_an_expression_wins_over_a_field(self):
        cfg = _labelled(expression=["concat", ["get", "a"], "!"])
        out = pg._vector_layers("src", _Layer(), cfg)
        assert out[1]["layout"]["text-field"] == ["concat", ["get", "a"], "!"]

    def test_labels_are_not_emitted_when_disabled_or_empty(self):
        for labels in ({"enabled": False, "field": "name"}, {"enabled": True}):
            cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
                   "style": {"color": "#111", "labels": labels}}
            assert [ml["id"] for ml in pg._vector_layers("src", _Layer(), cfg)] == ["vector-7"]

    def test_an_unfamiliar_font_is_a_STACK_with_the_shipped_face_behind_it(self):
        """MapLibre draws NOTHING for a face its glyphs lack — no error, no text. `text-font` is a
        PREFERENCE LIST, so naming the requested face followed by the shipped one means an instance
        that has it draws it and one that does not still draws the label.

        Rewriting the name to the fallback here instead would silently discard a font the operator
        had installed, since only the server knows what is in `templates/shared/fonts/`."""
        out = pg._vector_layers("src", _Layer(), _labelled(font="Noto Serif Bold"))
        assert out[1]["layout"]["text-font"] == ["Noto Serif Bold", "Noto Sans Regular"]

    def test_the_shipped_face_is_not_repeated(self):
        out = pg._vector_layers("src", _Layer(), _labelled(font="Noto Sans Regular"))
        assert out[1]["layout"]["text-font"] == ["Noto Sans Regular"]

    def test_the_offset_is_converted_from_pixels_to_ems(self):
        # `text-offset` is in ems; GeoDeploy states every offset in pixels.
        out = pg._vector_layers("src", _Layer(), _labelled(size=10, offset=[0, 15]))
        assert out[1]["layout"]["text-offset"] == [0.0, 1.5]

    def test_priority_is_inverted_because_the_scales_run_opposite_ways(self):
        """QGIS priority runs 0-10 with HIGHER meaning more important; MapLibre places LOWER sort
        keys first, and what is placed first wins the space."""
        out = pg._vector_layers("src", _Layer(), _labelled(priority=8))
        assert out[1]["layout"]["symbol-sort-key"] == -8

    def test_a_halo_is_only_written_when_it_has_width(self):
        assert "text-halo-width" not in pg._vector_layers(
            "src", _Layer(), _labelled())[1]["paint"]
        out = pg._vector_layers("src", _Layer(), _labelled(halo_width=2, halo_color="#eee"))
        assert out[1]["paint"]["text-halo-width"] == 2
        assert out[1]["paint"]["text-halo-color"] == "#eee"

    def test_labels_keep_their_own_zoom_range(self):
        out = pg._vector_layers("src", _Layer(), _labelled(minzoom=12))
        assert out[1]["minzoom"] == 12
        assert "minzoom" not in out[0]      # the geometry's range is the layer's, not the label's

    def test_a_layer_that_draws_nothing_still_draws_its_labels(self):
        """Which is the whole point of QGIS's "No symbols" renderer on a labelled layer."""
        cfg = _labelled()
        cfg["style"]["no_symbol"] = True
        out = pg._vector_layers("src", _Layer(), cfg)
        assert [ml["id"] for ml in out] == ["vector-7-labels"]

    def test_labels_are_emitted_for_a_rule_based_layer_too(self):
        cfg = _cfg(RULES)
        cfg["style"]["labels"] = {"enabled": True, "field": "name"}
        out = pg._vector_layers("src", _Layer(), cfg)
        assert [ml["id"] for ml in out] == ["vector-7-r0", "vector-7-r1", "vector-7-labels"]

    def test_the_layers_subset_filter_reaches_the_labels(self):
        # Otherwise a filtered layer would label features it does not draw.
        cfg = _labelled()
        cfg["style"]["filter"] = [">", ["get", "pop"], 5]
        out = pg._vector_layers("src", _Layer(), cfg)
        assert out[1]["filter"] == [">", ["get", "pop"], 5]


class TestGlyphs:
    def test_both_style_builders_name_one_route(self):
        """Only the server knows whether a font set is installed. If the published style and the
        editor preview each guessed, they would disagree the moment one was installed."""
        assert pg.GLYPHS_URL == "/api/fonts/{fontstack}/{range}.pbf"
        assert pg._glyphs_url() == pg.GLYPHS_URL


class TestFontRoute:
    """`routers/fonts.py` — the list is DISCOVERED from `templates/shared/fonts/`, not declared, so
    an operator who drops a face in gets it served and listed with no rebuild."""

    def test_the_shipped_faces_are_installed(self):
        from geodeploy.routers import fonts
        import pathlib
        # Read from the repo rather than the container mount, so this test says something on a
        # developer's machine as well as in CI.
        root = pathlib.Path(__file__).resolve().parents[2] / "templates" / "shared" / "fonts"
        faces = sorted(d.name for d in root.iterdir() if d.is_dir())
        assert faces == ["Noto Sans Bold", "Noto Sans Italic", "Noto Sans Regular"]
        # The one range every label needs.
        assert (root / fonts.FALLBACK / "0-255.pbf").is_file()

    def test_a_fontstack_is_a_preference_list(self, monkeypatch):
        from geodeploy.routers import fonts
        monkeypatch.setattr(fonts, "installed",
                            lambda: ["Noto Sans Regular", "Noto Serif Bold"])
        assert fonts.resolve("Noto Serif Bold,Noto Sans Regular") == "Noto Serif Bold"
        assert fonts.resolve("Nothing At All,Noto Sans Regular") == "Noto Sans Regular"

    def test_an_unknown_stack_falls_back_rather_than_drawing_nothing(self, monkeypatch):
        from geodeploy.routers import fonts
        monkeypatch.setattr(fonts, "installed", lambda: ["Noto Sans Regular", "Noto Sans Bold"])
        assert fonts.resolve("Helvetica Neue Condensed") == "Noto Sans Regular"

    def test_nothing_installed_resolves_to_nothing(self, monkeypatch):
        from geodeploy.routers import fonts
        monkeypatch.setattr(fonts, "installed", lambda: [])
        assert fonts.resolve("Noto Sans Regular") is None


PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"


class TestMarkerPictures:
    """A QGIS symbol GeoDeploy has no words for — an SVG, raster or font marker, or several layers
    stacked — arrives as a rendered PNG. MapLibre does not need to understand an icon, only to have
    its pixels, so the honest translation of an indescribable symbol is the picture of it."""

    def _cfg(self, **style):
        return {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
                "style": dict({"color": "#111"}, **style)}

    def test_a_picture_becomes_the_icon(self):
        from geodeploy.services import symbology as sym
        out = pg._vector_layers("src", _Layer(geometry_type="Point"),
                                self._cfg(marker_image=PNG))
        assert out[0]["layout"]["icon-image"] == sym.picture_id(PNG)

    def test_the_pixels_are_baked_into_the_metadata_for_the_runtime(self):
        from geodeploy.services import symbology as sym
        images = sym.marker_images({"marker_image": PNG})
        assert images == [{"id": sym.picture_id(PNG), "image": PNG}]

    def test_a_picture_wins_over_a_shape(self):
        from geodeploy.services import symbology as sym
        got = sym.icon_image_expression({"marker": "star", "radius": 9, "marker_image": PNG})
        assert got == sym.picture_id(PNG)

    def test_a_picture_is_one_image_even_when_the_layer_is_classified(self):
        """A raster icon cannot be recoloured per class the way a generated shape can. The
        classification still applies everywhere else; only the icon stops varying."""
        from geodeploy.services import symbology as sym
        got = sym.icon_image_expression({
            "marker_image": PNG, "color_mode": "categorized", "color_field": "k",
            "categories": [{"value": "a", "color": "#f00"}, {"value": "b", "color": "#0f0"}]})
        assert got == sym.picture_id(PNG)

    def test_the_id_is_content_addressed_so_two_layers_share_one_image(self):
        from geodeploy.services import symbology as sym
        assert sym.picture_id(PNG) == sym.picture_id(PNG)
        assert sym.picture_id(PNG) != sym.picture_id(PNG + "x")

    @pytest.mark.parametrize("value", [None, "", "not a uri", "http://example.org/x.png", 5])
    def test_anything_that_is_not_a_data_uri_is_ignored(self, value):
        """The key comes from a client. A URL here would make the style depend on a host nobody
        controls, and a non-string would crash the id."""
        from geodeploy.services import symbology as sym
        assert sym.marker_picture({"marker_image": value}) is None
        assert sym.icon_image_expression({"marker": "square", "radius": 5,
                                          "marker_image": value}).startswith("gd-pt-")


class TestLineMarkers:
    """QGIS repeats a symbol down a line — arrows on a river, ticks on a boundary. MapLibre draws
    exactly that with `symbol-placement: line`, so it is a translation, not an approximation."""

    def _cfg(self, **block):
        return {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
                "style": {"color": "#111", "line_marker": dict({"image": PNG}, **block)}}

    def test_a_second_layer_is_emitted_beside_the_line(self):
        """A second render LAYER, not a property of the line: MapLibre draws a line and places
        symbols along it with two different layer types — which is how QGIS builds it too."""
        out = pg._vector_layers("src", _Layer(), self._cfg())
        assert [ml["id"] for ml in out] == ["vector-7", "vector-7-linemarkers"]
        assert out[1]["type"] == "symbol"

    def test_it_is_placed_along_the_line_and_rotated_with_it(self):
        """`icon-rotation-alignment: map` is what makes an arrow point downstream rather than
        always up the screen."""
        layout = pg._vector_layers("src", _Layer(), self._cfg())[1]["layout"]
        assert layout["symbol-placement"] == "line"
        assert layout["icon-rotation-alignment"] == "map"

    def test_the_spacing_travels(self):
        out = pg._vector_layers("src", _Layer(), self._cfg(spacing=25))
        assert out[1]["layout"]["symbol-spacing"] == 25

    def test_a_missing_spacing_gets_a_default_rather_than_zero(self):
        from geodeploy.services import symbology as sym
        out = pg._vector_layers("src", _Layer(), self._cfg())
        assert out[1]["layout"]["symbol-spacing"] == sym.DEFAULT_LINE_MARKER_SPACING

    def test_a_decoration_is_never_dropped_for_colliding(self):
        """One missing every few metres reads as a rendering fault rather than a placement rule."""
        layout = pg._vector_layers("src", _Layer(), self._cfg())[1]["layout"]
        assert layout["icon-allow-overlap"] is True

    def test_it_carries_its_own_images(self):
        """Only the FIRST render layer gets the full `geodeploy:*` block, and the runtime registers
        marker bitmaps from `geodeploy:markerImages` — so the decoration must carry its own."""
        from geodeploy.services import symbology as sym
        out = pg._vector_layers("src", _Layer(), self._cfg())
        assert out[1]["metadata"]["geodeploy:markerImages"] == [
            {"id": sym.picture_id(PNG), "image": PNG}]

    def test_the_layers_own_filter_reaches_the_decoration(self):
        cfg = self._cfg()
        cfg["style"]["filter"] = [">", ["get", "pop"], 5]
        out = pg._vector_layers("src", _Layer(), cfg)
        assert out[1]["filter"] == [">", ["get", "pop"], 5]

    @pytest.mark.parametrize("block", [{}, {"image": None}, {"image": "http://x/y.png"}, "nope"])
    def test_anything_without_a_data_uri_emits_no_decoration(self, block):
        """The value comes from a client. A URL would make the style depend on a host nobody
        controls, and a non-string would crash the id."""
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
               "style": {"color": "#111", "line_marker": block}}
        assert [ml["id"] for ml in pg._vector_layers("src", _Layer(), cfg)] == ["vector-7"]


class TestFillPatterns:
    """A hatch, a line or point pattern, an SVG or raster fill. The tile is REBUILT by the plugin
    from QGIS's parameters rather than photographed, because `fill-pattern` repeats the image it is
    given and a rendered patch shows a seam every tile."""

    def _cfg(self, **style):
        return {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
                "style": dict({"color": "#111",
                               "fill_pattern": {"image": PNG, "width": 16, "height": 16}}, **style)}

    def test_the_pattern_replaces_the_colour(self):
        """MapLibre draws `fill-pattern` INSTEAD of `fill-color` — the tile carries its own
        colours — so leaving the colour set is a no-op that reads as if it still applied."""
        from geodeploy.services import symbology as sym
        out = pg._vector_layers("src", _Layer(geometry_type="Polygon"), self._cfg())
        paint = out[0]["paint"]
        assert paint["fill-pattern"] == sym.picture_id(PNG)
        assert "fill-color" not in paint

    def test_the_opacity_still_applies(self):
        """Which is how a hatch stays a wash rather than becoming opaque."""
        out = pg._vector_layers("src", _Layer(geometry_type="Polygon"),
                                self._cfg(fill_opacity=0.5))
        assert out[0]["paint"]["fill-opacity"] == 0.5

    def test_the_tile_is_registered_through_the_one_image_channel(self):
        """A fill layer is not a `symbol` layer, so the runtime creates the tile via
        `styleimagemissing` — which can only find it if the id is in a layer's metadata."""
        from geodeploy.services import symbology as sym
        images = sym.marker_images(self._cfg()["style"])
        assert images == [{"id": sym.picture_id(PNG), "image": PNG}]

    def test_a_tile_outranks_a_marker_picture_on_the_same_style(self):
        from geodeploy.services import symbology as sym
        style = dict(self._cfg()["style"], marker_image=PNG + "x")
        assert sym.marker_images(style)[0]["id"] == sym.picture_id(PNG)

    def test_the_outline_still_draws_over_a_pattern(self):
        out = pg._vector_layers("src", _Layer(geometry_type="Polygon"),
                                self._cfg(outline_color="#000000", outline_width=3))
        assert [ml["id"] for ml in out] == ["vector-7", "vector-7-outline"]
        assert out[1]["paint"]["line-color"] == "#000000"

    @pytest.mark.parametrize("block", [{}, {"image": None}, {"image": "http://x/y.png"}, "nope", 7])
    def test_anything_without_a_data_uri_leaves_the_colour_alone(self, block):
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
               "style": {"color": "#123456", "fill_pattern": block}}
        paint = pg._vector_layers("src", _Layer(geometry_type="Polygon"), cfg)[0]["paint"]
        assert paint["fill-color"] == "#123456"
        assert "fill-pattern" not in paint

    def test_a_line_layer_is_unaffected(self):
        """`fill_pattern` on a line is meaningless; it must not reach the paint."""
        paint = pg._vector_layers("src", _Layer(), self._cfg())[0]["paint"]
        assert "fill-pattern" not in paint

    def test_a_rule_can_carry_its_own_pattern(self):
        from geodeploy.services import symbology as sym
        rules = [{"label": "A", "filter": ["==", ["get", "k"], "a"],
                  "style": {"fill_pattern": {"image": PNG}}}]
        cfg = {"layer_id": 7, "layer_type": "vector", "opacity": 1.0,
               "style": {"color": "#111", "rules": rules}}
        out = pg._vector_layers("src", _Layer(geometry_type="Polygon"), cfg)
        assert out[0]["paint"]["fill-pattern"] == sym.picture_id(PNG)
