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
#: `bounds` is not a summary of a column like the others — it is the EXTENT of whatever the current
#: filter selects, four numbers in lon/lat. It lives here rather than in its own endpoint because it
#: takes exactly the same filters, geometry and joins as every other question the dashboard asks,
#: and a second endpoint would be a second place to keep that in step.
OPS = {"count", "sum", "avg", "min", "max", "bounds"}
#: The ops that summarise no column and therefore need no `field`.
FIELDLESS_OPS = {"count", "bounds"}

#: `date_trunc` units, spelled the same in Postgres and DuckDB — which is why the time-bucket
#: implementation below is genuinely shared rather than two lookalike branches.
BUCKETS = {"hour", "day", "week", "month", "quarter", "year"}

#: How many grouped rows a chart may receive. A bar chart with more bars than pixels is not a chart,
#: and the cap is what keeps a group-by on a high-cardinality column from returning the whole column.
MAX_GROUPS = 200

#: The ceiling for a KEYS-ONLY request, which is a different question with a different answer.
#:
#: `MAX_GROUPS` is a CHART's limit: 200 bars is already past the point of being readable, so
#: clamping there protects a widget from an author asking for something nobody can look at. A key
#: set is not looked at — it is a predicate the dashboard's map tests features against — and 200 of
#: them is a very small selection.
#:
#: They were the same number until the map's linked filter asked for 5 000 and silently got 200, so
#: the map reported "over 5 000 matching features" the moment a filter matched 201. One clamp
#: answering two unrelated questions is how that happens; hence two.
MAX_KEYS = 20000

#: Rows a table widget may receive in one page.
MAX_ROWS = 500

#: Candidates the shapely exact-intersection step will consider for a GeoParquet geometry filter.
#: Vectorised `shapely.intersects` runs at roughly a million simple geometries a second, so this is
#: a fraction of a second of work; past it the answer is reported as capped instead of guessed.
CANDIDATE_CAP = 250_000

#: The sample a DISTINCT-values selector reads. Same reasoning as `duckdb_engine.field_stats`: the
#: point is a usable dropdown, not a census.
DISTINCT_LIMIT = 200


#: Entries and seconds for the answer cache below. Same sizing reasoning as `zonal.py`: this exists
#: to absorb the burst of identical questions ONE interaction produces, not to be a store.
CACHE_MAX = 512
CACHE_TTL = 300

#: key -> (stamp, answer). Process-local, like the zonal cache — a second worker simply recomputes.
_cache: dict[str, tuple[float, dict]] = {}


class AggregateError(ValueError):
    """A caller error (unknown field, unsupported op) — the router turns this into a 400."""


# ── answer cache ─────────────────────────────────────────────────────────────
# WHY. Cross-filtering means one act by one visitor fans out into a query per wired widget, and the
# widgets over a layer very often ask overlapping questions of the SAME selection. Nothing here was
# cached, so eight widgets over one drawn polygon meant eight independent scans, and clearing the
# selection then drawing the same box again paid the whole cost a second time. The spatial path is
# where this hurts: measured at 750 ms for a broad selection over 200 k features (and that is on
# local disk, before object-storage latency).
#
# This is deliberately the lever that was chosen over making the spatial test itself cleverer — see
# notes_for_future.md "FAILED APPROACH … testing a dashboard geometry filter in the LAYER'S CRS",
# which traded a 1.8× win on selective queries for a 4× regression on broad ones. A cache removes
# the cost in BOTH cases instead of moving it between them.
#
# The key folds in the layer's storage identity, so it cannot serve one layer's numbers for another,
# and a GeoParquet re-prep changes `s3_key` (a new `parts-<hex>` prefix) and therefore self-
# invalidates. A PostGIS table is rewritten IN PLACE, which is why `invalidate()` exists and is
# called from the same place the public-layer caches are dropped.

def _layer_identity(layer) -> str:
    """What makes two layers different for caching. `s3_key` for a file-backed layer (it changes on
    re-prep), schema.table for PostGIS (it does not — hence `invalidate`)."""
    if getattr(layer, "storage_backend", "postgis") == "geoparquet":
        return f"gp:{getattr(layer, 's3_key', '')}"
    return f"pg:{getattr(layer, 'schema_name', '')}.{getattr(layer, 'table_name', '')}"


def cache_key(layer, spec: dict) -> str:
    """The question's identity. `sort_keys` + compact separators so two structurally identical specs
    hash the same however the client serialised them; geometry coordinates are NOT rounded, for the
    reason `zonal._canonical` gives — two selections differing in the sixth decimal are two
    selections, and conflating them reports one area's numbers under another's outline."""
    import hashlib
    import json
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]
    return f"{_layer_identity(layer)}|{getattr(layer, 'id', '?')}|{digest}"


def _cache_get(key: str) -> dict | None:
    import time
    hit = _cache.get(key)
    if not hit:
        return None
    stamp, value = hit
    if time.time() - stamp > CACHE_TTL:
        _cache.pop(key, None)
        return None
    # A copy, not the stored dict: callers annotate the answer they get back (the router and the
    # widgets both do), and handing out the cached object would let one response mutate the next.
    return dict(value)


