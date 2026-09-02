"""A restore must not drop and recreate the PostGIS extension.

THE BUG. `pg_restore --clean` drops the objects in the dump in reverse dependency order, so tables
go first and `DROP EXTENSION postgis` — which the dump also carries — runs when nothing depends on
it any more. It therefore SUCCEEDS, and `CREATE EXTENSION` rebuilds `geometry` and
`gist_geometry_ops_2d` with NEW OIDs.

Every connection open across that restore then holds PostGIS's per-backend operator cache pointing
at objects that no longer exist. `COUNT(*)` keeps working, because it touches no spatial operator;
every query using `&&` or `ST_Intersects` fails with

    no spatial operator found for 'st_intersects': opfamily N type M

until the process is restarted. It was found on the demo instance, whose hourly reset restores under
a live API: two errors an hour apart named two DIFFERENT pairs of OIDs, which is the extension being
rebuilt underneath the running service. `pool_pre_ping` cannot catch it — those connections are
alive and `SELECT 1` succeeds; it is the cache contents that are stale.

The invariant these tests pin is therefore about identity, not behaviour: the geometry type's OID
must be the SAME object before and after a restore.
"""
import pytest

from geodeploy.services.restore import toc_without_extensions


# A `pg_restore -l` listing, in the real format: `id; catalog oid TYPE schema name owner`.
TOC = """;
; Archive created at 2026-09-02 06:00:00 UTC
;     dbname: geodeploy
;     TOC Entries: 9
;
; Selected TOC Entries:
;
2; 3079 16385 EXTENSION - postgis
3; 0 0 COMMENT - EXTENSION postgis
4; 3079 16800 EXTENSION - postgis_topology
5; 0 0 COMMENT - EXTENSION postgis_topology
6; 2615 16390 SCHEMA - geodeploy_u1 geodeploy
7; 0 0 COMMENT - SCHEMA geodeploy_u1 geodeploy
215; 1259 16700 TABLE geodeploy_u1 gdp_per_capita geodeploy
216; 1259 16701 SEQUENCE public vector_layers_id_seq geodeploy
4210; 0 16700 TABLE DATA geodeploy_u1 gdp_per_capita geodeploy
4300; 2606 16720 CONSTRAINT geodeploy_u1 gdp_per_capita_pkey geodeploy
4301; 1259 16721 INDEX geodeploy_u1 gdp_per_capita_geom_idx geodeploy
"""


def test_the_extension_is_never_replayed():
    """The whole fix: no EXTENSION entry survives, so `--clean` emits no DROP EXTENSION and the
    geometry type keeps the OID every open connection already cached."""
    out = toc_without_extensions(TOC)
    lines = [l for l in out.splitlines() if l.strip() and not l.strip().startswith(";")]

    assert not [l for l in lines if " EXTENSION " in f" {l} "], out
    assert "postgis_topology" not in out          # every extension, not just postgis
    assert "COMMENT - EXTENSION" not in out       # and the comments hanging off them


def test_everything_else_survives():
    """A restore that dropped more than the extension would be a worse bug than the one being
    fixed — the point is to restore the data, and only the extension is exempt."""
    out = toc_without_extensions(TOC)

    for kept in ("TABLE geodeploy_u1 gdp_per_capita",
                 "TABLE DATA geodeploy_u1 gdp_per_capita",
                 "SEQUENCE public vector_layers_id_seq",
                 "CONSTRAINT geodeploy_u1 gdp_per_capita_pkey",
                 "INDEX geodeploy_u1 gdp_per_capita_geom_idx",
                 "SCHEMA - geodeploy_u1"):
        assert kept in out, f"{kept!r} was dropped from the TOC"


def test_a_comment_on_something_else_is_not_mistaken_for_an_extension_comment():
    """`COMMENT - SCHEMA ...` and `COMMENT - EXTENSION ...` differ only in one token, and dropping
    the wrong one silently loses a schema comment."""
    out = toc_without_extensions(TOC)
    assert "COMMENT - SCHEMA geodeploy_u1" in out


def test_the_header_survives_because_pg_restore_reads_this_file_back():
    """`-L` takes the same format it emits; the leading comment block is inert but pg_restore is
    handed the file verbatim, so mangling it is a needless risk."""
    out = toc_without_extensions(TOC)
    assert out.startswith(";")
    assert "; Selected TOC Entries:" in out
    assert out.endswith("\n")


def test_an_empty_or_header_only_listing_does_not_crash():
    """`pg_restore -l` on a dump with nothing in it, and the degenerate empty string. The caller
    falls back to an unfiltered restore when listing fails, so this must not raise instead."""
    assert toc_without_extensions("") == "\n"
    header_only = ";\n; Archive created at 2026-09-02\n;\n"
    assert toc_without_extensions(header_only).count(";") == 3


def test_a_short_line_is_kept_rather_than_indexed_past_its_end():
    """Defensive: a malformed line must not raise IndexError inside a restore. Keeping it is the
    safe direction — pg_restore judges it, and an unknown line is not an extension."""
    assert "999; 0" in toc_without_extensions("999; 0\n")


@pytest.mark.parametrize("obj_type", ["TABLE", "INDEX", "SEQUENCE", "VIEW", "FUNCTION", "TYPE"])
def test_no_other_object_type_is_swept_up(obj_type):
    line = f"300; 1259 16999 {obj_type} public thing geodeploy"
    assert line in toc_without_extensions(f";\n{line}\n")
