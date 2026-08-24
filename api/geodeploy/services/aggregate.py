"""V-16 Dashboard — server-side summarisation of a vector layer.

Indicators, gauges and charts need ONE number (or one small list of grouped numbers), not a feature
set. Shipping the features to the browser and reducing them there is the wrong shape twice: it is
megabytes per widget per filter change, and it silently disagrees with the map, which only ever
loaded the current viewport. So the summarisation happens here and only the answer travels.

WHICH ENGINE, and why this is not "matching existing conventions" but the measured-fastest route
for each backend:

  * A **PostGIS** layer is aggregated in PostGIS. The table is already there, already indexed, and
    already has a GiST index on the geometry column — an ST_Intersects-filtered SUM is one indexed
    pass. Exporting it to DuckDB to aggregate would copy the whole table over the wire to save
    nothing, because the aggregate is a single pass either way and the copy is pure added cost.
  * A **GeoParquet** layer is aggregated in DuckDB, in place, over object storage. It is columnar,
    so a SUM over one column reads one column; the GeoParquet covering-bbox column and the prep
    grid's hive partitions let DuckDB skip row groups and whole files for a spatial filter. Loading
    it into PostGIS first would mean the ingest this platform deliberately avoids (see
    notes §0h — GeoParquet layers are read in place, never ingested).

So the "fastest route" question resolves to: use each layer's own storage, and make the SPATIAL
filter cheap in both. That is the opposite of a single unified access path, and it is deliberate.

THE ONE PLACE THAT IS NOT PURE SQL: a geometry filter on a GeoParquet layer. The DuckDB spatial
extension cannot be loaded on the read path — its GeoParquet decoder rejects files tagged with spec
versions it does not know, and that check fires on `read_parquet` the moment spatial is loaded (see
`duckdb_engine._connect_read`). So the bbox of the selection prunes in SQL (cheap, and it is what
skips the row groups), and the exact point-in-polygon test runs in shapely over the surviving
candidates, vectorised. `CANDIDATE_CAP` bounds that second step; a query that hits the cap reports
`capped: true` rather than quietly answering from a sample.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Aggregations. `count` takes no field.
OPS = {"count", "sum", "avg", "min", "max"}

#: `date_trunc` units, spelled the same in Postgres and DuckDB — which is why the time-bucket
#: implementation below is genuinely shared rather than two lookalike branches.
BUCKETS = {"hour", "day", "week", "month", "quarter", "year"}

#: How many grouped rows a chart may receive. A bar chart with more bars than pixels is not a chart,
#: and the cap is what keeps a group-by on a high-cardinality column from returning the whole column.
MAX_GROUPS = 200

#: Rows a table widget may receive in one page.
MAX_ROWS = 500

#: Candidates the shapely exact-intersection step will consider for a GeoParquet geometry filter.
#: Vectorised `shapely.intersects` runs at roughly a million simple geometries a second, so this is
#: a fraction of a second of work; past it the answer is reported as capped instead of guessed.
CANDIDATE_CAP = 250_000

#: The sample a DISTINCT-values selector reads. Same reasoning as `duckdb_engine.field_stats`: the
#: point is a usable dropdown, not a census.
DISTINCT_LIMIT = 200


class AggregateError(ValueError):
    """A caller error (unknown field, unsupported op) — the router turns this into a 400."""


# ── shared: identifier quoting + filter validation ───────────────────────────

def qi(ident: str) -> str:
    """Quote a SQL identifier. Neither driver can parameterise one, and a column name here comes
    from a request; doubling embedded quotes is the whole defence and it is sufficient. Callers
    ALSO check the name against the layer's catalog — both, deliberately, exactly as
    `routers/data/vector.field_stats` does."""
    return '"' + str(ident).replace('"', '""') + '"'


def _check_field(name: str | None, known: set[str], what: str) -> str:
    if not name:
        raise AggregateError(f"A {what} is required.")
    if known and name not in known:
        raise AggregateError(f"No such field on this layer: {name}")
    return name


def normalize_filters(filters: Any, known: set[str]) -> list[dict]:
    """Validate the attribute filters a dashboard sends.

    Shape: `{field, op, values?|min?|max?}` where op is one of in / eq / between / gte / lte /
    notnull. Multiple filters combine with AND — that is the cross-filter semantics the dashboard
    promises (a selector AND a map selection both active narrow, they do not replace each other).
    """
    out: list[dict] = []
    for f in (filters or [])[:16]:
        if not isinstance(f, dict):
            continue
        op = str(f.get("op") or "in").lower()
        field = _check_field(f.get("field"), known, "filter field")
        if op == "in":
            values = [v for v in (f.get("values") or []) if v is not None][:500]
            if not values:
                continue          # an empty selection means "no selection", not "match nothing"
            out.append({"field": field, "op": "in", "values": values})
        elif op == "eq":
            if f.get("value") is None:
                continue
            out.append({"field": field, "op": "in", "values": [f["value"]]})
        elif op in ("between", "gte", "lte"):
            lo, hi = f.get("min"), f.get("max")
            lo = _maybe_number(lo)
            hi = _maybe_number(hi)
            if op == "gte":
                hi = None
            if op == "lte":
                lo = None
            if lo is None and hi is None:
                continue
            out.append({"field": field, "op": "between", "min": lo, "max": hi,
                        "date": bool(f.get("date"))})
        elif op == "daterange":
            lo = f.get("min") or None
            hi = f.get("max") or None
            if not lo and not hi:
                continue
            out.append({"field": field, "op": "daterange", "min": lo, "max": hi})
        elif op == "notnull":
            out.append({"field": field, "op": "notnull"})
    return out


def _maybe_number(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── PostGIS half ─────────────────────────────────────────────────────────────

def _srid_of(layer) -> int | None:
    """The layer's numeric SRID, from its stored `EPSG:n` CRS.

    Worth the parse: the spatial predicate below transforms the SELECTION into the layer's SRID, and
    writing that as `ST_Transform(…, ST_SRID(geom))` makes the right-hand side depend on the row, so
    Postgres cannot treat it as a constant and the GiST index on the geometry column goes unused —
    a full-table distance test per filter change on the busiest query the dashboard makes. A literal
    integer makes it a constant expression. Unknown CRS falls back to the per-row form, which is
    slower but never wrong.
    """
    crs = getattr(layer, "crs", None)
    if not isinstance(crs, str):
        return None
    text = crs.strip().upper()
    if text.startswith("EPSG:"):
        text = text[5:]
    try:
        return int(text)
    except ValueError:
        return None


def _pg_where(filters: list[dict], geometry: dict | None, geom_col: str,
              params: dict, srid: int | None = None) -> str:
    """WHERE clause + bound parameters for the PostGIS path.

    Values are BOUND, not interpolated — only identifiers reach the SQL text, and those went through
    the catalog check plus `qi`. The geometry is bound as GeoJSON text and handed to
    `ST_GeomFromGeoJSON`, so a hand-crafted selection cannot become SQL.
    """
    parts: list[str] = []
    for i, f in enumerate(filters):
        col = qi(f["field"])
        if f["op"] == "in":
            key = f"f{i}"
            params[key] = [str(v) for v in f["values"]]
            # Cast to text on BOTH sides: a selector reads its options as strings (that is what a
            # <select> holds), and comparing them to an int column would fail the whole query rather
            # than match. The column's own index is lost by the cast, but a filter list is small and
            # the alternative is a per-type branch that gets one type wrong.
            #
            # `CAST(... AS text[])` on the PARAMETER is not decoration: the driver is asyncpg, which
            # asks Postgres to infer each parameter's type, and a bare `= ANY($1)` against a
            # `::text` expression gives it nothing to infer from — the query fails with "could not
            # determine data type of parameter" before it ever runs.
            parts.append(f"{col}::text = ANY(CAST(:{key} AS text[]))")
        elif f["op"] == "between":
            if f.get("min") is not None:
                params[f"f{i}lo"] = f["min"]
                parts.append(f"{col}::double precision >= :f{i}lo")
            if f.get("max") is not None:
                params[f"f{i}hi"] = f["max"]
                parts.append(f"{col}::double precision <= :f{i}hi")
        elif f["op"] == "daterange":
            if f.get("min"):
                params[f"f{i}lo"] = str(f["min"])
                parts.append(f"{col} >= CAST(:f{i}lo AS timestamp)")
            if f.get("max"):
                params[f"f{i}hi"] = str(f["max"])
                parts.append(f"{col} <= CAST(:f{i}hi AS timestamp)")
        elif f["op"] == "notnull":
            parts.append(f"{col} IS NOT NULL")
    if geometry:
        params["gdgeom"] = _dumps(geometry)
        target = str(srid) if srid else f"ST_SRID({qi(geom_col)})"
        sel = (f"ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(CAST(:gdgeom AS text)), 4326), {target})")
        # ST_Intersects, not ST_Within: a selection is a question about what the visitor drew over,
        # and a polygon that straddles the boundary of a drawn box is part of what they pointed at.
        # `&&` first so the GiST index prunes before the exact test.
        parts.append(f"{qi(geom_col)} && {sel} AND ST_Intersects({qi(geom_col)}, {sel})")
    return ("WHERE " + " AND ".join(parts)) if parts else ""


def _dumps(obj) -> str:
    import json
    return json.dumps(obj)


def _pg_value_expr(op: str, field: str | None) -> str:
    if op == "count":
        return "COUNT(*)"
    col = f"{qi(field)}::double precision"
    return {"sum": f"SUM({col})", "avg": f"AVG({col})",
            "min": f"MIN({col})", "max": f"MAX({col})"}[op]


async def postgis_aggregate(db, layer, spec: dict) -> dict:
    """The PostGIS half of `aggregate`. Same contract as `parquet_aggregate`."""
    from sqlalchemy import text

    known = _known_columns(layer)
    op = spec.get("op", "count")
    if op not in OPS:
        raise AggregateError(f"Unsupported aggregation: {op}")
    field = None if op == "count" else _check_field(spec.get("field"), known, "field")
    filters = normalize_filters(spec.get("filters"), known)
    geometry = spec.get("geometry") if isinstance(spec.get("geometry"), dict) else None

    if not layer.schema_name or not layer.table_name:
        raise AggregateError("This layer has no table.")
    table = f"{qi(layer.schema_name)}.{qi(layer.table_name)}"
    geom_col = layer.geometry_column or "geom"
    params: dict = {}
    where = _pg_where(filters, geometry, geom_col, params, _srid_of(layer))
    value = _pg_value_expr(op, field)

    group_by = spec.get("groupBy")
    if not group_by:
        row = (await db.execute(text(f"SELECT {value} AS v, COUNT(*) AS n FROM {table} {where}"),
                                params)).first()
        return {"op": op, "value": _num(row[0]) if row else None,
                "count": int(row[1]) if row else 0}

    _check_field(group_by, known, "group-by field")
    bucket = spec.get("timeBucket")
    if bucket:
        if bucket not in BUCKETS:
            raise AggregateError(f"Unsupported time bucket: {bucket}")
        key_expr = f"date_trunc('{bucket}', {qi(group_by)}::timestamp)"
        order = "ORDER BY 1 ASC"        # a time series is ALWAYS chronological, never by value
    else:
        key_expr = f"{qi(group_by)}::text"
        order = {"value_asc": "ORDER BY 2 ASC NULLS LAST",
                 "key_asc": "ORDER BY 1 ASC NULLS LAST"}.get(
                     spec.get("sort") or "value_desc", "ORDER BY 2 DESC NULLS LAST")
    limit = max(2, min(int(spec.get("limit") or 12), MAX_GROUPS))
    sql = (f"SELECT {key_expr} AS k, {value} AS v, COUNT(*) AS n FROM {table} {where} "
           f"GROUP BY 1 {order} LIMIT {limit + 1}")
    rows = (await db.execute(text(sql), params)).fetchall()
    return _groups_out(rows, limit, bucket)


async def postgis_table(db, layer, spec: dict) -> dict:
    """Attribute ROWS for the list/table + details widgets. Ships a per-row bbox so a click can zoom
    the map without a second request — the whole point of the widget is click-to-zoom-and-highlight,
    and a round trip per click would make that feel broken."""
    from sqlalchemy import text

    known = _known_columns(layer)
    fields = [f for f in (spec.get("fields") or []) if f in known] or sorted(known)[:8]
    filters = normalize_filters(spec.get("filters"), known)
    geometry = spec.get("geometry") if isinstance(spec.get("geometry"), dict) else None
    if not layer.schema_name or not layer.table_name:
        raise AggregateError("This layer has no table.")
    table = f"{qi(layer.schema_name)}.{qi(layer.table_name)}"
    geom_col = layer.geometry_column or "geom"
    id_col = layer.id_column or None
    params: dict = {}
    where = _pg_where(filters, geometry, geom_col, params, _srid_of(layer))

    limit = max(1, min(int(spec.get("limit") or 50), MAX_ROWS))
    offset = max(0, int(spec.get("offset") or 0))
    sort = spec.get("sort")
    order = ""
    if sort and sort in known:
        direction = "DESC" if str(spec.get("dir")).lower() == "desc" else "ASC"
        order = f"ORDER BY {qi(sort)} {direction} NULLS LAST"

    # The bbox travels as four numbers in lon/lat, computed by the database. Sending the geometry
    # itself would be the row's largest field by far and the widget only ever needs to fly there.
    g4326 = f"ST_Transform({qi(geom_col)}, 4326)"
    cols = ", ".join(qi(f) for f in fields)
    id_expr = f"{qi(id_col)}::text" if id_col and id_col in known else "NULL::text"
    sql = (f"SELECT {id_expr} AS __id, ST_XMin({g4326}), ST_YMin({g4326}), "
           f"ST_XMax({g4326}), ST_YMax({g4326})" + (f", {cols}" if cols else "") +
           f" FROM {table} {where} {order} LIMIT {limit} OFFSET {offset}")
    rows = (await db.execute(text(sql), params)).fetchall()
    total = (await db.execute(text(f"SELECT COUNT(*) FROM {table} {where}"), params)).scalar()
    return _rows_out(rows, fields, int(total or 0), limit, offset)


async def postgis_distinct(db, layer, field: str, limit: int = DISTINCT_LIMIT) -> dict:
    """The option list a category selector shows, and the min/max a range slider spans."""
    from sqlalchemy import text

    known = _known_columns(layer)
    _check_field(field, known, "field")
    if not layer.schema_name or not layer.table_name:
        raise AggregateError("This layer has no table.")
    table = f"{qi(layer.schema_name)}.{qi(layer.table_name)}"
    col = qi(field)
    dtype = (await db.execute(text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = :s AND table_name = :t AND column_name = :c"),
        {"s": layer.schema_name, "t": layer.table_name, "c": field})).scalar()
    kind = _pg_kind(dtype or "")
    if kind in ("numeric", "date"):
        row = (await db.execute(text(
            f"SELECT MIN({col}), MAX({col}) FROM {table} WHERE {col} IS NOT NULL"))).first()
        return {"kind": kind, "min": _scalar(row[0]) if row else None,
                "max": _scalar(row[1]) if row else None}
    rows = (await db.execute(text(
        f"SELECT {col}::text AS v, COUNT(*) AS n FROM {table} WHERE {col} IS NOT NULL "
        f"GROUP BY 1 ORDER BY n DESC LIMIT {int(limit) + 1}"))).fetchall()
    return {"kind": "categorical",
            "values": [{"value": r[0], "count": int(r[1])} for r in rows[:limit]],
            "truncated": len(rows) > limit}


def _pg_kind(dtype: str) -> str:
    d = dtype.lower()
    if any(k in d for k in ("timestamp", "date", "time")):
        return "date"
    if any(k in d for k in ("int", "numeric", "decimal", "double", "real", "float", "serial")):
        return "numeric"
    return "categorical"


# ── GeoParquet / DuckDB half ─────────────────────────────────────────────────

def _duck_where(filters: list[dict], cols: dict) -> list[str]:
    """Attribute predicates for the DuckDB path.

    Literals are inlined rather than bound because DuckDB's Python parameter binding does not reach
    inside the `read_parquet(...)` source expression the rest of `duckdb_engine` builds, and the
    module's convention is to quote instead. Everything inlined here is either a validated
    identifier or a value pushed through `_lit`, which emits only a quoted string or a number.
    """
    parts: list[str] = []
    for f in filters:
        col = qi(f["field"])
        if f["op"] == "in":
            vals = ", ".join(_lit(str(v)) for v in f["values"])
            parts.append(f"CAST({col} AS VARCHAR) IN ({vals})")
        elif f["op"] == "between":
            if f.get("min") is not None:
                parts.append(f"TRY_CAST({col} AS DOUBLE) >= {float(f['min'])}")
            if f.get("max") is not None:
                parts.append(f"TRY_CAST({col} AS DOUBLE) <= {float(f['max'])}")
        elif f["op"] == "daterange":
            if f.get("min"):
                parts.append(f"TRY_CAST({col} AS TIMESTAMP) >= TIMESTAMP {_lit(str(f['min']))}")
            if f.get("max"):
                parts.append(f"TRY_CAST({col} AS TIMESTAMP) <= TIMESTAMP {_lit(str(f['max']))}")
        elif f["op"] == "notnull":
            parts.append(f"{col} IS NOT NULL")
    return parts


def _lit(text: str) -> str:
    """A SQL string literal. Doubling the quote is the whole escape and it is complete — the result
    cannot terminate early, and DuckDB does not honour a backslash escape inside a standard string."""
    return "'" + str(text).replace("'", "''") + "'"


def _duck_open(layer):
    """`(conn, src, meta, columns)` for a GeoParquet layer — the shared preamble of every DuckDB
    query here. Mirrors `duckdb_engine.query_features_geojson`'s opening, including reading the
    GeoParquet metadata WITHOUT the spatial extension (see the module docstring)."""
    from . import duckdb_engine
    from ..config import get_settings

    if not layer.s3_key:
        raise AggregateError("This layer has no data file yet.")
    settings = get_settings()
    meta_path, src = duckdb_engine._parquet_paths(f"s3://{settings.storage_bucket}/{layer.s3_key}")
    conn = duckdb_engine._connect_read()
    try:
        meta = duckdb_engine._read_geo_metadata(conn, meta_path)
        cols = {r[0]: (r[1] or "").upper()
                for r in conn.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
    except Exception:
        conn.close()
        raise
    return conn, src, meta, cols, meta_path


def _duck_bbox_prune(meta: dict, geometry: dict | None) -> tuple[str | None, Any]:
    """The cheap half of a geometry filter: a covering-bbox predicate that lets DuckDB skip row
    groups, plus the prepared shapely geometry the exact test will use.

    Returns `(predicate_or_None, prepared_geom_or_None)`. A layer with no covering column gets no
    predicate — it still answers correctly, just by scanning.
    """
    if not geometry:
        return None, None
    from shapely.geometry import shape
    from shapely import prepare
    geom = shape(geometry)
    prepare(geom)
    covering = meta.get("covering")
    if not covering:
        return None, geom
    src_epsg = meta.get("epsg") or "EPSG:4326"
    from . import duckdb_engine
    minx, miny, maxx, maxy = geom.bounds
    bbox = duckdb_engine._reproject_bbox([minx, miny, maxx, maxy], "EPSG:4326", src_epsg)
    col, fields = covering

    def ce(f):
        return f"struct_extract({qi(col)}, '{fields[f]}')"
    return (f"{ce('xmin')} <= {bbox[2]} AND {ce('xmax')} >= {bbox[0]} "
            f"AND {ce('ymin')} <= {bbox[3]} AND {ce('ymax')} >= {bbox[1]}"), geom


def _duck_geom_col(meta: dict, cols: dict) -> str:
    col = meta.get("column")
    if col and col in cols:
        return col
    for c in cols:
        if c.lower() in ("geometry", "geom", "wkb_geometry", "wkb"):
            return c
    raise AggregateError("No geometry column found on this layer.")


def _duck_reprojector(meta: dict):
    """A vectorised EPSG:file → EPSG:4326 transform, or None when the file is already 4326.
    The selection arrives in 4326, so either the geometries come to it or it goes to them; going to
    the geometries once per query beats reprojecting the selection per row group."""
    src_epsg = meta.get("epsg") or "EPSG:4326"
    if src_epsg == "EPSG:4326":
        return None
    import numpy as np
    from pyproj import Transformer
    from shapely import transform as shp_transform
    tr = Transformer.from_crs(src_epsg, "EPSG:4326", always_xy=True)

    def _coords(c):
        x, y = tr.transform(c[:, 0], c[:, 1])
        return np.column_stack([x, y])
    return lambda geoms: shp_transform(geoms, _coords)


def parquet_aggregate(layer, spec: dict) -> dict:
    """The GeoParquet half of `aggregate`. Runs in a threadpool (DuckDB is synchronous)."""
    known = _known_columns(layer)
    op = spec.get("op", "count")
    if op not in OPS:
        raise AggregateError(f"Unsupported aggregation: {op}")
    field = None if op == "count" else _check_field(spec.get("field"), known, "field")
    filters = normalize_filters(spec.get("filters"), known)
    geometry = spec.get("geometry") if isinstance(spec.get("geometry"), dict) else None
    group_by = spec.get("groupBy")
    bucket = spec.get("timeBucket")
    if group_by:
        _check_field(group_by, known, "group-by field")
    if bucket and bucket not in BUCKETS:
        raise AggregateError(f"Unsupported time bucket: {bucket}")

    conn, src, meta, cols, _ = _duck_open(layer)
    try:
        where = _duck_where(filters, cols)
        bbox_pred, geom = _duck_bbox_prune(meta, geometry)
        if bbox_pred:
            where.append(bbox_pred)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        if geom is None:
            # PURE SQL — one columnar pass, row groups pruned by the attribute predicates.
            value = _duck_value_expr(op, field)
            if not group_by:
                row = conn.execute(
                    f"SELECT {value}, COUNT(*) FROM {src} {where_sql}").fetchone()
                return {"op": op, "value": _num(row[0]) if row else None,
                        "count": int(row[1]) if row else 0}
            key_expr = (f"date_trunc('{bucket}', TRY_CAST({qi(group_by)} AS TIMESTAMP))"
                        if bucket else f"CAST({qi(group_by)} AS VARCHAR)")
            order = ("ORDER BY 1 ASC" if bucket else
                     {"value_asc": "ORDER BY 2 ASC", "key_asc": "ORDER BY 1 ASC"}.get(
                         spec.get("sort") or "value_desc", "ORDER BY 2 DESC"))
            limit = max(2, min(int(spec.get("limit") or 12), MAX_GROUPS))
            rows = conn.execute(
                f"SELECT {key_expr} AS k, {value} AS v, COUNT(*) AS n FROM {src} {where_sql} "
                f"GROUP BY 1 {order} LIMIT {limit + 1}").fetchall()
            return _groups_out(rows, limit, bucket)

        # GEOMETRY FILTER: bbox-pruned candidates out of SQL, exact test in shapely.
        return _parquet_spatial_aggregate(conn, src, meta, cols, where_sql, geom, spec,
                                          op, field, group_by, bucket)
    finally:
        conn.close()


def _duck_value_expr(op: str, field: str | None) -> str:
    if op == "count":
        return "COUNT(*)"
    col = f"TRY_CAST({qi(field)} AS DOUBLE)"
    return {"sum": f"SUM({col})", "avg": f"AVG({col})",
            "min": f"MIN({col})", "max": f"MAX({col})"}[op]


def _parquet_spatial_aggregate(conn, src, meta, cols, where_sql, geom, spec,
                               op, field, group_by, bucket) -> dict:
    """Exact spatial aggregation over a GeoParquet layer.

    Reads only the columns the answer needs — the geometry, the aggregated field and the group key —
    which is the difference between a few MB and the whole file. The intersection test is
    `shapely.intersects` over the whole candidate array at once (C, GIL released), not per row.
    """
    import numpy as np
    from shapely import from_wkb, intersects

    geom_col = _duck_geom_col(meta, cols)
    wanted = [f"{qi(geom_col)} AS __wkb"]
    if field:
        wanted.append(f"TRY_CAST({qi(field)} AS DOUBLE) AS __v")
    if group_by:
        wanted.append((f"date_trunc('{bucket}', TRY_CAST({qi(group_by)} AS TIMESTAMP))"
                       if bucket else f"CAST({qi(group_by)} AS VARCHAR)") + " AS __k")
    rows = conn.execute(
        f"SELECT {', '.join(wanted)} FROM {src} {where_sql} LIMIT {CANDIDATE_CAP + 1}").fetchall()
    capped = len(rows) > CANDIDATE_CAP
    rows = rows[:CANDIDATE_CAP]
    if not rows:
        return ({"op": op, "value": None, "count": 0, "capped": capped} if not group_by
                else {"groups": [], "truncated": False, "capped": capped})

    geoms = from_wkb([bytes(r[0]) if r[0] is not None else None for r in rows], on_invalid="ignore")
    reproject = _duck_reprojector(meta)
    if reproject is not None:
        geoms = reproject(geoms)
    hit = intersects(geoms, geom)

    values = None
    if field:
        idx = 1
        values = np.array([_num(r[idx]) for r in rows], dtype="float64")
    keys = None
    if group_by:
        idx = 2 if field else 1
        keys = [r[idx] for r in rows]

    if not group_by:
        return {"op": op, "value": _reduce(op, values, hit), "count": int(hit.sum()),
                "capped": capped}

    buckets: dict[Any, list[int]] = {}
    for i, ok in enumerate(hit):
        if not ok:
            continue
        buckets.setdefault(keys[i], []).append(i)
    out = []
    for key, idxs in buckets.items():
        mask = np.zeros(len(rows), dtype=bool)
        mask[idxs] = True
        out.append((key, _reduce(op, values, mask), len(idxs)))
    limit = max(2, min(int(spec.get("limit") or 12), MAX_GROUPS))
    if bucket:
        out.sort(key=lambda r: (r[0] is None, r[0]))
    else:
        sort = spec.get("sort") or "value_desc"
        if sort == "key_asc":
            out.sort(key=lambda r: (r[0] is None, str(r[0])))
        else:
            out.sort(key=lambda r: (r[1] is None, r[1]), reverse=(sort != "value_asc"))
    result = _groups_out(out, limit, bucket)
    result["capped"] = capped
    return result


def _reduce(op: str, values, mask) -> float | None:
    """One aggregation over the rows the mask keeps. `count` never touches `values`, which is why a
    counting widget on a layer with no numeric column at all still works."""
    import numpy as np
    n = int(mask.sum())
    if op == "count":
        return float(n)
    if values is None or n == 0:
        return None
    picked = values[mask]
    picked = picked[~np.isnan(picked)]
    if picked.size == 0:
        return None
    return float({"sum": np.sum, "avg": np.mean, "min": np.min, "max": np.max}[op](picked))


def parquet_table(layer, spec: dict) -> dict:
    """The GeoParquet half of `table`. Same output shape as `postgis_table`, including the per-row
    lon/lat bbox — computed here with shapely rather than in SQL, since there is no spatial
    extension on this connection."""
    from shapely import bounds as shp_bounds, from_wkb, intersects

    known = _known_columns(layer)
    fields = [f for f in (spec.get("fields") or []) if f in known] or sorted(known)[:8]
    filters = normalize_filters(spec.get("filters"), known)
    geometry = spec.get("geometry") if isinstance(spec.get("geometry"), dict) else None
    limit = max(1, min(int(spec.get("limit") or 50), MAX_ROWS))
    offset = max(0, int(spec.get("offset") or 0))

    conn, src, meta, cols, _ = _duck_open(layer)
    try:
        where = _duck_where(filters, cols)
        bbox_pred, geom = _duck_bbox_prune(meta, geometry)
        if bbox_pred:
            where.append(bbox_pred)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        geom_col = _duck_geom_col(meta, cols)
        sel = ", ".join([f"{qi(geom_col)} AS __wkb"] + [qi(f) for f in fields])
        sort = spec.get("sort")
        order = ""
        if sort and sort in known and geom is None:
            # Only when there is no exact spatial step: with one, SQL order would be the order of
            # the CANDIDATES, and the page shown would not be the page requested.
            order = f"ORDER BY {qi(sort)} " + ("DESC" if str(spec.get('dir')).lower() == "desc" else "ASC")

        if geom is None:
            total = conn.execute(f"SELECT COUNT(*) FROM {src} {where_sql}").fetchone()[0]
            rows = conn.execute(
                f"SELECT {sel} FROM {src} {where_sql} {order} LIMIT {limit} OFFSET {offset}"
            ).fetchall()
        else:
            candidates = conn.execute(
                f"SELECT {sel} FROM {src} {where_sql} LIMIT {CANDIDATE_CAP}").fetchall()
            geoms = from_wkb([bytes(r[0]) if r[0] is not None else None for r in candidates],
                             on_invalid="ignore")
            reproject = _duck_reprojector(meta)
            if reproject is not None:
                geoms = reproject(geoms)
            hit = intersects(geoms, geom)
            kept = [candidates[i] for i, ok in enumerate(hit) if ok]
            if sort and sort in known:
                col_i = 1 + fields.index(sort) if sort in fields else None
                if col_i is not None:
                    kept.sort(key=lambda r: (r[col_i] is None, r[col_i]),
                              reverse=str(spec.get("dir")).lower() == "desc")
            total = len(kept)
            rows = kept[offset:offset + limit]

        wkbs = [bytes(r[0]) if r[0] is not None else None for r in rows]
        geoms = from_wkb(wkbs, on_invalid="ignore")
        reproject = _duck_reprojector(meta)
        if reproject is not None:
            geoms = reproject(geoms)
        boxes = shp_bounds(geoms) if len(rows) else []
        out_rows = []
        for i, r in enumerate(rows):
            box = boxes[i] if len(boxes) else None
            props = {fields[j]: _jsonable(r[j + 1]) for j in range(len(fields))}
            out_rows.append({"id": None, "bbox": _bbox_or_none(box), "props": props})
        return {"rows": out_rows, "fields": fields, "total": int(total),
                "limit": limit, "offset": offset}
    finally:
        conn.close()


def parquet_distinct(layer, field: str, limit: int = DISTINCT_LIMIT) -> dict:
    """The GeoParquet half of `distinct`."""
    known = _known_columns(layer)
    _check_field(field, known, "field")
    conn, src, meta, cols, _ = _duck_open(layer)
    try:
        dtype = cols.get(field, "")
        if any(t in dtype for t in ("TIMESTAMP", "DATE", "TIME")):
            row = conn.execute(
                f"SELECT MIN({qi(field)}), MAX({qi(field)}) FROM {src}").fetchone()
            return {"kind": "date", "min": _scalar(row[0]), "max": _scalar(row[1])}
        if any(t in dtype for t in ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "HUGEINT", "NUMERIC")):
            row = conn.execute(
                f"SELECT MIN({qi(field)}), MAX({qi(field)}) FROM {src}").fetchone()
            return {"kind": "numeric", "min": _scalar(row[0]), "max": _scalar(row[1])}
        rows = conn.execute(
            f"SELECT CAST({qi(field)} AS VARCHAR) AS v, COUNT(*) AS n FROM {src} "
            f"WHERE {qi(field)} IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT {int(limit) + 1}"
        ).fetchall()
        return {"kind": "categorical",
                "values": [{"value": r[0], "count": int(r[1])} for r in rows[:limit]],
                "truncated": len(rows) > limit}
    finally:
        conn.close()


# ── shared output shaping ────────────────────────────────────────────────────

def _known_columns(layer) -> set[str]:
    import json
    try:
        return {c.get("name") for c in json.loads(layer.columns or "[]") if isinstance(c, dict)}
    except (ValueError, TypeError):
        return set()


def _groups_out(rows, limit: int, bucket: str | None) -> dict:
    groups = []
    for r in rows[:limit]:
        key = r[0]
        groups.append({"key": _scalar(key), "value": _num(r[1]), "count": int(r[2] or 0)})
    return {"groups": groups, "truncated": len(rows) > limit,
            "bucket": bucket or None}


def _rows_out(rows, fields, total: int, limit: int, offset: int) -> dict:
    out = []
    for r in rows:
        bbox = [r[1], r[2], r[3], r[4]]
        props = {fields[j]: _jsonable(r[j + 5]) for j in range(len(fields))}
        out.append({"id": r[0], "bbox": _bbox_or_none(bbox), "props": props})
    return {"rows": out, "fields": fields, "total": total, "limit": limit, "offset": offset}


def _bbox_or_none(box):
    if box is None:
        return None
    try:
        vals = [float(v) for v in box]
    except (TypeError, ValueError):
        return None
    if any(v != v for v in vals):     # NaN — an empty or unparseable geometry
        return None
    return vals


def _num(value):
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _scalar(value):
    """A cell value as something JSON can carry. Dates become ISO strings so a time-bucketed chart
    can label its axis without the client guessing at a format."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _jsonable(value):
    from . import duckdb_engine
    return duckdb_engine._jsonable(value)


