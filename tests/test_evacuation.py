"""
NiDa — Evacuation Routing Tests

ORS is always mocked here (httpx.AsyncClient.post monkeypatched) --
these tests never make real network calls, both because the sandbox
can't reach api.openrouteservice.org and because tests must be
deterministic regardless of network/API-quota availability. Real
end-to-end verification with the live ORS key happens on the
developer's machine, same pattern as FIRMS/wind integration testing
throughout this project.
"""

import pytest

from backend.db.database import FireClusterModel
from backend.geo.evacuation import (
    EvacuationError, cross_track_distance_km, get_route,
    nearest_safe_town, select_avoid_zones,
)


def _cluster(id, lat, lon, frp, wind=15.0, semi_major=8.0, semi_minor=4.0, orient=90.0):
    return FireClusterModel(
        id=id, centroid_lat=lat, centroid_lon=lon,
        point_count=10, max_frp=frp, mean_frp=frp * 0.7,
        has_high_confidence=1,
        semi_major_km=semi_major, semi_minor_km=semi_minor,
        orientation_deg=orient, lw_ratio=2.0,
        wind_speed_kmh=wind, wind_source="open-meteo",
        is_circular_fallback=0,
    )


# ── cross-track distance ──

def test_cross_track_zero_for_point_on_line():
    # Algiers to Béjaïa; a point roughly on that path should have small offset
    d = cross_track_distance_km(36.75, 5.06, 36.7538, 3.0588, 36.75, 5.08)
    assert d < 5


def test_cross_track_large_for_distant_point():
    # Tamanrasset is nowhere near an Algiers-Béjaïa route
    d = cross_track_distance_km(22.785, 5.522, 36.7538, 3.0588, 36.75, 5.08)
    assert d > 500


def test_cross_track_endpoint_fallback():
    """A point far beyond the destination end of the segment should fall
    back to distance-to-endpoint, not an unbounded infinite-line value."""
    d = cross_track_distance_km(20.0, 5.0, 36.7538, 3.0588, 36.75, 5.08)
    assert d > 0  # sane, finite


# ── nearest_safe_town ──

def test_nearest_safe_town_picks_closest_when_all_clear():
    town = nearest_safe_town([], lat=36.70, lon=3.10)  # near Algiers/Blida
    assert town["name"] in ("Algiers", "Blida")
    assert town["flagged_unsafe"] is False


def test_nearest_safe_town_skips_town_inside_danger_zone():
    """If the nearest candidate is itself inside a critical fire zone,
    the function must skip it and pick the next-nearest clear one."""
    # Huge critical fire centered exactly on Algiers
    danger = _cluster(1, 36.7538, 3.0588, frp=500.0, semi_major=15, semi_minor=15)
    town = nearest_safe_town([danger], lat=36.70, lon=3.10)
    assert town["name"] != "Algiers"
    assert town["flagged_unsafe"] is False


def test_nearest_safe_town_flags_when_all_blocked(monkeypatch):
    """Extreme edge case: every candidate town is inside a danger zone.
    Must still return something (nearest), flagged unsafe, not crash."""
    import backend.geo.evacuation as evac_mod
    monkeypatch.setattr(evac_mod, "SAFE_TOWNS", [
        {"name": "OnlyTown", "lat": 36.75, "lon": 3.06},
    ])
    danger = _cluster(1, 36.75, 3.06, frp=500.0, semi_major=20, semi_minor=20)
    town = evac_mod.nearest_safe_town([danger], lat=36.70, lon=3.10)
    assert town["name"] == "OnlyTown"
    assert town["flagged_unsafe"] is True


# ── select_avoid_zones ──

def test_select_avoid_zones_filters_by_corridor_and_severity():
    on_corridor = _cluster(1, 36.75, 4.0, frp=300.0)          # near Algiers->Béjaïa path, critical-ish
    far_away = _cluster(2, 22.785, 5.522, frp=300.0)           # Tamanrasset, irrelevant
    low_severity = _cluster(3, 36.75, 4.0, frp=0.5, wind=0.5)  # on corridor but advisory-level

    zones = select_avoid_zones([on_corridor, far_away, low_severity],
                                origin_lat=36.7538, origin_lon=3.0588,
                                dest_lat=36.75, dest_lon=5.08)
    ids = {c.id for c in zones}
    assert 1 in ids
    assert 2 not in ids
    assert 3 not in ids  # advisory severity excluded regardless of location


