"""Profiles, credentials and how a command decides which instance it is talking to."""
from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from geodeploy import config as cfg
from geodeploy.errors import ConfigError


class TestNormalizeUrl:
    """One instance must have ONE spelling, or a token saved under one is invisible to the other."""

    @pytest.mark.parametrize("given", [
        "https://gd.example.org",
        "https://gd.example.org/",
        "https://gd.example.org/api",
        "https://gd.example.org/api/",
        "https://gd.example.org/api/docs",
        "https://gd.example.org/api/openapi.json",
    ])
    def test_variants_collapse_to_one_origin(self, given):
        assert cfg.normalize_url(given) == "https://gd.example.org"

    def test_bare_host_defaults_to_https(self):
        # The wrong guess is a TLS error the user can read; defaulting to http would put their
        # token on the wire in clear.
        assert cfg.normalize_url("gd.example.org") == "https://gd.example.org"

    def test_port_and_http_are_kept(self):
        assert cfg.normalize_url("http://127.0.0.1:8080/") == "http://127.0.0.1:8080"

    @pytest.mark.parametrize("bad", ["", "   ", "ftp://gd.example.org", "https://"])
    def test_rejects_unusable(self, bad):
        with pytest.raises(ConfigError):
            cfg.normalize_url(bad)


class TestProfiles:
    def test_round_trip(self, home):
        config = cfg.Config.load()
        config.set_profile("prod", "https://gd.example.org/api/", email="k@example.org")
        config.save()

        reloaded = cfg.Config.load()
        assert reloaded.current == "prod"
        assert reloaded.get()["url"] == "https://gd.example.org"
        assert reloaded.get()["email"] == "k@example.org"

    def test_second_profile_becomes_current_and_removal_falls_back(self, home):
        config = cfg.Config.load()
        config.set_profile("a", "https://a.example.org")
        config.set_profile("b", "https://b.example.org")
        assert config.current == "b"
        config.remove_profile("b")
        assert config.current == "a"

    def test_unknown_profile_is_an_error_not_a_silent_default(self, home):
        config = cfg.Config.load()
        config.set_profile("a", "https://a.example.org")
        with pytest.raises(ConfigError):
            config.resolve_name("nope")

    def test_config_file_never_contains_a_token(self, home):
        config = cfg.Config.load()
        config.set_profile("prod", "https://gd.example.org")
        config.save()
        cfg.save_credential("https://gd.example.org", token="gdp_secret")
        assert "gdp_secret" not in open(cfg.config_path(), encoding="utf-8").read()


