"""Read ONE tile out of a PMTiles archive, so tiled layers can be served as ordinary XYZ.

WHY THIS EXISTS — the measurement that forced it
------------------------------------------------
GeoParquet layers are tiled to a single `.pmtiles` archive, and until now the only way to consume
one outside a browser was to open the whole archive with GDAL's PMTiles driver. That driver is not
viewport-driven: it presents the archive as an ordinary dataset, so the first thing a desktop GIS
does — ask for the feature count and the extent — makes it walk every tile at the archive's deepest
zoom. On this project's own instance, a layer with **five features** produced an archive with
**2,171,238 tile entries** across zooms 0–13 (tippecanoe's `--extend-zooms-if-still-dropping` keeps
adding levels), each read over HTTP. That is why QGIS sat "loading forever" and drew nothing, and
why it did so *worse* on small layers than on big ones.

MapLibre never had this problem because it asks for the four tiles under the viewport and no more.
This module gives every other client the same deal: a plain `{z}/{x}/{y}` URL.

WHAT IT IMPLEMENTS
------------------
PMTiles v3 (https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md), read-only, by hand
rather than by dependency — it is a fixed 127-byte header, varint directories and a Hilbert curve,
and the archive format is versioned and frozen. The alternative was another pinned package in the
API image for ~120 lines of parsing.

Every read is an HTTP Range request through the caller's `fetch(offset, length)`, so nothing is ever
downloaded whole; a tile costs one directory hop plus one range read, and the header and root
directory are cached per archive so the steady state is a single read per tile.
"""
from __future__ import annotations

import gzip
import struct
import zlib
from dataclasses import dataclass

HEADER_LEN = 127
_MAGIC = b"PMTiles"

# Compression ids from the spec. 1 = none, 2 = gzip, 3 = brotli, 4 = zstd.
_NONE, _GZIP, _BROTLI, _ZSTD = 1, 2, 3, 4

# Tile types, used to name the right media type back to the client.
TILETYPE_MVT, TILETYPE_PNG, TILETYPE_JPEG, TILETYPE_WEBP, TILETYPE_AVIF = 1, 2, 3, 4, 5
MEDIA_TYPES = {
    TILETYPE_MVT: "application/vnd.mapbox-vector-tile",
    TILETYPE_PNG: "image/png",
    TILETYPE_JPEG: "image/jpeg",
    TILETYPE_WEBP: "image/webp",
    TILETYPE_AVIF: "image/avif",
}


class PMTilesError(Exception):
    """The archive could not be read as PMTiles v3."""


@dataclass(frozen=True)
class Header:
    root_offset: int
    root_length: int
    metadata_offset: int
    metadata_length: int
    leaf_offset: int
    leaf_length: int
    data_offset: int
    data_length: int
    addressed_tiles: int
    tile_entries: int
    tile_contents: int
    clustered: bool
    internal_compression: int
    tile_compression: int
    tile_type: int
    min_zoom: int
    max_zoom: int
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @property
    def bounds(self) -> list[float]:
        return [self.min_lon, self.min_lat, self.max_lon, self.max_lat]

    @property
    def media_type(self) -> str:
        return MEDIA_TYPES.get(self.tile_type, "application/octet-stream")


def parse_header(raw: bytes) -> Header:
    if len(raw) < HEADER_LEN or raw[:7] != _MAGIC:
        raise PMTilesError("not a PMTiles archive")
    if raw[7] != 3:
        raise PMTilesError(f"PMTiles v{raw[7]} is not supported (v3 only)")
    (root_offset, root_length, meta_offset, meta_length, leaf_offset, leaf_length,
     data_offset, data_length, addressed, entries, contents) = struct.unpack_from("<11Q", raw, 8)
    clustered, internal_comp, tile_comp, tile_type, min_zoom, max_zoom = struct.unpack_from(
        "<6B", raw, 96)
    min_lon, min_lat, max_lon, max_lat = struct.unpack_from("<4i", raw, 102)
    return Header(
        root_offset, root_length, meta_offset, meta_length, leaf_offset, leaf_length,
        data_offset, data_length, addressed, entries, contents, bool(clustered),
        internal_comp, tile_comp, tile_type, min_zoom, max_zoom,
        min_lon / 1e7, min_lat / 1e7, max_lon / 1e7, max_lat / 1e7,
    )


