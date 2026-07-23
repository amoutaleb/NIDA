"""
NiDa — Fire Weather Conditions

Retrieves the meteorological drivers of wildfire danger at a location and
combines them into a transparent, explainable fire-weather score. This
powers the "why is it dangerous today" panel and the regional risk
context layer in the dashboard.

Data source
-----------
Open-Meteo forecast API (CC-BY 4.0, no API key). All inputs are directly
measured/forecast physical variables:
    - temperature at 2 m               (deg C)
    - relative humidity at 2 m         (%)
    - wind speed at 10 m               (km/h)
    - precipitation                    (mm)

Honest scope
------------
This is NOT the official Canadian Forest Fire Weather Index (FWI). The
canonical FWI requires sequential day-to-day bookkeeping of fuel-moisture
codes (FFMC, DMC, DC) that cannot be reconstructed from a single spot
reading. Rather than mislabel our output, NiDa computes an explicit,
inspectable "fire-weather score" from the same physical drivers the FWI
uses (heat, dryness, wind, recent rain), with every component visible to
the user. The score is a decision-support heuristic for relative danger,
not a calibrated physical index, and is labelled as such in the UI.

Reliability
-----------
Network failures never raise into the caller: get_fire_weather() returns
None on any error (missing data, timeout, HTTP error, malformed
response), so the dashboard can show "unavailable" rather than a wrong or
fabricated value. No component is ever invented to fill a gap.
"""

import logging
from dataclasses import dataclass, asdict
from typing import Optional

import httpx

from backend.config import settings

logger = logging.getLogger("nida.geo.fireweather")

# Danger-class thresholds on the 0-100 score. Chosen to align, in spirit,
# with the six-class fire-danger scale used operationally (EFFIS/Canadian
# system): very low / low / moderate / high / very high / extreme.
_CLASS_BREAKS = [
    (85, "extreme"),
    (70, "very_high"),
    (50, "high"),
    (30, "moderate"),
    (15, "low"),
    (0,  "very_low"),
]


@dataclass
class FireWeather:
    score: float                # 0-100 composite fire-weather score
    danger_class: str           # very_low ... extreme
    temperature_c: float
    relative_humidity_pct: float
    wind_speed_kmh: float
    precipitation_mm: float
    # component contributions (0-1 each, before weighting) for the
    # "why is it dangerous" explainer
    heat_factor: float
    dryness_factor: float
    wind_factor: float
    rain_suppression: float

    def to_dict(self) -> dict:
        return asdict(self)


def _classify(score: float) -> str:
    for threshold, label in _CLASS_BREAKS:
        if score >= threshold:
            return label
    return "very_low"


def compute_score(temp_c: float, rh_pct: float, wind_kmh: float,
                  precip_mm: float) -> FireWeather:
    """
    Combine the physical drivers into a 0-100 fire-weather score.

    Each driver is normalised to 0-1 in the direction of INCREASING danger:
      - heat:    rises with temperature (negligible below ~15 C, saturating ~45 C)
      - dryness: rises as relative humidity falls (RH 100% -> 0, RH 20% -> high)
      - wind:    rises with wind speed (saturating ~50 km/h)
    Recent precipitation suppresses the score (wet fuel resists ignition).

    Weights emphasise dryness and wind, the two factors most associated
    with rapid fire spread, consistent with operational fire-weather
    understanding. The formula is intentionally simple and fully exposed
    so the UI can explain exactly why a given score is high or low.
    """
    heat = _clamp((temp_c - 15.0) / (45.0 - 15.0))
    dryness = _clamp((100.0 - rh_pct) / (100.0 - 20.0))
    wind = _clamp(wind_kmh / 50.0)

    # Rain suppression: even a few mm of recent rain sharply lowers danger.
    rain_suppression = _clamp(precip_mm / 5.0)

    base = 0.30 * heat + 0.40 * dryness + 0.30 * wind
    score = base * (1.0 - 0.7 * rain_suppression)   # rain removes up to 70%
    score100 = round(100.0 * _clamp(score), 1)

    return FireWeather(
        score=score100,
        danger_class=_classify(score100),
        temperature_c=round(temp_c, 1),
        relative_humidity_pct=round(rh_pct, 0),
        wind_speed_kmh=round(wind_kmh, 1),
        precipitation_mm=round(precip_mm, 1),
        heat_factor=round(heat, 3),
        dryness_factor=round(dryness, 3),
        wind_factor=round(wind, 3),
        rain_suppression=round(rain_suppression, 3),
    )


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


async def get_fire_weather(lat: float, lon: float) -> Optional[FireWeather]:
    """
    Fetch current weather drivers at a location and return the derived
    fire-weather score, or None on any failure (never raises, never
    fabricates). Uses Open-Meteo, matching the wind client's provider
    and reliability conventions.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation",
        "wind_speed_unit": "kmh",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(settings.OPENMETEO_BASE_URL, params=params)
        if resp.status_code != 200:
            logger.warning(f"Open-Meteo (fire weather) HTTP {resp.status_code}: {resp.text[:150]}")
            return None
        current = resp.json().get("current", {})
        temp = current.get("temperature_2m")
        rh = current.get("relative_humidity_2m")
        wind = current.get("wind_speed_10m")
        precip = current.get("precipitation", 0.0)
        # Require the three core drivers; treat missing precip as 0 (no rain).
        if temp is None or rh is None or wind is None:
            logger.warning("Open-Meteo fire-weather response missing core fields; returning None.")
            return None
        return compute_score(float(temp), float(rh), float(wind), float(precip or 0.0))
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning(f"Fire-weather retrieval failed for ({lat},{lon}): {exc}")
        return None