def test_select_avoid_zones_respects_max_cap(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "EVACUATION_MAX_AVOID_ZONES", 3)
    many = [_cluster(i, 36.75, 4.0 + i * 0.01, frp=300.0) for i in range(10)]
    zones = select_avoid_zones(many, origin_lat=36.7538, origin_lon=3.0588,
                                dest_lat=36.75, dest_lon=5.08)
    assert len(zones) == 3


# ── get_route (mocked ORS) ──

class _FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


ORS_SUCCESS_BODY = {
    "features": [{
        "geometry": {"coordinates": [[3.0588, 36.7538], [5.08, 36.75]]},
        "properties": {"summary": {"distance": 185000, "duration": 9000}},
    }]
}


@pytest.mark.asyncio
async def test_get_route_success(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "ORS_API_KEY", "fake-key-for-test")

    async def fake_post(self, url, json=None, headers=None):
        return _FakeResponse(200, ORS_SUCCESS_BODY)

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    result = await get_route(36.7538, 3.0588, 36.75, 5.08, [])
    assert result.distance_km == 185.0
    assert result.duration_min == 150.0
    assert len(result.geometry) == 2


@pytest.mark.asyncio
async def test_get_route_no_key_raises():
    from backend.config import settings as s
    original = s.ORS_API_KEY
    s.ORS_API_KEY = ""
    try:
        with pytest.raises(EvacuationError, match="not configured"):
            await get_route(36.7538, 3.0588, 36.75, 5.08, [])
    finally:
        s.ORS_API_KEY = original


@pytest.mark.asyncio
async def test_get_route_api_failure_raises_evacuation_error(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "ORS_API_KEY", "fake-key-for-test")

    async def fake_post(self, url, json=None, headers=None):
        return _FakeResponse(500, text="internal server error")

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    with pytest.raises(EvacuationError):
        await get_route(36.7538, 3.0588, 36.75, 5.08, [])


@pytest.mark.asyncio
async def test_get_route_no_route_found_raises(monkeypatch):
    """When ORS can't find a path (e.g. every road blocked by avoid
    zones), we must surface a clear error, not crash or fabricate data."""
    from backend.config import settings
    monkeypatch.setattr(settings, "ORS_API_KEY", "fake-key-for-test")

    async def fake_post(self, url, json=None, headers=None):
        return _FakeResponse(200, {"features": []})

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    with pytest.raises(EvacuationError):
        await get_route(36.7538, 3.0588, 36.75, 5.08, [])


@pytest.mark.asyncio
async def test_get_route_sends_avoid_polygons_when_zones_present(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "ORS_API_KEY", "fake-key-for-test")

    captured = {}

    async def fake_post(self, url, json=None, headers=None):
        captured["body"] = json
        return _FakeResponse(200, ORS_SUCCESS_BODY)

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    zone = _cluster(1, 36.75, 4.0, frp=300.0)
    await get_route(36.7538, 3.0588, 36.75, 5.08, [zone])

    assert "options" in captured["body"]
    assert "avoid_polygons" in captured["body"]["options"]
    assert captured["body"]["options"]["avoid_polygons"]["type"] == "MultiPolygon"


# ── trapped-origin detection ──

def test_zones_containing_point_detects_trapped_origin():
    from backend.geo.evacuation import zones_containing_point
    zone = _cluster(7, 36.75, 4.0, frp=300.0, semi_major=10, semi_minor=10)
    # point at the zone's centroid -> inside
    assert zones_containing_point([zone], 36.75, 4.0) == [7]
    # far away point -> not inside
    assert zones_containing_point([zone], 30.0, 8.0) == []


def test_zones_containing_point_ignores_advisory_zones():
    from backend.geo.evacuation import zones_containing_point
    weak = _cluster(8, 36.75, 4.0, frp=0.5, wind=0.5, semi_major=10, semi_minor=10)
    assert zones_containing_point([weak], 36.75, 4.0) == []


# ── directional diversity ──

def test_diverse_safe_towns_spread_across_bearings():
    from backend.geo.evacuation import diverse_safe_towns
    from backend.geo.distance import bearing_deg
    # Origin in central-north Algeria: Blida, Algiers, Tizi Ouzou are all
    # nearby but in similar directions; diversity must not pick three
    # towns bunched within 60 degrees of each other.
    towns = diverse_safe_towns([], lat=36.2, lon=3.5, max_destinations=4)
    assert 1 <= len(towns) <= 4
    bearings = [bearing_deg(36.2, 3.5, t["lat"], t["lon"]) for t in towns]
    for i in range(len(bearings)):
        for j in range(i + 1, len(bearings)):
            diff = min(abs(bearings[i] - bearings[j]), 360 - abs(bearings[i] - bearings[j]))
            assert diff >= 60.0, f"{towns[i]['name']} and {towns[j]['name']} only {diff:.0f} deg apart"


