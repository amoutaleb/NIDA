"""
NiDa — Directional Alert Ellipse Model

Wildfires do not spread as circles. Under wind forcing, a fire front
elongates in the downwind direction while remaining comparatively narrow
crosswind -- a well-established result in operational fire behavior
modeling. NiDa adapts this principle to define directional alert zones
instead of naive fixed-radius circles, so that a community downwind of
an active fire receives an earlier/higher-severity alert than a
community equidistant but upwind or crosswind.

Scientific basis
-----------------
The length-to-width (L/W) ratio of an elliptical fire perimeter as a
function of wind speed follows Anderson (1983), as modified by Finney
for the FARSITE fire simulator (used operationally by the US Forest
Service) and implemented in FlamMap/ELMFIRE:

    L/W = min(0.936 * exp(0.2566 * U) + 0.461 * exp(-0.1548 * U) - 0.397, 8)

where U is the effective mid-flame wind speed in mph.

Design scope and limitation (documented for the paper)
--------------------------------------------------------
The full FARSITE/Rothermel model additionally requires fuel moisture,
canopy bulk density, and terrain slope to simulate actual fire spread
distance and rate. NiDa does not have real-time access to these inputs
for Algeria and does NOT claim to simulate fire spread. Instead, NiDa
adapts only the L/W *shape* relationship as a directional proxy for
alert-zone geometry: the base (no-wind) radius is derived from satellite
Fire Radiative Power (a measure of fire intensity) rather than fuel
model outputs, and the ellipse is oriented using live wind data. This
is an explicit simplification, stated as a limitation and a direction
for future work (incorporating a full Rothermel-based spread simulation
if ground-truth fuel data becomes available for Algerian WUI zones).

Geometric note: consistent with Anderson (1983)/Alexander (1985)/FARSITE,
the fire detection point is treated as the REAR FOCUS of the alert
ellipse rather than its center, so downwind reach is substantially
greater than upwind ("backing") reach -- see point_in_ellipse() below.
"""

import logging
import math
from dataclasses import dataclass
from typing import Optional

from backend.config import settings
from backend.geo.distance import bearing_deg, destination_point, haversine_km

logger = logging.getLogger("nida.geo.ellipse")

KMH_TO_MPH = 0.621371


@dataclass
class AlertEllipse:
    centroid_lat: float
    centroid_lon: float
    semi_major_km: float     # downwind axis (long axis)
    semi_minor_km: float     # crosswind axis (short axis)
    orientation_deg: float   # compass bearing of the downwind direction (major axis)
    lw_ratio: float
    wind_speed_kmh: Optional[float]
    wind_source: Optional[str]
    is_circular_fallback: bool  # True if no wind data was available


def compute_lw_ratio(wind_speed_kmh: float) -> float:
    """
    Anderson (1983) / Finney length-to-width ratio, capped at 8 as in
    FARSITE/ELMFIRE default configuration.

    IMPORTANT: Anderson's relationship is defined for MID-FLAME wind
    speed, not the 10m open wind reported by weather APIs. We convert
    using a standard open-terrain wind adjustment factor (WAF ~ 0.4;
    Baughman & Albini 1980, Andrews 2012). Feeding raw 10m wind into
    the formula overstates elongation dramatically -- discovered during
    live validation against the July 2026 Algeria fire season, when
    every moderately windy cluster saturated the L/W cap.
    """
    u_midflame_mph = wind_speed_kmh * KMH_TO_MPH * settings.MIDFLAME_WIND_ADJUSTMENT
    lw = 0.936 * math.exp(0.2566 * u_midflame_mph) + 0.461 * math.exp(-0.1548 * u_midflame_mph) - 0.397
    return max(1.0, min(lw, settings.ELLIPSE_MAX_LW_RATIO))


def base_radius_km(frp_mw: float) -> float:
    """
    No-wind base radius, scaled by Fire Radiative Power (MW) as a proxy
    for fire intensity/energy release, and capped so a single extreme
    FRP reading cannot produce an operationally meaningless mega-zone.
    """
    r = settings.ELLIPSE_BASE_RADIUS_KM + settings.ELLIPSE_FRP_SCALE_KM * max(frp_mw, 0.0)
    return min(r, settings.ELLIPSE_MAX_BASE_RADIUS_KM)


def build_ellipse(
    centroid_lat: float,
    centroid_lon: float,
    frp_mw: float,
    wind_speed_kmh: Optional[float],
    wind_direction_deg: Optional[float],
    wind_source: Optional[str] = None,
) -> AlertEllipse:
    """
    Construct the directional alert ellipse for a fire cluster.

    If wind data is unavailable (wind_speed_kmh is None), gracefully
    degrades to a circular zone (semi_major == semi_minor) rather than
    failing -- this fallback is flagged via is_circular_fallback so the
    API response and paper evaluation can report how often it occurred.
    """
    base = base_radius_km(frp_mw)

    if wind_speed_kmh is None or wind_direction_deg is None:
        logger.info(f"No wind data for cluster at ({centroid_lat},{centroid_lon}); "
                    f"falling back to circular zone.")
        return AlertEllipse(
            centroid_lat=centroid_lat,
            centroid_lon=centroid_lon,
            semi_major_km=base,
            semi_minor_km=base,
            orientation_deg=0.0,
            lw_ratio=1.0,
            wind_speed_kmh=None,
            wind_source=None,
            is_circular_fallback=True,
        )

    lw = compute_lw_ratio(wind_speed_kmh)

    # wind_direction_deg is meteorological "from" direction; the downwind
    # (fire spread) direction is the reciprocal bearing (+180 deg)
    downwind_bearing = (wind_direction_deg + 180) % 360

    semi_minor = base
    semi_major = base * lw

    return AlertEllipse(
        centroid_lat=centroid_lat,
        centroid_lon=centroid_lon,
        semi_major_km=semi_major,
        semi_minor_km=semi_minor,
        orientation_deg=downwind_bearing,
        lw_ratio=lw,
        wind_speed_kmh=wind_speed_kmh,
        wind_source=wind_source,
        is_circular_fallback=False,
    )


