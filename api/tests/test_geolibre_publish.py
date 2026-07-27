"""SSRF guard on the GeoLibre COG-import download (runs in the container where celery/rasterio exist).

Imports are done inside the tests so collection doesn't fail in a minimal env without the worker deps.
"""
import pytest


def _gp():
    from geodeploy.tasks import geolibre_publish as gp
    return gp


@pytest.mark.parametrize("addr", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254",
                                  "::1", "fc00::1"])
def test_assert_public_https_blocks_non_public(monkeypatch, addr):
    gp = _gp()
    monkeypatch.setattr(gp.socket, "getaddrinfo",
                        lambda *a, **k: [(0, 0, 0, "", (addr, 443))])
    with pytest.raises(ValueError):
        gp._assert_public_https("https://evil.example/dem.tif")


def test_assert_public_https_allows_public(monkeypatch):
    gp = _gp()
    monkeypatch.setattr(gp.socket, "getaddrinfo",
                        lambda *a, **k: [(0, 0, 0, "", ("93.184.216.34", 443))])
    gp._assert_public_https("https://example.com/dem.tif")  # must not raise


def test_assert_public_https_rejects_http():
    gp = _gp()
    with pytest.raises(ValueError):
        gp._assert_public_https("http://example.com/dem.tif")
