"""
NiDa — Wind Data Client (Dual-Source)

Fetches wind speed and direction at a fire cluster's location, needed to
orient the directional alert ellipse (see ellipse.py). Uses two
independent providers for resilience:

    Primary:  Open-Meteo   (free, no API key, generous rate limits)
    Fallback: OpenWeatherMap (free tier, requires API key)

If both fail, callers should fall back to a circular (wind-agnostic)
alert zone rather than blocking alert dispatch -- documented in the
paper as a graceful-degradation design decision, not a silent failure.
"""

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from backend.config import settings

logger = logging.getLogger("nida.geo.wind")


@dataclass
class WindReading:
    speed_kmh: float
    direction_deg: float   # meteorological convention: direction the wind blows FROM, 0=N
    source: str            # 'open-meteo' or 'openweathermap'


class WindDataError(Exception):
    """Raised only if ALL wind sources fail."""


async def get_wind(lat: float, lon: float) -> Optional[WindReading]:
    """
    Fetch current wind speed/direction at a location, trying Open-Meteo
    first and falling back to OpenWeatherMap. Returns None (not an
    exception) if both fail, so callers can gracefully degrade to a
    circular zone instead of blocking the whole alert pipeline.
    """
    reading = await _try_open_meteo(lat, lon)
    if reading:
        return reading

    logger.warning("Open-Meteo failed, falling back to OpenWeatherMap")
    reading = await _try_openweathermap(lat, lon)
    if reading:
        return reading

    logger.error(f"Both wind sources failed for ({lat}, {lon}); "
                 f"caller should fall back to circular zone.")
    return None


async def _try_open_meteo(lat: float, lon: float) -> Optional[WindReading]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "kmh",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(settings.OPENMETEO_BASE_URL, params=params)
        if resp.status_code != 200:
            logger.warning(f"Open-Meteo HTTP {resp.status_code}: {resp.text[:150]}")
            return None
        data = resp.json()
        current = data.get("current", {})
        speed = current.get("wind_speed_10m")
        direction = current.get("wind_direction_10m")
        if speed is None or direction is None:
            return None
        return WindReading(speed_kmh=round(float(speed), 1), direction_deg=round(float(direction), 0), source="open-meteo")
    except Exception as exc:
        logger.warning(f"Open-Meteo request failed: {exc}")
        return None


async def _try_openweathermap(lat: float, lon: float) -> Optional[WindReading]:
    if not settings.OPENWEATHERMAP_API_KEY:
        logger.warning("No OpenWeatherMap API key configured; skipping fallback.")
        return None

    params = {
        "lat": lat,
        "lon": lon,
        "appid": settings.OPENWEATHERMAP_API_KEY,
        "units": "metric",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(settings.OPENWEATHERMAP_BASE_URL, params=params)
        if resp.status_code != 200:
            logger.warning(f"OpenWeatherMap HTTP {resp.status_code}: {resp.text[:150]}")
            return None
        data = resp.json()
        wind = data.get("wind", {})
        speed_ms = wind.get("speed")
        direction = wind.get("deg")
        if speed_ms is None or direction is None:
            return None
        speed_kmh = float(speed_ms) * 3.6  # m/s -> km/h
        return WindReading(speed_kmh=round(speed_kmh, 1), direction_deg=round(float(direction), 0), source="openweathermap")
    except Exception as exc:
        logger.warning(f"OpenWeatherMap request failed: {exc}")
        return None
