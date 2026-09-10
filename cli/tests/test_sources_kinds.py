"""Registering every kind of external source, and the checks that need no network.

An external source is a REFERENCE to somebody else's service — nothing is ingested. The instance
holds seven kinds and validates each one when it is created (a WFS and an OGC API collection are
fetched, a TileJSON is read, a PMTiles header is parsed), which is also where the missing pieces
come from: the layer inside a tile set, its zoom range, its extent, and whether a PMTiles archive
holds raster or vector tiles.

WHAT THIS FILE IS ABOUT is the half that must NOT need a network: the kinds the client will accept,
the arguments each one requires, and the body it sends. Those are the ones where a mistake is worth
a message rather than a round trip — and they are also where a client silently falls behind the
instance, which is exactly what happened while the instance grew four new kinds.
"""
from __future__ import annotations

import pytest

from geodeploy.errors import ValidationError
from geodeploy.sources import NEEDS_LAYER_NAME, SOURCE_TYPES, TAKES_SOURCE_LAYER


class Recorder(object):
    """A client that records the body instead of sending it."""

    def __init__(self):
        self.sent = None

    def post(self, path, body=None):
        self.sent = (path, body)
        return dict(body or {}, id=1)


def sources_with_recorder():
    from geodeploy.sources import Sources
    recorder = Recorder()
    return Sources(recorder), recorder


class TestTheKindsTheClientKnows:
    def test_every_kind_the_instance_holds(self):
        # The instance's own list, restated: `api/geodeploy/services/external_sources.SOURCE_TYPES`.
        # A client that knows fewer refuses a source the instance would have accepted, which reads
        # as "GeoDeploy does not support PMTiles" when it does.
        assert SOURCE_TYPES == ("xyz", "wms", "wmts", "wfs", "ogcapi", "vectortile", "pmtiles")

    def test_an_unknown_kind_is_refused_before_the_network(self):
        sources, recorder = sources_with_recorder()
        with pytest.raises(ValidationError) as caught:
            sources.create("x", "wcs", "https://example.org/wcs")
        # …and the message names what IS possible, rather than only what is not.
        assert "xyz" in str(caught.value) and "pmtiles" in str(caught.value)
        assert recorder.sent is None, "nothing should have been sent"


class TestWhatEachKindNeeds:
    @pytest.mark.parametrize("source_type", list(NEEDS_LAYER_NAME))
    def test_a_service_with_many_layers_must_name_one(self, source_type):
        sources, recorder = sources_with_recorder()
        with pytest.raises(ValidationError) as caught:
            sources.create("x", source_type, "https://example.org/svc")
        assert "--layer-name" in str(caught.value)
        assert recorder.sent is None

    def test_an_ogcapi_collection_may_be_in_the_url_instead(self):
        # Which is how people usually have it — clicking through the API lands you on the
        # collection or on its items.
        sources, recorder = sources_with_recorder()
        sources.create("Roads", "ogcapi", "https://example.org/ogc/collections/roads")
        assert recorder.sent[1]["source_type"] == "ogcapi"

    def test_or_named_separately(self):
        sources, recorder = sources_with_recorder()
        sources.create("Roads", "ogcapi", "https://example.org/ogc", layer_name="roads")
        assert recorder.sent[1]["layer_name"] == "roads"

    def test_but_one_of_the_two_is_required(self):
        sources, recorder = sources_with_recorder()
        with pytest.raises(ValidationError) as caught:
            sources.create("Roads", "ogcapi", "https://example.org/ogc")
        assert "collection" in str(caught.value)
        assert recorder.sent is None

    @pytest.mark.parametrize("source_type", ["xyz", "vectortile", "pmtiles"])
    def test_a_kind_with_one_layer_per_url_needs_no_name(self, source_type):
        sources, recorder = sources_with_recorder()
        sources.create("x", source_type, "https://example.org/a")
        assert recorder.sent is not None


class TestTheBodyItSends:
    def test_only_what_was_given(self):
        # An absent value must be ABSENT, not null: the instance fills in its own defaults (a WMS
        # version, a WMTS matrix set, an image format), and sending null would overwrite them.
        sources, recorder = sources_with_recorder()
        sources.create("OSM", "xyz", "https://t.example/{z}/{x}/{y}.png")
        assert recorder.sent[0] == "/data/sources"
        assert set(recorder.sent[1]) == {"name", "source_type", "url"}

    def test_the_tile_layer_travels(self):
        sources, recorder = sources_with_recorder()
        sources.create("Basemap", "vectortile", "https://t.example/{z}/{x}/{y}.pbf",
                       source_layer="water")
        assert recorder.sent[1]["source_layer"] == "water"

    def test_the_matrix_set_travels(self):
        sources, recorder = sources_with_recorder()
        sources.create("Topo", "wmts", "https://example.org/wmts", layer_name="topo",
                       matrix_set="EPSG:3857")
        assert recorder.sent[1]["matrix_set"] == "EPSG:3857"

    def test_the_attribution_travels(self):
        # The provider's credit is the condition of using their service, and a portal shows it.
        sources, recorder = sources_with_recorder()
        sources.create("OSM", "xyz", "https://t.example/{z}/{x}/{y}.png",
                       attribution="© OpenStreetMap")
        assert recorder.sent[1]["attribution"] == "© OpenStreetMap"

    def test_the_kinds_that_take_a_tile_layer_are_named(self):
        assert TAKES_SOURCE_LAYER == ("vectortile", "pmtiles")


class TestTheCommandLine:
    """The parser offers every kind, and hands each argument to the client under the right name."""

    def _parsed(self, argv):
        from geodeploy.cli.main import build_parser
        return build_parser().parse_args(argv)

    def test_every_kind_is_offered(self):
        for source_type in SOURCE_TYPES:
            args = self._parsed(["sources", "add", "n", "https://example.org/x",
                                 "--type", source_type])
            assert args.source_type == source_type

    def test_a_kind_the_instance_does_not_hold_is_refused_by_the_parser(self):
        with pytest.raises(SystemExit):
            self._parsed(["sources", "add", "n", "https://example.org/x", "--type", "wcs"])

    def test_the_new_arguments_are_there(self):
        args = self._parsed(["sources", "add", "n", "https://t.example/{z}/{x}/{y}.pbf",
                             "--type", "vectortile", "--source-layer", "water",
                             "--matrix-set", "EPSG:3857"])
        assert args.source_layer == "water" and args.matrix_set == "EPSG:3857"


class TestTheLabelKeysTheCliCanSet:
    """Two label properties travel now that did not: where a line's labels sit, and whether every
    part of a multi-part feature gets one."""

    def _labels(self, argv):
        from geodeploy.cli.commands._common import style_from_args
        from geodeploy.cli.main import build_parser
        return (style_from_args(build_parser().parse_args(argv)).get("labels") or {})

    def test_the_line_position(self):
        labels = self._labels(["layers", "style", "roads", "--label-field", "name",
                               "--label-placement", "line", "--label-line-position", "on"])
        assert labels["placement"] == "line" and labels["line_position"] == "on"

    def test_per_part_is_only_sent_when_asked(self):
        # `store_true` leaves False for "not asked", and sending that would turn the setting OFF on
        # a style that had it on.
        assert "label_per_part" not in self._labels(
            ["layers", "style", "roads", "--label-field", "name"])
        assert self._labels(["layers", "style", "roads", "--label-field", "name",
                             "--label-per-part"])["label_per_part"] is True