# ── the exact geometry of a clicked feature ──────────────────────────────────
# The map widget's third selection mode is "click a feature" — and a clicked feature has to yield
# its REAL geometry, not the one MapLibre drew. `queryRenderedFeatures` returns geometry clipped to
# the vector TILE the click landed in, so zonal statistics over a parcel that straddles a tile
# boundary would be computed over the visible fragment and reported as the parcel's. That is a wrong
# number with no symptom, which is the worst kind. So the geometry comes from the source of truth.
#
# GeoParquet layers already had `/identify` for attributes, but it deliberately ships no geometry
# (the deck transports carry geometry separately). PostGIS layers had no public geometry route at
# all. One endpoint, both backends.

async def postgis_pick(db, layer, lng: float, lat: float, tol: float = 0.0) -> dict | None:
    from sqlalchemy import text

    if not layer.schema_name or not layer.table_name:
        raise AggregateError("This layer has no table.")
    table = f"{qi(layer.schema_name)}.{qi(layer.table_name)}"
    geom = qi(layer.geometry_column or "geom")
    known = sorted(_known_columns(layer))[:40]
    cols = ", ".join(qi(f) for f in known)
    # The click point is built in 4326 and transformed INTO the layer's SRID, so the index on the
    # layer's own column is usable — transforming the column instead would defeat it on every row.
    # `tol` widens the hit box for point and line layers, where an exact intersection almost never
    # happens; it is in degrees and the client scales it by zoom.
    srid = _srid_of(layer)
    target = str(srid) if srid else f"ST_SRID({geom})"
    pt = (f"ST_Transform(ST_Buffer(ST_SetSRID(ST_MakePoint(:lng, :lat), 4326), :tol), {target})"
          if tol > 0 else
          f"ST_Transform(ST_SetSRID(ST_MakePoint(:lng, :lat), 4326), {target})")
    # NEAREST wins, not "whichever row the scan reached first". With a zero-width probe the two are
    # the same thing, but a click radius gives the hit box area, and over dense points or adjacent
    # parcels several features intersect it — picking arbitrarily among them means clicking the same
    # spot twice can select two different features. `<->` is PostGIS's KNN operator and it reads the
    # same GiST index the `&&` prefilter uses, so the ordering is not a second pass.
    order = f" ORDER BY {geom} <-> {pt}" if tol > 0 else ""
    sql = (f"SELECT ST_AsGeoJSON(ST_Transform({geom}, 4326))" + (f", {cols}" if cols else "") +
           f" FROM {table} WHERE {geom} && {pt} AND ST_Intersects({geom}, {pt}){order} LIMIT 1")
    params = {"lng": float(lng), "lat": float(lat)}
    if tol > 0:
        params["tol"] = float(tol)
    row = (await db.execute(text(sql), params)).first()
    if not row or not row[0]:
        return None
    import json
    return {"geometry": json.loads(row[0]),
            "props": {known[i]: _jsonable(row[i + 1]) for i in range(len(known))}}


