"""The contract between the multipart REQUEST SCHEMA and `minio.complete_multipart`.

A 170 MB raster upload failed at the final assemble step with `KeyError: 'part_number'`: the raster
router hand-built `{"PartNumber": …, "ETag": …}` (boto3's casing) while `complete_multipart` takes the
schema's own field names and applies that casing itself. The vector router had it right, so the two
callers of one function disagreed about its input.

No S3 needed to pin this — the bug was purely the shape of the dict, which is checkable directly.
"""
import inspect

from geodeploy.routers.data import raster as raster_router
from geodeploy.routers.data.vector import MultipartComplete


def _code(fn) -> str:
    """Source with comment lines removed. Needed because the fix's own comment NAMES the wrong key it
    replaced — a plain substring search over the source flagged the explanation as the bug."""
    lines = [l for l in inspect.getsource(fn).splitlines() if not l.lstrip().startswith("#")]
    return chr(10).join(lines)


def _part():
    body = MultipartComplete(s3_key="rasters/1/x.tif", upload_id="u",
                             parts=[{"part_number": 2, "etag": "b"}, {"part_number": 1, "etag": "a"}])
    return body.parts[0].model_dump()


def test_model_dump_carries_the_keys_complete_multipart_indexes():
    """`complete_multipart` sorts on p["part_number"] and reads p["etag"]. If the schema is renamed,
    this fails here rather than at the end of a multi-gigabyte upload."""
    part = _part()
    assert "part_number" in part
    assert "etag" in part


def test_complete_multipart_documents_that_shape():
    from geodeploy.services.minio import complete_multipart
    doc = inspect.getdoc(complete_multipart) or ""
    assert "part_number" in doc and "etag" in doc


def test_raster_router_passes_model_dump_not_boto_casing():
    """The regression itself: the raster route must hand over the schema's own field names. A
    hand-built PartNumber/ETag dict is the bug that broke every large raster upload."""
    src = _code(raster_router.raster_multipart_complete)
    assert "model_dump()" in src
    assert '"PartNumber"' not in src, "boto3 casing is complete_multipart's job, not the router's"


def test_both_routers_build_the_same_payload():
    from geodeploy.routers.data.vector import multipart_complete as vector_complete
    v = _code(vector_complete)
    r = _code(raster_router.raster_multipart_complete)
    assert ("model_dump()" in v) == ("model_dump()" in r)
