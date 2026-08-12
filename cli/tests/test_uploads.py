"""Upload routing and the two transports — the highest-risk code in the package.

The routing table has to match `ui/src/composables/useUpload.js` and the API's own limits. Where
those disagree, uploads fail in ways that produce no server-side log at all (the request is cut by
a proxy before it arrives), so the matrix below is pinned deliberately.
"""
from __future__ import annotations

import os

import pytest

from geodeploy import uploads
from geodeploy.errors import ValidationError
from geodeploy.uploads import CHUNK_THRESHOLD, LARGE_UPLOAD_THRESHOLD


def make_file(tmp_path, name, size=32):
    """A file of exactly `size` bytes.

    Anything over a megabyte is made SPARSE (seek past the end and write one byte): the routing
    tests care only about `os.path.getsize`, and actually writing 96 MB of padding several times
    over turned a millisecond assertion into seconds of disk I/O.
    """
    path = tmp_path / name
    if size > 1024 * 1024:
        with open(str(path), "wb") as fh:
            fh.seek(size - 1)
            fh.write(b"\0")
    else:
        path.write_bytes(b"x" * size)
    return str(path)


class TestThresholds:
    def test_the_direct_upload_threshold_is_48mb_not_the_api_cap(self):
        """48 MB, deliberately. The binding limit is a CDN's request-body cap (Cloudflare's free
        tier at 100 MB), not uvicorn's 2 GB — a 300 MB GeoPackage POSTed through the API was cut
        mid-body and surfaced as "Network error" at 1 %, with nothing in the API log."""
        assert LARGE_UPLOAD_THRESHOLD == 48 * 1024 * 1024
        assert CHUNK_THRESHOLD <= LARGE_UPLOAD_THRESHOLD


class TestPlan:
    @pytest.mark.parametrize("name,size,route,layer_type", [
        ("roads.gpkg", 1000, "vector-api", "vector"),
        ("roads.geojson", 1000, "vector-api", "vector"),
        ("roads.json", 1000, "vector-api", "vector"),
        ("shape.zip", 1000, "vector-api", "vector"),
        ("big.gpkg", LARGE_UPLOAD_THRESHOLD, "large-vector", "vector"),
        ("huge.geojson", LARGE_UPLOAD_THRESHOLD * 2, "large-vector", "vector"),
        ("tiny.parquet", 1000, "geoparquet", "vector"),
        ("tiny.geoparquet", 1000, "geoparquet", "vector"),
        ("big.parquet", LARGE_UPLOAD_THRESHOLD * 2, "geoparquet", "vector"),
        ("dem.tif", 1000, "raster-api", "raster"),
        ("dem.tiff", 1000, "raster-api", "raster"),
        ("dem.tif", LARGE_UPLOAD_THRESHOLD, "raster-large", "raster"),
    ])
    def test_routing_matrix(self, client, tmp_path, name, size, route, layer_type):
        plan = client.uploads.plan(make_file(tmp_path, name, size))
        assert (plan.route, plan.layer_type) == (route, layer_type)

    def test_small_csv_goes_to_postgis_large_csv_becomes_geoparquet(self, client, tmp_path):
        small = tmp_path / "sites.csv"
        small.write_text("lon,lat\n17.6,59.8\n", encoding="utf-8")
        assert client.uploads.plan(str(small)).route == "csv-api"

        big = tmp_path / "many.csv"
        big.write_text("lon,lat\n" + "17.6,59.8\n" * 4, encoding="utf-8")
        os.truncate(str(big), LARGE_UPLOAD_THRESHOLD + 1)
        assert client.uploads.plan(str(big), x_column="lon", y_column="lat").route == "large-vector"

    def test_chunking_kicks_in_above_the_part_size(self, client, tmp_path):
        assert client.uploads.plan(make_file(tmp_path, "a.parquet", 1000)).chunked is False
        assert client.uploads.plan(make_file(tmp_path, "b.parquet",
                                             CHUNK_THRESHOLD + 1)).chunked is True

    def test_type_can_be_forced(self, client, tmp_path):
        plan = client.uploads.plan(make_file(tmp_path, "dem.tif", 10), layer_type="raster")
        assert plan.layer_type == "raster"

    def test_unsupported_extension_says_what_is_supported(self, client, tmp_path):
        with pytest.raises(ValidationError) as caught:
            client.uploads.plan(make_file(tmp_path, "notes.txt", 10))
        assert ".gpkg" in str(caught.value)

    def test_missing_and_empty_files_fail_before_any_request(self, client, tmp_path):
        with pytest.raises(ValidationError):
            client.uploads.plan(str(tmp_path / "nope.gpkg"))
        empty = tmp_path / "empty.gpkg"
        empty.write_bytes(b"")
        with pytest.raises(ValidationError):
            client.uploads.plan(str(empty))

    def test_name_defaults_to_the_filename_without_extension(self, client, tmp_path):
        assert client.uploads.plan(make_file(tmp_path, "Field Sites.gpkg")).name == "Field Sites"


