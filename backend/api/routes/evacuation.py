"""
NiDa — Evacuation Routing Endpoint

GET /api/v1/evacuation/route -> road route from an origin to safety,
    avoiding active critical/warning fire zones. Destination is
    auto-suggested (nearest safe town not inside a danger zone) unless
    the caller supplies dest_lat/dest_lon explicitly (map-click override).
GET /api/v1/evacuation/safe-towns -> the candidate town list, for the
    dashboard to optionally display as pins.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db.database import FireClusterModel, get_db
from backend.geo.evacuation import (
    SAFE_TOWNS, EvacuationError, get_multi_routes, get_route,
    nearest_safe_town, select_avoid_zones, zones_containing_point,
)

logger = logging.getLogger("nida.api.evacuation")

router = APIRouter()


class RouteOut(BaseModel):
    status: str  # "ok" | "no_route" | "error"
    origin: Optional[dict] = None
    destination: Optional[dict] = None
    distance_km: Optional[float] = None
    duration_min: Optional[float] = None
    geometry: Optional[list] = None
    avoided_zone_ids: list = []
    destination_auto_selected: bool = False
    destination_flagged_unsafe: bool = False
    detail: Optional[str] = None


@router.get("/evacuation/safe-towns")
def safe_towns():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [t["lon"], t["lat"]]},
                "properties": {"name": t["name"]},
            }
            for t in SAFE_TOWNS
        ],
    }


class MultiRouteOut(BaseModel):
    status: str  # "ok" | "no_route"
    origin: dict
    origin_inside_zone: bool
    destinations_tried: int
    routes: list = []
    detail: Optional[str] = None


@router.get("/evacuation/routes", response_model=MultiRouteOut)
async def evacuation_routes(
    origin_lat: float = Query(..., ge=18.9, le=37.1),
    origin_lon: float = Query(..., ge=-8.7, le=11.9),
    max_destinations: int = Query(4, ge=1, le=6),
    db: Session = Depends(get_db),
):
    """
    Multiple escape options at once: routes from the origin to up to
    max_destinations directionally diverse safe towns, computed
    concurrently. If the origin lies inside an active danger zone, that
    zone is excluded from avoidance (see zones_containing_point) so an
    escape path can actually be found. Only if EVERY candidate fails is
    status "no_route".
    """
    clusters = db.query(FireClusterModel).all()
    result = await get_multi_routes(clusters, origin_lat, origin_lon, max_destinations)

    routes = [
        {
            "destination": {**s["town"]},
            "distance_km": s["route"].distance_km,
            "duration_min": s["route"].duration_min,
            "geometry": s["route"].geometry,
            "avoided_zone_ids": s["route"].avoided_zone_ids,
        }
        for s in result["successes"]
    ]

    if not routes:
        detail = "; ".join(f'{f["town"]["name"]}: {f["error"]}' for f in result["failures"])[:400]
        return MultiRouteOut(
            status="no_route",
            origin={"lat": origin_lat, "lon": origin_lon},
            origin_inside_zone=bool(result["origin_inside_zone_ids"]),
            destinations_tried=result["destinations_tried"],
            detail=detail or "No candidate destinations available.",
        )

    return MultiRouteOut(
        status="ok",
        origin={"lat": origin_lat, "lon": origin_lon},
        origin_inside_zone=bool(result["origin_inside_zone_ids"]),
        destinations_tried=result["destinations_tried"],
        routes=routes,
    )


@router.get("/evacuation/route", response_model=RouteOut)
async def evacuation_route(
    origin_lat: float = Query(..., ge=18.9, le=37.1),
    origin_lon: float = Query(..., ge=-8.7, le=11.9),
    dest_lat: Optional[float] = Query(None, ge=18.9, le=37.1),
    dest_lon: Optional[float] = Query(None, ge=-8.7, le=11.9),
    db: Session = Depends(get_db),
):
    """
    Compute a road route from (origin_lat, origin_lon) to safety.

    If dest_lat/dest_lon are omitted, the nearest candidate safe town
    NOT inside an active critical/warning zone is auto-selected. If
    provided, that exact point is used as the destination instead
    (a map-click override), without safety filtering on the
    destination itself -- the caller chose it deliberately.
    """
    if (dest_lat is None) != (dest_lon is None):
        raise HTTPException(status_code=422, detail="Provide both dest_lat and dest_lon, or neither.")

    clusters = db.query(FireClusterModel).all()

    destination_auto = dest_lat is None
    flagged_unsafe = False
    dest_name = None

    if destination_auto:
        town = nearest_safe_town(clusters, origin_lat, origin_lon)
        dest_lat, dest_lon = town["lat"], town["lon"]
        dest_name = town["name"]
        flagged_unsafe = town["flagged_unsafe"]
    else:
        # Manual destination override: the user chose this point, but we
        # still check whether it sits inside an active danger zone and
        # flag it if so (earlier this check was skipped entirely, so the
        # UI would call a destination inside a fire "safe").
        dest_zones = zones_containing_point(clusters, dest_lat, dest_lon)
        flagged_unsafe = bool(dest_zones)

    avoid_zones = select_avoid_zones(clusters, origin_lat, origin_lon, dest_lat, dest_lon)

    # Same trapped-origin fix as the multi-route endpoint: if the origin
    # is inside a danger zone, don't ask the router to avoid that zone
    # (it would make any route from there impossible by definition).
    origin_zone_ids = set(zones_containing_point(clusters, origin_lat, origin_lon))
    if origin_zone_ids:
        avoid_zones = [c for c in avoid_zones if c.id not in origin_zone_ids]

    try:
        result = await get_route(origin_lat, origin_lon, dest_lat, dest_lon, avoid_zones)
    except EvacuationError as exc:
        logger.warning(f"Evacuation routing failed: {exc}")
        return RouteOut(
            status="no_route",
            destination_auto_selected=destination_auto,
            destination_flagged_unsafe=flagged_unsafe,
            detail=str(exc),
        )

    return RouteOut(
        status="ok",
        origin=result.origin,
        destination={**result.destination, "name": dest_name},
        distance_km=result.distance_km,
        duration_min=result.duration_min,
        geometry=result.geometry,
        avoided_zone_ids=result.avoided_zone_ids,
        destination_auto_selected=destination_auto,
        destination_flagged_unsafe=flagged_unsafe,
    )
