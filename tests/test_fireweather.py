"""
NiDa — Fire Weather Score Tests

Validates the transparent fire-weather scoring heuristic: monotonic
responses to each driver, correct danger-class banding, rain suppression,
and graceful handling of upstream failure (never fabricates a value).
Network calls are mocked; no test touches the real Open-Meteo service.
"""

import pytest

from backend.geo.fireweather import (
    FireWeather, compute_score, get_fire_weather, _classify,
)


# ── monotonicity: each driver moves the score the correct direction ──

def test_higher_temperature_increases_score():
    cool = compute_score(temp_c=18, rh_pct=40, wind_kmh=15, precip_mm=0)
    hot = compute_score(temp_c=42, rh_pct=40, wind_kmh=15, precip_mm=0)
    assert hot.score > cool.score


def test_lower_humidity_increases_score():
    humid = compute_score(temp_c=35, rh_pct=80, wind_kmh=15, precip_mm=0)
    dry = compute_score(temp_c=35, rh_pct=15, wind_kmh=15, precip_mm=0)
    assert dry.score > humid.score


def test_higher_wind_increases_score():
    calm = compute_score(temp_c=35, rh_pct=30, wind_kmh=5, precip_mm=0)
    windy = compute_score(temp_c=35, rh_pct=30, wind_kmh=45, precip_mm=0)
    assert windy.score > calm.score


def test_rain_suppresses_score():
    dry = compute_score(temp_c=38, rh_pct=25, wind_kmh=30, precip_mm=0)
    wet = compute_score(temp_c=38, rh_pct=25, wind_kmh=30, precip_mm=5)
    assert wet.score < dry.score


# ── banding and bounds ──

def test_extreme_conditions_score_high():
    fw = compute_score(temp_c=45, rh_pct=10, wind_kmh=50, precip_mm=0)
    assert fw.score >= 85
    assert fw.danger_class == "extreme"


def test_benign_conditions_score_low():
    fw = compute_score(temp_c=16, rh_pct=90, wind_kmh=3, precip_mm=2)
    assert fw.score < 15
    assert fw.danger_class == "very_low"


def test_score_bounded_0_100():
    hi = compute_score(temp_c=60, rh_pct=0, wind_kmh=200, precip_mm=0)
    lo = compute_score(temp_c=-10, rh_pct=100, wind_kmh=0, precip_mm=50)
    assert 0 <= lo.score <= 100
    assert 0 <= hi.score <= 100
    assert hi.score <= 100


def test_classify_banding():
    assert _classify(90) == "extreme"
    assert _classify(75) == "very_high"
    assert _classify(55) == "high"
    assert _classify(35) == "moderate"
    assert _classify(20) == "low"
    assert _classify(5) == "very_low"


def test_components_exposed_for_explainer():
    fw = compute_score(temp_c=40, rh_pct=20, wind_kmh=30, precip_mm=0)
    # every component present and in 0-1
    for comp in (fw.heat_factor, fw.dryness_factor, fw.wind_factor, fw.rain_suppression):
        assert 0.0 <= comp <= 1.0


# ── network handling: never fabricate ──

@pytest.mark.asyncio
async def test_get_fire_weather_success(monkeypatch):
    class _Resp:
        status_code = 200
        def json(self):
            return {"current": {"temperature_2m": 40.0, "relative_humidity_2m": 20.0,
                                "wind_speed_10m": 30.0, "precipitation": 0.0}}
    async def fake_get(self, url, params=None):
        return _Resp()
    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    fw = await get_fire_weather(36.5, 4.0)
    assert isinstance(fw, FireWeather)
    assert fw.temperature_c == 40.0


@pytest.mark.asyncio
async def test_get_fire_weather_http_error_returns_none(monkeypatch):
    class _Resp:
        status_code = 500
        text = "server error"
        def json(self): return {}
    async def fake_get(self, url, params=None):
        return _Resp()
    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    assert await get_fire_weather(36.5, 4.0) is None


@pytest.mark.asyncio
async def test_get_fire_weather_missing_fields_returns_none(monkeypatch):
    """If the upstream omits a core driver, we must return None rather
    than inventing a value."""
    class _Resp:
        status_code = 200
        def json(self):
            return {"current": {"temperature_2m": 40.0}}  # missing rh, wind
    async def fake_get(self, url, params=None):
        return _Resp()
    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    assert await get_fire_weather(36.5, 4.0) is None


@pytest.mark.asyncio
async def test_get_fire_weather_network_exception_returns_none(monkeypatch):
    import httpx
    async def fake_get(self, url, params=None):
        raise httpx.ConnectTimeout("timeout")
    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    assert await get_fire_weather(36.5, 4.0) is None