class TestCredentials:
    def test_token_and_session_live_side_by_side(self, home):
        cfg.save_credential("https://gd.example.org", token="gdp_abc")
        cfg.save_credential("https://gd.example.org", jwt="jwt-xyz", email="k@example.org")
        stored = cfg.load_credential("https://gd.example.org")
        assert stored["token"] == "gdp_abc"      # storing a session must not drop the token
        assert stored["jwt"] == "jwt-xyz"
        assert stored["email"] == "k@example.org"

    def test_spelling_variants_share_one_credential(self, home):
        cfg.save_credential("https://gd.example.org/api/", token="gdp_abc")
        assert cfg.load_token("gd.example.org") == "gdp_abc"

    def test_delete_session_only_keeps_the_token(self, home):
        cfg.save_credential("https://gd.example.org", token="gdp_abc", jwt="jwt-xyz")
        assert cfg.delete_token("https://gd.example.org", "jwt") is True
        stored = cfg.load_credential("https://gd.example.org")
        assert stored.get("token") == "gdp_abc"
        assert "jwt" not in stored

    def test_delete_removes_everything(self, home):
        cfg.save_credential("https://gd.example.org", token="gdp_abc")
        assert cfg.delete_token("https://gd.example.org") is True
        assert cfg.load_credential("https://gd.example.org") == {}

    def test_legacy_bare_string_is_still_readable(self, home, monkeypatch):
        """Pre-1.3 entries stored the raw token; reading one must not look like "not logged in"."""
        class FakeKeyring:
            @staticmethod
            def get_password(service, key):
                return "gdp_legacy"
        monkeypatch.delenv("GEODEPLOY_NO_KEYRING")
        monkeypatch.setattr(cfg, "_keyring", lambda: FakeKeyring)
        assert cfg.load_credential("https://gd.example.org")["token"] == "gdp_legacy"

    @pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX file modes")
    def test_credentials_file_is_not_world_readable(self, home):
        cfg.save_credential("https://gd.example.org", token="gdp_abc")
        mode = stat.S_IMODE(os.stat(cfg.credentials_path()).st_mode)
        assert mode == 0o600
        assert stat.S_IMODE(os.stat(os.path.dirname(cfg.credentials_path())).st_mode) == 0o700

    def test_write_is_atomic_and_leaves_no_temp_files(self, home):
        cfg.save_credential("https://gd.example.org", token="gdp_abc")
        cfg.save_credential("https://gd.example.org", token="gdp_def")
        leftovers = [f for f in os.listdir(cfg.config_dir()) if f.endswith(".tmp")]
        assert leftovers == []
        assert cfg.load_token("https://gd.example.org") == "gdp_def"

    def test_unreadable_config_is_reported_not_ignored(self, home):
        os.makedirs(cfg.config_dir(), exist_ok=True)
        with open(cfg.config_path(), "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        with pytest.raises(ConfigError):
            cfg.Config.load()

    def test_bom_written_by_a_windows_editor_is_tolerated(self, home):
        os.makedirs(cfg.config_dir(), exist_ok=True)
        with open(cfg.config_path(), "w", encoding="utf-8-sig") as fh:
            json.dump({"current": "prod", "profiles": {"prod": {"url": "https://gd.example.org"}}},
                      fh)
        assert cfg.Config.load().get()["url"] == "https://gd.example.org"


class TestResolution:
    """Flags beat the environment, which beats the active profile — for URL and token separately."""

    def test_profile_is_the_baseline(self, home):
        config = cfg.Config.load()
        config.set_profile("prod", "https://gd.example.org")
        config.save()
        cfg.save_credential("https://gd.example.org", token="gdp_stored")

        resolved = cfg.resolve()
        assert (resolved.url, resolved.token) == ("https://gd.example.org", "gdp_stored")
        assert resolved.source_url == "profile:prod"
        assert resolved.source_token == "stored"

    def test_environment_overrides_the_profile(self, home, monkeypatch):
        config = cfg.Config.load()
        config.set_profile("prod", "https://gd.example.org")
        config.save()
        monkeypatch.setenv("GEODEPLOY_URL", "https://staging.example.org")
        monkeypatch.setenv("GEODEPLOY_TOKEN", "gdp_env")

        resolved = cfg.resolve()
        assert resolved.url == "https://staging.example.org"
        assert resolved.token == "gdp_env"
        assert resolved.source_token == "env"

    def test_flags_override_everything(self, home, monkeypatch):
        monkeypatch.setenv("GEODEPLOY_URL", "https://staging.example.org")
        monkeypatch.setenv("GEODEPLOY_TOKEN", "gdp_env")
        resolved = cfg.resolve(url="https://other.example.org", token="gdp_flag")
        assert (resolved.url, resolved.token) == ("https://other.example.org", "gdp_flag")
        assert resolved.source_url == "flag" and resolved.source_token == "flag"

    def test_url_and_token_resolve_independently(self, home, monkeypatch):
        """A token in the environment with the profile's URL is a normal thing to do."""
        config = cfg.Config.load()
        config.set_profile("prod", "https://gd.example.org")
        config.save()
        monkeypatch.setenv("GEODEPLOY_TOKEN", "gdp_env")
        resolved = cfg.resolve()
        assert resolved.url == "https://gd.example.org"
        assert resolved.token == "gdp_env"

    def test_session_is_offered_when_there_is_no_token(self, home):
        config = cfg.Config.load()
        config.set_profile("prod", "https://gd.example.org")
        config.save()
        cfg.save_credential("https://gd.example.org", jwt="jwt-xyz")
        resolved = cfg.resolve()
        assert resolved.token is None
        assert resolved.jwt == "jwt-xyz"

    def test_nothing_configured_is_not_an_exception(self, home):
        resolved = cfg.resolve()
        assert resolved.url is None and resolved.token is None
        assert resolved.source_url == "none"
