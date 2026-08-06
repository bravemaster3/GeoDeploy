"""What an instance FOLLOWS, and what the panel should therefore offer.

`test_update_target.py` covers choosing a target and the shell boundary it crosses. This file covers
what was still missing afterwards: the choice did not STICK. Nothing recorded which ref an instance
was on, so a box deliberately pinned to v1.0 kept being measured against `main` and reported as
"40 commits behind" — the state its operator had chosen, described as a problem. And the release
list carried no commit, so the panel could not say which release was RUNNING.

Also here: BRANCH targets, so an unreleased feature branch can be tried on a real instance.

Deliberately DB-free — these are pure functions and a stubbed HTTP client.
"""
import inspect

import pytest

from geodeploy.routers import admin


# ── Which channel this instance follows ──────────────────────────────────────────────────────────

def test_deployed_ref_defaults_to_main(tmp_path, monkeypatch):
    """No marker = an install that predates release pinning, and it has been tracking main."""
    monkeypatch.setattr(admin, "get_settings", lambda: _Settings(str(tmp_path)))
    assert admin._deployed_ref() == "main"


def test_deployed_ref_is_read_from_the_marker_per_call(tmp_path, monkeypatch):
    """Per call, not per process: the file is written by the updater in ANOTHER container, and the
    API is not necessarily recreated afterwards (the same reasoning as `deployed-sha`)."""
    monkeypatch.setattr(admin, "get_settings", lambda: _Settings(str(tmp_path)))
    (tmp_path / "temp").mkdir()
    (tmp_path / "temp" / "deployed-ref").write_text("v1.0\n")
    assert admin._deployed_ref() == "v1.0"


def test_the_remote_tracking_prefix_is_not_part_of_the_answer(tmp_path, monkeypatch):
    """The API normalises `main` to `origin/main` before handing it to git, so that is what the
    script records. `origin/` is a git detail; the operator picked "main", and `main` is what has to
    match the release/branch names the panel compares against."""
    monkeypatch.setattr(admin, "get_settings", lambda: _Settings(str(tmp_path)))
    (tmp_path / "temp").mkdir()
    (tmp_path / "temp" / "deployed-ref").write_text("origin/main")
    assert admin._deployed_ref() == "main"


def test_the_updater_records_the_ref_it_deployed():
    """Without this the marker never appears and every instance looks like it tracks main."""
    import pathlib

    sh = (pathlib.Path(__file__).resolve().parents[2] / "installer" / "self-update.sh"
          ).read_text(encoding="utf-8")
    assert "data/temp/deployed-ref" in sh
    assert 'record_ref "$TARGET"' in sh
    assert 'record_ref "$OLD_REF"' in sh, "a rollback must restore the ref with the code"


def test_the_updater_verifies_the_new_code_is_actually_RUNNING():
    """A health check proves the stack is UP, not that it is NEW.

    Observed 2026-08-06: an update to a commit merged two hours earlier left `geodeploy/api:latest`
    47 hours old, both the API and the worker on pre-fix code, and the dashboard reading "Up to
    date" — because `/health` answers perfectly from an old-but-healthy API and `record_sha` had
    already moved the marker. The operator was told a fix was deployed that was not, and spent the
    next hour debugging its symptoms.

    Two independent things must hold, and the second catches what the first cannot:
      * an image whose build CONTEXT changed must have been rebuilt (scoped to the context, so a
        docs-only update legitimately produces no new image and stays silent);
      * every service must be RUNNING the image that was just built — "built fine, recreated
        nothing" is the other half.

    And when it fails, the version marker must go BACK. A wrong marker is worse than a failed
    update: it tells the operator, and the next debugging session, that a fix is live when it is not.
    """
    import pathlib

    sh = (pathlib.Path(__file__).resolve().parents[2] / "installer" / "self-update.sh"
          ).read_text(encoding="utf-8")

    assert "verify_deployed" in sh
    assert "context_changed" in sh, "must not cry wolf on a docs-only update"
    assert "container_image_id" in sh, "must catch 'built but not recreated'"
    # The verification runs AFTER the health check — being up is a precondition, not the proof.
    assert sh.index("if ! healthy;") < sh.index("DEPLOY_PROBLEM=")
    # …and a failure restores the previous marker rather than leaving a lie behind.
    fail_block = sh[sh.index('if [ -n "$DEPLOY_PROBLEM" ]'):]
    assert 'record_sha "$OLD_SHA"' in fail_block[:400]
    assert "write_status error" in fail_block[:800]