def parquet_pick(layer, lng: float, lat: float, tol: float = 0.0) -> dict | None:
    from shapely import from_wkb, intersects, to_geojson
    from shapely.geometry import Point

    known = sorted(_known_columns(layer))[:40]
    conn, src, meta, cols, _ = _duck_open(layer)
    try:
        geom_col = _duck_geom_col(meta, cols)
        probe = Point(float(lng), float(lat))
        if tol > 0:
            probe = probe.buffer(float(tol))
        bbox_pred, _ = _duck_bbox_prune(meta, __import__("json").loads(to_geojson(probe)))
        where = ("WHERE " + bbox_pred) if bbox_pred else ""
        fields = [f for f in known if f in cols]
        sel = ", ".join([f"{qi(geom_col)} AS __wkb"] + [qi(f) for f in fields])
        # A click resolves against a handful of candidates once the covering bbox has pruned; the
        # cap is a guard for a layer with no covering column, where this degrades to a scan.
        rows = conn.execute(f"SELECT {sel} FROM {src} {where} LIMIT 5000").fetchall()
        if not rows:
            return None
        geoms = from_wkb([bytes(r[0]) if r[0] is not None else None for r in rows],
                         on_invalid="ignore")
        reproject = _duck_reprojector(meta)
        if reproject is not None:
            geoms = reproject(geoms)
        hit = intersects(geoms, probe)
        # Nearest of the intersecting candidates, for the same reason the PostGIS path orders by
        # `<->`: a click radius can cover several features and "first in file order" would make the
        # same click select different ones as the file is rewritten.
        best, best_d = None, None
        centre = probe.centroid if tol > 0 else probe
        for i, ok in enumerate(hit):
            if not ok:
                continue
            d = geoms[i].distance(centre)
            if best_d is None or d < best_d:
                best, best_d = i, d
            if best_d == 0 and tol <= 0:
                break
        if best is None:
            return None
        import json
        return {"geometry": json.loads(to_geojson(geoms[best])),
                "props": {fields[j]: _jsonable(rows[best][j + 1]) for j in range(len(fields))}}
    finally:
        conn.close()
