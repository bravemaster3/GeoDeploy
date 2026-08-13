"""Symbology assembly and job polling."""
from __future__ import annotations

import pytest

from geodeploy import styles
from geodeploy.errors import ValidationError
from geodeploy.jobs import JobFailed, JobTimeout


class TestBuildStyle:
    def test_only_what_was_given_is_applied(self):
        """The rule the whole CLI depends on: `--color` must not reset somebody's marker shape."""
        base = {"color": "#111", "marker": "star", "radius": 6}
        assert styles.build_style(base, color="#222") == {"color": "#222", "marker": "star",
                                                          "radius": 6}

    def test_none_arguments_are_absent_not_defaults(self):
        assert styles.build_style({}, color=None, radius=None) == {}

    def test_line_type_is_stored_camelcase(self):
        # The stored key is `lineType`; three renderers read it, so the spelling is not ours to fix.
        assert styles.build_style({}, line_type="dashed") == {"lineType": "dashed"}

    def test_outline_none_is_a_sentinel_not_an_empty_string(self):
        assert styles.build_style({}, outline_color="none")["outline_color"] == styles.NO_OUTLINE

    @pytest.mark.parametrize("kwargs", [
        {"marker": "hexagon"}, {"line_type": "dot-dash"}, {"color_mode": "rainbow"},
        {"fill_opacity": 2.0}, {"outline_width": -1},
    ])
    def test_invalid_values_are_refused_before_any_request(self, kwargs):
        with pytest.raises(ValidationError):
            styles.build_style({}, **kwargs)

    def test_size_stops_imply_proportional_sizing(self):
        style = styles.build_style({"size_field": "pop"}, size_stops="0:2,1000:12")
        assert style["size_mode"] == "proportional"
        assert style["size_stops"] == [[0.0, 2.0], [1000.0, 12.0]]

    def test_proportional_without_a_field_is_refused(self):
        with pytest.raises(ValidationError):
            styles.build_style({}, size_mode="proportional")

    def test_clearing_classification_removes_every_related_key(self):
        base = {"color_mode": "graduated", "color_field": "pop", "classes": [{"min": 1}],
                "other_color": "#ccc", "color": "#111"}
        cleared = styles.build_style(base, clear_classification=True)
        assert cleared == {"color": "#111"}

    def test_extrusion_needs_a_field(self):
        with pytest.raises(ValidationError):
            styles.build_style({}, extrude=True)

    def test_extrusion_block(self):
        style = styles.build_style({}, extrude=True, extrude_field="height", extrude_scale=2,
                                   extrude_radius=250)
        assert style["extrusion"] == {"enabled": True, "field": "height", "scale": 2,
                                      "radius": 250}

    def test_no_extrude_turns_it_off_without_losing_the_field(self):
        base = {"extrusion": {"enabled": True, "field": "height"}}
        assert styles.build_style(base, extrude=False)["extrusion"]["enabled"] is False


class TestParsers:
    def test_size_stops_are_sorted(self):
        assert styles.parse_size_stops("100:9,0:2") == [[0.0, 2.0], [100.0, 9.0]]

    @pytest.mark.parametrize("bad", ["0:2", "abc", "0-2", "x:y,1:2"])
    def test_bad_size_stops(self, bad):
        with pytest.raises(ValidationError):
            styles.parse_size_stops(bad)

    def test_classes_with_open_edges(self):
        parsed = styles.parse_classes("*-10:#fee,10-50:#f88,50-*:#f00")
        assert parsed[0] == {"min": None, "max": 10.0, "color": "#fee"}
        assert parsed[-1] == {"min": 50.0, "max": None, "color": "#f00"}

    def test_categories(self):
        assert styles.parse_categories("forest:#2c7,water:#39f") == [
            {"value": "forest", "color": "#2c7"}, {"value": "water", "color": "#39f"}]

    @pytest.mark.parametrize("bad", ["nonsense", "10-20", ""])
    def test_bad_classes(self, bad):
        with pytest.raises(ValidationError):
            styles.parse_classes(bad)


