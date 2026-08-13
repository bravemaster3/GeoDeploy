"""The command line itself: exit codes, --json, and the commands people will actually run."""
from __future__ import annotations

import json
import os

import pytest

from geodeploy.cli.output import (EXIT_AUTH, EXIT_GENERIC, EXIT_NETWORK, EXIT_OK, EXIT_SERVER,
                                  EXIT_USAGE)


class TestExitCodes:
    """The published matrix. A CI job branches on these long before it parses any output."""

    def test_constants(self):
        assert (EXIT_OK, EXIT_GENERIC, EXIT_USAGE, EXIT_AUTH, EXIT_NETWORK, EXIT_SERVER) == (
            0, 1, 2, 3, 4, 5)

    def test_no_command_prints_help_and_exits_usage(self, run):
        code, out, err = run()
        assert code == EXIT_USAGE
        assert "geodeploy" in err

    def test_group_without_a_subcommand_shows_that_group(self, run):
        code, out, err = run("portals")
        assert code == EXIT_USAGE
        assert "publish" in err          # the group's own help, not the root's

    def test_unknown_command_is_argparse_usage(self, run):
        with pytest.raises(SystemExit) as caught:
            run("nonsense")
        assert caught.value.code == EXIT_USAGE

    def test_not_logged_in_is_an_auth_failure_with_a_way_out(self, run):
        code, out, err = run("whoami")
        assert code == EXIT_AUTH
        assert "geodeploy login" in err

    def test_a_401_from_the_instance_is_auth(self, run, logged_in, instance):
        instance.fail_next = (401, "Token has expired")
        code, out, err = run("layers", "list")
        assert code == EXIT_AUTH
        assert "expired" in err

    def test_a_403_names_the_missing_scope(self, run, logged_in, instance):
        instance.fail_next = (403, "Token missing scope: data:write")
        code, out, err = run("layers", "share", "roads", "--visibility", "public")
        assert code == EXIT_AUTH
        assert "data:write" in err

    def test_a_500_is_a_server_error(self, run, logged_in, instance):
        instance.fail_next = (500, "Internal Server Error")
        code, out, err = run("layers", "list")
        assert code == EXIT_SERVER

    def test_an_unreachable_instance_is_a_network_error(self, run, home):
        code, out, err = run("--url", "http://127.0.0.1:1", "--token", "gdp_x", "layers", "list")
        assert code == EXIT_NETWORK
        assert "127.0.0.1:1" in err       # name the host that failed, not just "connection error"

    def test_a_404_is_a_plain_failure(self, run, logged_in):
        code, out, err = run("portals", "show", "no-such-portal")
        assert code == EXIT_GENERIC


class TestGlobalFlags:
    def test_json_is_accepted_after_the_subcommand(self, run, logged_in):
        """Where people actually type it. Argparse alone only allows it before the subcommand."""
        code, out, err = run("layers", "list", "--json")
        assert code == EXIT_OK
        assert isinstance(json.loads(out), list)

    def test_json_is_also_accepted_before_the_subcommand(self, run, logged_in):
        code, out, err = run("--json", "layers", "list")
        assert code == EXIT_OK
        json.loads(out)

    def test_json_errors_are_json_on_stdout(self, run, logged_in, instance):
        instance.fail_next = (500, "boom")
        code, out, err = run("layers", "list", "--json")
        payload = json.loads(out)
        assert payload["ok"] is False and "boom" in payload["error"]

    def test_quiet_keeps_stdout_and_drops_commentary(self, run, logged_in):
        code, out, err = run("layers", "list", "--quiet")
        assert code == EXIT_OK
        assert err == ""

    def test_verbose_logs_requests_to_stderr(self, run, logged_in):
        code, out, err = run("layers", "list", "--verbose")
        assert "GET" in err and "/api/data/vector" in err

    def test_url_and_token_flags_need_no_config(self, run, home, server):
        code, out, err = run("--url", server, "--token", "gdp_x", "whoami", "--json")
        assert code == EXIT_OK
        assert json.loads(out)["email"] == "k@example.org"

    def test_environment_variables_are_honoured(self, run, home, server, monkeypatch):
        monkeypatch.setenv("GEODEPLOY_URL", server)
        monkeypatch.setenv("GEODEPLOY_TOKEN", "gdp_env")
        code, out, err = run("whoami", "--json")
        assert code == EXIT_OK


