"""
NiDa — Fire Event Clustering

Groups raw satellite fire detections (VIIRS pixels) into coherent fire
events using DBSCAN with a Haversine metric, following the methodology
justified in the NiDa literature review (Section 2.4): a single wildfire
triggers dozens of individual pixel detections, and pushing a separate
alert per pixel causes catastrophic alert fatigue. Density-based
clustering (rather than K-means, which requires a pre-specified number
of clusters) automatically discovers fire events and treats isolated,
low-density detections as noise -- providing inherent false-alarm
filtration consistent with prior geospatial disaster-response literature.
"""

import logging
from dataclasses import dataclass, field
from typing import List

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

from backend.config import settings
from backend.geo.distance import haversine_km

logger = logging.getLogger("nida.geo.clustering")


@dataclass
class FireCluster:
    """A single clustered fire event, aggregated from one or more raw detections."""
    cluster_id: int
    centroid_lat: float
    centroid_lon: float
    point_count: int
    max_frp: float                 # peak Fire Radiative Power in the cluster (MW)
    mean_frp: float
    max_confidence_high: bool      # True if any point had 'h' (high) confidence
    member_indices: List[int] = field(default_factory=list)
    bbox: tuple = None             # (min_lat, min_lon, max_lat, max_lon)


def cluster_fires(df: pd.DataFrame) -> List[FireCluster]:
    """
    Cluster raw fire detections into fire events using DBSCAN with a
    haversine-based distance metric.

    Args:
        df: DataFrame with at least 'latitude', 'longitude' columns.
            Optionally 'frp' and 'confidence' for enriched cluster stats.

    Returns:
        List of FireCluster objects. Points classified as noise (label -1)
        are excluded, consistent with the false-alarm filtration rationale
        in the literature review.
    """
    if df.empty:
        return []

    coords = df[["latitude", "longitude"]].to_numpy()
    coords_rad = np.radians(coords)

    # DBSCAN with haversine metric expects eps in radians on the unit sphere
    eps_rad = settings.CLUSTER_EPS_KM / 6371.0088

    db = DBSCAN(
        eps=eps_rad,
        min_samples=settings.CLUSTER_MIN_SAMPLES,
        metric="haversine",
    ).fit(coords_rad)

    labels = db.labels_
    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = int(np.sum(labels == -1))
    logger.info(
        f"DBSCAN clustering: {n_clusters} fire events found, "
        f"{n_noise} isolated detections filtered as noise "
        f"(eps={settings.CLUSTER_EPS_KM}km, min_samples={settings.CLUSTER_MIN_SAMPLES})"
    )

    clusters = []
    for label in sorted(set(labels)):
        if label == -1:
            continue  # noise / isolated false-positive candidates

        mask = labels == label
        subset = df[mask]

        centroid_lat = subset["latitude"].mean()
        centroid_lon = subset["longitude"].mean()

        frp_values = subset["frp"].dropna() if "frp" in subset.columns else pd.Series(dtype=float)
        max_frp = float(frp_values.max()) if not frp_values.empty else 0.0
        mean_frp = float(frp_values.mean()) if not frp_values.empty else 0.0

        has_high_conf = False
        if "confidence" in subset.columns:
            has_high_conf = subset["confidence"].astype(str).isin(["h", "high"]).any()

        bbox = (
            float(subset["latitude"].min()),
            float(subset["longitude"].min()),
            float(subset["latitude"].max()),
            float(subset["longitude"].max()),
        )

        clusters.append(FireCluster(
            cluster_id=int(label),
            centroid_lat=float(centroid_lat),
            centroid_lon=float(centroid_lon),
            point_count=int(mask.sum()),
            max_frp=max_frp,
            mean_frp=mean_frp,
            max_confidence_high=bool(has_high_conf),
            member_indices=subset.index.tolist(),
            bbox=bbox,
        ))

    return clusters


def merge_close_clusters(clusters: List[FireCluster], merge_distance_km: float = 3.0) -> List[FireCluster]:
    """
    Optional second pass: merge cluster centroids that ended up very close
    together (e.g. two DBSCAN clusters split by a satellite swath gap but
    representing the same physical fire front). Not part of the core
    HDBSCAN/DBSCAN algorithm -- an explicit, documented post-processing
    step so the paper can describe it as a distinct design decision.
    """
    if len(clusters) <= 1:
        return clusters

    merged = []
    used = set()

    for i, c1 in enumerate(clusters):
        if i in used:
            continue
        group = [c1]
        for j, c2 in enumerate(clusters):
            if j <= i or j in used:
                continue
            d = haversine_km(c1.centroid_lat, c1.centroid_lon, c2.centroid_lat, c2.centroid_lon)
            if d <= merge_distance_km:
                group.append(c2)
                used.add(j)

        if len(group) == 1:
            merged.append(c1)
        else:
            total_points = sum(g.point_count for g in group)
            weighted_lat = sum(g.centroid_lat * g.point_count for g in group) / total_points
            weighted_lon = sum(g.centroid_lon * g.point_count for g in group) / total_points
            merged.append(FireCluster(
                cluster_id=c1.cluster_id,
                centroid_lat=weighted_lat,
                centroid_lon=weighted_lon,
                point_count=total_points,
                max_frp=max(g.max_frp for g in group),
                mean_frp=sum(g.mean_frp * g.point_count for g in group) / total_points,
                max_confidence_high=any(g.max_confidence_high for g in group),
                member_indices=[idx for g in group for idx in g.member_indices],
                bbox=(
                    min(g.bbox[0] for g in group),
                    min(g.bbox[1] for g in group),
                    max(g.bbox[2] for g in group),
                    max(g.bbox[3] for g in group),
                ),
            ))

    if len(merged) < len(clusters):
        logger.info(f"Merged {len(clusters)} -> {len(merged)} clusters within {merge_distance_km}km")
    return merged
