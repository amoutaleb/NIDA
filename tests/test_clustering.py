"""
NiDa — Tests for fire event clustering (DBSCAN with haversine metric).
"""

import pandas as pd
import pytest

from backend.geo.clustering import cluster_fires, merge_close_clusters


def test_cluster_fires_groups_nearby_points():
    """Ten points tightly packed around Béjaïa should form ONE cluster,
    not ten separate alerts (this is the core alert-fatigue prevention
    behavior described in the literature review)."""
    df = pd.DataFrame({
        "latitude": [36.750 + i * 0.001 for i in range(10)],
        "longitude": [5.080 + i * 0.001 for i in range(10)],
        "frp": [10.0] * 10,
        "confidence": ["n"] * 10,
    })
    clusters = cluster_fires(df)
    assert len(clusters) == 1
    assert clusters[0].point_count == 10


def test_cluster_fires_separates_distant_events():
    """Points in Béjaïa and points in Tizi Ouzou (~100km apart) should
    form TWO separate clusters."""
    bejaia = pd.DataFrame({
        "latitude": [36.750 + i * 0.001 for i in range(5)],
        "longitude": [5.080 + i * 0.001 for i in range(5)],
        "frp": [10.0] * 5,
        "confidence": ["n"] * 5,
    })
    tizi_ouzou = pd.DataFrame({
        "latitude": [36.710 + i * 0.001 for i in range(5)],
        "longitude": [4.050 + i * 0.001 for i in range(5)],
        "frp": [8.0] * 5,
        "confidence": ["n"] * 5,
    })
    df = pd.concat([bejaia, tizi_ouzou], ignore_index=True)
    clusters = cluster_fires(df)
    assert len(clusters) == 2


def test_cluster_fires_filters_isolated_noise():
    """A single isolated point far from anything else should be treated
    as noise and excluded, providing false-alarm filtration."""
    df = pd.DataFrame({
        "latitude": [36.750, 36.751, 36.752, 10.0],  # last point is isolated
        "longitude": [5.080, 5.081, 5.082, 10.0],
        "frp": [10.0, 10.0, 10.0, 5.0],
        "confidence": ["n", "n", "n", "n"],
    })
    clusters = cluster_fires(df)
    assert len(clusters) == 1
    assert clusters[0].point_count == 3


def test_cluster_fires_empty_input():
    df = pd.DataFrame(columns=["latitude", "longitude", "frp", "confidence"])
    clusters = cluster_fires(df)
    assert clusters == []


def test_cluster_centroid_and_frp_stats():
    df = pd.DataFrame({
        "latitude": [36.0, 36.001, 36.002],
        "longitude": [5.0, 5.001, 5.002],
        "frp": [5.0, 15.0, 10.0],
        "confidence": ["n", "h", "n"],
    })
    clusters = cluster_fires(df)
    assert len(clusters) == 1
    c = clusters[0]
    assert c.max_frp == 15.0
    assert c.mean_frp == pytest.approx(10.0)
    assert c.max_confidence_high is True
    assert c.centroid_lat == pytest.approx(36.001, abs=1e-3)


def test_merge_close_clusters():
    from backend.geo.clustering import FireCluster
    c1 = FireCluster(0, 36.750, 5.080, 5, 10.0, 8.0, False, [0, 1], (36.749, 5.079, 36.751, 5.081))
    c2 = FireCluster(1, 36.752, 5.082, 3, 12.0, 9.0, False, [2, 3], (36.751, 5.081, 36.753, 5.083))
    merged = merge_close_clusters([c1, c2], merge_distance_km=3.0)
    assert len(merged) == 1
    assert merged[0].point_count == 8


def test_merge_keeps_distant_clusters_separate():
    from backend.geo.clustering import FireCluster
    c1 = FireCluster(0, 36.750, 5.080, 5, 10.0, 8.0, False, [0], (36.75, 5.08, 36.75, 5.08))
    c2 = FireCluster(1, 30.000, 8.000, 3, 12.0, 9.0, False, [1], (30.0, 8.0, 30.0, 8.0))
    merged = merge_close_clusters([c1, c2], merge_distance_km=3.0)
    assert len(merged) == 2
