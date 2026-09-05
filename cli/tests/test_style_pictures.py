"""Pictures from the CLI: a marker, a pattern, a line marker, a centroid marker.

These four keys already SURVIVED a CLI restyle — `build_style` merges onto the existing style, so a
marker the QGIS plugin rendered was never dropped by `geodeploy portals style --color red`. What was
missing was any way to SET one without going through QGIS, which made "can it all be done from the
CLI?" a no for the one part of the vocabulary that is pixels rather than words.

A file on disk is the obvious other source, and the renderers cannot tell the two apart: the plugin
renders a QGIS symbol to a PNG, this reads a PNG somebody already has, and both arrive as the same
`data:` URI in the same key.
"""
import base64

import pytest

from geodeploy.errors import ValidationError
from geodeploy.styles import MAX_PICTURE_BYTES, build_style, picture_data_uri

# A real 1x1 transparent PNG.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


@pytest.fixture
def png(tmp_path):
    path = tmp_path / "marker.png"
    path.write_bytes(PNG_BYTES)
    return str(path)


class TestReadingTheFile:

    def test_a_png_becomes_a_data_uri(self, png):
        uri = picture_data_uri(png, "--marker-image")
        assert uri.startswith("data:image/png;base64,")
        assert base64.b64decode(uri.split(",", 1)[1]) == PNG_BYTES

    @pytest.mark.parametrize("name,mime", [("a.png", "image/png"), ("a.jpg", "image/jpeg"),
                                           ("a.jpeg", "image/jpeg"), ("a.gif", "image/gif"),
                                           ("a.webp", "image/webp"), ("a.svg", "image/svg+xml")])
    def test_every_accepted_type_names_itself_correctly(self, tmp_path, name, mime):
        path = tmp_path / name
        path.write_bytes(PNG_BYTES)
        assert picture_data_uri(str(path), "--marker-image").startswith("data:%s;base64," % mime)

    def test_a_type_we_do_not_accept_is_refused_by_name(self, tmp_path):
        """Reading an arbitrary file off disk and calling it an image is how a style ends up
        holding a PDF."""
        path = tmp_path / "notes.pdf"
        path.write_bytes(b"%PDF-1.4")
        with pytest.raises(ValidationError) as caught:
            picture_data_uri(str(path), "--marker-image")
        assert ".pdf" in str(caught.value) and "--marker-image" in str(caught.value)

    def test_a_missing_file_says_so_rather_than_traceback(self, tmp_path):
        with pytest.raises(ValidationError) as caught:
            picture_data_uri(str(tmp_path / "nope.png"), "--fill-pattern")
        assert "--fill-pattern" in str(caught.value)

    def test_an_empty_file_is_refused(self, tmp_path):
        path = tmp_path / "empty.png"
        path.write_bytes(b"")
        with pytest.raises(ValidationError):
            picture_data_uri(str(path), "--marker-image")

    def test_a_picture_too_large_to_carry_is_refused_with_its_size(self, tmp_path):
        """A style is JSON in a database row AND in every published portal's style.json, so this is
        a budget rather than a preference. Same number and same measure as the QGIS plugin, or a
        file the CLI accepted would be one the plugin would have refused."""
        path = tmp_path / "huge.png"
        path.write_bytes(b"\\x89PNG" + b"x" * (MAX_PICTURE_BYTES + 1))
        with pytest.raises(ValidationError) as caught:
            picture_data_uri(str(path), "--marker-image")
        assert "KB" in str(caught.value)


class TestSettingThemOnAStyle:

    def test_a_marker_image_is_a_bare_string(self, png):
        """It is the marker, not a block: there is no size or spacing to carry beside it."""
        style = build_style({}, marker_image=png)
        assert style["marker_image"].startswith("data:image/png;base64,")

    @pytest.mark.parametrize("key,flag", [("fill_pattern", "fill_pattern"),
                                          ("line_marker", "line_marker"),
                                          ("centroid_marker", "centroid_marker")])
    def test_the_others_are_blocks_with_the_image_inside(self, png, key, flag):
        style = build_style({}, **{flag: png})
        assert isinstance(style[key], dict)
        assert style[key]["image"].startswith("data:image/png;base64,")

    def test_setting_a_new_image_keeps_the_rest_of_the_block(self, png):
        """Replacing the whole block would silently reset a spacing somebody chose."""
        style = build_style({"line_marker": {"image": "data:image/png;base64,OLD", "spacing": 40}},
                            line_marker=png)
        assert style["line_marker"]["spacing"] == 40
        assert style["line_marker"]["image"] != "data:image/png;base64,OLD"

    def test_spacing_can_be_set_beside_the_image(self, png):
        style = build_style({}, line_marker=png, line_marker_spacing=25)
        assert style["line_marker"]["spacing"] == 25.0

    def test_spacing_alone_refuses_rather_than_making_a_block_with_no_picture(self):
        """A `line_marker` with no image draws nothing; the server discards it. Failing here says
        why, instead of leaving a key that quietly does nothing."""
        with pytest.raises(ValidationError):
            build_style({}, line_marker_spacing=25)

    @pytest.mark.parametrize("key", ["marker_image", "fill_pattern", "line_marker",
                                     "centroid_marker"])
    def test_each_can_be_removed(self, png, key):
        style = build_style({}, **{key: png})
        assert key in style
        assert key not in build_style(style, **{"no_" + key: True})

    def test_they_survive_an_unrelated_restyle(self, png):
        """The behaviour that was already right, pinned: `--color` must not drop the pixels."""
        style = build_style({}, marker_image=png, fill_pattern=png, line_marker=png,
                            centroid_marker=png)
        after = build_style(style, color="#ff0000")
        assert after["color"] == "#ff0000"
        for key in ("marker_image", "fill_pattern", "line_marker", "centroid_marker"):
            assert key in after, key

    def test_the_uri_is_the_shape_the_server_accepts(self, png):
        """`services/symbology` takes a picture only when it starts `data:image/` — anything else is
        refused rather than sanitised, so producing the wrong shape here would fail silently at the
        far end."""
        style = build_style({}, marker_image=png, centroid_marker=png)
        assert style["marker_image"].startswith("data:image/")
        assert style["centroid_marker"]["image"].startswith("data:image/")
