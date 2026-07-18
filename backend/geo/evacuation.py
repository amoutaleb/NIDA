"""
NiDa — Evacuation Routing

Given an origin point, computes a road route to safety that AVOIDS all
active critical/warning fire alert zones, using OpenRouteService's
avoid-area directions API. The alert ellipses NiDa already computes
(geo/ellipse.py) become the no-go polygons for routing -- so wildfire
detection, wind-driven risk zones, and evacuation guidance form a single
connected pipeline rather than three disconnected features.

Destination selection is dual-mode (per project decision): if the
caller doesn't supply a destination, NiDa auto-suggests the nearest
candidate safe town that is NOT itself inside an active critical/warning
zone; the caller (map click) can always override with their own point.

Scope and honesty notes for the paper:
  - SAFE_TOWNS is a small, hand-curated list of major wilaya capitals,
    NOT an official Algerian civil-protection shelter registry (no such
    open dataset exists at the time of writing). This is documented as
    a limitation: a production deployment should ingest an authoritative
    shelter/muster-point list from Algerian civil protection.
  - Only fire zones near the route corridor are sent to ORS as avoid
    polygons (not every active zone nationwide), both for API payload
    size limits and because a fire 300km from the route is irrelevant
    to it.
  - If every road out is blocked by fire zones, ORS returns no route;
    this is surfaced explicitly ("no_route") rather than silently
    falling back to a route that ignores the danger zones.
"""

import logging
import math
from dataclasses import dataclass
from typing import List, Optional

import httpx

from backend.config import settings
from backend.db.database import FireClusterModel
from backend.geo.distance import bearing_deg, haversine_km
from backend.geo.ellipse import AlertEllipse, ellipse_boundary_points, severity_at_point

logger = logging.getLogger("nida.geo.evacuation")

# Hand-curated candidate safe destinations: major wilaya capitals spread
# across Algeria's geography, so a reasonable candidate exists near any
# fire location. See module docstring re: this is NOT an official
# shelter registry.
SAFE_TOWNS = [
    {"name": "Algiers",     "lat": 36.7538, "lon": 3.0588},
    {"name": "Oran",        "lat": 35.6971, "lon": -0.6308},
    {"name": "Constantine", "lat": 36.3650, "lon": 6.6147},
    {"name": "Annaba",      "lat": 36.9000, "lon": 7.7667},
    {"name": "Setif",       "lat": 36.1911, "lon": 5.4137},
    {"name": "Batna",       "lat": 35.5559, "lon": 6.1741},
    {"name": "Tlemcen",     "lat": 34.8828, "lon": -1.3167},
    {"name": "Bechar",      "lat": 31.6167, "lon": -2.2167},
    {"name": "Ouargla",     "lat": 31.9500, "lon": 5.3333},
    {"name": "Tamanrasset", "lat": 22.7850, "lon": 5.5228},
    {"name": "Ghardaia",    "lat": 32.4900, "lon": 3.6700},
    {"name": "Blida",       "lat": 36.4700, "lon": 2.8300},
    {"name": "Tizi Ouzou",  "lat": 36.7169, "lon": 4.0497},
    {"name": "Bejaia",      "lat": 36.7500, "lon": 5.0667},
    {"name": "Skikda",      "lat": 36.8790, "lon": 6.9095},
    {"name": "Djelfa",      "lat": 34.6667, "lon": 3.2500},
    {"name": "El Oued",     "lat": 33.3550, "lon": 6.8650},
    {"name": "Adrar",       "lat": 27.8742, "lon": -0.2939},
]


class EvacuationError(Exception):
    """Raised when no safe route can be determined (all candidates
    blocked, or the routing API itself fails)."""


@dataclass
class RouteResult:
    origin: dict
    destination: dict
    distance_km: float
    duration_min: float
    geometry: List[List[float]]   # [[lon,lat], ...] road path
    avoided_zone_ids: List[int]


def _cluster_to_ellipse(c: FireClusterModel) -> AlertEllipse:
    return AlertEllipse(
        centroid_lat=c.centroid_lat, centroid_lon=c.centroid_lon,
        semi_major_km=c.semi_major_km, semi_minor_km=c.semi_minor_km,
        orientation_deg=c.orientation_deg, lw_ratio=c.lw_ratio,
        wind_speed_kmh=c.wind_speed_kmh, wind_source=c.wind_source,
        is_circular_fallback=bool(c.is_circular_fallback),
    )


