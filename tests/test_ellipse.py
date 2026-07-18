"""
NiDa — Tests for the directional alert ellipse model (Anderson 1983 L/W ratio).
"""

import pytest

from backend.geo.ellipse import (
    build_ellipse,
    compute_lw_ratio,
    point_in_ellipse,
    severity_at_point,
)


def test_lw_ratio_no_wind_is_near_one():
    """At zero wind speed, Anderson's formula should be close to 1
    (circular fire), consistent with the physical expectation that
    fires spread roughly uniformly with no wind forcing."""
    lw = compute_lw_ratio(0.0)
    assert 0.9 <= lw <= 1.2


def test_lw_ratio_increases_with_wind():
    """Higher wind speed should produce a more elongated ellipse."""
    lw_low = compute_lw_ratio(5.0)
    lw_high = compute_lw_ratio(40.0)
    assert lw_high > lw_low


def test_lw_ratio_capped_at_max():
    lw = compute_lw_ratio(200.0)  # extreme wind
    assert lw <= 8.0


def test_build_ellipse_with_wind_is_elongated():
    ellipse = build_ellipse(
        centroid_lat=36.75, centroid_lon=5.08, frp_mw=20.0,
        wind_speed_kmh=30.0, wind_direction_deg=270.0, wind_source="open-meteo",
    )
    assert ellipse.semi_major_km > ellipse.semi_minor_km
    assert not ellipse.is_circular_fallback


def test_build_ellipse_no_wind_falls_back_to_circle():
    ellipse = build_ellipse(
        centroid_lat=36.75, centroid_lon=5.08, frp_mw=20.0,
        wind_speed_kmh=None, wind_direction_deg=None,
    )
    assert ellipse.semi_major_km == ellipse.semi_minor_km
    assert ellipse.is_circular_fallback


def test_downwind_point_is_inside_ellipse():
    """A point directly downwind of the fire, within the major axis
    distance, should be classified as inside the ellipse."""
    # wind blows FROM the west (270 deg) -> fire spreads EAST (90 deg)
    ellipse = build_ellipse(
        centroid_lat=36.75, centroid_lon=5.08, frp_mw=50.0,
        wind_speed_kmh=25.0, wind_direction_deg=270.0, wind_source="open-meteo",
    )
    # point ~5km due east of centroid (downwind direction)
    from backend.geo.distance import destination_point
    down_lat, down_lon = destination_point(36.75, 5.08, bearing_degrees=90, distance_km=5)
    assert point_in_ellipse(ellipse, down_lat, down_lon)


def test_upwind_point_at_same_distance_may_be_outside():
    """A point the SAME distance away but in the upwind direction should
    be less likely to be inside the ellipse than the downwind point,
    demonstrating the directional (non-circular) behavior."""
    ellipse = build_ellipse(
        centroid_lat=36.75, centroid_lon=5.08, frp_mw=50.0,
        wind_speed_kmh=35.0, wind_direction_deg=270.0, wind_source="open-meteo",
    )
    from backend.geo.distance import destination_point
    # upwind = west direction (270 deg), at a distance beyond the minor axis
    # but within what the major axis would allow
    test_distance = (ellipse.semi_major_km + ellipse.semi_minor_km) / 2
    up_lat, up_lon = destination_point(36.75, 5.08, bearing_degrees=270, distance_km=test_distance)
    down_lat, down_lon = destination_point(36.75, 5.08, bearing_degrees=90, distance_km=test_distance)

    upwind_inside = point_in_ellipse(ellipse, up_lat, up_lon)
    downwind_inside = point_in_ellipse(ellipse, down_lat, down_lon)

    # downwind must be inside; upwind at the same distance must NOT be
    # (this is the whole point of using an ellipse instead of a circle)
    assert downwind_inside is True
    assert upwind_inside is False


def test_centroid_itself_is_inside():
    ellipse = build_ellipse(
        centroid_lat=36.75, centroid_lon=5.08, frp_mw=10.0,
        wind_speed_kmh=10.0, wind_direction_deg=180.0, wind_source="open-meteo",
    )
    assert point_in_ellipse(ellipse, 36.75, 5.08) is True


def test_far_away_point_is_outside():
    ellipse = build_ellipse(
        centroid_lat=36.75, centroid_lon=5.08, frp_mw=10.0,
        wind_speed_kmh=10.0, wind_direction_deg=180.0, wind_source="open-meteo",
    )
    assert point_in_ellipse(ellipse, 10.0, 10.0) is False


def test_severity_at_point_tiers_correctly():
    ellipse = build_ellipse(
        centroid_lat=36.75, centroid_lon=5.08, frp_mw=50.0,
        wind_speed_kmh=20.0, wind_direction_deg=270.0, wind_source="open-meteo",
    )
    # centroid itself must be critical
    assert severity_at_point(ellipse, 36.75, 5.08) == "critical"
    # far away must be None
    assert severity_at_point(ellipse, 10.0, 10.0) is None
