"""The local-storage images: the wizard's and Compose's must be the same, and must not be MinIO's.

`minio/minio` and `minio/mc` stopped existing on Docker Hub (MinIO ended community distribution),
and every new install then failed at the storage step. These pin what replaced them — SILO, the
maintained fork — and stop the two places that name the server from drifting apart.
"""
import pathlib
import re

from geodeploy.services import minio

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _compose_minio_image() -> str:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    block = compose.split("\n  minio:\n", 1)[1]
    return re.search(r"^\s+image:\s*(\S+)", block, re.M).group(1)


def test_the_wizard_and_compose_run_the_same_storage_server():
    assert _compose_minio_image() == minio.IMAGE


def test_neither_image_is_one_that_no_longer_exists():
    for image in (minio.IMAGE, minio.MC_IMAGE, _compose_minio_image()):
        assert not image.startswith(("minio/", "quay.io/minio/")), image


def test_the_images_are_pinned_not_moving():
    """A moving tag would change the storage server under an installation nobody chose to update."""
    for image in (minio.IMAGE, minio.MC_IMAGE):
        assert ":" in image and not image.endswith(":latest"), image
