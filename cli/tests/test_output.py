"""Formatting rules: stdout is the answer, stderr is the commentary, --json is a contract."""
from __future__ import annotations

import io
import json

from geodeploy.cli.output import Formatter, Progress, human_size


def make(**kw):
    out, err = io.StringIO(), io.StringIO()
    return Formatter(stdout=out, stderr=err, **kw), out, err


class TestStreams:
    def test_the_answer_goes_to_stdout_and_the_hints_to_stderr(self):
        fmt, out, err = make()
        fmt.out("the answer")
        fmt.info("a hint")
        fmt.success("done")
        fmt.warn("careful")
        assert out.getvalue() == "the answer\n"
        assert "a hint" in err.getvalue() and "done" in err.getvalue()

    def test_json_mode_keeps_stdout_pure(self):
        fmt, out, err = make(json_mode=True)
        fmt.info("a hint")
        fmt.success("done")
        fmt.warn("careful")
        fmt.render([{"id": 1}])
        assert err.getvalue() == ""
        assert json.loads(out.getvalue()) == [{"id": 1}]

    def test_quiet_silences_commentary_but_not_errors(self):
        fmt, out, err = make(quiet=True)
        fmt.info("hint")
        fmt.success("done")
        fmt.error("it broke")
        assert "hint" not in err.getvalue() and "done" not in err.getvalue()
        assert "it broke" in err.getvalue()

    def test_json_errors_are_a_document_a_script_can_read(self):
        fmt, out, err = make(json_mode=True)
        fmt.error("no such layer", hint="try `layers list`")
        payload = json.loads(out.getvalue())
        assert payload == {"ok": False, "error": "no such layer", "hint": "try `layers list`"}

    def test_debug_only_when_verbose(self):
        fmt, out, err = make()
        fmt.debug("GET /api/x")
        assert err.getvalue() == ""
        fmt, out, err = make(verbose=True)
        fmt.debug("GET /api/x")
        assert "GET /api/x" in err.getvalue()

    def test_no_color_is_honoured(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        fmt, out, err = make()
        fmt.out(fmt.green("ok"))
        assert "\033[" not in out.getvalue()


class TestTables:
    def test_headers_and_alignment(self):
        fmt, out, err = make()
        fmt.table([{"id": 1, "name": "roads"}, {"id": 22, "name": "dem"}], ["id", "name"])
        lines = out.getvalue().splitlines()
        assert lines[0].split() == ["ID", "NAME"]
        assert lines[1].startswith("1 ") and lines[2].startswith("22")

    def test_missing_values_read_as_missing(self):
        fmt, out, err = make()
        fmt.table([{"id": 1, "crs": None}], ["id", "crs"])
        assert "—" in out.getvalue()

    def test_booleans_are_words(self):
        fmt, out, err = make()
        fmt.table([{"published": True, "draft": False}], ["published", "draft"])
        assert "yes" in out.getvalue() and "no" in out.getvalue()

    def test_nested_values_are_summarised_not_dumped(self):
        fmt, out, err = make()
        fmt.table([{"style": {"a": 1, "b": 2}, "tags": ["x", "y", "z"]}], ["style", "tags"])
        text = out.getvalue()
        assert "2 keys" in text and "3 items" in text

    def test_a_bbox_stays_readable(self):
        fmt, out, err = make()
        fmt.table([{"bbox": [11.1, 55.2, 24.3, 69.4]}], ["bbox"])
        assert "11.1, 55.2, 24.3, 69.4" in out.getvalue()

    def test_empty_list_says_so_on_stderr(self):
        fmt, out, err = make()
        fmt.render([], ["id"], empty="No layers yet.")
        assert out.getvalue() == ""
        assert "No layers yet." in err.getvalue()

    def test_record_prints_key_and_value(self):
        fmt, out, err = make()
        fmt.record({"id": 3, "title": "Sites"}, ["id", "title"])
        assert "id" in out.getvalue() and "Sites" in out.getvalue()


class TestProgress:
    def test_silent_when_not_a_terminal(self):
        fmt, out, err = make()
        progress = Progress(fmt, "upload", 100)
        progress.update(50)
        progress.finish()
        assert err.getvalue() == ""
        assert out.getvalue() == ""

    def test_never_writes_to_stdout_even_on_a_terminal(self):
        class TtyIO(io.StringIO):
            def isatty(self):
                return True
        out, err = io.StringIO(), TtyIO()
        fmt = Formatter(stdout=out, stderr=err)
        progress = Progress(fmt, "upload", 100)
        progress.update(100)
        progress.finish()
        assert out.getvalue() == ""
        assert "upload" in err.getvalue()


class TestHumanSize:
    def test_units(self):
        assert human_size(0) == "0 B"
        assert human_size(999) == "999 B"
        assert human_size(1024) == "1.0 KB"
        assert human_size(48 * 1024 * 1024) == "48.0 MB"
        assert human_size(3 * 1024 ** 3) == "3.0 GB"


class TestLegacyConsoles:
    """A Windows console on code page 437 cannot encode "✓" — and a successful command must not
    end in a UnicodeEncodeError because of a decoration."""

    class Cp437IO(io.StringIO):
        encoding = "cp437"

    def test_glyphs_degrade_instead_of_crashing(self):
        out, err = self.Cp437IO(), self.Cp437IO()
        fmt = Formatter(stdout=out, stderr=err)
        fmt.success("uploaded")
        fmt.info("roads → parcels")
        fmt.out("a — b")
        text = out.getvalue() + err.getvalue()
        assert "OK uploaded" in text
        assert "roads -> parcels" in text
        assert "a - b" in text
        text.encode("cp437")          # the real assertion: this would have raised

    def test_utf8_consoles_keep_the_real_glyphs(self):
        class Utf8IO(io.StringIO):
            encoding = "utf-8"
        out, err = Utf8IO(), Utf8IO()
        fmt = Formatter(stdout=out, stderr=err)
        fmt.success("uploaded")
        assert "✓" in err.getvalue()

    def test_json_output_is_never_degraded(self):
        """JSON is a document for a machine; mangling a name inside it would be data loss."""
        out, err = self.Cp437IO(), self.Cp437IO()
        fmt = Formatter(json_mode=True, stdout=out, stderr=err)
        fmt.json({"name": "Åkerö — fält"})
        assert "Åkerö — fält" in out.getvalue()
