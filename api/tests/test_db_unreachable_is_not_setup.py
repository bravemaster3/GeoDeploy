"""An installed instance whose database is down must not be offered the setup wizard.

The answers about what is configured live IN the database, so when it cannot be read everything
comes back false and the app concluded "fresh server" — showing an operator whose instance had run
for weeks the initial-setup screen. That reads as "your data is gone" and offers re-installing as
the remedy, which is the one action that could cause the loss it appears to describe.

`.env` holding a database host is the proof the instance was installed: the wizard wrote it.
"""
import pytest

from geodeploy.schemas import SetupStatus


def test_status_carries_the_distinction():
    assert SetupStatus(completed=False, postgis_configured=False, storage_configured=False,
                       admin_created=False).database_unreachable is False


@pytest.mark.asyncio
async def test_installed_but_unreachable_is_not_reported_as_fresh(monkeypatch):
    from geodeploy.routers import setup as setup_router

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **k):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(setup_router, "_session", lambda: _Boom())

    class _S:
        postgis_host = "postgres"

    monkeypatch.setattr(setup_router, "get_settings", lambda: _S())

    status = await setup_router.setup_status()
    assert status.database_unreachable is True
    assert status.completed is False       # still false: we genuinely cannot know


@pytest.mark.asyncio
async def test_a_genuinely_fresh_server_still_gets_the_wizard(monkeypatch):
    """The flag must not fire on a real first run, or nobody can ever install."""
    from geodeploy.routers import setup as setup_router

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **k):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(setup_router, "_session", lambda: _Boom())

    class _S:
        postgis_host = ""

    monkeypatch.setattr(setup_router, "get_settings", lambda: _S())

    status = await setup_router.setup_status()
    assert status.database_unreachable is False
    assert status.completed is False


@pytest.mark.asyncio
async def test_no_engine_at_all_is_a_fresh_server(monkeypatch):
    from geodeploy.routers import setup as setup_router

    monkeypatch.setattr(setup_router, "_session", lambda: None)
    status = await setup_router.setup_status()
    assert status.database_unreachable is False
