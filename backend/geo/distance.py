"""
NiDa — Haversine Distance Calculator

Computes great-circle distance between two lat/lon points on Earth.
Used for both simple radius alerts and as the base metric feeding the
directional ellipse model (see ellipse.py).

Reference: standard spherical law of cosines / haversine formula,
as justified in the NiDa literature review (Section 2.4).
"""

import math

EARTH_RADIUS_KM = 6371.0088  # IUGG mean Earth radius


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Great-circle distance between two points in kilometers.

    Args:
        lat1, lon1: first point (decimal degrees)
        lat2, lon2: second point (decimal degrees)

    Returns:
        Distance in kilometers.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Initial compass bearing (0-360 deg, 0=North, 90=East) from point 1 to point 2.
    Used to determine a user's angular position relative to a fire centroid,
    which the ellipse test needs alongside distance.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)

    x = math.sin(dlambda) * math.cos(phi2)
    y = (
        math.cos(phi1) * math.sin(phi2)
        - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    )
    theta = math.atan2(x, y)
    return (math.degrees(theta) + 360) % 360


def destination_point(lat: float, lon: float, bearing_degrees: float, distance_km: float):
    """
    Given a start point, bearing, and distance, compute the destination
    lat/lon. Used to draw ellipse boundary points for map visualization
    in the mobile app (Phase 4).
    """
    R = EARTH_RADIUS_KM
    br = math.radians(bearing_degrees)
    phi1 = math.radians(lat)
    lambda1 = math.radians(lon)

    phi2 = math.asin(
        math.sin(phi1) * math.cos(distance_km / R)
        + math.cos(phi1) * math.sin(distance_km / R) * math.cos(br)
    )
    lambda2 = lambda1 + math.atan2(
        math.sin(br) * math.sin(distance_km / R) * math.cos(phi1),
        math.cos(distance_km / R) - math.sin(phi1) * math.sin(phi2),
    )
    return math.degrees(phi2), (math.degrees(lambda2) + 540) % 360 - 180
