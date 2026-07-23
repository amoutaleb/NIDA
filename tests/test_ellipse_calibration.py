"""
NiDa — Tests for the recalibrated ellipse sizing (midflame WAF + capped base).

Regression tests for the two calibration issues found during July 2026
live validation: (1) raw 10m wind fed into Anderson's midflame-wind
formula saturated L/W at the cap for any moderate breeze, and (2) the
FRP scaling produced operationally meaningless 100-200 km zones.
"""

from backend.geo.ellipse import base_radius_km, build_ellipse, compute_lw_ratio


def test_moderate_wind_no_longer_saturates_lw_cap():
    """25 km/h (a common summer breeze) must NOT hit the L/W=8 cap.
    Pre-fix, 25 km/h raw -> 15.8 mph -> L/W ~54 (capped to 8).
    Post-fix, 25 km/h * 0.4 WAF -> 6.3 mph midflame -> L/W ~4-5."""
    lw = compute_lw_ratio(25.0)
    assert lw < 8.0
    assert lw > 2.0  # still meaningfully elongated


def test_strong_wind_still_reaches_high_lw():
    """Genuinely strong wind (60+ km/h) should still produce strong elongation."""
    lw = compute_lw_ratio(70.0)
    assert lw >= 6.0


def test_base_radius_is_capped():
    """A 624 MW mega-fire must not produce an unbounded base radius."""
    r = base_radius_km(624.0)
    assert r <= 8.0


def test_extreme_cluster_zone_is_operationally_sane():
    """The worst cluster from live validation (473 MW, 18.4 km/h wind)
    previously produced a 213 km major axis. Post-recalibration the
    zone must stay within an operationally plausible envelope."""
    ellipse = build_ellipse(
        centroid_lat=34.851, centroid_lon=-1.071, frp_mw=473.0,
        wind_speed_kmh=18.4, wind_direction_deg=270.0, wind_source="open-meteo",
    )
    assert ellipse.semi_major_km < 40.0
    assert ellipse.semi_minor_km <= 8.0
