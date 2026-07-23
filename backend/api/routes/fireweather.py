"""
NiDa — Fire Weather Endpoint

GET /api/v1/fire-weather?lat=..&lon=..  -> current fire-weather score and
its component breakdown at a location, for the dashboard's "why is it
dangerous today" panel.

Returns HTTP 200 with {"available": false} when the upstream weather
source cannot be reached, so the client can show an honest "unavailable"
state rather than a fabricated value.
"""

import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.geo.fireweather import get_fire_weather

logger = logging.getLogger("nida.api.fireweather")

router = APIRouter()


class FireWeatherOut(BaseModel):
    available: bool
    score: float | None = None
    danger_class: str | None = None
    temperature_c: float | None = None
    relative_humidity_pct: float | None = None
    wind_speed_kmh: float | None = None
    precipitation_mm: float | None = None
    heat_factor: float | None = None
    dryness_factor: float | None = None
    wind_factor: float | None = None
    rain_suppression: float | None = None


@router.get("/fire-weather", response_model=FireWeatherOut)
async def fire_weather(
    lat: float = Query(..., ge=18.9, le=37.1),
    lon: float = Query(..., ge=-8.7, le=11.9),
):
    fw = await get_fire_weather(lat, lon)
    if fw is None:
        return FireWeatherOut(available=False)
    return FireWeatherOut(available=True, **fw.to_dict())