def cross_track_distance_km(
    point_lat: float, point_lon: float,
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
) -> float:
    """
    Great-circle distance from a point to the nearest point on the
    (start -> end) path, using the standard cross-track distance
    formula. Used to decide which fire zones are "near enough to the
    route" to matter for avoidance -- reuses the Haversine/bearing
    primitives already validated in test_distance.py.
    """
    R = 6371.0088
    d13 = haversine_km(start_lat, start_lon, point_lat, point_lon) / R
    theta13 = math.radians(bearing_deg(start_lat, start_lon, point_lat, point_lon))
    theta12 = math.radians(bearing_deg(start_lat, start_lon, end_lat, end_lon))

    cross_track = math.asin(math.sin(d13) * math.sin(theta13 - theta12)) * R

    # Clamp to the segment: if the point's closest approach falls beyond
    # either endpoint, fall back to distance-to-endpoint instead of the
    # infinite-line cross-track value.
    d12 = haversine_km(start_lat, start_lon, end_lat, end_lon)
    along = math.acos(min(1.0, max(-1.0,
        math.cos(d13) / math.cos(cross_track / R)))) * R
    if along > d12:
        return haversine_km(end_lat, end_lon, point_lat, point_lon)
    return abs(cross_track)


def nearest_safe_town(clusters: List[FireClusterModel], lat: float, lon: float) -> dict:
    """
    Candidate safe destinations sorted by straight-line distance from
    the origin; returns the nearest one that is NOT currently inside any
    active critical/warning alert zone. Falls back to the single nearest
    town (flagged) in the extreme case that all candidates are affected.
    """
    danger_ellipses = [
        _cluster_to_ellipse(c) for c in clusters
        if _severity_of(c) in ("critical", "warning")
    ]

    candidates = sorted(
        SAFE_TOWNS, key=lambda t: haversine_km(lat, lon, t["lat"], t["lon"])
    )

    for town in candidates:
        inside_any = any(
            severity_at_point(e, town["lat"], town["lon"]) is not None
            for e in danger_ellipses
        )
        if not inside_any:
            return {**town, "auto_selected": True, "flagged_unsafe": False}

    logger.warning("All candidate safe towns fall inside an active alert zone; "
                    "returning nearest anyway, flagged unsafe.")
    nearest = candidates[0]
    return {**nearest, "auto_selected": True, "flagged_unsafe": True}


def _severity_of(c: FireClusterModel) -> Optional[str]:
    """Lightweight severity classification for filtering, without
    importing the full severity module (avoids a circular import and
    keeps this module's concern to geometry/routing)."""
    if c.max_frp is None:
        return None
    from backend.geo.severity import compute_severity
    return compute_severity(
        max_frp_mw=c.max_frp,
        has_high_confidence=bool(c.has_high_confidence),
        wind_speed_kmh=c.wind_speed_kmh,
    ).level


def select_avoid_zones(
    clusters: List[FireClusterModel],
    origin_lat: float, origin_lon: float,
    dest_lat: float, dest_lon: float,
) -> List[FireClusterModel]:
    """
    Filter active clusters down to the ones worth sending to the routing
    API as avoid-polygons: critical/warning severity AND within
    EVACUATION_CORRIDOR_BUFFER_KM of the straight-line origin-destination
    path, capped at EVACUATION_MAX_AVOID_ZONES (closest first).
    """
    relevant = []
    for c in clusters:
        if _severity_of(c) not in ("critical", "warning"):
            continue
        dist = cross_track_distance_km(
            c.centroid_lat, c.centroid_lon,
            origin_lat, origin_lon, dest_lat, dest_lon,
        )
        if dist <= settings.EVACUATION_CORRIDOR_BUFFER_KM:
            relevant.append((dist, c))

    relevant.sort(key=lambda x: x[0])
    return [c for _, c in relevant[: settings.EVACUATION_MAX_AVOID_ZONES]]


def zones_containing_point(
    clusters: List[FireClusterModel], lat: float, lon: float
) -> List[int]:
    """
    IDs of active critical/warning clusters whose alert ellipse contains
    the given point. Used to solve the "trapped origin" problem observed
    in live validation: if the evacuee is standing INSIDE a danger zone,
    the routing engine cannot route from a point it was told to avoid --
    every request fails with "no route". The fix: their own containing
    zone(s) are excluded from the avoid-list (escaping through your own
    zone's edge is physically unavoidable), while all OTHER zones remain
    avoided.
    """
    hits = []
    for c in clusters:
        if _severity_of(c) not in ("critical", "warning"):
            continue
        if severity_at_point(_cluster_to_ellipse(c), lat, lon) is not None:
            hits.append(c.id)
    return hits


