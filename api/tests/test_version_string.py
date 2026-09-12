"""The version the API advertises is the version that was released.

FOUND STALE, ON A LIVE INSTANCE. `/api/openapi.json` said `1.5.4` while the platform was at v1.6.4
— three releases and a month behind. The string is hard-coded in `main.py`, it had been bumped by
hand exactly twice in the project's life, and nothing checked it, so it drifted the moment somebody
cut a release without remembering this particular literal. It is not cosmetic: it is what an
operator reads to answer "did the update actually take?", and a wrong answer there sends them
looking for a fault that does not exist.

So the CHANGELOG is the single source of truth and this is the thing that keeps them in step.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHANGELOG = REPO / "CHANGELOG.md"

#: `## v1.6.4 — 2026-09-11`, and also `## v1.6 — …`. Not `## Unreleased`, which has no version.
HEADING = re.compile(r"^##\s+v(\d+\.\d+(?:\.\d+)?)\b", re.M)


def released_version() -> str:
    """The newest version the changelog names."""
    found = HEADING.search(CHANGELOG.read_text(encoding="utf-8"))
    assert found, "CHANGELOG.md has no `## vX.Y` heading to read a version from"
    return found.group(1)


def test_the_api_advertises_the_released_version():
    from geodeploy.main import app

    expected = released_version()
    assert app.version == expected, (
        "the API advertises {0} but the newest release in CHANGELOG.md is {1} — bump "
        "`version=` in api/geodeploy/main.py when you cut a release, or /api/openapi.json "
        "and the docs page will keep telling operators the wrong thing".format(
            app.version, expected)
    )


def test_the_changelog_heading_is_the_one_being_read():
    """A guard on the guard: if the changelog's format changes, this test must fail loudly rather
    than quietly matching nothing and passing whatever `app.version` happens to say."""
    text = CHANGELOG.read_text(encoding="utf-8")
    assert text.lstrip().startswith("# Changelog")
    assert "## Unreleased" in text, "the Unreleased heading is what the version regex must skip"
    assert HEADING.search(text).start() > text.index("## Unreleased"), (
        "the newest version heading should sit below `## Unreleased`")