def test_the_updater_can_reach_tags_and_branches_of_a_default_clone():
    """`install.sh` clones `--depth 1 --branch main`: SHALLOW and SINGLE-BRANCH. Only the new targets
    hit either limit, so a normal update never revealed them — the first branch target would have
    failed as "No such version" on every real installation."""
    import pathlib

    sh = (pathlib.Path(__file__).resolve().parents[2] / "installer" / "self-update.sh"
          ).read_text(encoding="utf-8")
    assert "--unshallow" in sh
    assert "set-branches origin '*'" in sh
    # …and both before the ref is resolved, or they cannot help.
    assert sh.index("set-branches") < sh.index("rev-parse --verify")


class _Settings:
    def __init__(self, data_dir):
        self.data_dir = data_dir


# ── What the panel is told ───────────────────────────────────────────────────────────────────────

def _result(**over):
    base = {
        "latest_full": "f" * 40, "up_to_date": None, "update_available": None,
        "releases": [
            {"tag": "v1.1", "sha": "b" * 40, "is_release": True, "prerelease": False},
            {"tag": "v1.0", "sha": "a" * 40, "is_release": True, "prerelease": False},
        ],
        "latest_release": {"tag": "v1.1", "sha": "b" * 40, "is_release": True, "prerelease": False},
    }
    base.update(over)
    return base


def test_a_pinned_instance_reports_the_release_channel(monkeypatch):
    monkeypatch.setattr(admin, "_deployed_ref", lambda: "v1.0")
    result = _result()
    admin._finalize_versions(result, "a" * 40)

    assert result["channel"] == "release"
    assert result["current_tag"] == "v1.0"
    # It is behind a RELEASE, which is the comparison that matters here — not how far main has moved.
    assert result["release_update_available"] is True
    assert [r["is_current"] for r in result["releases"]] == [False, True]


def test_on_the_newest_release_nothing_is_offered(monkeypatch):
    monkeypatch.setattr(admin, "_deployed_ref", lambda: "v1.1")
    result = _result()
    admin._finalize_versions(result, "b" * 40)
    assert result["release_update_available"] is False
    assert result["current_tag"] == "v1.1"


def test_a_branch_is_its_own_channel(monkeypatch):
    """Neither `main` nor a tag. Calling it "release" would announce "a new release is available" to
    someone deliberately sitting on a feature branch, and calling it "main" would report it as
    behind — which is the point of being there."""
    monkeypatch.setattr(admin, "_deployed_ref", lambda: "feat/symbology")
    result = _result(branches=[{"name": "feat/symbology", "sha": "d" * 40}])
    admin._finalize_versions(result, "d" * 40)
    assert result["channel"] == "branch"


def test_an_unknown_ref_is_pinned_not_misfiled(monkeypatch):
    """A bare commit, or any ref GitHub did not list because we are offline. Report what it follows
    rather than judging it against a channel we cannot confirm."""
    monkeypatch.setattr(admin, "_deployed_ref", lambda: "9f8e7d6")
    result = _result()
    admin._finalize_versions(result, "9f8e7d6")
    assert result["channel"] == "pinned"


def test_main_channel_is_still_judged_against_main(monkeypatch):
    """The release fields must not disturb the original behaviour: an instance on main that is not
    on the latest commit is offered an update, exactly as before."""
    monkeypatch.setattr(admin, "_deployed_ref", lambda: "main")
    result = _result()
    admin._finalize_versions(result, "c" * 40)
    assert result["channel"] == "main"
    assert result["current_tag"] is None          # this commit is no release
    assert result["update_available"] is True