def decompress(raw: bytes, compression: int) -> bytes:
    """Undo one of the spec's compressions.

    Brotli and zstd are decoded only if the interpreter has a decoder; tippecanoe writes gzip, so
    the others are a courtesy for archives produced elsewhere and are reported honestly rather than
    returned as garbage.
    """
    if compression in (_NONE, 0):
        return raw
    if compression == _GZIP:
        # `gzip.decompress` rejects a bare deflate stream; some writers emit one. Try both.
        try:
            return gzip.decompress(raw)
        except (OSError, EOFError, zlib.error):
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    if compression == _BROTLI:
        try:
            import brotli
        except ImportError:
            raise PMTilesError("this archive is brotli-compressed and brotli is not installed")
        return brotli.decompress(raw)
    if compression == _ZSTD:
        try:
            import zstandard
        except ImportError:
            raise PMTilesError("this archive is zstd-compressed and zstandard is not installed")
        return zstandard.ZstdDecompressor().decompress(raw)
    raise PMTilesError(f"unknown compression {compression}")


def _varints(raw: bytes, count: int, pos: int) -> tuple[list[int], int]:
    """`count` LEB128 varints starting at `pos`, plus the new position."""
    out = []
    for _ in range(count):
        value, shift = 0, 0
        while True:
            byte = raw[pos]
            pos += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        out.append(value)
    return out, pos


@dataclass(frozen=True)
class Entry:
    tile_id: int
    offset: int
    length: int
    run_length: int     # 0 means "this points at a LEAF DIRECTORY, not at a tile"


def parse_directory(raw: bytes) -> list[Entry]:
    """A decompressed directory blob into entries, sorted by tile id (the spec guarantees that).

    The serialization is columnar and delta-coded: all the ids, then all the run lengths, then all
    the lengths, then all the offsets. An offset of 0 is the spec's "immediately after the previous
    one" shorthand, which is what makes a clustered archive so compact.
    """
    count, pos = _varints(raw, 1, 0)
    count = count[0]
    deltas, pos = _varints(raw, count, pos)
    runs, pos = _varints(raw, count, pos)
    lengths, pos = _varints(raw, count, pos)
    raw_offsets, pos = _varints(raw, count, pos)

    entries: list[Entry] = []
    tile_id = 0
    for i in range(count):
        tile_id += deltas[i]
        if raw_offsets[i] == 0 and i > 0:
            offset = entries[i - 1].offset + entries[i - 1].length
        else:
            offset = raw_offsets[i] - 1
        entries.append(Entry(tile_id, offset, lengths[i], runs[i]))
    return entries


def zxy_to_tile_id(z: int, x: int, y: int) -> int:
    """The Hilbert-curve index PMTiles addresses tiles by.

    Zoom levels are laid end to end (all of z0, then all of z1, …) and within a level the tiles
    follow a Hilbert curve, which is what keeps neighbouring tiles adjacent in the file — the
    property that makes one range request serve a whole screenful.
    """
    if z < 0 or z > 26:
        raise PMTilesError("zoom out of range")
    n = 1 << z
    if x < 0 or y < 0 or x >= n or y >= n:
        raise PMTilesError("tile outside the zoom level")
    # Tiles in all levels above this one: 1 + 4 + 16 + … = (4**z - 1) / 3.
    acc = (n * n - 1) // 3
    rx = ry = 0
    d = 0
    s = n >> 1
    tx, ty = x, y
    while s > 0:
        rx = 1 if (tx & s) > 0 else 0
        ry = 1 if (ty & s) > 0 else 0
        d += s * s * ((3 * rx) ^ ry)
        # Rotate the quadrant so the curve stays continuous.
        if ry == 0:
            if rx == 1:
                tx = s - 1 - tx
                ty = s - 1 - ty
            tx, ty = ty, tx
        s >>= 1
    return acc + d