def diverse_safe_towns(
    clusters: List[FireClusterModel],
    lat: float, lon: float,
    max_destinations: int = 4,
    min_bearing_separation_deg: float = 60.0,
) -> List[dict]:
    """
    Select up to max_destinations candidate safe towns that are (a) not
    inside an active danger zone and (b) spread across DIFFERENT compass
    directions from the origin (>= min_bearing_separation_deg apart).
    Directional diversity is the point: if the route north is blocked by
    fire, a candidate to the east may still work -- offering N nearest
    towns that all happen to lie in the same blocked direction would
    defeat the purpose of computing multiple routes.
    """
    danger_ellipses = [
        _cluster_to_ellipse(c) for c in clusters
        if _severity_of(c) in ("critical", "warning")
    ]

    candidates = sorted(
        SAFE_TOWNS, key=lambda t: haversine_km(lat, lon, t["lat"], t["lon"])
    )

    selected: List[dict] = []
    for town in candidates:
        if len(selected) >= max_destinations:
            break
        if any(severity_at_point(e, town["lat"], town["lon"]) is not None
               for e in danger_ellipses):
            continue
        brg = bearing_deg(lat, lon, town["lat"], town["lon"])
        too_close_in_bearing = any(
            min(abs(brg - s["_bearing"]), 360 - abs(brg - s["_bearing"]))
            < min_bearing_separation_deg
            for s in selected
        )
        if too_close_in_bearing:
            continue
        selected.append({**town, "_bearing": brg})

    # Fallback: if directional diversity found nothing usable (e.g. only
    # one clear town exists), relax to plain nearest-clear selection.
    if not selected:
        for town in candidates:
            if len(selected) >= max_destinations:
                break
            if not any(severity_at_point(e, town["lat"], town["lon"]) is not None
                       for e in danger_ellipses):
                selected.append({**town, "_bearing": bearing_deg(lat, lon, town["lat"], town["lon"])})

    return [{k: v for k, v in s.items() if k != "_bearing"} for s in selected]


async def get_multi_routes(
    clusters: List[FireClusterModel],
    origin_lat: float, origin_lon: float,
    max_destinations: int = 4,
) -> dict:
    """
    Compute escape routes from the origin to several directionally
    diverse safe towns CONCURRENTLY, excluding the origin's own
    containing zone(s) from avoidance. Returns successes (ranked by
    road distance) and failures separately, so the caller can present
    "here are your 2 viable options" instead of a binary route/no-route.
    """
    import asyncio

    origin_zone_ids = set(zones_containing_point(clusters, origin_lat, origin_lon))
    destinations = diverse_safe_towns(clusters, origin_lat, origin_lon, max_destinations)

    async def _one(dest):
        avoid = [
            c for c in select_avoid_zones(
                clusters, origin_lat, origin_lon, dest["lat"], dest["lon"]
            )
            if c.id not in origin_zone_ids
        ]
        try:
            route = await get_route(origin_lat, origin_lon, dest["lat"], dest["lon"], avoid)
            return {"town": dest, "route": route, "error": None}
        except EvacuationError as exc:
            return {"town": dest, "route": None, "error": str(exc)}

    results = await asyncio.gather(*[_one(d) for d in destinations])

    successes = sorted(
        [r for r in results if r["route"] is not None],
        key=lambda r: r["route"].distance_km,
    )
    failures = [r for r in results if r["route"] is None]

    return {
        "origin_inside_zone_ids": sorted(origin_zone_ids),
        "destinations_tried": len(destinations),
        "successes": successes,
        "failures": failures,
    }


async def get_route(
    origin_lat: float, origin_lon: float,
    dest_lat: float, dest_lon: float,
    avoid_clusters: List[FireClusterModel],
) -> RouteResult:
    """
    Request a driving route from origin to destination via
    OpenRouteService, avoiding the given fire clusters' alert ellipses.
    Raises EvacuationError if ORS is unreachable, rejects the request,
    or genuinely cannot find any path around the danger zones.
    """
    if not settings.ORS_API_KEY:
        raise EvacuationError("Evacuation routing is not configured (missing ORS_API_KEY).")

    body = {
        "coordinates": [[origin_lon, origin_lat], [dest_lon, dest_lat]],
        "instructions": False,
    }
    if avoid_clusters:
        polygons = [
            [ellipse_boundary_points(_cluster_to_ellipse(c))]
            for c in avoid_clusters
        ]
        body["options"] = {
            "avoid_polygons": {"type": "MultiPolygon", "coordinates": polygons}
        }

    headers = {
        "Authorization": settings.ORS_API_KEY,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(settings.ORS_BASE_URL, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise EvacuationError(f"Routing service unreachable: {exc}") from exc

    if resp.status_code != 200:
        detail = resp.text[:300]
        if resp.status_code == 404 or "route" in detail.lower():
            raise EvacuationError(
                f"No safe route found avoiding {len(avoid_clusters)} active fire zone(s)."
            )
        raise EvacuationError(f"Routing service error (HTTP {resp.status_code}): {detail}")

    data = resp.json()
    features = data.get("features") or []
    if not features:
        raise EvacuationError("Routing service returned no route.")

    feature = features[0]
    props = feature["properties"]["summary"]
    return RouteResult(
        origin={"lat": origin_lat, "lon": origin_lon},
        destination={"lat": dest_lat, "lon": dest_lon},
        distance_km=round(props["distance"] / 1000.0, 2),
        duration_min=round(props["duration"] / 60.0, 1),
        geometry=feature["geometry"]["coordinates"],
        avoided_zone_ids=[c.id for c in avoid_clusters],
    )