# ── Merging GitHub's two endpoints ───────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    """Stands in for httpx.AsyncClient: answers /tags, /releases and /branches, nothing else."""
    def __init__(self, tags=None, releases=None, tags_status=200, releases_status=200,
                 branches=None, branches_status=200):
        self.tags = tags if tags is not None else []
        self.releases = releases if releases is not None else []
        self.branches = branches if branches is not None else []
        self.tags_status, self.releases_status = tags_status, releases_status
        self.branches_status = branches_status

    async def get(self, url, params=None):
        if url.endswith("/tags"):
            return _Resp(self.tags, self.tags_status)
        if url.endswith("/releases"):
            return _Resp(self.releases, self.releases_status)
        if url.endswith("/branches"):
            return _Resp(self.branches, self.branches_status)
        raise AssertionError(f"unexpected request: {url}")


TAGS = [{"name": "v1.1", "commit": {"sha": "b" * 40}},
        {"name": "v1.0", "commit": {"sha": "a" * 40}}]


@pytest.mark.asyncio
async def test_releases_carry_the_sha_from_tags_and_the_metadata_from_releases():
    """Neither endpoint is sufficient alone: `/releases` has the title and date but NOT the commit,
    and the updater needs a commit to check out."""
    result = {}
    await admin._load_releases(_Client(TAGS, [
        {"tag_name": "v1.1", "name": "Vector styling", "published_at": "2026-08-05T00:00:00Z",
         "prerelease": False, "html_url": "https://example.invalid/v1.1"},
    ]), result)

    by_tag = {r["tag"]: r for r in result["releases"]}
    assert by_tag["v1.1"]["sha"] == "b" * 40
    assert by_tag["v1.1"]["name"] == "Vector styling"
    assert result["latest_release"]["tag"] == "v1.1"


@pytest.mark.asyncio
async def test_a_tag_without_release_notes_is_still_installable():
    """A project that tags before it writes notes must not look versionless."""
    result = {}
    await admin._load_releases(_Client(TAGS, []), result)
    assert [r["tag"] for r in result["releases"]] == ["v1.1", "v1.0"]
    assert all(r["is_release"] is False for r in result["releases"])
    assert result["latest_release"]["tag"] == "v1.1"       # falls back to the newest tag


@pytest.mark.asyncio
async def test_a_prerelease_is_listed_but_never_the_recommended_one():
    result = {}
    await admin._load_releases(_Client(TAGS, [
        {"tag_name": "v1.1", "prerelease": True, "published_at": None, "html_url": None},
        {"tag_name": "v1.0", "prerelease": False, "published_at": None, "html_url": None},
    ]), result)
    assert result["latest_release"]["tag"] == "v1.0"
    assert {r["tag"] for r in result["releases"]} == {"v1.0", "v1.1"}


@pytest.mark.asyncio
async def test_a_draft_release_is_invisible():
    """`git fetch --tags` cannot see a draft either — its tag does not exist yet."""
    result = {}
    await admin._load_releases(_Client(TAGS, [
        {"tag_name": "v1.1", "draft": True, "prerelease": False},
    ]), result)
    assert result["latest_release"]["tag"] == "v1.1"   # listed via /tags, but as a bare tag
    assert result["releases"][0]["is_release"] is False


@pytest.mark.asyncio
async def test_branches_are_offered_except_main():
    """`main` has its own option; listing it twice under two labels is a way to pick the wrong one."""
    result = {}
    await admin._load_branches(_Client(branches=[
        {"name": "main", "commit": {"sha": "f" * 40}},
        {"name": "feat/symbology", "commit": {"sha": "d" * 40}},
    ]), result)
    assert [b["name"] for b in result["branches"]] == ["feat/symbology"]
    assert result["branches"][0]["sha"] == "d" * 40


@pytest.mark.asyncio
async def test_no_branch_list_is_not_a_broken_update():
    result = {"latest": "abc1234"}
    await admin._load_branches(_Client(branches_status=403), result)
    assert "branches" not in result


@pytest.mark.asyncio
async def test_github_being_unreachable_leaves_the_normal_update_path_alone():
    """Rate-limited or offline: an admin who cannot see the release list must still be able to take
    a normal update, so the failure is swallowed and the key simply stays absent."""
    result = {"latest": "abc1234"}
    await admin._load_releases(_Client(tags_status=403), result)
    assert "releases" not in result
    assert result["latest"] == "abc1234"