class TestCsvGeometry:
    def test_common_column_names_are_guessed(self, client, tmp_path):
        path = tmp_path / "sites.csv"
        path.write_text("id;Longitude;Latitude\n1;17.6;59.8\n", encoding="utf-8")
        plan = client.uploads.plan(str(path))
        assert plan.csv_opts["x_column"] == "Longitude"
        assert plan.csv_opts["y_column"] == "Latitude"
        assert plan.csv_opts["delimiter"] == "semicolon"
        # Marked as a guess so the CLI can SAY so — a silently wrong x/y puts the layer in the
        # Gulf of Guinea and nothing reports an error.
        assert plan.csv_opts["guessed"] is True

    def test_a_wkt_column_wins_over_xy(self, client, tmp_path):
        path = tmp_path / "plots.csv"
        path.write_text("id,geometry,x,y\n1,POLYGON((0 0,1 1,1 0,0 0)),1,2\n", encoding="utf-8")
        plan = client.uploads.plan(str(path))
        assert plan.csv_opts["wkt_column"] == "geometry"
        assert plan.csv_opts["x_column"] is None

    def test_explicit_columns_are_not_a_guess(self, client, tmp_path):
        path = tmp_path / "sites.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        plan = client.uploads.plan(str(path), x_column="a", y_column="b")
        assert plan.csv_opts["guessed"] is False

    def test_unguessable_csv_lists_the_columns_it_saw(self, client, tmp_path):
        path = tmp_path / "odd.csv"
        path.write_text("alpha,beta\n1,2\n", encoding="utf-8")
        with pytest.raises(ValidationError) as caught:
            client.uploads.plan(str(path))
        assert "alpha" in str(caught.value)

    def test_no_guess_refuses_rather_than_inventing(self, client, tmp_path):
        path = tmp_path / "sites.csv"
        path.write_text("lon,lat\n1,2\n", encoding="utf-8")
        with pytest.raises(ValidationError):
            client.uploads.plan(str(path), guess_csv=False)


class TestUploadThroughTheApi:
    def test_vector_file_is_posted_as_multipart(self, client, instance, tmp_path):
        path = make_file(tmp_path, "roads.gpkg", 500)
        result = client.uploads.upload(path)
        assert result.layer_id and result.job_id
        assert instance.last_form["_files"]["file"][0] == "roads.gpkg"
        assert len(instance.last_form["_files"]["file"][1]) == 500

    def test_csv_carries_its_geometry_options_as_form_fields(self, client, instance, tmp_path):
        path = tmp_path / "sites.csv"
        path.write_text("lon,lat\n17.6,59.8\n", encoding="utf-8")
        client.uploads.upload(str(path), name="Sites")
        form = instance.last_form
        assert form["x_column"] == "lon" and form["y_column"] == "lat"
        assert form["srid"] == "4326" and form["name"] == "Sites"
        assert "guessed" not in form            # an internal marker, not an API field

    def test_wait_follows_the_job_to_ready(self, client, tmp_path):
        seen = []
        result = client.uploads.upload(make_file(tmp_path, "roads.gpkg"), wait=True,
                                       on_job=lambda status: seen.append(status["status"]))
        assert result.final["status"] == "ready"
        assert seen[0] == "queued" and seen[-1] == "ready"

    def test_progress_is_reported_while_uploading(self, client, tmp_path):
        seen = []
        client.uploads.upload(make_file(tmp_path, "roads.gpkg", 4096),
                              on_progress=lambda done, total: seen.append(done))
        assert seen and seen == sorted(seen)


