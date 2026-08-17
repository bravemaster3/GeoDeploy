"""The PMTiles v3 reader, against an archive built here byte by byte.

Why a hand-built archive instead of a fixture file: every one of these fields is a place where an
off-by-one produces a tile that LOOKS fine and belongs somewhere else, which is the worst kind of
bug in a map. Writing the bytes means the test states the expected layout rather than trusting a
binary blob nobody can read in a diff.

What this reader is for: serving one tile out of an archive so clients get an ordinary XYZ URL.
Handed a whole archive, GDAL walks every tile at the deepest zoom just to report a feature count —
on this project's own instance a five-feature layer tiles to 2.17 million entries — which is why
QGIS hung on small layers. Everything below protects the code that replaced that.
"""
import gzip
import json
import struct

import pytest

from geodeploy.services import pmtiles_reader as pm


def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def directory(entries) -> bytes:
    """Serialize `[(tile_id, offset, length, run_length)]` the way the spec does: columnar, with
    the ids delta-coded and an offset of 0 meaning "right after the previous one"."""
    body = bytearray(varint(len(entries)))
    last = 0
    for tile_id, _o, _l, _r in entries:
        body += varint(tile_id - last)
        last = tile_id
    for _t, _o, _l, run in entries:
        body += varint(run)
    for _t, _o, length, _r in entries:
        body += varint(length)
    for i, (_t, offset, _l, _r) in enumerate(entries):
        if i > 0 and offset == entries[i - 1][1] + entries[i - 1][2]:
            body += varint(0)                       # the contiguous shorthand
        else:
            body += varint(offset + 1)
    return bytes(body)


def build(tiles, *, leaf_at=None, min_zoom=0, max_zoom=2, tile_type=1):
    """An archive from `{(z, x, y): payload}`. `leaf_at` splits the directory in two so the leaf
    path is exercised — one level of leaves is what a real archive of any size has."""
    blobs, entries, data = [], [], bytearray()
    for (z, x, y), payload in sorted(tiles.items(), key=lambda kv: pm.zxy_to_tile_id(*kv[0])):
        raw = gzip.compress(payload)
        entries.append((pm.zxy_to_tile_id(z, x, y), len(data), len(raw), 1))
        data += raw
        blobs.append(raw)

    if leaf_at is None:
        root_entries, leaves = entries, b""
    else:
        head, tail = entries[:leaf_at], entries[leaf_at:]
        leaves = gzip.compress(directory(tail))
        # run_length 0 = "this is a pointer to a leaf directory", covering every id at or above it.
        root_entries = head + [(tail[0][0], 0, len(leaves), 0)]
    root = gzip.compress(directory(root_entries))
    meta = gzip.compress(json.dumps({"vector_layers": [{"id": "geodeploy"}]}).encode())

    root_off = pm.HEADER_LEN
    meta_off = root_off + len(root)
    leaf_off = meta_off + len(meta)
    data_off = leaf_off + len(leaves)
    header = bytearray(b"PMTiles" + bytes([3]))
    header += struct.pack("<11Q", root_off, len(root), meta_off, len(meta), leaf_off, len(leaves),
                          data_off, len(data), len(entries), len(entries), len(entries))
    header += struct.pack("<6B", 1, 2, 2, tile_type, min_zoom, max_zoom)
    header += struct.pack("<4i", -int(10.5 * 1e7), int(1.25 * 1e7), int(20 * 1e7), int(30 * 1e7))
    header += struct.pack("<B", 1) + struct.pack("<2i", 0, 0)
    assert len(header) == pm.HEADER_LEN, len(header)
    return bytes(header) + root + meta + leaves + bytes(data)


def reader(archive):
    """A `fetch` over an in-memory archive that also RECORDS its reads, so the tests can assert on
    how many range requests a tile costs — the whole point of the module."""
    calls = []

    def fetch(offset, length):
        calls.append((offset, length))
        return archive[offset:offset + length]
    return fetch, calls


@pytest.fixture(autouse=True)
def _clean():
    pm.forget()
    yield
    pm.forget()


def test_hilbert_ids_match_the_spec():
    # The published examples. Getting this wrong returns a neighbouring tile, which draws real data
    # in the wrong place — the failure mode most likely to be mistaken for bad source data.
    assert pm.zxy_to_tile_id(0, 0, 0) == 0
    assert [pm.zxy_to_tile_id(1, *xy) for xy in ((0, 0), (0, 1), (1, 1), (1, 0))] == [1, 2, 3, 4]
    assert pm.zxy_to_tile_id(2, 0, 0) == 5
    assert pm.zxy_to_tile_id(3, 0, 0) == 21
    # Levels are laid end to end: the first id at z is the count of every tile above it.
    assert pm.zxy_to_tile_id(12, 0, 0) == (4 ** 12 - 1) // 3