def test_diverse_safe_towns_excludes_burning_towns():
    from backend.geo.evacuation import diverse_safe_towns
    fire_on_blida = _cluster(1, 36.4700, 2.8300, frp=500.0, semi_major=12, semi_minor=12)
    towns = diverse_safe_towns([fire_on_blida], lat=36.5, lon=2.9, max_destinations=4)
    assert all(t["name"] != "Blida" for t in towns)


# ── multi-route ──

@pytest.mark.asyncio
async def test_multi_routes_excludes_origin_zone_from_avoidance(monkeypatch):
    """THE trapped-origin fix: when origin sits inside zone 7, that
    zone's polygon must NOT appear in any ORS avoid payload, while other
    zones still must."""
    from backend.config import settings
    from backend.geo.evacuation import get_multi_routes
    monkeypatch.setattr(settings, "ORS_API_KEY", "fake-key-for-test")

    captured_bodies = []

    async def fake_post(self, url, json=None, headers=None):
        captured_bodies.append(json)
        return _FakeResponse(200, ORS_SUCCESS_BODY)

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    trapping_zone = _cluster(7, 36.5, 2.9, frp=400.0, semi_major=15, semi_minor=15)
    other_zone = _cluster(9, 36.6, 3.5, frp=300.0)

    result = await get_multi_routes([trapping_zone, other_zone],
                                     origin_lat=36.5, origin_lon=2.9)

    assert result["origin_inside_zone_ids"] == [7]
    assert len(result["successes"]) >= 1
    # trapping zone (15km ellipse ~30km major axis at some LW) polygons are
    # distinguishable by size; simpler: count polygons per request must be
    # at most 1 (only zone 9 when relevant), never 2.
    for body in captured_bodies:
        polys = body.get("options", {}).get("avoid_polygons", {}).get("coordinates", [])
        assert len(polys) <= 1


