"""
NiDa — Land Cover Classification & Filtering Tests

Validates the MODIS MCD12C1 land cover module against known Algerian
geography: desert/oil regions must be flagged as likely false positives
(gas flares), while vegetated northern fire zones must be preserved.
"""

from backend.geo.landcover import (
    IGBP_CLASSES, classify_point, is_likely_false_positive,
)


# ── desert / oil regions: flare false positives ──

def test_deep_sahara_flagged_false_positive():
    assert is_likely_false_positive(22.79, 5.52) is True   # Tamanrasset


def test_hassi_messaoud_oilfield_flagged():
    assert is_likely_false_positive(31.68, 6.07) is True    # major oil region


def test_in_salah_gas_region_flagged():
    assert is_likely_false_positive(27.19, 2.47) is True


# ── northern vegetated fire zones: preserved ──

def test_kabylie_not_flagged():
    assert is_likely_false_positive(36.7, 4.05) is False


def test_bejaia_coast_not_flagged():
    assert is_likely_false_positive(36.75, 5.08) is False


def test_el_tarf_northeast_forest_not_flagged():
    assert is_likely_false_positive(36.77, 8.31) is False


# ── classification output shape ──

def test_classify_point_returns_expected_fields():
    info = classify_point(36.7, 4.05)
    assert set(info.keys()) == {
        "igbp_class", "igbp_name", "fuel_group", "is_vegetated", "window_has_fuel"
    }
    assert info["igbp_name"] in IGBP_CLASSES.values()


def test_classify_desert_is_barren_fuel_group():
    info = classify_point(22.79, 5.52)
    assert info["fuel_group"] == "barren"


def test_classify_out_of_bounds_is_failsafe():
    """A point outside the grid must fail SAFE (kept, not filtered) --
    never drop a detection due to missing land cover data."""
    info = classify_point(0.0, 0.0)  # equator, far outside Algeria crop
    assert info["is_vegetated"] is True
    assert is_likely_false_positive(0.0, 0.0) is False


def test_neighborhood_sampling_rescues_coastal_forest():
    """Béjaïa's exact cell may read urban at 5.5km resolution, but forest
    nearby should make window_has_fuel True so it is never filtered."""
    info = classify_point(36.75, 5.08, neighborhood=1)
    assert info["window_has_fuel"] is True
