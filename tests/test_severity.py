"""
NiDa — Tests for the composite severity scoring model.
"""

from backend.geo.severity import compute_severity


def test_high_frp_high_wind_high_confidence_is_critical():
    result = compute_severity(max_frp_mw=200.0, has_high_confidence=True, wind_speed_kmh=60.0)
    assert result.level == "critical"
    assert result.score == 1.0


def test_low_everything_is_advisory():
    result = compute_severity(max_frp_mw=1.0, has_high_confidence=False, wind_speed_kmh=2.0)
    assert result.level == "advisory"


def test_missing_wind_data_does_not_crash():
    result = compute_severity(max_frp_mw=50.0, has_high_confidence=True, wind_speed_kmh=None)
    assert result.wind_component == 0.0
    assert 0.0 <= result.score <= 1.0


def test_score_monotonic_in_frp():
    low = compute_severity(max_frp_mw=5.0, has_high_confidence=False, wind_speed_kmh=10.0)
    high = compute_severity(max_frp_mw=150.0, has_high_confidence=False, wind_speed_kmh=10.0)
    assert high.score > low.score


def test_score_bounded_zero_to_one():
    result = compute_severity(max_frp_mw=99999.0, has_high_confidence=True, wind_speed_kmh=999.0)
    assert result.score <= 1.0