def _cache_put(key: str, value: dict) -> dict:
    import time
    if len(_cache) >= CACHE_MAX:
        # Oldest-first eviction; a plain dict preserves insertion order, so this is LRU-ish without
        # carrying an OrderedDict for a cache this size. Mirrors zonal.py.
        for old in list(_cache)[:max(1, CACHE_MAX // 4)]:
            _cache.pop(old, None)
    _cache[key] = (time.time(), dict(value))
    return value


def invalidate(layer=None) -> None:
    """Drop cached answers — everything, or one layer's.

    Needed because a PostGIS layer is re-ingested IN PLACE: same schema.table, new rows. Serving the
    previous ingest's totals under it is a wrong answer with no symptom, which is exactly the failure
    mode `zonal.invalidate` exists to prevent on the raster side."""
    if layer is None:
        _cache.clear()
        return
    prefix = f"{_layer_identity(layer)}|"
    for key in [k for k in _cache if k.startswith(prefix)]:
        _cache.pop(key, None)


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


# ── joins between two layers ─────────────────────────────────────────────────
# An attribute filter is layer-scoped, because a predicate on `canton` means nothing against a table
# without that column. A RELATION is the author saying two layers describe the same things and naming
# the pair of columns that proves it — at which point the filter can travel.
#
# HOW, and why not the obvious way. The obvious way is to resolve the filter to a set of key values
# and send them along: `entrances.egid IN (…)`. That collapses the moment the key is
# high-cardinality — narrowing 3.4M buildings to one canton yields ~477k egids, which is not a
# predicate, it is a data transfer. So the join is pushed INTO the engine as a subquery, and the
# engine is chosen by what the two layers actually are:
#
#   GeoParquet + GeoParquet -> one DuckDB query reading both parquet sources
#   PostGIS    + PostGIS    -> one SQL subquery
#   mixed                   -> refused, and SAID so (see `join_note`), because the alternative is a
#                              silent half-filter and a number nobody can account for
#
# The subquery carries the SOURCE layer's own attribute filters. It does NOT carry the geometry:
# a geometry filter already applies to every target whatever its layer, so passing it here would
# apply it twice — once directly and once through the join — which for an intersection is harmless
# and for a future non-idempotent filter would not be.


#: Related layers one query may pull filters through. A LIST, not a single join, because two
#: different layers can each be filtering this one at the same time and "the first one wins" would
#: silently drop the other -- the same silent half-filter this whole design exists to avoid.
MAX_JOINS = 4


def _join_specs(spec: dict) -> list[dict]:
    raw = spec.get("joins")
    if isinstance(raw, dict):                     # tolerate the single-join form
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [j for j in raw[:MAX_JOINS] if isinstance(j, dict) and j.get("layer") is not None]


def _duck_join_predicates(spec: dict, cols: dict) -> list[str]:
    """One predicate per related layer, ANDed by the caller. A join that cannot be built is skipped
    here and reported by the ROUTER, which refused it before the query was assembled."""
    return [p for p in (_duck_one_join(j, cols) for j in _join_specs(spec)) if p]


def _duck_one_join(j: dict, cols: dict) -> str | None:
    other = j["layer"]
    right, left = _str_field(j.get("rightField")), _str_field(j.get("leftField"))
    if not right or not left or right not in cols:
        return None
    if getattr(other, "storage_backend", "postgis") != "geoparquet" or not getattr(other, "s3_key", None):
        return None

    from ..config import get_settings
    from . import duckdb_engine
    bucket = get_settings().storage_bucket
    _meta, src_other = duckdb_engine._parquet_paths(f"s3://{bucket}/{other.s3_key}")
    known_other = _known_columns(other)
    if known_other and left not in known_other:
        return None
    where = _duck_where(normalize_filters(j.get("filters"), known_other), {})
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    # CAST to VARCHAR on both sides, for the same reason the `in` filter does it: the two columns
    # can be an int and a string of the same id and must still match.
    return (f"CAST({qi(right)} AS VARCHAR) IN "
            f"(SELECT DISTINCT CAST({qi(left)} AS VARCHAR) FROM {src_other} {where_sql})")


def _pg_join_predicates(spec: dict, params: dict) -> list[str]:
    """The PostGIS twin. Nested parameters are namespaced PER JOIN (`j0…`, `j1…`), or the subqueries'
    binds would overwrite the outer query's and each other's, and every one of them would silently
    filter by the wrong values."""
    out = []
    for n, j in enumerate(_join_specs(spec)):
        other = j["layer"]
        right, left = _str_field(j.get("rightField")), _str_field(j.get("leftField"))
        if not right or not left:
            continue
        if getattr(other, "storage_backend", "postgis") == "geoparquet":
            continue
        known_other = _known_columns(other)
        if known_other and left not in known_other:
            continue
        table = f"{qi(other.schema_name)}.{qi(other.table_name)}"
        where = _pg_where(normalize_filters(j.get("filters"), known_other), None,
                          other.geometry_column or "geom", params, _srid_of(other), prefix=f"j{n}")
        out.append(f"{qi(right)}::text IN "
                   f"(SELECT DISTINCT {qi(left)}::text FROM {table} {where})")
    return out


def _str_field(v) -> str | None:
    return v if isinstance(v, str) and v.strip() else None


def _pg_where(filters: list[dict], geometry: dict | None, geom_col: str,
              params: dict, srid: int | None = None, prefix: str = "f") -> str:
    """WHERE clause + bound parameters for the PostGIS path.

    Values are BOUND, not interpolated — only identifiers reach the SQL text, and those went through
    the catalog check plus `qi`. The geometry is bound as GeoJSON text and handed to
    `ST_GeomFromGeoJSON`, so a hand-crafted selection cannot become SQL.
    """
    parts: list[str] = []
    for i, f in enumerate(filters):
        col = qi(f["field"])
        if f["op"] == "in":
            key = f"{prefix}{i}"
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
                params[f"{prefix}{i}lo"] = f["min"]
                parts.append(f"{col}::double precision >= :{prefix}{i}lo")
            if f.get("max") is not None:
                params[f"{prefix}{i}hi"] = f["max"]
                parts.append(f"{col}::double precision <= :{prefix}{i}hi")
        elif f["op"] == "daterange":
            if f.get("min"):
                params[f"{prefix}{i}lo"] = str(f["min"])
                parts.append(f"{col} >= CAST(:{prefix}{i}lo AS timestamp)")
            if f.get("max"):
                params[f"{prefix}{i}hi"] = str(f["max"])
                parts.append(f"{col} <= CAST(:{prefix}{i}hi AS timestamp)")
        elif f["op"] == "notnull":
            parts.append(f"{col} IS NOT NULL")
    if geometry:
        params[f"{prefix}gdgeom"] = _dumps(geometry)
        target = str(srid) if srid else f"ST_SRID({qi(geom_col)})"
        sel = (f"ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(CAST(:{prefix}gdgeom AS text)), 4326), {target})")
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
    ckey = cache_key(layer, spec)
    cached = _cache_get(ckey)
    if cached is not None:
        return cached
    return _cache_put(ckey, await _postgis_aggregate_uncached(db, layer, spec))


async def _postgis_aggregate_uncached(db, layer, spec: dict) -> dict:
    from sqlalchemy import text

    known = _known_columns(layer)
    op = spec.get("op", "count")
    if op not in OPS:
        raise AggregateError(f"Unsupported aggregation: {op}")
    field = None if op in FIELDLESS_OPS else _check_field(spec.get("field"), known, "field")
    filters = normalize_filters(spec.get("filters"), known)
    geometry = spec.get("geometry") if isinstance(spec.get("geometry"), dict) else None

    if not layer.schema_name or not layer.table_name:
        raise AggregateError("This layer has no table.")
    table = f"{qi(layer.schema_name)}.{qi(layer.table_name)}"
    geom_col = layer.geometry_column or "geom"
    params: dict = {}
    where = _pg_where(filters, geometry, geom_col, params, _srid_of(layer))
    for jp in _pg_join_predicates(spec, params):
        where = (where + " AND " + jp) if where else ("WHERE " + jp)
    value = _pg_value_expr(op, field)

    group_by = spec.get("groupBy")
    if not group_by:
        # SEVERAL MEASURES, NO GROUPING. The grouped path has always answered N aggregates in
        # one pass; ungrouped it ignored `series` and answered the single `op`/`field` — so
        # "average of these 56 columns over everything" was unaskable, and asking it with an op
        # that needs a field but no field to give came back 400.
        #
        # It is the shape a chart's OVERALL line needs: the same measures as the per-group
        # lines, over the whole selection rather than one group. Answered in the same shape as
        # the grouped reply (`values` alongside `value`) so the client reads one thing.
        if spec.get("series"):
            series = _series_specs(spec, known)
            vals = ", ".join(f"{_pg_value_expr(sp['op'], sp['field'])} AS v{i}"
                             for i, sp in enumerate(series))
            row = (await db.execute(
                text(f"SELECT {vals}, COUNT(*) AS n FROM {table} {where}"), params)).first()
            values = [_num(row[i]) for i in range(len(series))] if row else []
            return {"op": op, "value": values[0] if values else None, "values": values,
                    "count": int(row[len(series)]) if row else 0,
                    "series": [{"label": sp["label"], "op": sp["op"], "field": sp.get("field")}
                               for sp in series]}
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
    # THE EXTENT of the filtered set. Asked before the group-by branch because it never groups: the
    # caller wants one rectangle for everything the filter selects, not one per category.
    if op == "bounds":
        g4326 = f"ST_Transform({qi(geom_col)}, 4326)"
        row = (await db.execute(text(
            f"SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) "
            f"FROM (SELECT ST_Extent({g4326}) AS e FROM {table} {where}) t"), params)).first()
        return _bounds_out(row)

    # KEYS ONLY: no measures, no count, no ordering by a value that is not computed. The caller is
    # building a predicate, not a chart, so every column but the key is work nobody reads.
    if spec.get("keysOnly"):
        limit = max(2, min(int(spec.get("limit") or 12), MAX_KEYS))
        sql = (f"SELECT {key_expr} AS k FROM {table} {where} "
               f"GROUP BY 1 ORDER BY 1 ASC NULLS LAST LIMIT {limit + 1}")
        return _keys_out((await db.execute(text(sql), params)).fetchall(), limit)

    limit = max(2, min(int(spec.get("limit") or 12), MAX_GROUPS))
    # One query for every measure: N series over the same grouping is N aggregate expressions in one
    # GROUP BY, not N round trips. `ORDER BY 2` therefore sorts by the FIRST series, which is the one
    # the author named first and the one the chart draws in front.
    series = _series_specs(spec, known)
    vals = ", ".join(f"{_pg_value_expr(sp['op'], sp['field'])} AS v{i}"
                     for i, sp in enumerate(series))
    sql = (f"SELECT {key_expr} AS k, {vals}, COUNT(*) AS n FROM {table} {where} "
           f"GROUP BY 1 {order} LIMIT {limit + 1}")
    rows = (await db.execute(text(sql), params)).fetchall()
    return _groups_out(rows, limit, bucket, series)


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
    # See the note in `parquet_table`: search is a predicate on the table query, not its own path.
    search = _pg_search(_search_term(spec), _search_fields(spec, known), params,
                        _search_mode(spec.get('searchMode')))
    if search:
        where = (where + " AND " + search) if where else ("WHERE " + search)
    for jp in _pg_join_predicates(spec, params):
        where = (where + " AND " + jp) if where else ("WHERE " + jp)

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
    # Skippable for the same reason as the DuckDB path above: a search box wants matches, not a
    # census, and the count is a second full pass over the same predicate.
    total = ((await db.execute(text(f"SELECT COUNT(*) FROM {table} {where}"), params)).scalar()
             if spec.get("withTotal", True) else None)
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


#: How many characters of a search term are honoured. Long enough for an address, short enough that
#: a pasted essay cannot become a scan predicate.
SEARCH_MAX_LEN = 120

#: Columns one search may look in. A search that reads every column of a wide table is a table scan
#: dressed as a feature, and the author almost always means two or three named ones.
SEARCH_MAX_FIELDS = 8


def _search_term(spec: dict) -> str:
    """The search text, trimmed and bounded. Empty means "no search" — NOT "match nothing", the same
    convention `normalize_filters` uses for an empty selection: a cleared box shows everything."""
    q = spec.get("search") if isinstance(spec.get("search"), str) else ""
    return (q or "").strip()[:SEARCH_MAX_LEN]


def _search_fields(spec: dict, known: set[str]) -> list[str]:
    named = [f for f in (spec.get("searchFields") or []) if f in known]
    return named[:SEARCH_MAX_FIELDS]


#: How a search term is matched. `contains` finds it anywhere (what people expect of a search box);
#: `prefix` only at the start, which is what a "find this street / this name" box usually means and
#: is measurably cheaper — see the cost note below.
SEARCH_MODES = {"contains", "prefix"}


def _search_mode(value) -> str:
    return value if value in SEARCH_MODES else "contains"


def _like_escape(term: str) -> str:
    """LIKE metacharacters made literal. Without this, typing `%` matches the whole layer and `_`
    matches any single character — which reads as a broken search, not as a wildcard anyone asked
    for."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _duck_search(term: str, fields: list[str], mode: str = "contains") -> str | None:
    """A case-insensitive match across `fields`, OR-ed. The term goes through `_lit` like every other
    inlined literal on this path.

    ON COST, measured over 400k rows on local disk, one text column, LIMIT 10:

        contains  ILIKE '%term%'   154 ms       prefix  ILIKE 'term%'    108 ms
        contains, no match          88 ms       prefix, no match          66 ms
        prefix    LIKE  'term%'     11 ms   <-- case-SENSITIVE

    That 11 ms is DuckDB pruning row groups from the string column's zonemaps, which a leading `%`
    makes impossible and which `ILIKE` also defeats (the stored min/max are not case-folded). It is
    deliberately NOT taken: a search box that misses `bern` because the data says `Bern` is broken in
    a way no amount of speed repays. `prefix` is the honest middle — same case-insensitive
    behaviour, less scanning — and the larger saving is the client not asking on every keystroke.
    """
    if not term or not fields:
        return None
    pat = _lit(f"{_like_escape(term)}%" if mode == "prefix" else f"%{_like_escape(term)}%")
    parts = [f"CAST({qi(f)} AS VARCHAR) ILIKE {pat} ESCAPE '\\'" for f in fields]
    return "(" + " OR ".join(parts) + ")"


def _pg_search(term: str, fields: list[str], params: dict, mode: str = "contains") -> str | None:
    """The PostGIS twin of `_duck_search`. The term is BOUND (this path binds everything); the LIKE
    metacharacters still need escaping for the same reason.

    A prefix match here can use a `text_pattern_ops` btree index where a deployment has added one; a
    contains match cannot use any index and is always a sequential scan. Ingest creates neither —
    worth knowing before pointing a search box at a very large PostGIS table."""
    if not term or not fields:
        return None
    safe = _like_escape(term)
    params["gdsearch"] = f"{safe}%" if mode == "prefix" else f"%{safe}%"
    parts = [f"{qi(f)}::text ILIKE :gdsearch ESCAPE '\\'" for f in fields]
    return "(" + " OR ".join(parts) + ")"


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


def _duck_bounds(conn, src: str, meta: dict, cols: dict, where_sql: str) -> dict:
    """The extent of the filtered set, in lon/lat.

    Prefers the COVERING bbox struct the prep step writes: four numeric columns whose min/max is the
    extent, with no geometry decoded at all. Falls back to the geometry itself when a layer has no
    covering column — correct either way, and the difference is whole seconds on a large file.

    Reprojected at the END rather than per row: the extent of the reprojected set and the reprojected
    extent differ only for a shape spanning a projection's discontinuity, and reprojecting four
    numbers instead of millions is the reason this is worth asking for at all.
    """
    covering = meta.get("covering")
    if covering:
        col, f = covering
        def ce(k):
            return f"struct_extract({qi(col)}, '{f[k]}')"
        sel = f"MIN({ce('xmin')}), MIN({ce('ymin')}), MAX({ce('xmax')}), MAX({ce('ymax')})"
    else:
        g = qi(_duck_geom_col(meta, cols))
        sel = f"MIN(ST_XMin({g})), MIN(ST_YMin({g})), MAX(ST_XMax({g})), MAX(ST_YMax({g}))"
    row = conn.execute(f"SELECT {sel} FROM {src} {where_sql}").fetchone()
    if not row or row[0] is None:
        return {"bounds": None}
    b = [float(row[0]), float(row[1]), float(row[2]), float(row[3])]
    src_epsg = meta.get("epsg") or "EPSG:4326"
    if src_epsg not in ("EPSG:4326", "4326"):
        from . import duckdb_engine
        b = list(duckdb_engine._reproject_bbox(b, src_epsg, "EPSG:4326"))
    return {"bounds": b}


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


# ── column profile ───────────────────────────────────────────────────────────
# "What is IN this layer?" — asked of the current selection, not of the whole table, so it narrows
# with everything else. Per field: how many values are present, how many are distinct, and either the
# numeric range or the commonest few values.
#
# THE ONE DESIGN DECISION WORTH READING. With a geometry filter on a GeoParquet layer, the exact
# point-in-polygon test is the expensive step (measured at 750 ms for a broad selection over 200 k
# features). Computing each field's statistics in SQL would re-run that test once PER FIELD. So the
# geometry-filtered path reads the candidates ONCE, tests once, and reduces every field over the
# survivors in Python — one pass for the whole panel, the same cost as a single aggregate. The
# unfiltered path stays in SQL, where the columnar read is already the fast thing.
#
# What it must NOT do is answer from the bbox prune alone: that would be cheap and would silently
# disagree with the chart sitting next to it, which does run the exact test.

#: Fields a profile will describe in one request, and how many top values each may return.
PROFILE_MAX_FIELDS = 12
PROFILE_TOP_MAX = 20

#: A column with more distinct values than this is described by its RANGE rather than a top list,
#: even when it is textual — a "top 5" over a column of unique ids says nothing.
PROFILE_CATEGORICAL_MAX = 5000


def _profile_kind(duck_type: str) -> str:
    t = (duck_type or "").upper()
    if any(k in t for k in ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "NUMERIC", "HUGEINT")):
        return "numeric"
    if any(k in t for k in ("DATE", "TIME")):
        return "date"
    if "BOOL" in t:
        return "boolean"
    return "text"


def _profile_from_values(name: str, kind: str, values: list, top_n: int) -> dict:
    """Reduce one column's already-materialised values to its profile entry. Shared by both engines'
    in-memory paths so the two cannot describe the same column differently."""
    present = [v for v in values if v is not None]
    out: dict = {"field": name, "kind": kind, "count": len(present),
                 "nulls": len(values) - len(present)}
    if not present:
        return out
    if kind == "numeric":
        nums = [float(v) for v in present if isinstance(v, (int, float))]
        if nums:
            nums.sort()
            mid = len(nums) // 2
            out["min"] = nums[0]
            out["max"] = nums[-1]
            out["avg"] = sum(nums) / len(nums)
            out["median"] = nums[mid] if len(nums) % 2 else (nums[mid - 1] + nums[mid]) / 2
        out["distinct"] = len(set(present))
        return out
    counts: dict = {}
    for v in present:
        key = str(v)
        counts[key] = counts.get(key, 0) + 1
    out["distinct"] = len(counts)
    if len(counts) <= PROFILE_CATEGORICAL_MAX:
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
        out["top"] = [{"value": k, "count": n} for k, n in top]
    return out


def parquet_profile(layer, spec: dict) -> dict:
    """Column profile for a GeoParquet layer. Cached like the aggregates — a profile panel and the
    charts beside it ask overlapping questions of one selection."""
    ckey = cache_key(layer, dict(spec, __kind="profile"))
    cached = _cache_get(ckey)
    if cached is not None:
        return cached
    return _cache_put(ckey, _parquet_profile_uncached(layer, spec))


def _parquet_profile_uncached(layer, spec: dict) -> dict:
    known = _known_columns(layer)
    fields = [f for f in (spec.get("fields") or []) if f in known][:PROFILE_MAX_FIELDS]
    if not fields:
        raise AggregateError("Pick at least one column to describe.")
    top_n = max(3, min(int(spec.get("topN") or 5), PROFILE_TOP_MAX))
    filters = normalize_filters(spec.get("filters"), known)
    geometry = spec.get("geometry") if isinstance(spec.get("geometry"), dict) else None

    conn, src, meta, cols, _ = _duck_open(layer)
    try:
        where = _duck_where(filters, cols)
        bbox_pred, geom = _duck_bbox_prune(meta, geometry)
        if bbox_pred:
            where.append(bbox_pred)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        kinds = {f: _profile_kind(cols.get(f, "")) for f in fields}

        if geom is None:
            # No exact test to pay for: let the columnar engine do it. One scalar pass for every
            # field, then one grouped pass per field that can carry a top list.
            sel = []
            for f in fields:
                q = qi(f)
                sel.append(f"COUNT({q}) AS c_{len(sel)}")
                sel.append(f"COUNT(DISTINCT {q}) AS d_{len(sel)}")
                if kinds[f] == "numeric":
                    n = f"TRY_CAST({q} AS DOUBLE)"
                    sel += [f"MIN({n}) AS mn_{len(sel)}", f"MAX({n}) AS mx_{len(sel)}",
                            f"AVG({n}) AS av_{len(sel)}", f"MEDIAN({n}) AS md_{len(sel)}"]
            row = conn.execute(
                f"SELECT COUNT(*) AS __total, {', '.join(sel)} FROM {src} {where_sql}").fetchone()
            total = int(row[0] or 0)
            out, i = [], 1
            for f in fields:
                entry = {"field": f, "kind": kinds[f], "count": int(row[i] or 0),
                         "nulls": total - int(row[i] or 0), "distinct": int(row[i + 1] or 0)}
                i += 2
                if kinds[f] == "numeric":
                    entry.update({"min": _num(row[i]), "max": _num(row[i + 1]),
                                  "avg": _num(row[i + 2]), "median": _num(row[i + 3])})
                    i += 4
                elif entry["distinct"] and entry["distinct"] <= PROFILE_CATEGORICAL_MAX:
                    tops = conn.execute(
                        f"SELECT CAST({qi(f)} AS VARCHAR) AS k, COUNT(*) AS n FROM {src} "
                        f"{where_sql} {'AND' if where_sql else 'WHERE'} {qi(f)} IS NOT NULL "
                        f"GROUP BY 1 ORDER BY 2 DESC, 1 ASC LIMIT {top_n}").fetchall()
                    entry["top"] = [{"value": r[0], "count": int(r[1])} for r in tops]
                out.append(entry)
            return {"total": total, "fields": out, "capped": False}

        # Geometry filter: ONE read, ONE exact test, every field reduced over the survivors.
        from shapely import from_wkb, intersects
        geom_col = _duck_geom_col(meta, cols)
        sel = ", ".join([f"{qi(geom_col)} AS __wkb"] + [qi(f) for f in fields])
        rows = conn.execute(
            f"SELECT {sel} FROM {src} {where_sql} LIMIT {CANDIDATE_CAP + 1}").fetchall()
        capped = len(rows) > CANDIDATE_CAP
        rows = rows[:CANDIDATE_CAP]
        if not rows:
            return {"total": 0, "fields": [{"field": f, "kind": kinds[f], "count": 0, "nulls": 0}
                                           for f in fields], "capped": capped}
        geoms = from_wkb([bytes(r[0]) if r[0] is not None else None for r in rows],
                         on_invalid="ignore")
        reproject = _duck_reprojector(meta)
        if reproject is not None:
            geoms = reproject(geoms)
        hit = intersects(geoms, geom)
        kept = [rows[i] for i, ok in enumerate(hit) if ok]
        out = [_profile_from_values(f, kinds[f], [r[j + 1] for r in kept], top_n)
               for j, f in enumerate(fields)]
        return {"total": len(kept), "fields": out, "capped": capped}
    finally:
        conn.close()


async def postgis_profile(db, layer, spec: dict) -> dict:
    """Column profile for a PostGIS layer. One statement: the scalar stats for every field, plus a
    lateral top-N for each field that can carry one. PostGIS does the exact test in the WHERE, so
    there is no candidate-materialising step to avoid here."""
    ckey = cache_key(layer, dict(spec, __kind="profile"))
    cached = _cache_get(ckey)
    if cached is not None:
        return cached
    return _cache_put(ckey, await _postgis_profile_uncached(db, layer, spec))


async def _postgis_profile_uncached(db, layer, spec: dict) -> dict:
    from sqlalchemy import text

    known = _known_columns(layer)
    fields = [f for f in (spec.get("fields") or []) if f in known][:PROFILE_MAX_FIELDS]
    if not fields:
        raise AggregateError("Pick at least one column to describe.")
    top_n = max(3, min(int(spec.get("topN") or 5), PROFILE_TOP_MAX))
    filters = normalize_filters(spec.get("filters"), known)
    geometry = spec.get("geometry") if isinstance(spec.get("geometry"), dict) else None

    table = f"{qi(layer.schema_name)}.{qi(layer.table_name)}"
    geom_col = layer.geometry_column or "geom"
    params: dict = {}
    where = _pg_where(filters, geometry, geom_col, params, _srid_of(layer))
    types = _pg_types(layer)

    sel = ["COUNT(*) AS total"]
    for i, f in enumerate(fields):
        q = qi(f)
        sel.append(f"COUNT({q}) AS c{i}")
        sel.append(f"COUNT(DISTINCT {q}) AS d{i}")
        if _profile_kind(types.get(f, "")) == "numeric":
            n = f"({q})::double precision"
            sel += [f"MIN({n}) AS mn{i}", f"MAX({n}) AS mx{i}", f"AVG({n}) AS av{i}",
                    f"PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {n}) AS md{i}"]
    row = (await db.execute(text(f"SELECT {', '.join(sel)} FROM {table} {where}"), params)).mappings().first()
    total = int((row or {}).get("total") or 0)

    out = []
    for i, f in enumerate(fields):
        kind = _profile_kind(types.get(f, ""))
        count = int((row or {}).get(f"c{i}") or 0)
        entry = {"field": f, "kind": kind, "count": count, "nulls": total - count,
                 "distinct": int((row or {}).get(f"d{i}") or 0)}
        if kind == "numeric":
            entry.update({"min": _num((row or {}).get(f"mn{i}")), "max": _num((row or {}).get(f"mx{i}")),
                          "avg": _num((row or {}).get(f"av{i}")), "median": _num((row or {}).get(f"md{i}"))})
        elif entry["distinct"] and entry["distinct"] <= PROFILE_CATEGORICAL_MAX:
            tops = (await db.execute(text(
                f"SELECT {qi(f)}::text AS k, COUNT(*) AS n FROM {table} {where} "
                f"{'AND' if where else 'WHERE'} {qi(f)} IS NOT NULL "
                f"GROUP BY 1 ORDER BY 2 DESC, 1 ASC LIMIT {top_n}"), params)).fetchall()
            entry["top"] = [{"value": r[0], "count": int(r[1])} for r in tops]
        out.append(entry)
    return {"total": total, "fields": out, "capped": False}


# ── scatter (Y against X, per feature) ───────────────────────────────────────
# Not an aggregate: a scatter plots FEATURES, so it is the one chart that needs rows rather than a
# summary. That makes sampling the whole design problem.
#
# THE SAMPLE MUST BE RANDOM, NOT THE FIRST N. A prepped GeoParquet layer is written in spatial
# partitions (`partition_with_covering` scatters rows into a grid and writes `__cell=N/` files), so
# "the first 2000 rows" is one corner of the map. A scatter drawn from that is not a slow answer or a
# partial answer — it is a WRONG one, showing the relationship in one district and labelling it the
# layer's. DuckDB's `USING SAMPLE n ROWS` is reservoir sampling over the scan; PostGIS gets
# `ORDER BY random()`, which is a sort but on a bounded result.

#: Points one scatter ships. Past this the plot is a solid blob and the honest thing is a density
#: chart, not more dots — and the browser has to draw every one of them.
SCATTER_MAX_POINTS = 3000

#: How many columns may name a scatter's points. Three is a line of hover text; more is a record,
#: and a record belongs in the details panel, which is what a click already fills.
MAX_LABEL_FIELDS = 3


def _label_fields(spec: dict, known) -> list[str]:
    """The columns a scatter's hover label is built from, validated like any other field.

    A scatter plots two numbers per feature and, without this, says nothing about WHICH feature: the
    reader can see an outlier and has no way to find out what it is. The label is built server-side
    because the point is already being read there — sending the columns back so the browser can
    concatenate them would be the same bytes and one more thing to keep in step.
    """
    out, seen = [], set()
    for name in (spec.get("labelFields") or [])[:MAX_LABEL_FIELDS]:
        if not isinstance(name, str) or name in seen:
            continue
        _check_field(name, known, "label field")
        seen.add(name)
        out.append(name)
    return out


def _label_of(row, start: int, count: int) -> str:
    """One row's label, from `count` values beginning at `row[start]`."""
    parts = [str(row[start + i]) for i in range(count) if row[start + i] is not None]
    return " · ".join(parts)


def _scatter_fields(spec: dict, known: set[str]) -> tuple[str, str]:
    x = _check_field(spec.get("xField"), known, "X field")
    y = _check_field(spec.get("yField"), known, "Y field")
    return x, y


def parquet_scatter(layer, spec: dict) -> dict:
    """Y against X for a GeoParquet layer, randomly sampled."""
    ckey = cache_key(layer, dict(spec, __kind="scatter"))
    cached = _cache_get(ckey)
    if cached is not None:
        return cached
    return _cache_put(ckey, _parquet_scatter_uncached(layer, spec))


def _parquet_scatter_uncached(layer, spec: dict) -> dict:
    known = _known_columns(layer)
    xf, yf = _scatter_fields(spec, known)
    limit = max(50, min(int(spec.get("limit") or 1500), SCATTER_MAX_POINTS))
    filters = normalize_filters(spec.get("filters"), known)
    geometry = spec.get("geometry") if isinstance(spec.get("geometry"), dict) else None

    conn, src, meta, cols, _ = _duck_open(layer)
    try:
        where = _duck_where(filters, cols)
        bbox_pred, geom = _duck_bbox_prune(meta, geometry)
        if bbox_pred:
            where.append(bbox_pred)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        xe = f"TRY_CAST({qi(xf)} AS DOUBLE)"
        ye = f"TRY_CAST({qi(yf)} AS DOUBLE)"
        keep = f"{xe} IS NOT NULL AND {ye} IS NOT NULL"
        where_sql = (where_sql + " AND " + keep) if where_sql else ("WHERE " + keep)

        labels = _label_fields(spec, known)
        lab_sel = "".join(", " + qi(c) for c in labels)
        if geom is None:
            rows = conn.execute(
                f"SELECT {xe} AS x, {ye} AS y{lab_sel} FROM {src} {where_sql} "
                f"USING SAMPLE {limit} ROWS").fetchall()
            total = conn.execute(f"SELECT COUNT(*) FROM {src} {where_sql}").fetchone()[0]
            pts = [[_num(r[0]), _num(r[1])] for r in rows]
            out = {"points": pts, "x": xf, "y": yf, "total": int(total or 0),
                   "sampled": len(pts) < int(total or 0), "capped": False}
            if labels:
                out["labels"] = [_label_of(r, 2, len(labels)) for r in rows]
            return out

        # With a geometry filter the exact test has to run before the sample, or the sample is drawn
        # from candidates that are not in the selection. Candidates are bounded by CANDIDATE_CAP as
        # everywhere else, then thinned evenly rather than truncated.
        from shapely import from_wkb, intersects
        geom_col = _duck_geom_col(meta, cols)
        rows = conn.execute(
            f"SELECT {qi(geom_col)} AS __wkb, {xe} AS x, {ye} AS y{lab_sel} FROM {src} {where_sql} "
            f"LIMIT {CANDIDATE_CAP + 1}").fetchall()
        capped = len(rows) > CANDIDATE_CAP
        rows = rows[:CANDIDATE_CAP]
        if not rows:
            return {"points": [], "x": xf, "y": yf, "total": 0, "sampled": False, "capped": capped}
        geoms = from_wkb([bytes(r[0]) if r[0] is not None else None for r in rows],
                         on_invalid="ignore")
        reproject = _duck_reprojector(meta)
        if reproject is not None:
            geoms = reproject(geoms)
        hit = intersects(geoms, geom)
        kept = [rows[i] for i, ok in enumerate(hit) if ok]
        total = len(kept)
        if total > limit:
            # An even stride, not the first `limit`: the candidate order is the file's spatial
            # order, so truncating would again plot one corner.
            step = total / float(limit)
            kept = [kept[int(i * step)] for i in range(limit)]
        out = {"points": [[_num(r[1]), _num(r[2])] for r in kept], "x": xf, "y": yf,
               "total": total, "sampled": total > limit, "capped": capped}
        if labels:
            # 3, not 2: this SELECT leads with the geometry.
            out["labels"] = [_label_of(r, 3, len(labels)) for r in kept]
        return out
    finally:
        conn.close()


async def postgis_scatter(db, layer, spec: dict) -> dict:
    """Y against X for a PostGIS layer, randomly sampled."""
    ckey = cache_key(layer, dict(spec, __kind="scatter"))
    cached = _cache_get(ckey)
    if cached is not None:
        return cached
    return _cache_put(ckey, await _postgis_scatter_uncached(db, layer, spec))


async def _postgis_scatter_uncached(db, layer, spec: dict) -> dict:
    from sqlalchemy import text

    known = _known_columns(layer)
    xf, yf = _scatter_fields(spec, known)
    limit = max(50, min(int(spec.get("limit") or 1500), SCATTER_MAX_POINTS))
    filters = normalize_filters(spec.get("filters"), known)
    geometry = spec.get("geometry") if isinstance(spec.get("geometry"), dict) else None

    table = f"{qi(layer.schema_name)}.{qi(layer.table_name)}"
    geom_col = layer.geometry_column or "geom"
    params: dict = {}
    where = _pg_where(filters, geometry, geom_col, params, _srid_of(layer))
    xe, ye = f"({qi(xf)})::double precision", f"({qi(yf)})::double precision"
    keep = f"{qi(xf)} IS NOT NULL AND {qi(yf)} IS NOT NULL"
    where = (where + " AND " + keep) if where else ("WHERE " + keep)

    total = (await db.execute(text(f"SELECT COUNT(*) FROM {table} {where}"), params)).scalar() or 0
    labels = _label_fields(spec, known)
    lab_sel = "".join(", " + qi(c) for c in labels)
    rows = (await db.execute(text(
        f"SELECT {xe} AS x, {ye} AS y{lab_sel} FROM {table} {where} "
        f"ORDER BY random() LIMIT {limit}"), params)).fetchall()
    pts = [[_num(r[0]), _num(r[1])] for r in rows]
    out = {"points": pts, "x": xf, "y": yf, "total": int(total),
           "sampled": len(pts) < int(total), "capped": False}
    if labels:
        out["labels"] = [_label_of(r, 2, len(labels)) for r in rows]
    return out


def parquet_aggregate(layer, spec: dict) -> dict:
    """The GeoParquet half of `aggregate`. Runs in a threadpool (DuckDB is synchronous)."""
    ckey = cache_key(layer, spec)
    cached = _cache_get(ckey)
    if cached is not None:
        return cached
    return _cache_put(ckey, _parquet_aggregate_uncached(layer, spec))


def _parquet_aggregate_uncached(layer, spec: dict) -> dict:
    known = _known_columns(layer)
    op = spec.get("op", "count")
    if op not in OPS:
        raise AggregateError(f"Unsupported aggregation: {op}")
    field = None if op in FIELDLESS_OPS else _check_field(spec.get("field"), known, "field")
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
        where.extend(_duck_join_predicates(spec, cols))
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        if geom is None:
            # PURE SQL — one columnar pass, row groups pruned by the attribute predicates.
            value = _duck_value_expr(op, field)
            if not group_by:
            # SEVERAL MEASURES, NO GROUPING. The grouped path has always answered N aggregates in
            # one pass; ungrouped it ignored `series` and answered the single `op`/`field` — so
            # "average of these 56 columns over everything" was unaskable, and asking it with an op
            # that needs a field but no field to give came back 400.
            #
            # It is the shape a chart's OVERALL line needs: the same measures as the per-group
            # lines, over the whole selection rather than one group. Answered in the same shape as
            # the grouped reply (`values` alongside `value`) so the client reads one thing.
                if spec.get("series"):
                    series = _series_specs(spec, known)
                    vals = ", ".join(_duck_value_expr(sp["op"], sp.get("field"))
                                     for sp in series)
                    row = conn.execute(
                        f"SELECT {vals}, COUNT(*) FROM {src} {where_sql}").fetchone()
                    values = [_num(row[i]) for i in range(len(series))] if row else []
                    return {"op": op, "value": values[0] if values else None, "values": values,
                            "count": int(row[len(series)]) if row else 0,
                            "series": [{"label": sp["label"], "op": sp["op"],
                                        "field": sp.get("field")} for sp in series]}
                row = conn.execute(
                    f"SELECT {value}, COUNT(*) FROM {src} {where_sql}").fetchone()
                return {"op": op, "value": _num(row[0]) if row else None,
                        "count": int(row[1]) if row else 0}
            key_expr = (f"date_trunc('{bucket}', TRY_CAST({qi(group_by)} AS TIMESTAMP))"
                        if bucket else f"CAST({qi(group_by)} AS VARCHAR)")
            order = ("ORDER BY 1 ASC" if bucket else
                     {"value_asc": "ORDER BY 2 ASC", "key_asc": "ORDER BY 1 ASC"}.get(
                         spec.get("sort") or "value_desc", "ORDER BY 2 DESC"))
            # THE EXTENT — see the PostGIS branch.
            if op == "bounds":
                return _duck_bounds(conn, src, meta, cols, where_sql)

            # KEYS ONLY — see the PostGIS branch: a predicate, not a chart.
            if spec.get("keysOnly"):
                limit = max(2, min(int(spec.get("limit") or 12), MAX_KEYS))
                return _keys_out(conn.execute(
                    f"SELECT {key_expr} AS k FROM {src} {where_sql} "
                    f"GROUP BY 1 ORDER BY 1 LIMIT {limit + 1}").fetchall(), limit)

            limit = max(2, min(int(spec.get("limit") or 12), MAX_GROUPS))
            # As on the PostGIS side: N measures are N aggregate expressions in ONE grouped scan,
            # which is the whole reason a columnar engine is worth using here.
            series = _series_specs(spec, known)
            vals = ", ".join(f"{_duck_value_expr(sp['op'], sp['field'])} AS v{i}"
                             for i, sp in enumerate(series))
            rows = conn.execute(
                f"SELECT {key_expr} AS k, {vals}, COUNT(*) AS n FROM {src} {where_sql} "
                f"GROUP BY 1 {order} LIMIT {limit + 1}").fetchall()
            return _groups_out(rows, limit, bucket, series)

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
    # ONE value column per series. A chart with several measures must answer the same shape whether
    # or not a geometry filter is active — otherwise wiring it to the map silently drops every series
    # but the first, which looks like the map broke the chart.
    series = _series_specs(spec, _known_columns_from(cols))
    wanted = [f"{qi(geom_col)} AS __wkb"]
    for i, sp in enumerate(series):
        wanted.append((f"TRY_CAST({qi(sp['field'])} AS DOUBLE)" if sp.get("field") else "NULL")
                      + f" AS __v{i}")
    if group_by:
        wanted.append((f"date_trunc('{bucket}', TRY_CAST({qi(group_by)} AS TIMESTAMP))"
                       if bucket else f"CAST({qi(group_by)} AS VARCHAR)") + " AS __k")
    rows = conn.execute(
        f"SELECT {', '.join(wanted)} FROM {src} {where_sql} LIMIT {CANDIDATE_CAP + 1}").fetchall()
    capped = len(rows) > CANDIDATE_CAP
    rows = rows[:CANDIDATE_CAP]
    if not rows:
        return ({"op": op, "value": None, "count": 0, "capped": capped} if not group_by
                else {"groups": [], "truncated": False, "capped": capped,
                      "series": [{"label": sp["label"], "op": sp["op"], "field": sp.get("field")}
                                 for sp in series]})

    geoms = from_wkb([bytes(r[0]) if r[0] is not None else None for r in rows], on_invalid="ignore")
    reproject = _duck_reprojector(meta)
    if reproject is not None:
        geoms = reproject(geoms)
    hit = intersects(geoms, geom)

    cols_v = [np.array([_num(r[1 + i]) for r in rows], dtype="float64")
              for i in range(len(series))]
    keys = [r[1 + len(series)] for r in rows] if group_by else None

    if not group_by:
        return {"op": op, "value": _reduce(series[0]["op"], cols_v[0], hit),
                "count": int(hit.sum()), "capped": capped}

    buckets: dict[Any, list[int]] = {}
    for i, ok in enumerate(hit):
        if not ok:
            continue
        buckets.setdefault(keys[i], []).append(i)
    out = []
    for key, idxs in buckets.items():
        mask = np.zeros(len(rows), dtype=bool)
        mask[idxs] = True
        # (key, v0, v1, …, count) — the row shape `_groups_out` reads, so the SQL and the in-memory
        # paths hand it the same thing and cannot describe one chart two ways.
        out.append((key,) + tuple(_reduce(sp["op"], cols_v[i], mask)
                                  for i, sp in enumerate(series)) + (len(idxs),))
    limit = max(2, min(int(spec.get("limit") or 12), MAX_GROUPS))
    if bucket:
        out.sort(key=lambda r: (r[0] is None, r[0]))
    else:
        sort = spec.get("sort") or "value_desc"
        if sort == "key_asc":
            out.sort(key=lambda r: (r[0] is None, str(r[0])))
        else:
            out.sort(key=lambda r: (r[1] is None, r[1]), reverse=(sort != "value_asc"))
    result = _groups_out(out, limit, bucket, series)
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
        # Text search rides on the table query rather than getting an endpoint of its own: a search
        # result IS a table row — same columns, same per-row bbox for click-to-zoom, same paging.
        # A second endpoint would be a second place to build a row and a second place to secure.
        search = _duck_search(_search_term(spec), _search_fields(spec, known),
                              _search_mode(spec.get('searchMode')))
        if search:
            where.append(search)
        where.extend(_duck_join_predicates(spec, cols))
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
            # COUNTING IS THE EXPENSIVE HALF OF A SEARCH, so it is skippable. `COUNT(*)` over the
            # predicate is a FULL scan of the matched column — and worse, it runs before the row
            # query and therefore throws away the `LIMIT` short-circuit that lets DuckDB stop as
            # soon as it has enough rows. A search box wants the first ten matches, not how many
            # matches exist, so it asks for no total and gets one scan that ends early instead of
            # two that do not. A table widget still counts: its pager needs the number.
            rows = conn.execute(
                f"SELECT {sel} FROM {src} {where_sql} {order} LIMIT {limit} OFFSET {offset}"
            ).fetchall()
            total = (conn.execute(f"SELECT COUNT(*) FROM {src} {where_sql}").fetchone()[0]
                     if spec.get("withTotal", True) else offset + len(rows))
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


def _known_columns_from(cols: dict) -> set[str]:
    """The column names a DuckDB `DESCRIBE` already gave us. Used where the layer object is not to
    hand — `_series_specs` validates measure fields against it, and validating against the file's
    real columns is strictly better than against the catalog's copy of them."""
    return set(cols or {})


def _pg_types(layer) -> dict[str, str]:
    """`{column: declared type}` from the layer's stored catalog — the PostGIS path's equivalent of
    the DuckDB `DESCRIBE` the GeoParquet path runs. Used only to decide whether a column gets a
    numeric summary or a top-N list; an unknown type falls through to the categorical branch, which
    is the safe way round (a top list over numbers is merely uninteresting, while a MIN/MAX over
    text would be a type error at the database)."""
    import json
    try:
        return {c.get("name"): str(c.get("type") or "")
                for c in json.loads(layer.columns or "[]") if isinstance(c, dict)}
    except (ValueError, TypeError):
        return {}


#: Measures one chart may plot against a single group key. Four is where a legend stops being a
#: legend and starts being a table, and where line colours stop being tellable apart at a glance.
#: How many aggregate expressions one grouped scan may carry.
#:
#: This was 4, borrowed from a READABILITY limit: four coloured lines is where a legend stops being
#: a legend. That reasoning belongs to the client, and only to the mode where colour names the
#: measure — when the measures become the X AXIS (a chart plotting one column per year), they are
#: ticks, not legend entries, and four of them is a two-year timeline.
#:
#: What the server should bound is the QUERY: N measures are N aggregate expressions in one pass
#: over one grouping, and a hundred of those is still one scan. Raised from 24 the first time a real
#: file turned up: GDP per capita 1960-2016 is 57 columns, and a limit set to a comfortable-looking
#: number rather than a measured one is a limit that fails on the first honest use. The builder keeps
#: its own limit of four for hand-authored measures, where the legend argument does apply.
MAX_SERIES = 120


def _series_label(op: str, field: str | None) -> str:
    return "Count" if op == "count" else f"{op.capitalize()} of {field}"


def _series_specs(spec: dict, known: set[str]) -> list[dict]:
    """The measures this chart plots: `[{op, field, label}]`.

    MULTI-SERIES IS SEVERAL MEASURES OVER ONE GROUPING, not a second grouping field. "Average height
    and average age per district" is a question about two columns; "count per district per year" is a
    question about two dimensions, and answering it well needs a stacked/grouped renderer and a
    cardinality guard that this does not have. The first is what a Y~X chart with several Y means,
    and it is what this returns.

    Falls back to the single `op`/`field` pair every existing chart uses, so a chart authored before
    this asks exactly the query it asked before — one series, same SQL shape.
    """
    out: list[dict] = []
    raw = spec.get("series")
    if isinstance(raw, list):
        for s in raw[:MAX_SERIES]:
            if not isinstance(s, dict):
                continue
            op = s.get("op", "count")
            if op not in OPS:
                continue
            field = None if op == "count" else s.get("field")
            if op != "count" and not (field and (not known or field in known)):
                continue          # a measure naming a column this layer lacks is dropped, not fatal
            label = str(s.get("label") or _series_label(op, field))[:40]
            out.append({"op": op, "field": field, "label": label})
    if out:
        return out
    op = spec.get("op", "count")
    field = None if op == "count" else spec.get("field")
    return [{"op": op, "field": field, "label": _series_label(op, field)}]


def _bounds_out(row) -> dict:
    """`{bounds: [minx, miny, maxx, maxy]}` in lon/lat, or `{bounds: None}`.

    None is a real answer and the caller must handle it: a filter that matches nothing has no
    extent, and neither does a set whose every geometry is null. Flying the map to a rectangle
    invented for that case would be worse than not moving at all."""
    if not row or row[0] is None:
        return {"bounds": None}
    b = [float(row[0]), float(row[1]), float(row[2]), float(row[3])]
    # A single point selects a zero-area box, which `fitBounds` cannot frame — it is left to the
    # client, which knows its own viewport and can simply centre on it.
    return {"bounds": b}


#: How far a zero-area filter geometry is grown before it is tested. 1e-6 degrees is about 11 cm at
#: the equator: far larger than the precision a geometry loses on its round trip, far smaller than
#: the distance between any two features anyone would want to tell apart.
GEOM_EPS = 1e-6


def usable_geometry(geom: dict | None) -> dict | None:
    """A geometry filter that can actually match what it came from.

    Clicking a POINT layer publishes the picked feature's own geometry as the area filter, and that
    filter then matched NOTHING — not even the feature it was taken from. The point makes the round
    trip as GeoJSON, rounded to nine decimals on the way out and reprojected on the way back, and an
    exact `intersects` between two zero-area geometries fails on the last few digits. Measured on a
    live layer: the picked point scored 0, the same point grown by 1e-9 degrees scored 1.

    What the visitor saw was worse than an error. The attribute channel matched (`Country_Na =
    Luxembourg`, 1 row) and the geometry channel did not, so every widget answered "no records" and
    the chart flattened to zero — a dashboard that looked like it had lost its data.

    So a geometry with no area is grown by `GEOM_EPS` before it is used. Polygons are untouched:
    they have area, their tests are not knife-edge, and nothing here should widen a selection the
    visitor actually drew.
    """
    if not isinstance(geom, dict) or not geom.get("type"):
        return geom
    try:
        from shapely.geometry import shape, mapping
        g = shape(geom)
        if g.is_empty or g.area > 0:
            return geom
        return mapping(g.buffer(GEOM_EPS))
    except Exception:
        # A geometry shapely cannot read is one the engines will reject with a better message than
        # anything invented here.
        return geom


def _keys_out(rows, limit: int) -> dict:
    """Shape a keys-only answer: the distinct values themselves, and whether there were more.

    Exists because the caller that wants keys wants ONLY keys. `_groups_out` emits
    `{key, value, values, count}` per row, which for 5 000 keys is ~239 KB on the wire where the
    keys alone are ~39 KB — six times the payload to deliver the one field that gets read. Nulls are
    dropped here rather than by the caller: a null key matches nothing on either side of a join, so
    it is not a value the answer should spend a row on."""
    keys = [_scalar(r[0]) for r in rows[:limit]]
    return {"keys": [k for k in keys if k is not None], "truncated": len(rows) > limit}


def _groups_out(rows, limit: int, bucket: str | None, series: list[dict] | None = None) -> dict:
    """Shape grouped rows for the wire.

    Rows arrive as `(key, v0, v1, …, count)`. `value` carries the FIRST series and is kept even for a
    multi-series answer: every chart renderer and every widget written before this reads it, and a
    response that dropped it would break them for no gain. `values` carries all of them.
    """
    n = len(series or [{}])
    groups = []
    for r in rows[:limit]:
        values = [_num(r[1 + i]) for i in range(n)]
        groups.append({"key": _scalar(r[0]), "value": values[0],
                       "values": values, "count": int(r[1 + n] or 0)})
    out = {"groups": groups, "truncated": len(rows) > limit, "bucket": bucket or None}
    if series:
        out["series"] = [{"label": s["label"], "op": s["op"], "field": s.get("field")}
                         for s in series]
    return out


def _rows_out(rows, fields, total: int, limit: int, offset: int) -> dict:
    out = []
    for r in rows:
        bbox = [r[1], r[2], r[3], r[4]]
        props = {fields[j]: _jsonable(r[j + 5]) for j in range(len(fields))}
        out.append({"id": r[0], "bbox": _bbox_or_none(bbox), "props": props})
    # `total` is None when the caller skipped the count (a search box). Report the rows actually on
    # this page rather than null, so a client never has to special-case the field it pages with.
    return {"rows": out, "fields": fields,
            "total": int(total) if total is not None else offset + len(out),
            "limit": limit, "offset": offset}


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