class TestClassify:
    """Breaks come from the SERVER, so the CLI cannot disagree with the editor about a class."""

    def test_numeric_field_produces_graduated_classes(self, client):
        style, stats = styles.classify(client, 1, "pop", classes=3, method="quantile")
        assert style["color_mode"] == "graduated"
        assert style["color_field"] == "pop"
        assert len(style["classes"]) == 3
        assert stats["kind"] == "numeric"

    def test_text_field_produces_categories(self, client):
        style, _ = styles.classify(client, 1, "name")
        assert style["color_mode"] == "categorized"
        assert [c["value"] for c in style["categories"]] == ["forest", "water"]

    def test_classify_asks_the_server_with_the_parameters_given(self, client, instance):
        styles.classify(client, 1, "pop", classes=7, method="jenks", ramp="magma")
        query = instance.requests_to("field-stats")[0]["query"]
        assert query["classes"] == ["7"] and query["method"] == ["jenks"]
        assert query["ramp"] == ["magma"]

    def test_unknown_method_and_ramp_fail_locally(self, client):
        with pytest.raises(ValidationError):
            styles.classify(client, 1, "pop", method="kmeans")
        with pytest.raises(ValidationError):
            styles.classify(client, 1, "pop", ramp="rainbow")

    def test_graduated_on_a_text_field_explains_itself(self, client):
        with pytest.raises(ValidationError) as caught:
            styles.classify(client, 1, "name", mode="graduated")
        assert "numeric" in str(caught.value)

    def test_switching_mode_drops_the_other_shape(self, client):
        base = {"categories": [{"value": "x", "color": "#111"}]}
        style, _ = styles.classify(client, 1, "pop", base=base)
        assert "categories" not in style and "classes" in style


class TestDescribe:
    def test_summaries(self):
        assert styles.describe({}) == "default"
        assert "graduated on pop" in styles.describe(
            {"color_mode": "graduated", "color_field": "pop", "classes": [1, 2, 3]})
        assert "categorized on kind" in styles.describe(
            {"color_mode": "categorized", "color_field": "kind", "categories": [1]})
        assert "color=#111" in styles.describe({"color": "#111"})
        assert "3D" in styles.describe({"extrusion": {"enabled": True, "field": "h"}})


class TestJobs:
    def test_wait_returns_the_final_status(self, client):
        created = client.vector.tile(1)
        final = client.jobs.wait(created["id"], interval=0)
        assert final["status"] == "ready"

    def test_progress_is_reported_only_when_it_changes(self, client, instance):
        created = client.vector.tile(1)
        job_id = created["id"]
        # Two identical polls in the middle: the callback must not fire for the repeat.
        instance.jobs[job_id] = [
            {"id": job_id, "layer_id": 1, "layer_type": "vector", "status": "processing",
             "progress": 10, "current_step": "Reading"},
            {"id": job_id, "layer_id": 1, "layer_type": "vector", "status": "processing",
             "progress": 10, "current_step": "Reading"},
            {"id": job_id, "layer_id": 1, "layer_type": "vector", "status": "ready",
             "progress": 100, "current_step": "Done"},
        ]
        seen = []
        client.jobs.wait(job_id, interval=0, on_progress=lambda st: seen.append(st["progress"]))
        assert seen == [10, 100]

    def test_a_failed_job_raises_with_the_server_message(self, client, instance):
        created = client.vector.tile(1)
        job_id = created["id"]
        instance.jobs[job_id] = [{"id": job_id, "layer_id": 1, "layer_type": "vector",
                                  "status": "failed", "progress": 30,
                                  "error_message": "Invalid geometry at row 12"}]
        with pytest.raises(JobFailed) as caught:
            client.jobs.wait(job_id, interval=0)
        assert "Invalid geometry at row 12" in str(caught.value)

    def test_completed_counts_as_done(self, client, instance):
        """Two pipelines report success differently; polling forever on the other one is a bug."""
        created = client.vector.tile(1)
        job_id = created["id"]
        instance.jobs[job_id] = [{"id": job_id, "layer_id": 1, "layer_type": "vector",
                                  "status": "completed", "progress": 100}]
        assert client.jobs.wait(job_id, interval=0)["status"] == "completed"

    def test_timeout_says_the_job_is_still_running(self, client, instance):
        created = client.vector.tile(1)
        job_id = created["id"]
        instance.jobs[job_id] = [{"id": job_id, "layer_id": 1, "layer_type": "vector",
                                  "status": "processing", "progress": 5}]
        with pytest.raises(JobTimeout) as caught:
            client.jobs.wait(job_id, interval=0, timeout=0)
        assert "keeps running on the server" in str(caught.value)