@pytest.mark.asyncio
async def test_multi_routes_partial_success(monkeypatch):
    """If some destinations fail routing and others succeed, successes
    are returned ranked by distance and failures reported separately --
    not all-or-nothing."""
    from backend.config import settings
    from backend.geo.evacuation import get_multi_routes
    monkeypatch.setattr(settings, "ORS_API_KEY", "fake-key-for-test")

    call_count = {"n": 0}

    async def fake_post(self, url, json=None, headers=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeResponse(200, {"features": []})  # first dest: no route
        dist = 100000 + call_count["n"] * 10000
        return _FakeResponse(200, {"features": [{
            "geometry": {"coordinates": [[3.0, 36.7], [5.0, 36.7]]},
            "properties": {"summary": {"distance": dist, "duration": 6000}},
        }]})

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    result = await get_multi_routes([], origin_lat=36.2, origin_lon=3.5,
                                     max_destinations=3)

    assert result["destinations_tried"] == 3
    assert len(result["failures"]) == 1
    assert len(result["successes"]) == 2
    dists = [s["route"].distance_km for s in result["successes"]]
    assert dists == sorted(dists)


@pytest.mark.asyncio
async def test_multi_routes_all_fail(monkeypatch):
    from backend.config import settings
    from backend.geo.evacuation import get_multi_routes
    monkeypatch.setattr(settings, "ORS_API_KEY", "fake-key-for-test")

    async def fake_post(self, url, json=None, headers=None):
        return _FakeResponse(200, {"features": []})

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    result = await get_multi_routes([], origin_lat=36.2, origin_lon=3.5)
    assert result["successes"] == []
    assert len(result["failures"]) == result["destinations_tried"] > 0


def test_manual_destination_inside_zone_flagged(monkeypatch, tmp_path):
    """Regression: choosing a destination that sits inside an active fire
    zone must be flagged unsafe (previously the manual path skipped this
    check entirely and reported such destinations as safe)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import backend.db.database as dbmod
    from backend.db.database import Base, FireClusterModel

    engine = create_engine(f"sqlite:///{tmp_path}/t.db",
                           connect_args={"check_same_thread": False})
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(dbmod, "engine", engine)
    monkeypatch.setattr(dbmod, "SessionLocal", TestSession)
    Base.metadata.create_all(engine)

    from backend.config import settings
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", False)
    monkeypatch.setattr(settings, "ORS_API_KEY", "fake-key-for-test")

    async def fake_post(self, url, json=None, headers=None):
        return _FakeResponse(200, ORS_SUCCESS_BODY)
    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    # Seed a critical fire zone centred at (36.5, 3.0)
    db = TestSession()
    db.add(FireClusterModel(
        centroid_lat=36.5, centroid_lon=3.0, point_count=20,
        max_frp=400.0, mean_frp=200.0, has_high_confidence=1,
        semi_major_km=12.0, semi_minor_km=12.0, orientation_deg=90.0,
        lw_ratio=1.5, wind_speed_kmh=10.0, wind_source="open-meteo",
        is_circular_fallback=0,
    ))
    db.commit()
    db.close()

    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as c:
        # Destination exactly at the fire centroid -> must be flagged unsafe
        r = c.get("/api/v1/evacuation/route"
                  "?origin_lat=36.2&origin_lon=3.0&dest_lat=36.5&dest_lon=3.0")
        assert r.status_code == 200
        assert r.json()["destination_flagged_unsafe"] is True

        # A destination far from any fire -> not flagged
        r = c.get("/api/v1/evacuation/route"
                  "?origin_lat=36.2&origin_lon=3.0&dest_lat=35.0&dest_lon=-0.6")
        assert r.json()["destination_flagged_unsafe"] is False


# ── ORS avoid-polygon size-capping and no-avoidance fallback ──

def test_capped_avoid_polygon_within_ors_limits():
    """Each avoid polygon sent to ORS must stay within ~20 km extent, even
    for a fire whose display ellipse is far larger."""
    from backend.geo.evacuation import _capped_avoid_polygon, _ORS_MAX_AVOID_HALF_DEG
    from backend.db.database import FireClusterModel

    # A large fire: long downwind ellipse (semi_major 40 km), but the
    # avoid box must still be capped.
    c = FireClusterModel(
        id=1, centroid_lat=36.0, centroid_lon=5.0, point_count=50,
        max_frp=800, mean_frp=400, has_high_confidence=1,
        semi_major_km=40.0, semi_minor_km=12.0, orientation_deg=90.0,
        lw_ratio=6.0, wind_speed_kmh=40.0, wind_source="test", is_circular_fallback=0,
    )
    ring = _capped_avoid_polygon(c)[0]
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    # extent in degrees must not exceed 2 * cap
    assert (max(lons) - min(lons)) <= 2 * _ORS_MAX_AVOID_HALF_DEG + 1e-9
    assert (max(lats) - min(lats)) <= 2 * _ORS_MAX_AVOID_HALF_DEG + 1e-9


@pytest.mark.asyncio
async def test_get_route_falls_back_when_avoidance_fails(monkeypatch):
    """If routing WITH avoid polygons fails, get_route retries WITHOUT
    avoidance and returns a route flagged avoids_fires=False, rather than
    raising -- so the user gets a real escape path with a warning."""
    from backend.config import settings
    from backend.geo.evacuation import get_route
    from backend.db.database import FireClusterModel
    monkeypatch.setattr(settings, "ORS_API_KEY", "fake-key-for-test")

    calls = {"n": 0}
    async def fake_post(self, url, json=None, headers=None):
        calls["n"] += 1
        # First call (with avoid_polygons) fails; second (no avoidance) succeeds.
        if "options" in (json or {}):
            return _FakeResponse(500, text="avoid area too large")
        return _FakeResponse(200, ORS_SUCCESS_BODY)
    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    avoid = [FireClusterModel(
        id=1, centroid_lat=36.0, centroid_lon=5.0, point_count=10,
        max_frp=100, mean_frp=50, has_high_confidence=1,
        semi_major_km=30.0, semi_minor_km=10.0, orientation_deg=90.0,
        lw_ratio=5.0, wind_speed_kmh=30.0, wind_source="test", is_circular_fallback=0,
    )]
    route = await get_route(36.2, 5.0, 36.75, 5.08, avoid)
    assert route.avoids_fires is False       # flagged as the fallback
    assert route.avoided_zone_ids == []       # nothing actually avoided
    assert calls["n"] == 2                     # tried avoid, then fell back


@pytest.mark.asyncio
async def test_get_route_raises_only_if_even_fallback_fails(monkeypatch):
    from backend.config import settings
    from backend.geo.evacuation import get_route, EvacuationError
    monkeypatch.setattr(settings, "ORS_API_KEY", "fake-key-for-test")

    async def fake_post(self, url, json=None, headers=None):
        return _FakeResponse(200, {"features": []})   # everything fails
    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    with pytest.raises(EvacuationError):
        await get_route(36.2, 5.0, 36.75, 5.08, [])