class TestLoginAndProfiles:
    def test_login_stores_a_profile_and_a_credential(self, run, server, home):
        code, out, err = run("login", server, "--token", "gdp_test_token", "--json")
        assert code == EXIT_OK
        payload = json.loads(out)
        assert payload["instance"] == server
        assert payload["session"] is False        # no password was used
        assert os.path.isfile(payload["stored"]) or payload["stored"] == "keyring"

        code, out, _ = run("whoami", "--json")
        assert json.loads(out)["credential"].startswith("API token")

    def test_login_with_a_bad_token_does_not_save_anything(self, run, server, home, instance):
        instance.fail_next = (401, "Invalid token")
        code, out, err = run("login", server, "--token", "gdp_wrong")
        assert code == EXIT_AUTH
        code, out, err = run("profile", "list", "--json")
        assert json.loads(out) == []

    def test_password_login_stores_a_session_beside_the_token(self, run, server, home, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        monkeypatch.setattr("getpass.getpass", lambda *a, **kw: "correct-horse")
        code, out, err = run("login", server, "--token", "gdp_test_token", "--password",
                             "--email", "k@example.org", "--json")
        assert code == EXIT_OK
        assert json.loads(out)["session"] is True

        from geodeploy import config as cfg
        stored = cfg.load_credential(server)
        assert stored["token"] == "gdp_test_token"     # the session did not displace the token
        assert stored["jwt"].startswith("jwt-for-")

    def test_a_wrong_password_fails_without_storing(self, run, server, home, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        monkeypatch.setattr("getpass.getpass", lambda *a, **kw: "wrong")
        code, out, err = run("login", server, "--password", "--email", "k@example.org")
        assert code == EXIT_AUTH
        from geodeploy import config as cfg
        assert cfg.load_credential(server) == {}

    def test_logout_forgets_the_credential(self, run, logged_in):
        code, out, err = run("logout", "--json")
        assert json.loads(out)["forgotten"] is True
        code, out, err = run("whoami")
        assert code == EXIT_AUTH

    def test_profile_show_says_where_each_setting_came_from(self, run, logged_in):
        code, out, err = run("profile", "show", "--json")
        payload = json.loads(out)
        assert payload["instance_from"].startswith("profile:")
        assert payload["credential_from"] == "stored"

    def test_switching_profiles(self, run, logged_in, server):
        run("login", server, "--token", "gdp_x", "--name", "staging", "--json")
        code, out, _ = run("profile", "list", "--json")
        names = {row["name"]: row["active"] for row in json.loads(out)}
        assert names["staging"] is True
        run("profile", "use", "127.0.0.1")
        code, out, _ = run("profile", "list", "--json")
        assert {r["name"]: r["active"] for r in json.loads(out)}["127.0.0.1"] is True


class TestLayerCommands:
    def test_list_as_a_table(self, run, logged_in):
        code, out, err = run("layers", "list")
        assert code == EXIT_OK
        assert "roads" in out and "dem" in out
        assert "NAME" in out                       # a header, so the columns are readable

    def test_the_listing_shows_the_stable_uid(self, run, logged_in):
        """The integer is unique only within one kind and one database. If the listing shows only
        that, people script with it — so the durable handle has to be on screen."""
        code, out, err = run("layers", "list")
        assert "UID" in out
        assert "aaaaaaaaaaaa" in out and "cccccccccccc" in out

    def test_processing_layers_show_their_progress(self, run, logged_in):
        code, out, err = run("layers", "list")
        assert "42%" in out

    def test_filtering_by_type(self, run, logged_in):
        code, out, err = run("layers", "list", "--type", "raster", "--json")
        rows = json.loads(out)
        assert [r["name"] for r in rows] == ["dem"]

    def test_show_accepts_a_name(self, run, logged_in):
        code, out, err = run("layers", "show", "roads", "--json")
        assert json.loads(out)["id"] == 1

    def test_share_reports_what_it_opened_up(self, run, logged_in):
        code, out, err = run("layers", "share", "roads", "--visibility", "public")
        assert code == EXIT_OK
        assert "STAC" in err

    def test_style_saves_a_default_style(self, run, logged_in, instance):
        code, out, err = run("layers", "style", "roads", "--color", "#e11d48", "--marker", "star")
        assert code == EXIT_OK
        body = json.loads(instance.requests_to("/default-style", "PUT")[0]["body"])
        assert body["style"]["color"] == "#e11d48"
        assert body["style"]["marker"] == "star"

    def test_style_classification_goes_through_the_server(self, run, logged_in, instance):
        code, out, err = run("layers", "style", "roads", "--color-field", "pop", "--classify",
                             "--classes", "3")
        assert code == EXIT_OK
        body = json.loads(instance.requests_to("/default-style", "PUT")[0]["body"])
        assert body["style"]["color_mode"] == "graduated"
        assert len(body["style"]["classes"]) == 3

    def test_classify_without_a_field_is_refused(self, run, logged_in):
        code, out, err = run("layers", "style", "roads", "--classify")
        assert code == EXIT_USAGE
        assert "--color-field" in err

    def test_an_invalid_marker_never_reaches_the_api(self, run, logged_in, instance):
        with pytest.raises(SystemExit):
            run("layers", "style", "roads", "--marker", "hexagon")
        assert not instance.requests_to("/default-style", "PUT")

    def test_delete_refuses_without_yes_when_not_a_terminal(self, run, logged_in, instance):
        code, out, err = run("layers", "delete", "roads")
        assert code == EXIT_GENERIC
        assert not instance.requests_to("/data/vector/1", "DELETE")

    def test_delete_with_yes(self, run, logged_in, instance):
        code, out, err = run("layers", "delete", "roads", "--yes")
        assert code == EXIT_OK
        assert instance.requests_to("/data/vector/1", "DELETE")

    def test_links_warns_when_the_layer_is_not_public(self, run, logged_in):
        code, out, err = run("layers", "links", "roads")
        assert "not public" in err

    def test_rename(self, run, logged_in, instance):
        code, out, err = run("layers", "rename", "roads", "Main roads")
        assert code == EXIT_OK
        assert json.loads(instance.requests_to("/rename", "PUT")[0]["body"])["name"] == "Main roads"


class TestUploadCommand:
    def test_dry_run_explains_the_route_and_sends_nothing(self, run, logged_in, instance, tmp_path):
        path = tmp_path / "roads.gpkg"
        path.write_bytes(b"x" * 100)
        code, out, err = run("upload", str(path), "--dry-run", "--json")
        assert code == EXIT_OK
        assert json.loads(out)[0]["route"] == "vector-api"
        assert not instance.requests_to("/data/vector/upload", "POST")

    def test_several_files_at_once(self, run, logged_in, instance, tmp_path):
        paths = []
        for name in ("a.gpkg", "b.geojson", "c.tif"):
            path = tmp_path / name
            path.write_bytes(b"x" * 50)
            paths.append(str(path))
        code, out, err = run("upload", *paths + ["--json"])
        assert code == EXIT_OK
        payload = json.loads(out)
        assert payload["ok"] is True and len(payload["uploaded"]) == 3
        assert len(instance.requests_to("/upload", "POST")) == 3

    def test_a_guessed_csv_geometry_is_announced(self, run, logged_in, tmp_path):
        path = tmp_path / "sites.csv"
        path.write_text("lon,lat\n17.6,59.8\n", encoding="utf-8")
        code, out, err = run("upload", str(path))
        assert code == EXIT_OK
        assert "guessed geometry" in err

    def test_an_unsupported_file_stops_the_batch_before_anything_uploads(self, run, logged_in,
                                                                         instance, tmp_path):
        """Every file is planned first, so a typo in the last argument does not leave half a batch
        uploaded. An unsupported extension is the CLI rejecting the command line: exit 2."""
        good = tmp_path / "a.gpkg"
        good.write_bytes(b"x" * 10)
        bad = tmp_path / "b.txt"
        bad.write_bytes(b"x")
        code, out, err = run("upload", str(good), str(bad))
        assert code == EXIT_USAGE
        assert "Unsupported file type" in err
        assert not instance.requests_to("/data/vector/upload", "POST")

    def test_name_with_several_files_is_refused(self, run, logged_in, tmp_path):
        paths = []
        for name in ("a.gpkg", "b.gpkg"):
            path = tmp_path / name
            path.write_bytes(b"x")
            paths.append(str(path))
        code, out, err = run("upload", *paths + ["--name", "One"])
        assert code == EXIT_GENERIC


class TestPortalCommands:
    def test_list(self, run, logged_in):
        code, out, err = run("portals", "list")
        assert "Field sites 2026" in out

    def test_create_and_publish(self, run, logged_in):
        code, out, err = run("portals", "create", "New portal", "--json")
        assert code == EXIT_OK
        portal_id = json.loads(out)["id"]
        code, out, err = run("portals", "publish", str(portal_id), "--json")
        assert json.loads(out)["published"] is True

    def test_add_layer_by_name_with_styling(self, run, logged_in, instance):
        code, out, err = run("portals", "add-layer", "3", "dem", "--type", "raster",
                             "--colormap", "viridis", "--rescale", "0,2400", "--bottom")
        assert code == EXIT_OK
        configs = json.loads(instance.requests_to("/portals/3", "PUT")[0]["body"])["layer_configs"]
        assert configs[-1]["layer_type"] == "raster"
        assert configs[-1]["style"] == {"colormap": "viridis", "rescale": "0,2400"}

    def test_add_layer_inherits_the_layer_default_style(self, run, logged_in, instance):
        """With no styling flags, a layer arrives on a portal looking like it does everywhere else."""
        code, out, _ = run("portals", "create", "Second", "--json")
        portal_id = json.loads(out)["id"]
        code, out, err = run("portals", "add-layer", str(portal_id), "roads")
        assert code == EXIT_OK
        put = instance.requests_to("/portals/{0}".format(portal_id), "PUT")[0]
        configs = json.loads(put["body"])["layer_configs"]
        assert configs[0]["style"] == {"color": "#3b82f6"}

    def test_a_draft_edit_says_it_is_not_live(self, run, logged_in):
        code, out, err = run("portals", "set-description", "3", "About this map")
        assert "still shows the previous version" in err

    def test_publish_flag_publishes_in_one_step(self, run, logged_in, instance):
        code, out, err = run("portals", "add-layer", "3", "dem", "--type", "raster", "--publish")
        assert instance.requests_to("/portals/3/publish", "POST")

    def test_style_on_a_portal_merges(self, run, logged_in, instance):
        code, out, err = run("portals", "style", "3", "roads", "--radius", "8")
        style = json.loads(instance.requests_to("/portals/3", "PUT")[0]["body"])
        assert style["layer_configs"][0]["style"] == {"color": "#3b82f6", "radius": 8.0}

    def test_export_writes_utf8_json_that_import_can_read_back(self, run, logged_in, tmp_path):
        out_path = tmp_path / "portal3.json"
        code, out, err = run("portals", "export", "3", str(out_path))
        assert code == EXIT_OK
        # UTF-8 without a BOM: PowerShell's `>` writes UTF-16, which import could not read.
        raw = open(str(out_path), "rb").read()
        assert not raw.startswith(b"\xef\xbb\xbf")
        json.loads(raw.decode("utf-8"))

        code, out, err = run("portals", "import", "3", str(out_path))
        assert code == EXIT_OK

    def test_layers_table_shows_the_draw_order(self, run, logged_in):
        code, out, err = run("portals", "layers", "3")
        assert "roads" in out
        assert "top of the layer list" in err

    def test_url(self, run, logged_in, server):
        code, out, err = run("portals", "url", "3")
        assert out.strip() == "{0}/portals/field-sites-2026/".format(server)


class TestAdminAndCatalog:
    def test_admin_needs_a_session_and_says_so(self, run, logged_in, instance):
        instance.token_forbidden.add("/admin")
        code, out, err = run("admin", "health")
        assert code == EXIT_AUTH
        assert "session-only" in err
        assert "login --password" in err

    def test_admin_works_with_a_session(self, run, server, home, instance, monkeypatch):
        instance.token_forbidden.add("/admin")
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        monkeypatch.setattr("getpass.getpass", lambda *a, **kw: "correct-horse")
        run("login", server, "--password", "--email", "k@example.org", "--json")
        code, out, err = run("admin", "health", "--json")
        assert code == EXIT_OK
        assert json.loads(out)[0]["name"] == "api"

    def test_storage_reports_unmeasured_as_unmeasured_not_zero(self, run, logged_in):
        code, out, err = run("admin", "storage")
        assert "not measured" in out          # raster_bytes is null in the fixture

    def test_catalog_needs_no_credential(self, run, home, server):
        code, out, err = run("--url", server, "catalog", "collections", "--json")
        assert code == EXIT_OK
        assert json.loads(out)[0]["id"] == "vector-aaaaaaaaaaaa"

    def test_templates(self, run, logged_in):
        code, out, err = run("catalog", "templates")
        assert "minimal" in out

    def test_users_list_works_with_a_token(self, run, logged_in):
        code, out, err = run("users", "list", "--json")
        assert json.loads(out)[0]["role"] == "owner"


class TestBrowse:
    """The anonymous entry point: paste a URL, see what is there. No credential involved."""

    def test_needs_no_credential_at_all(self, run, home, server):
        code, out, err = run("browse", server)
        assert code == EXIT_OK
        assert "Field sites 2026" in out          # the public portal
        assert "roads" in out and "dem" in out    # public layers, by kind

    def test_it_groups_layers_by_storage_kind(self, run, home, server):
        code, out, err = run("browse", server)
        assert "Vector (PostGIS)" in out
        assert "Raster (COG)" in out

    def test_json_is_the_index_itself(self, run, home, server):
        code, out, err = run("browse", server, "--json")
        payload = json.loads(out)
        assert set(payload["layers"]) == {"postgis", "geoparquet", "raster"}
        assert payload["portals"][0]["slug"] == "field-sites-2026"

    def test_it_uses_the_active_profile_when_no_url_is_given(self, run, logged_in):
        code, out, err = run("browse")
        assert code == EXIT_OK

    def test_with_a_token_it_also_reports_the_private_view(self, run, logged_in):
        code, out, err = run("browse")
        assert "With your token" in err

    def test_without_one_it_says_the_view_is_anonymous(self, run, home, server):
        code, out, err = run("browse", server)
        assert "anonymous view" in err

    def test_an_instance_with_the_index_off_explains_itself(self, run, home, server, instance):
        instance.public_index_enabled = False
        code, out, err = run("browse", server)
        assert code == EXIT_GENERIC
        assert "does not publish a public index" in err

    def test_filtering_to_one_kind(self, run, home, server):
        code, out, err = run("browse", server, "--kind", "raster")
        assert "Raster (COG)" in out
        assert "Vector (PostGIS)" not in out


class TestBrowseAPortal:
    """A portal URL names its own instance. Pasting one must not also require --url or a login."""

    def test_a_pasted_portal_url_needs_nothing_else(self, run, home, server):
        code, out, err = run("browse", "--portal", server + "/portals/field-sites-2026/")
        assert code == EXIT_OK
        assert "Field sites 2026" in out
        assert "Roads" in out
        assert "basemap" not in out          # the template's own layer, not the author's data

    def test_layers_are_named_the_way_the_portal_names_them(self, run, home, server):
        code, out, err = run("browse", "--portal", "field-sites-2026", "--url", server)
        assert "Roads" in out and "Plots" in out and "DEM" in out
        assert "Trees" in out                       # a GeoParquet layer, drawn by deck.gl
        assert out.count("Plots") == 1              # not once per MapLibre layer it is drawn with
        assert "vector-3-outline" not in out        # …and its outline is not a layer of its own

    def test_a_layer_that_starts_switched_off_says_so(self, run, home, server):
        code, out, err = run("browse", "--portal", "field-sites-2026", "--url", server)
        assert "VISIBLE" in out.upper()

    def test_links_prints_the_url_behind_each_layer(self, run, home, server):
        code, out, err = run("browse", "--portal", "field-sites-2026", "--url", server, "--links")
        assert "/tiles/vector_1/{z}/{x}/{y}.pbf" in out
        assert "/data/trees.parquet" in out

    def test_the_url_alone_is_enough_as_the_positional(self, run, home, server):
        code, out, err = run("browse", server + "/portals/field-sites-2026/style.json")
        assert code == EXIT_OK
        assert "Field sites 2026" in out

    def test_a_slug_still_works_against_the_active_profile(self, run, logged_in):
        code, out, err = run("browse", "--portal", "field-sites-2026")
        assert code == EXIT_OK
        assert "Field sites 2026" in out

    def test_an_unknown_portal_explains_itself(self, run, home, server):
        code, out, err = run("browse", "--portal", server + "/portals/nope/")
        assert code == EXIT_GENERIC
        assert "Public portals only" in err

    def test_a_url_that_is_not_a_portal_is_a_usage_error(self, run, home, server):
        code, out, err = run("browse", "--portal", "https://gd.example.org/layers/roads")
        assert code != EXIT_OK
        assert "does not look like a portal URL" in err


class TestLayerDownload:
    def test_a_vector_layer_defaults_to_a_built_geopackage(self, run, logged_in, instance,
                                                           tmp_path):
        target = tmp_path / "roads.zip"
        code, out, err = run("layers", "download", "roads", "-o", str(target))
        assert code == EXIT_OK
        assert instance.last_export["format"] == "gpkg"
        assert "bbox" not in instance.last_export       # whole layer, not a clip
        assert target.read_bytes().startswith(b"PK")

    def test_a_bbox_and_a_native_crs_are_passed_through(self, run, logged_in, instance, tmp_path):
        run("layers", "download", "roads", "-o", str(tmp_path / "x.zip"),
            "--format", "csv", "--bbox", "11,55,12,56", "--crs", "native")
        assert instance.last_export == {"format": "csv", "bbox": "11,55,12,56",
                                        "target_crs": "native"}

    def test_a_raster_defaults_to_its_cog(self, run, logged_in, instance, tmp_path):
        target = tmp_path / "dem.tif"
        code, out, err = run("layers", "download", "dem", "--type", "raster", "-o", str(target))
        assert code == EXIT_OK
        assert instance.requests_to("/cog"), "the stored file should stream, not be rebuilt"

    def test_asking_for_a_cog_from_a_vector_layer_is_refused(self, run, logged_in, tmp_path):
        code, out, err = run("layers", "download", "roads", "--format", "cog",
                             "-o", str(tmp_path / "x.tif"))
        assert code == EXIT_GENERIC

    def test_a_public_layer_downloads_without_a_token(self, run, home, server, tmp_path):
        target = tmp_path / "roads.zip"
        code, out, err = run("--url", server, "layers", "download", "roads", "-o", str(target))
        assert code == EXIT_OK
        assert target.exists()


class TestTruncatedExport:
    """A capped export looks exactly like a complete one. The command must not let that pass."""

    def test_it_fails_loudly_when_the_server_reports_a_cap(self, run, logged_in, instance,
                                                           tmp_path):
        instance.export_truncated = [{"file": "roads.gpkg", "rows": 1000000, "cap": 1000000}]
        code, out, err = run("layers", "download", "roads", "-o", str(tmp_path / "roads.zip"))
        assert code == EXIT_GENERIC              # a script must be able to notice
        assert "INCOMPLETE" in err
        assert "1,000,000" in err
        assert (tmp_path / "roads.zip").exists()  # the partial file is still written, not hidden

    def test_it_points_at_the_uncapped_route(self, run, logged_in, instance, tmp_path):
        instance.export_truncated = [{"file": "roads.gpkg", "rows": 1000000, "cap": 1000000}]
        code, out, err = run("layers", "download", "roads", "-o", str(tmp_path / "roads.zip"))
        assert "OAPIF:" in err and "ogr2ogr" in err

    def test_json_mode_carries_the_report(self, run, logged_in, instance, tmp_path):
        instance.export_truncated = [{"file": "roads.gpkg", "rows": 1000000, "cap": 1000000}]
        code, out, err = run("layers", "download", "roads", "-o", str(tmp_path / "roads.zip"),
                             "--json")
        assert json.loads(out)["truncated"][0]["file"] == "roads.gpkg"

    def test_an_untruncated_export_says_nothing_of_the_sort(self, run, logged_in, tmp_path):
        code, out, err = run("layers", "download", "roads", "-o", str(tmp_path / "roads.zip"))
        assert code == EXIT_OK
        assert "INCOMPLETE" not in err


class TestGeoParquetDirectDownload:
    """A prepared GeoParquet layer already exists as files. Rebuilding it through the worker is
    slower, lossy at the row cap, and pointless."""

    def test_it_reads_the_stored_files_instead_of_queueing_an_export(self, run, logged_in,
                                                                     instance, tmp_path):
        target = tmp_path / "parcels"
        code, out, err = run("layers", "download", "parcels", "-o", str(target))
        assert code == EXIT_OK
        assert instance.last_export is None, "no export job should have been queued"
        assert (target / "manifest.json").exists()
        assert (target / "__cell=0" / "part0.parquet").read_bytes().startswith(b"PAR1")
        assert (target / "__cell=1" / "part0.parquet").exists()

    def test_it_says_how_to_read_what_it_wrote(self, run, logged_in, tmp_path):
        code, out, err = run("layers", "download", "parcels", "-o", str(tmp_path / "p"))
        assert "read_parquet" in err
        assert "2,400,000 features" in err

    def test_json_mode_reports_the_directory(self, run, logged_in, tmp_path):
        code, out, err = run("layers", "download", "parcels", "-o", str(tmp_path / "p"), "--json")
        payload = json.loads(out)
        assert payload["files"] == 2 and payload["format"] == "geoparquet"
        assert payload["truncated"] == []

    def test_a_bbox_still_goes_through_the_export_job(self, run, logged_in, instance, tmp_path):
        """A clip is real work — only the WHOLE layer is already a file."""
        run("layers", "download", "parcels", "--bbox", "11,55,12,56",
            "-o", str(tmp_path / "p.zip"), "--format", "geoparquet")
        assert instance.last_export["bbox"] == "11,55,12,56"

    def test_a_postgis_layer_is_unaffected(self, run, logged_in, instance, tmp_path):
        code, out, err = run("layers", "download", "roads", "-o", str(tmp_path / "roads.zip"))
        assert instance.last_export["format"] == "gpkg"

    def test_a_geoparquet_layer_without_a_manifest_falls_back_to_the_export(self, run, logged_in,
                                                                           instance, tmp_path):
        instance.PARQUET = set()          # e.g. a single .parquet uploaded as-is
        code, out, err = run("layers", "download", "parcels", "-o", str(tmp_path / "p.zip"))
        assert code == EXIT_OK
        assert instance.last_export is not None


class TestPublicListingToggle:
    """The switch that decides whether `browse` finds anything — reachable from the CLI, not only
    from the API, because an operator should never have to reach for curl."""

    def test_it_reports_the_current_state(self, run, logged_in):
        code, out, err = run("admin", "public-index", "--json")
        assert code == EXIT_OK
        assert json.loads(out) == {"enabled": True}

    def test_turning_it_off_and_on(self, run, logged_in, instance):
        run("admin", "public-index", "--off")
        assert instance.public_index_enabled is False
        code, out, err = run("browse")
        assert code == EXIT_GENERIC          # the index is gone…
        code, out, err = run("portals", "list")
        assert code == EXIT_OK               # …but the instance is unaffected

        run("admin", "public-index", "--on")
        assert instance.public_index_enabled is True

    def test_it_explains_what_off_means(self, run, logged_in):
        code, out, err = run("admin", "public-index", "--off")
        assert "reachable by their links" in err

    def test_on_and_off_together_is_a_usage_error(self, run, logged_in):
        with pytest.raises(SystemExit) as caught:
            run("admin", "public-index", "--on", "--off")
        assert caught.value.code == EXIT_USAGE
