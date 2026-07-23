"""
NiDa — Industrial-Source Filter Tests

Verifies that detections on known industrial sites are dropped, that real
fires away from industry are kept, that the small buffer does not over-
reach, and that a missing/empty catalogue is a safe no-op.
"""

import json

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _clear_caches():
    # The loader/bucketing are lru_cached; clear between tests so a
    # monkeypatched path or buffer takes effect.
    from backend.geo import industrial
    industrial._load_sites.cache_clear()
    industrial._bucketed.cache_clear()
    yield
    industrial._load_sites.cache_clear()
    industrial._bucketed.cache_clear()


def _write_catalogue(tmp_path, sites):
    p = tmp_path / "sites.json"
    p.write_text(json.dumps({"source": "test", "count": len(sites), "sites": sites}),
                 encoding="utf-8")
    return p


def test_detection_on_industrial_site_flagged(tmp_path, monkeypatch):
    from backend.geo import industrial
    monkeypatch.setattr(industrial, "_DATA_PATH",
                        _write_catalogue(tmp_path, [{"lat": 35.79167, "lon": 5.28333, "kind": "works", "name": "cement"}]))
    industrial._load_sites.cache_clear(); industrial._bucketed.cache_clear()
    # exact location -> industrial
    assert industrial.is_industrial_source(35.79167, 5.28333) is True


def test_real_fire_far_from_industry_kept(tmp_path, monkeypatch):
    from backend.geo import industrial
    monkeypatch.setattr(industrial, "_DATA_PATH",
                        _write_catalogue(tmp_path, [{"lat": 35.79167, "lon": 5.28333, "kind": "works", "name": "cement"}]))
    industrial._load_sites.cache_clear(); industrial._bucketed.cache_clear()
    # a fire 50 km away must not be flagged
    assert industrial.is_industrial_source(36.2, 5.4) is False


def test_buffer_does_not_overreach(tmp_path, monkeypatch):
    """A fire ~5 km from a plant (well beyond the ~1 km facility buffer)
    must be kept -- we suppress only detections essentially on-site."""
    from backend.geo import industrial
    from backend.config import settings
    monkeypatch.setattr(settings, "INDUSTRIAL_FILTER_BUFFER_KM", 1.0)
    monkeypatch.setattr(industrial, "_DATA_PATH",
                        _write_catalogue(tmp_path, [{"lat": 35.5, "lon": 6.0, "kind": "plant", "name": "p"}]))
    industrial._load_sites.cache_clear(); industrial._bucketed.cache_clear()
    # 0.05 deg lat ~ 5.5 km away
    assert industrial.is_industrial_source(35.55, 6.0) is False


def test_filter_drops_only_industrial(tmp_path, monkeypatch):
    from backend.geo import industrial
    monkeypatch.setattr(industrial, "_DATA_PATH",
                        _write_catalogue(tmp_path, [{"lat": 35.79167, "lon": 5.28333, "kind": "works", "name": "cement"}]))
    industrial._load_sites.cache_clear(); industrial._bucketed.cache_clear()

    df = pd.DataFrame({
        "latitude":  [35.79167, 36.70000],   # on-cement, real northern fire
        "longitude": [5.28333, 4.05000],
        "frp":       [6.0, 180.0],
    })
    kept, dropped = industrial.filter_industrial_sources(df)
    assert dropped == 1
    assert len(kept) == 1
    assert kept.iloc[0]["latitude"] == 36.70000


def test_missing_catalogue_is_safe_noop(tmp_path, monkeypatch):
    from backend.geo import industrial
    monkeypatch.setattr(industrial, "_DATA_PATH", tmp_path / "does_not_exist.json")
    industrial._load_sites.cache_clear(); industrial._bucketed.cache_clear()

    df = pd.DataFrame({"latitude": [35.79167], "longitude": [5.28333], "frp": [6.0]})
    kept, dropped = industrial.filter_industrial_sources(df)
    assert dropped == 0
    assert len(kept) == 1


def test_disabled_is_noop(tmp_path, monkeypatch):
    from backend.geo import industrial
    from backend.config import settings
    monkeypatch.setattr(settings, "INDUSTRIAL_FILTER_ENABLED", False)
    monkeypatch.setattr(industrial, "_DATA_PATH",
                        _write_catalogue(tmp_path, [{"lat": 35.79167, "lon": 5.28333, "kind": "works", "name": "c"}]))
    industrial._load_sites.cache_clear(); industrial._bucketed.cache_clear()

    df = pd.DataFrame({"latitude": [35.79167], "longitude": [5.28333], "frp": [6.0]})
    kept, dropped = industrial.filter_industrial_sources(df)
    assert dropped == 0


def test_shipped_catalogue_loads():
    """The seed catalogue shipped with the project must be valid and
    non-empty so the filter is active out of the box."""
    from backend.geo import industrial
    sites = industrial._load_sites()
    assert len(sites) > 0
