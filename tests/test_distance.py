"""
NiDa — Tests for Haversine distance, bearing, and destination point math.
"""

import math
import pytest

from backend.geo.distance import haversine_km, bearing_deg, destination_point


def test_haversine_zero_distance():
    assert haversine_km(36.75, 5.08, 36.75, 5.08) == pytest.approx(0.0, abs=1e-6)


def test_haversine_known_distance():
    # Algiers (36.7538N, 3.0588E) to Béjaïa (36.75N, 5.08E) ~ 178 km
    d = haversine_km(36.7538, 3.0588, 36.75, 5.08)
    assert 170 <= d <= 190


def test_haversine_symmetric():
    d1 = haversine_km(36.0, 4.0, 30.0, 8.0)
    d2 = haversine_km(30.0, 8.0, 36.0, 4.0)
    assert d1 == pytest.approx(d2, abs=1e-9)


def test_bearing_due_north():
    # Point directly north should have bearing ~0
    b = bearing_deg(36.0, 5.0, 37.0, 5.0)
    assert b == pytest.approx(0.0, abs=1.0)


def test_bearing_due_east():
    b = bearing_deg(36.0, 5.0, 36.0, 6.0)
    assert b == pytest.approx(90.0, abs=2.0)


def test_destination_point_roundtrip():
    lat, lon = 36.75, 5.08
    dest_lat, dest_lon = destination_point(lat, lon, bearing_degrees=90, distance_km=50)
    # travelling east should increase longitude, latitude roughly unchanged
    assert dest_lon > lon
    assert abs(dest_lat - lat) < 1.0
    # distance back should be ~50km
    d = haversine_km(lat, lon, dest_lat, dest_lon)
    assert d == pytest.approx(50, abs=1.0)