def test_a_tile_costs_one_range_read_once_the_archive_is_open():
    tiles = {(0, 0, 0): b"zero", (1, 0, 0): b"one", (2, 1, 1): b"two"}
    archive = build(tiles)
    fetch, calls = reader(archive)

    body, header = pm.get_tile("k", fetch, 0, 0, 0)
    assert body == b"zero"
    assert header.media_type == "application/vnd.mapbox-vector-tile"
    assert len(calls) == 3, "the first tile pays for the header and the root directory too"

    calls.clear()
    assert pm.get_tile("k", fetch, 1, 0, 0)[0] == b"one"
    assert pm.get_tile("k", fetch, 2, 1, 1)[0] == b"two"
    assert len(calls) == 2, "one range read per tile after that — no directory re-read"


def test_a_missing_tile_is_none_not_an_error():
    # Sparse is the normal state of an archive. A 404 here would have clients retrying tiles that
    # were never written, which is exactly the retry storm this work removed.
    fetch, _ = reader(build({(0, 0, 0): b"zero"}))
    assert pm.get_tile("k", fetch, 1, 1, 1) is None
    assert pm.get_tile("k", fetch, 2, 3, 3) is None


def test_zoom_outside_the_archive_is_refused_without_a_read():
    fetch, calls = reader(build({(1, 0, 0): b"one"}, min_zoom=1, max_zoom=1))
    pm.open_archive("k", fetch)
    calls.clear()
    assert pm.get_tile("k", fetch, 0, 0, 0) is None
    assert pm.get_tile("k", fetch, 5, 1, 1) is None
    assert calls == [], "a zoom the archive does not cover must not reach storage"


def test_out_of_range_coordinates_are_refused():
    fetch, _ = reader(build({(1, 0, 0): b"one"}))
    assert pm.get_tile("k", fetch, 1, 2, 0) is None      # x >= 2**z
    assert pm.get_tile("k", fetch, 1, -1, 0) is None


def test_leaf_directories_are_followed_and_cached():
    tiles = {(2, x, y): f"{x}-{y}".encode() for x in range(4) for y in range(4)}
    archive = build(tiles, leaf_at=3)
    fetch, calls = reader(archive)
    pm.open_archive("k", fetch)

    calls.clear()
    assert pm.get_tile("k", fetch, 2, 3, 3)[0] == b"3-3"
    assert len(calls) == 2, "the leaf, then the tile"
    calls.clear()
    assert pm.get_tile("k", fetch, 2, 2, 3)[0] == b"2-3"
    assert len(calls) == 1, "the leaf is cached, so only the tile is read"


def test_a_run_covers_its_range_and_stops_at_the_end_of_it():
    # Runs are how identical tiles (empty ocean, repeated background) are stored once. Ignoring the
    # run length returns the run's bytes for a tile that is not in it.
    entries = [pm.Entry(tile_id=5, offset=0, length=4, run_length=3)]
    assert pm.find(entries, 5).tile_id == 5
    assert pm.find(entries, 7).tile_id == 5
    assert pm.find(entries, 8) is None, "one past the run must miss"
    assert pm.find(entries, 4) is None, "before the first entry must miss"
    assert pm.find([], 1) is None


def test_metadata_names_the_layer_inside_the_tiles():
    # A consumer that guesses this name renders nothing at all, so the TileJSON reads it from here.
    fetch, _ = reader(build({(0, 0, 0): b"zero"}))
    assert pm.metadata("k", fetch)["vector_layers"][0]["id"] == "geodeploy"


def test_the_header_carries_the_bounds_and_the_zoom_range():
    fetch, _ = reader(build({(0, 0, 0): b"z"}, min_zoom=0, max_zoom=9))
    header, _root = pm.open_archive("k", fetch)
    assert (header.min_zoom, header.max_zoom) == (0, 9)
    assert header.bounds == pytest.approx([-10.5, 1.25, 20.0, 30.0])


def test_a_raster_archive_reports_its_own_media_type():
    fetch, _ = reader(build({(0, 0, 0): b"png"}, tile_type=pm.TILETYPE_PNG))
    assert pm.get_tile("k", fetch, 0, 0, 0)[1].media_type == "image/png"


def test_forget_drops_a_stale_archive():
    # A re-tile writes a NEW object at the same layer; the route calls `forget` so the next request
    # re-reads instead of serving tiles from an archive that no longer exists.
    fetch, calls = reader(build({(0, 0, 0): b"zero"}))
    pm.open_archive("k", fetch)
    calls.clear()
    pm.forget("k")
    pm.open_archive("k", fetch)
    assert len(calls) == 2


def test_not_a_pmtiles_archive_is_reported_clearly():
    with pytest.raises(pm.PMTilesError):
        pm.parse_header(b"not pmtiles" + b"\0" * 200)
    with pytest.raises(pm.PMTilesError):
        pm.parse_header(b"PMTiles" + bytes([2]) + b"\0" * 200)
