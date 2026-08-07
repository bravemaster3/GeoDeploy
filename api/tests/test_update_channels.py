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


def test_a_BRANCH_target_resolves_through_the_remote_tracking_ref():
    """THE bug this test exists for, shipped and caught by the user in the UI.

    A branch this checkout has never been on exists ONLY as `refs/remotes/origin/<name>`. Resolving
    the bare name finds nothing, so selecting a branch failed with "No such version" even though the
    fetch had just brought it down. `main` and tags both hid it: the API rewrites `main` to
    `origin/main`, and a tag IS a local ref after `--tags`.

    Why the earlier test missed it: it asserted the FETCH mechanics (`--unshallow`,
    `set-branches origin '*'`) and inferred that branches therefore worked. Mechanics are not an
    outcome. This one asserts the resolution order itself.
    """
    import pathlib

    sh = (pathlib.Path(__file__).resolve().parents[2] / "installer" / "self-update.sh"
          ).read_text(encoding="utf-8")

    assert 'refs/remotes/origin/${TARGET#origin/}' in sh,         "a bare branch name must be resolved via its remote-tracking ref"
    # Remote BEFORE local: a stale local branch of the same name must not win over the remote.
    remote_at = sh.index('refs/remotes/origin/${TARGET#origin/}')
    assert remote_at < sh.index('"refs/tags/$TARGET"')
    # …and the reset must use the RESOLVED commit, not the raw string.
    assert 'git reset --hard "$TARGET_COMMIT"' in sh


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
    assert sh.index("set-branches") < sh.index("rev-parse -q --verify")


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
    releases = await admin._fetch_releases(_Client(TAGS, [
        {"tag_name": "v1.1", "name": "Vector styling", "published_at": "2026-08-05T00:00:00Z",
         "prerelease": False, "html_url": "https://example.invalid/v1.1"},
    ]))
    by_tag = {r["tag"]: r for r in releases}
    assert by_tag["v1.1"]["sha"] == "b" * 40
    assert by_tag["v1.1"]["name"] == "Vector styling"


@pytest.mark.asyncio
async def test_a_tag_without_release_notes_is_still_installable():
    """A project that tags before it writes notes must not look versionless."""
    releases = await admin._fetch_releases(_Client(TAGS, []))
    assert [r["tag"] for r in releases] == ["v1.1", "v1.0"]
    assert all(r["is_release"] is False for r in releases)


@pytest.mark.asyncio
async def test_a_draft_release_is_invisible():
    """`git fetch --tags` cannot see a draft either — its tag does not exist yet."""
    releases = await admin._fetch_releases(_Client(TAGS, [
        {"tag_name": "v1.1", "draft": True, "prerelease": False},
    ]))
    assert releases[0]["is_release"] is False


@pytest.mark.asyncio
async def test_branches_are_offered_except_main():
    """`main` has its own option; listing it twice under two labels is a way to pick the wrong one."""
    branches = await admin._fetch_branches(_Client(branches=[
        {"name": "main", "commit": {"sha": "f" * 40}},
        {"name": "feat/symbology", "commit": {"sha": "d" * 40}},
    ]))
    assert [b["name"] for b in branches] == ["feat/symbology"]
    assert branches[0]["sha"] == "d" * 40


@pytest.mark.asyncio
async def test_a_failed_lookup_returns_None_not_an_empty_list():
    """The distinction the cache depends on: `None` means "could not find out" and must preserve
    whatever was known before; `[]` means the repository genuinely has none."""
    assert await admin._fetch_releases(_Client(tags_status=403)) is None
    assert await admin._fetch_branches(_Client(branches_status=403)) is None


# ── The metadata cache ───────────────────────────────────────────────────────────────────────────
# Tags, releases and branches change rarely, and the version check went from 2 GitHub calls to 5
# when they were added — against an unauthenticated budget of 60 per HOUR per IP. Pressing Check a
# dozen times while testing an update exhausts it, and the failure is silent: the picker loses every
# release and branch and offers only `main`, as though the repository had neither. That happened.

@pytest.fixture(autouse=True)
def _clear_meta_cache():
    admin._META_CACHE.update({"at": 0.0, "releases": None, "latest_release": None, "branches": None})
    yield
    admin._META_CACHE.update({"at": 0.0, "releases": None, "latest_release": None, "branches": None})


class _CountingClient(_Client):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls = 0

    async def get(self, url, params=None):
        self.calls += 1
        return await super().get(url, params)


@pytest.mark.asyncio
async def test_the_metadata_is_cached_so_a_check_does_not_spend_five_calls():
    """The whole reason the picker went empty: five calls per refresh against 60 an hour."""
    c = _CountingClient(TAGS, [], branches=[{"name": "dev", "commit": {"sha": "d" * 40}}])
    await admin._load_version_metadata(c, {})
    first = c.calls
    assert first >= 2

    await admin._load_version_metadata(c, {})       # within the TTL
    assert c.calls == first, "a second call inside the TTL must not hit GitHub again"


@pytest.mark.asyncio
async def test_a_rate_limited_lookup_KEEPS_the_last_known_versions():
    """THE fix. A failed fetch must not replace a good list with nothing — the picker exists to name
    versions, and last hour's versions are still true. Losing them turns a transient 403 into "this
    repository has no releases and no branches", which is what the user saw."""
    ok = _Client(TAGS, [], branches=[{"name": "dev", "commit": {"sha": "d" * 40}}])
    await admin._load_version_metadata(ok, {})

    result = {}
    dead = _Client(tags_status=403, branches_status=403)
    await admin._load_version_metadata(dead, result, force=True)

    assert [r["tag"] for r in result["releases"]] == ["v1.1", "v1.0"]
    assert [b["name"] for b in result["branches"]] == ["dev"]


@pytest.mark.asyncio
async def test_a_prerelease_is_never_the_recommended_one():
    result = {}
    await admin._load_version_metadata(_Client(TAGS, [
        {"tag_name": "v1.1", "prerelease": True, "published_at": None, "html_url": None},
        {"tag_name": "v1.0", "prerelease": False, "published_at": None, "html_url": None},
    ]), result)
    assert result["latest_release"]["tag"] == "v1.0"
    assert {r["tag"] for r in result["releases"]} == {"v1.0", "v1.1"}


@pytest.mark.asyncio
async def test_the_cached_rows_are_COPIES():
    """`_finalize_versions` stamps `is_current` on release rows. Handing out the cached objects would
    let a stale "you are running this one" survive the next update."""
    result = {}
    await admin._load_version_metadata(_Client(TAGS, []), result)
    result["releases"][0]["is_current"] = True

    second = {}
    await admin._load_version_metadata(_Client(TAGS, []), second)
    assert "is_current" not in second["releases"][0]
