"""The tile servers are pinned, and the code and Compose pin the SAME versions.

Martin and TiTiler used to run from `:latest`, so a new install, or any recreate of the container,
could get a tile server nobody had tested. TiTiler's `:latest` is rebuilt from its development
branch, and its contract has already changed under us (contour parameters retyped to integers, which
blanked every contour layer). Two places name each image, the wizard's code and docker-compose.yml,
and an instance runs whichever created the container, so they must agree.
`installer/tests/test-tiles.sh` is the behavioural check to run before moving either pin.
"""
import pathlib
import re

from geodeploy.services import minio, postgis

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _compose_image(service: str) -> str:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    block = compose.split(f"\n  {service}:\n", 1)[1]
    return re.search(r"^\s+image:\s*(\S+)", block, re.M).group(1)


def test_the_wizard_and_compose_run_the_same_martin():
    assert _compose_image("martin") == postgis.MARTIN_IMAGE


def test_the_wizard_and_compose_run_the_same_titiler():
    assert _compose_image("titiler") == minio.TITILER_IMAGE


def test_the_tile_servers_are_pinned_to_a_release():
    for image in (postgis.MARTIN_IMAGE, minio.TITILER_IMAGE,
                  _compose_image("martin"), _compose_image("titiler")):
        tag = image.rsplit(":", 1)[-1]
        assert re.fullmatch(r"v?\d+\.\d+\.\d+", tag), f"{image} is not pinned to a release"


def test_the_tile_test_reads_the_pins_from_the_code():
    """test-tiles.sh tests what ships by reading these constants — so renaming one must not
    quietly turn it into a test of nothing."""
    script = (ROOT / "installer/tests/test-tiles.sh").read_text(encoding="utf-8")
    assert "pinned MARTIN_IMAGE postgis.py" in script
    assert "pinned TITILER_IMAGE minio.py" in script
    for name, module in (("MARTIN_IMAGE", "postgis.py"), ("TITILER_IMAGE", "minio.py")):
        source = (ROOT / "api/geodeploy/services" / module).read_text(encoding="utf-8")
        assert re.search(rf'^{name} = "[^"]+"\r?$', source, re.M), f"{name} line shape changed"