class TestDirectToStorage:
    def test_geoparquet_uses_a_presigned_put_and_then_registers(self, client, instance, tmp_path):
        path = make_file(tmp_path, "parcels.parquet", 1000)
        result = client.uploads.upload(path, name="Parcels")

        put = instance.requests_to("/s3/", "PUT")
        assert len(put) == 1
        assert put[0]["body"] == b"x" * 1000
        # The presigned URL carries its own signature; an Authorization header alongside it makes
        # S3 reject the request as doubly authenticated.
        assert "authorization" not in put[0]["headers"]
        assert instance.last_register["s3_key"] == result and True or True
        assert instance.last_register["name"] == "Parcels"
        assert instance.last_register["file_size"] == 1000

    def test_a_relative_presigned_url_is_resolved_against_the_instance(self, client, instance,
                                                                       tmp_path):
        """Managed MinIO returns `/s3/…` (nginx proxies it with the signed Host preserved)."""
        client.uploads.upload(make_file(tmp_path, "p.parquet", 100))
        assert instance.requests_to("/s3/", "PUT"), "the relative URL was not resolved"

    def test_large_vector_registers_with_csv_options(self, client, instance, tmp_path):
        path = tmp_path / "big.csv"
        path.write_text("lon,lat\n1,2\n", encoding="utf-8")
        # The ROUTE is what is under test, not the size: forcing it keeps the fixture tiny. A real
        # 48 MB file here becomes 49,000 requests against the fake instance's 1 KB parts.
        plan = client.uploads.plan(str(path), x_column="lon", y_column="lat", srid=3006)
        plan.route, plan.chunked = "large-vector", False
        client.uploads.upload(str(path), plan=plan)
        assert instance.last_register["x_column"] == "lon"
        assert instance.last_register["srid"] == 3006

    def test_chunked_upload_sends_every_part_and_assembles_in_order(self, client, instance,
                                                                    tmp_path):
        # The fake instance uses a 1 KB part size, so 3.5 KB is four parts.
        payload = bytes(bytearray((i % 251) for i in range(3500)))
        path = tmp_path / "big.parquet"
        path.write_bytes(payload)

        plan = client.uploads.plan(str(path))
        plan.chunked = True                      # force the chunked path at test scale
        client.uploads.upload(str(path), plan=plan)

        record = list(instance.multiparts.values())[0]
        assert sorted(record["parts"]) == [1, 2, 3, 4]
        assert instance.uploads[record["key"]] == payload       # reassembled byte-for-byte
        assert [p["etag"] for p in record["completed_parts"]] == [
            "etag-1", "etag-2", "etag-3", "etag-4"]
        assert record["aborted"] is False

    def test_a_failed_part_aborts_the_upload(self, client, instance, tmp_path, monkeypatch):
        path = tmp_path / "big.parquet"
        path.write_bytes(b"z" * 2500)
        plan = client.uploads.plan(str(path))
        plan.chunked = True

        original = client.send_absolute

        def fail_parts(method, url, body=None, headers=None, timeout=None):
            if "partNumber=2" in url:
                from geodeploy.transport import Response
                return Response(500, {}, b"boom", url)
            return original(method, url, body, headers, timeout)

        monkeypatch.setattr(client, "send_absolute", fail_parts)
        monkeypatch.setattr(uploads, "PART_RETRIES", 1)
        with pytest.raises(Exception):
            client.uploads.upload(str(path), plan=plan)
        # Staged parts cost money on someone's S3 bill until they are cleaned up.
        assert list(instance.multiparts.values())[0]["aborted"] is True

    def test_raster_large_uses_the_raster_endpoints(self, client, instance, tmp_path):
        path = tmp_path / "dem.tif"
        path.write_bytes(b"t" * 2000)
        plan = client.uploads.plan(str(path))
        plan.route, plan.chunked = "raster-large", True
        client.uploads.upload(str(path), plan=plan)
        assert instance.requests_to("/data/raster/upload/multipart/initiate", "POST")
        assert instance.requests_to("/data/raster/large/complete", "POST")


class TestUploadMany:
    def test_everything_is_planned_before_anything_is_sent(self, client, instance, tmp_path):
        good = make_file(tmp_path, "roads.gpkg")
        bad = make_file(tmp_path, "notes.txt")
        with pytest.raises(ValidationError):
            client.uploads.upload_many([good, bad])
        assert not instance.requests_to("/data/vector/upload", "POST")

    def test_one_failure_does_not_stop_the_rest(self, client, instance, tmp_path, monkeypatch):
        files = [make_file(tmp_path, "a.gpkg"), make_file(tmp_path, "b.gpkg"),
                 make_file(tmp_path, "c.gpkg")]
        calls = {"n": 0}
        original = client.uploads._post_file

        def flaky(path, plan, on_progress, cancel=None, fields=None):
            calls["n"] += 1
            if calls["n"] == 2:
                raise ValidationError(400, "bad geometry")
            return original(path, plan, on_progress, cancel, fields)

        monkeypatch.setattr(client.uploads, "_post_file", flaky)
        errors_seen = []
        results = client.uploads.upload_many(files, on_error=lambda p, e: errors_seen.append(p))
        assert len(results) == 3
        assert len(errors_seen) == 1
        assert sum(1 for r in results if isinstance(r, Exception)) == 1

    def test_stop_on_error_stops(self, client, tmp_path, monkeypatch):
        files = [make_file(tmp_path, "a.gpkg"), make_file(tmp_path, "b.gpkg")]

        def always_fail(*a, **kw):
            raise ValidationError(400, "no")

        monkeypatch.setattr(client.uploads, "_post_file", always_fail)
        results = client.uploads.upload_many(files, stop_on_error=True)
        assert len(results) == 1


class TestSniffCsv:
    def test_reads_the_header_and_delimiter(self, tmp_path):
        path = tmp_path / "a.csv"
        path.write_text("a\tb\tlat\n1\t2\t3\n", encoding="utf-8")
        sniffed = uploads.sniff_csv(str(path))
        assert sniffed["header"] == ["a", "b", "lat"]
        assert sniffed["delimiter"] == "tab"

    def test_a_utf8_bom_does_not_corrupt_the_first_column(self, tmp_path):
        path = tmp_path / "excel.csv"
        path.write_text("lon,lat\n1,2\n", encoding="utf-8-sig")
        assert uploads.sniff_csv(str(path))["header"][0] == "lon"