def find(entries: list[Entry], tile_id: int) -> Entry | None:
    """The entry covering `tile_id`, honouring RUN LENGTHS.

    A run is how PMTiles stores "these N consecutive tiles are byte-identical" — overwhelmingly
    common in a sparse archive, where thousands of empty ocean tiles share one blob. Binary search
    for the last entry at or below the id, then check the run actually reaches it; forgetting that
    check returns a neighbouring tile's bytes, which renders as data in the wrong place.
    """
    lo, hi = 0, len(entries) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if tile_id < entries[mid].tile_id:
            hi = mid - 1
        elif tile_id > entries[mid].tile_id:
            lo = mid + 1
        else:
            return entries[mid]
    if hi < 0:
        return None
    entry = entries[hi]
    if entry.run_length == 0:                       # a leaf pointer covers everything after it
        return entry
    if tile_id - entry.tile_id < entry.run_length:
        return entry
    return None


# Header + root directory per archive. Small (a root directory is capped at 16 KB by the spec) and
# the hot path for every tile, so caching it turns a 3-request tile into a 1-request tile. Keyed by
# the object key; `forget` is called when a re-tile repoints the layer at a new key.
_CACHE: dict[str, tuple[Header, list[Entry]]] = {}
_LEAVES: dict[tuple[str, int, int], list[Entry]] = {}
_MAX_LEAVES = 256


def forget(key: str | None = None) -> None:
    if key is None:
        _CACHE.clear()
        _LEAVES.clear()
        return
    _CACHE.pop(key, None)
    for k in [k for k in _LEAVES if k[0] == key]:
        _LEAVES.pop(k, None)


def open_archive(key: str, fetch) -> tuple[Header, list[Entry]]:
    """`(header, root_directory)` for an archive, reading at most two ranges and caching both.

    `fetch(offset, length) -> bytes` does one HTTP Range read.
    """
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    header = parse_header(fetch(0, HEADER_LEN))
    root = parse_directory(decompress(
        fetch(header.root_offset, header.root_length), header.internal_compression))
    _CACHE[key] = (header, root)
    return header, root


def get_tile(key: str, fetch, z: int, x: int, y: int) -> tuple[bytes, Header] | None:
    """One tile's DECOMPRESSED bytes, or None when the archive has no tile there.

    "No tile" is the normal case, not an error — an archive is sparse, and a client scanning a
    viewport asks for tiles that were never written. Returning None lets the route answer 204 rather
    than manufacturing a 404 the client would retry.

    Decompressed rather than passed through: the response then needs no `Content-Encoding` contract
    with whatever proxy sits in front, and a tile is tens of kilobytes.
    """
    header, root = open_archive(key, fetch)
    if z < header.min_zoom or z > header.max_zoom:
        return None
    try:
        tile_id = zxy_to_tile_id(z, x, y)
    except PMTilesError:
        return None

    entries = root
    # The spec allows nested leaves; in practice one level. Bounded so a malformed archive cannot
    # spin here.
    for _ in range(4):
        entry = find(entries, tile_id)
        if entry is None:
            return None
        if entry.run_length > 0:
            raw = fetch(header.data_offset + entry.offset, entry.length)
            return decompress(raw, header.tile_compression), header
        cache_key = (key, entry.offset, entry.length)
        leaf = _LEAVES.get(cache_key)
        if leaf is None:
            leaf = parse_directory(decompress(
                fetch(header.leaf_offset + entry.offset, entry.length),
                header.internal_compression))
            if len(_LEAVES) >= _MAX_LEAVES:
                _LEAVES.pop(next(iter(_LEAVES)))
            _LEAVES[cache_key] = leaf
        entries = leaf
    return None


def metadata(key: str, fetch) -> dict:
    """The archive's JSON metadata — tippecanoe puts the layer name and field types here."""
    import json

    header, _ = open_archive(key, fetch)
    if not header.metadata_length:
        return {}
    try:
        raw = decompress(fetch(header.metadata_offset, header.metadata_length),
                         header.internal_compression)
        return json.loads(raw)
    except (PMTilesError, ValueError):
        return {}