def point_in_ellipse(ellipse: AlertEllipse, user_lat: float, user_lon: float) -> bool:
    """
    Determine whether a user's location falls within the directional
    alert ellipse.

    Physical basis: following Anderson (1983)/Alexander (1985)/FARSITE
    convention, the fire detection point is treated as the REAR FOCUS of
    the ellipse, not its geometric center. This means forward (downwind)
    reach extends much farther from the fire than backward (upwind
    "backing") reach -- correctly modeling that fires spread aggressively
    downwind but crawl slowly against the wind. A naive ellipse centered
    on the fire location would incorrectly give equal upwind/downwind
    reach, which contradicts real fire behavior documented in the
    literature review.

    Method:
        1. Compute the focal distance c = sqrt(a^2 - b^2)
        2. Shift the ellipse center forward (downwind) from the fire
           origin by c, so the fire origin sits at the rear focus
        3. Test standard ellipse membership (x'/a)^2 + (y'/b)^2 <= 1
           relative to that shifted center
    """
    a = ellipse.semi_major_km
    b = ellipse.semi_minor_km
    if a <= 0 or b <= 0:
        return False

    c = math.sqrt(max(a * a - b * b, 0.0))

    # Shift the ellipse center forward (downwind) from the true fire
    # origin by the focal distance c
    if c > 0:
        center_lat, center_lon = destination_point(
            ellipse.centroid_lat, ellipse.centroid_lon, ellipse.orientation_deg, c
        )
    else:
        center_lat, center_lon = ellipse.centroid_lat, ellipse.centroid_lon

    dist_km = haversine_km(center_lat, center_lon, user_lat, user_lon)
    if dist_km == 0:
        return True

    brg = bearing_deg(center_lat, center_lon, user_lat, user_lon)
    theta = math.radians(brg - ellipse.orientation_deg)

    x_prime = dist_km * math.cos(theta)
    y_prime = dist_km * math.sin(theta)

    value = (x_prime / a) ** 2 + (y_prime / b) ** 2
    return value <= 1.0


def ellipse_boundary_points(ellipse: AlertEllipse, n_points: int = 48) -> list:
    """
    Generate lat/lon boundary points of the alert ellipse for map
    rendering (GeoJSON). Accounts for the rear-focus geometry: the
    ellipse center is shifted downwind from the fire origin by the
    focal distance, matching point_in_ellipse().

    Returns a list of [lon, lat] pairs (GeoJSON coordinate order),
    closed (first point repeated at the end).
    """
    a = ellipse.semi_major_km
    b = ellipse.semi_minor_km
    c = math.sqrt(max(a * a - b * b, 0.0))

    if c > 0:
        center_lat, center_lon = destination_point(
            ellipse.centroid_lat, ellipse.centroid_lon, ellipse.orientation_deg, c
        )
    else:
        center_lat, center_lon = ellipse.centroid_lat, ellipse.centroid_lon

    coords = []
    for i in range(n_points + 1):
        t = 2 * math.pi * i / n_points
        # point in ellipse frame (x along major axis)
        x = a * math.cos(t)
        y = b * math.sin(t)
        dist = math.hypot(x, y)
        angle_in_frame = math.degrees(math.atan2(y, x))
        brg = (ellipse.orientation_deg + angle_in_frame) % 360
        lat, lon = destination_point(center_lat, center_lon, brg, dist)
        coords.append([lon, lat])
    return coords


def severity_at_point(ellipse: AlertEllipse, user_lat: float, user_lon: float) -> Optional[str]:
    """
    Classify a user's alert level based on how deep inside the ellipse
    they fall, using concentric scaled copies of the same ellipse shape
    (25%, 60%, 100% of full size) rather than fixed-radius rings -- so
    the tiering itself remains directionally aware.

    Returns 'critical', 'warning', 'advisory', or None (outside all zones).
    """
    for level, scale in (("critical", 0.25), ("warning", 0.6), ("advisory", 1.0)):
        scaled = AlertEllipse(
            centroid_lat=ellipse.centroid_lat,
            centroid_lon=ellipse.centroid_lon,
            semi_major_km=ellipse.semi_major_km * scale,
            semi_minor_km=ellipse.semi_minor_km * scale,
            orientation_deg=ellipse.orientation_deg,
            lw_ratio=ellipse.lw_ratio,
            wind_speed_kmh=ellipse.wind_speed_kmh,
            wind_source=ellipse.wind_source,
            is_circular_fallback=ellipse.is_circular_fallback,
        )
        if point_in_ellipse(scaled, user_lat, user_lon):
            return level
    return None
