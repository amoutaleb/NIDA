"""
NiDa — Tests for the Algeria national boundary filter.
"""

import pandas as pd

from backend.geo.boundary import filter_to_algeria, is_in_algeria


def test_algiers_is_inside():
    assert is_in_algeria(36.7538, 3.0588) is True


def test_bejaia_is_inside():
    assert is_in_algeria(36.75, 5.08) is True


def test_deep_south_tamanrasset_is_inside():
    assert is_in_algeria(22.785, 5.522) is True


def test_marrakech_morocco_is_outside():
    assert is_in_algeria(31.63, -7.99) is False


def test_tunis_tunisia_is_outside():
    assert is_in_algeria(36.8, 10.18) is False


def test_real_morocco_cluster_from_validation_is_outside():
    """The exact 624 MW Morocco cluster that leaked through the bbox
    filter during July 2026 live validation must be rejected."""
    assert is_in_algeria(30.908, -8.096) is False


def test_filter_dataframe_drops_foreign_points():
    df = pd.DataFrame({
        "latitude": [36.75, 31.63, 36.8, 22.785],
        "longitude": [5.08, -7.99, 10.18, 5.522],
    })
    out = filter_to_algeria(df)
    assert len(out) == 2  # Béjaïa + Tamanrasset kept; Marrakech + Tunis dropped


def test_filter_empty_dataframe():
    df = pd.DataFrame(columns=["latitude", "longitude"])
    out = filter_to_algeria(df)
    assert out.empty
