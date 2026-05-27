import pytest


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_setup_status_initial(client):
    r = await client.get("/api/setup/status")
    assert r.status_code == 200
    data = r.json()
    assert data["completed"] is False
    assert data["postgis_configured"] is False
    assert data["storage_configured"] is False
