"""Whether a raster may be drawn zoomed OUT (issue #17).

The minzoom floor exists for a real reason: a z3 tile of a drone orthomosaic once took TiTiler long
enough that nginx answered 504, and MapLibre waits on a hanging tile, so the portal's load handler
never settles and the whole page sits on its loading screen. A 404 costs nothing; that 504 cost
everything.

But the floor was computed from the layer's EXTENT, which is only a proxy for cost. The actual
question is whether the file has an overview small enough to answer a zoomed-out tile from directly
— and a COG this instance built always does, because the converter writes a full pyramid. For those
layers the floor is guesswork that makes a small high-resolution layer VANISH below a computed zoom,
with no message and nothing in the console.

So: measure it at ingest, from the file. `None` (never measured) keeps the heuristic, which is what
every layer ingested before this does.
"""
import pytest

from geodeploy.services.cog_converter import low_zoom_is_cheap


class TestCheapWhenThePyramidSaysSo:
    def test_a_small_image_needs_no_overviews(self):
        """The whole file IS the cheap read."""
        assert low_zoom_is_cheap(800, 600, []) is True

    def test_a_big_image_with_a_deep_pyramid_is_cheap(self):
        """20000px ÷ 32 = 625px — one small read, whatever area the tile covers."""
        assert low_zoom_is_cheap(20000, 18000, [2, 4, 8, 16, 32]) is True

    def test_a_big_image_with_a_shallow_pyramid_is_not(self):
        """20000 ÷ 4 = 5000px still has to be read and warped for a single tile."""
        assert low_zoom_is_cheap(20000, 18000, [2, 4]) is False

    def test_a_big_image_with_no_overviews_is_not(self):
        """An imported GeoTIFF that was already a COG but without a pyramid: the original
        reasoning still applies to it, so it keeps the floor."""
        assert low_zoom_is_cheap(20000, 18000, []) is False

    def test_the_longest_side_decides(self):
        """A tall thin raster is as expensive as a wide one — the read is 2D."""
        assert low_zoom_is_cheap(500, 40000, []) is False
        assert low_zoom_is_cheap(40000, 500, [64]) is True


class TestItNeverThrows:
    """This runs inside ingest. A display hint must never fail an otherwise-good upload."""

    @pytest.mark.parametrize("args", [
        (None, None, None),
        (0, 0, []),
        (1000, None, [2]),
        (20000, 20000, [0]),          # a zero factor would divide by zero
        (20000, 20000, [None, "x"]),  # nonsense from an odd driver
    ])
    def test_bad_input_answers_false_rather_than_raising(self, args):
        assert low_zoom_is_cheap(*args) in (True, False)


class _Layer:
    """Just the attribute `raster_minzoom` reads."""

    def __init__(self, low_zoom_ok):
        self.low_zoom_ok = low_zoom_ok


class TestThePublishedStyle:
    """What `portal_generator` writes as the source's `minzoom` — 0 meaning "write none"."""

    #: Drone-sized: small enough that the extent heuristic produces a floor well above 0.
    SMALL = [11.0000, 55.0000, 11.0020, 55.0015]
    #: Continent-sized: the heuristic already writes nothing.
    HUGE = [-20.0, 30.0, 40.0, 70.0]

    def test_the_heuristic_still_applies_by_default(self):
        from geodeploy.services.portal_generator import raster_minzoom
        floor = raster_minzoom(_Layer(None), self.SMALL)
        assert floor > 0, "a small extent must still get a floor when nothing was measured"

    def test_a_file_measured_as_cheap_gets_no_floor(self):
        """The point of the issue: the layer stops vanishing at low zoom."""
        from geodeploy.services.portal_generator import raster_minzoom
        assert raster_minzoom(_Layer(True), self.SMALL) == 0

    def test_measured_as_EXPENSIVE_keeps_the_floor(self):
        from geodeploy.services.portal_generator import raster_minzoom
        assert raster_minzoom(_Layer(False), self.SMALL) > 0

    def test_an_existing_layer_is_unchanged(self):
        """NULL is not False. Every raster ingested before the measurement existed keeps exactly
        the behaviour it has today, until it is re-ingested — the conservative direction for a
        guard that was protecting against a page-wide hang."""
        from geodeploy.services.portal_generator import raster_minzoom
        assert raster_minzoom(_Layer(None), self.SMALL) == raster_minzoom(object(), self.SMALL)

    def test_a_continent_sized_raster_never_had_a_floor_anyway(self):
        from geodeploy.services.portal_generator import raster_minzoom
        assert raster_minzoom(_Layer(None), self.HUGE) == 0
