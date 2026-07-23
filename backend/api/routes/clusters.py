"""
NiDa — Fire Clustering Endpoints

POST /api/v1/clusters/run  -> run the full Phase 2 pipeline:
    stored fire points -> DBSCAN clustering -> wind lookup ->
    directional ellipse -> severity scoring -> stored FireClusterModel rows

GET  /api/v1/clusters      -> list stored clusters
"""

import asyncio
import logging
from typing import List

import pandas as pd
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.schemas import ClusterOut, ClusterRunResult
from backend.db.database import FireClusterModel, FireEvent, get_db
from backend.geo.clustering import cluster_fires, merge_close_clusters
from backend.geo.ellipse import build_ellipse
from backend.geo.severity import compute_severity
from backend.geo.wind_client import get_wind

logger = logging.getLogger("nida.api.clusters")

router = APIRouter()


@router.post("/clusters/run", response_model=ClusterRunResult)
async def run_clustering(db: Session = Depends(get_db)):
    """
    Run the full Phase 2 geospatial pipeline against all stored fire
    detections: DBSCAN clustering -> live wind lookup per cluster ->
    Anderson (1983) directional ellipse -> composite severity score.

    Results are stored in the fire_clusters table (previous run's
    clusters are cleared first, since this recomputes from the full
    current fire_events table each time).
    """
    return await run_clustering_impl(db)


async def run_clustering_impl(db: Session) -> ClusterRunResult:
    """
    Core clustering pipeline, shared by the HTTP route above and the
    automated scheduler (backend/scheduler.py).
    """
    rows = db.query(FireEvent).all()
    if not rows:
        return ClusterRunResult(input_points=0, clusters_found=0, noise_points_filtered=0, clusters=[])

    df = pd.DataFrame([{
        "latitude": r.latitude,
        "longitude": r.longitude,
        "frp": r.frp or 0.0,
        "confidence": r.confidence,
    } for r in rows])

    # Defense-in-depth: re-apply the national boundary filter at clustering
    # time as well as at ingestion time. During live validation, fire rows
    # ingested BEFORE the boundary filter existed persisted in the database
    # and re-emerged as a Moroccan cluster in the results -- filtering only
    # at the ingest boundary is insufficient when the datastore may contain
    # legacy or externally-inserted rows.
    from backend.geo.boundary import filter_to_algeria
    df = filter_to_algeria(df)

    raw_clusters = cluster_fires(df)
    merged_clusters = merge_close_clusters(raw_clusters, merge_distance_km=3.0)
    noise_count = len(df) - sum(c.point_count for c in merged_clusters)

    # Snapshot the outgoing cluster set into the archive BEFORE clearing:
    # the live table is fully rebuilt every run, so without this snapshot
    # there would be no historical record of how each fire's zone,
    # wind, and severity evolved over time. Severity is recomputed and
    # frozen here so the archive reflects the state as it was.
    outgoing = db.query(FireClusterModel).all()
    from backend.db.database import ArchivedFireCluster
    for oc in outgoing:
        sev = compute_severity(
            max_frp_mw=oc.max_frp,
            has_high_confidence=bool(oc.has_high_confidence),
            wind_speed_kmh=oc.wind_speed_kmh,
        )
        db.add(ArchivedFireCluster(
            live_cluster_id=oc.id,
            centroid_lat=oc.centroid_lat,
            centroid_lon=oc.centroid_lon,
            point_count=oc.point_count,
            max_frp=oc.max_frp,
            mean_frp=oc.mean_frp,
            has_high_confidence=oc.has_high_confidence,
            semi_major_km=oc.semi_major_km,
            semi_minor_km=oc.semi_minor_km,
            orientation_deg=oc.orientation_deg,
            lw_ratio=oc.lw_ratio,
            wind_speed_kmh=oc.wind_speed_kmh,
            wind_source=oc.wind_source,
            is_circular_fallback=oc.is_circular_fallback,
            severity_level=sev.level,
            severity_score=sev.score,
            cluster_created_at=oc.created_at,
        ))

    # Clear previous run's stored clusters
    db.query(FireClusterModel).delete()
    db.commit()

    # Fetch wind for ALL clusters in parallel -- one slow/failed lookup no
    # longer stacks 30s timeouts sequentially (which froze the browser on
    # large fire days). asyncio.gather caps total wind wait at the single
    # slowest lookup instead of the sum of all of them.
    wind_results = await asyncio.gather(
        *[get_wind(c.centroid_lat, c.centroid_lon) for c in merged_clusters],
        return_exceptions=True,
    )

    output: List[ClusterOut] = []
    for c, wind in zip(merged_clusters, wind_results):
        if isinstance(wind, Exception):
            logger.warning(f"Wind lookup raised for cluster at "
                           f"({c.centroid_lat},{c.centroid_lon}): {wind}")
            wind = None

        ellipse = build_ellipse(
            centroid_lat=c.centroid_lat,
            centroid_lon=c.centroid_lon,
            frp_mw=c.max_frp,
            wind_speed_kmh=wind.speed_kmh if wind else None,
            wind_direction_deg=wind.direction_deg if wind else None,
            wind_source=wind.source if wind else None,
        )

        severity = compute_severity(
            max_frp_mw=c.max_frp,
            has_high_confidence=c.max_confidence_high,
            wind_speed_kmh=wind.speed_kmh if wind else None,
        )

        # Tag the cluster with its dominant vegetation/fuel type (MODIS
        # land cover). Advisory context for the map and paper statistics;
        # a future refinement can feed fuel_group into severity scoring.
        from backend.geo.landcover import classify_point
        lc = classify_point(c.centroid_lat, c.centroid_lon, neighborhood=1)

        record = FireClusterModel(
            centroid_lat=c.centroid_lat,
            centroid_lon=c.centroid_lon,
            point_count=c.point_count,
            max_frp=c.max_frp,
            mean_frp=c.mean_frp,
            has_high_confidence=int(c.max_confidence_high),
            semi_major_km=ellipse.semi_major_km,
            semi_minor_km=ellipse.semi_minor_km,
            orientation_deg=ellipse.orientation_deg,
            lw_ratio=ellipse.lw_ratio,
            wind_speed_kmh=ellipse.wind_speed_kmh,
            wind_source=ellipse.wind_source,
            is_circular_fallback=int(ellipse.is_circular_fallback),
            fuel_group=lc["fuel_group"],
            igbp_name=lc["igbp_name"],
        )
        db.add(record)
        db.flush()  # get record.id without committing yet

        output.append(ClusterOut(
            id=record.id,
            centroid_lat=c.centroid_lat,
            centroid_lon=c.centroid_lon,
            point_count=c.point_count,
            max_frp=c.max_frp,
            mean_frp=c.mean_frp,
            has_high_confidence=c.max_confidence_high,
            semi_major_km=ellipse.semi_major_km,
            semi_minor_km=ellipse.semi_minor_km,
            orientation_deg=ellipse.orientation_deg,
            lw_ratio=ellipse.lw_ratio,
            wind_speed_kmh=ellipse.wind_speed_kmh,
            wind_source=ellipse.wind_source,
            is_circular_fallback=ellipse.is_circular_fallback,
            severity_score=severity.score,
            severity_level=severity.level,
            fuel_group=lc["fuel_group"],
            igbp_name=lc["igbp_name"],
            created_at=record.created_at,
        ))

    db.commit()
    logger.info(
        f"Clustering run complete: {len(df)} points -> "
        f"{len(merged_clusters)} clusters ({noise_count} noise points filtered)"
    )

    return ClusterRunResult(
        input_points=len(df),
        clusters_found=len(merged_clusters),
        noise_points_filtered=noise_count,
        clusters=output,
    )


@router.get("/clusters/geojson")
def clusters_geojson(db: Session = Depends(get_db)):
    """
    Return current fire clusters as a GeoJSON FeatureCollection:
    one Polygon feature per alert ellipse plus one Point feature per
    fire centroid. Consumed by the web dashboard map and (later) the
    mobile app.
    """
    from backend.geo.ellipse import ellipse_boundary_points

    rows = db.query(FireClusterModel).all()
    features = []
    for r in rows:
        ellipse = _cluster_to_geo_ellipse(r)
        severity = compute_severity(
            max_frp_mw=r.max_frp,
            has_high_confidence=bool(r.has_high_confidence),
            wind_speed_kmh=r.wind_speed_kmh,
        )
        props = {
            "cluster_id": r.id,
            "point_count": r.point_count,
            "max_frp": round(r.max_frp, 1),
            "wind_speed_kmh": r.wind_speed_kmh,
            "wind_direction_deg": r.orientation_deg,  # downwind bearing (see ellipse.py)
            "wind_source": r.wind_source,
            "lw_ratio": round(r.lw_ratio, 2),
            "semi_major_km": round(r.semi_major_km, 2),
            "semi_minor_km": round(r.semi_minor_km, 2),
            "severity_level": severity.level,
            "severity_score": round(severity.score, 3),
            "is_circular_fallback": bool(r.is_circular_fallback),
            "fuel_group": r.fuel_group,
            "igbp_name": r.igbp_name,
        }
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [ellipse_boundary_points(ellipse)],
            },
            "properties": {**props, "feature_type": "alert_zone"},
        })
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [r.centroid_lon, r.centroid_lat],
            },
            "properties": {**props, "feature_type": "fire_centroid"},
        })

    return {"type": "FeatureCollection", "features": features}


def _cluster_to_geo_ellipse(r: FireClusterModel):
    from backend.geo.ellipse import AlertEllipse
    return AlertEllipse(
        centroid_lat=r.centroid_lat,
        centroid_lon=r.centroid_lon,
        semi_major_km=r.semi_major_km,
        semi_minor_km=r.semi_minor_km,
        orientation_deg=r.orientation_deg,
        lw_ratio=r.lw_ratio,
        wind_speed_kmh=r.wind_speed_kmh,
        wind_source=r.wind_source,
        is_circular_fallback=bool(r.is_circular_fallback),
    )


@router.get("/simulate-alert")
def simulate_alert(lat: float, lon: float, language: str = "en", db: Session = Depends(get_db)):
    """
    Given an arbitrary point (from a map click), evaluate it against every
    current fire cluster's directional ellipse and return the resulting
    alert(s) -- exactly what a real device at that location would receive
    from the alert engine. Read-only: does not create Alert rows or send
    push notifications. Powers the dashboard's "simulate a device" tool.
    """
    from backend.geo.distance import haversine_km
    from backend.geo.ellipse import severity_at_point
    from backend.notifications.messages import build_alert_message

    clusters = db.query(FireClusterModel).all()
    matches = []
    for c in clusters:
        ellipse = _cluster_to_geo_ellipse(c)
        level = severity_at_point(ellipse, lat, lon)
        if level is None:
            continue
        dist = haversine_km(c.centroid_lat, c.centroid_lon, lat, lon)
        lang = language if language in ("en", "ar", "fr") else "en"
        message = build_alert_message(
            level=level, device_lat=lat, device_lon=lon,
            cluster_lat=c.centroid_lat, cluster_lon=c.centroid_lon,
            distance_km=dist, language=lang,
        )
        matches.append({
            "cluster_id": c.id,
            "level": level,
            "distance_km": round(dist, 2),
            "message": message,
            "wind_speed_kmh": c.wind_speed_kmh,
            "is_circular_fallback": bool(c.is_circular_fallback),
        })

    matches.sort(key=lambda m: _LEVEL_RANK.get(m["level"], 0), reverse=True)
    return {
        "latitude": lat,
        "longitude": lon,
        "alerts_triggered": len(matches),
        "highest_level": matches[0]["level"] if matches else None,
        "matches": matches,
    }


_LEVEL_RANK = {"advisory": 1, "warning": 2, "critical": 3}


@router.get("/clusters", response_model=List[ClusterOut])
def list_clusters(db: Session = Depends(get_db)):
    """Return the most recently computed fire clusters."""
    rows = db.query(FireClusterModel).order_by(FireClusterModel.max_frp.desc()).all()
    result = []
    for r in rows:
        severity = compute_severity(
            max_frp_mw=r.max_frp,
            has_high_confidence=bool(r.has_high_confidence),
            wind_speed_kmh=r.wind_speed_kmh,
        )
        result.append(ClusterOut(
            id=r.id,
            centroid_lat=r.centroid_lat,
            centroid_lon=r.centroid_lon,
            point_count=r.point_count,
            max_frp=r.max_frp,
            mean_frp=r.mean_frp,
            has_high_confidence=bool(r.has_high_confidence),
            semi_major_km=r.semi_major_km,
            semi_minor_km=r.semi_minor_km,
            orientation_deg=r.orientation_deg,
            lw_ratio=r.lw_ratio,
            wind_speed_kmh=r.wind_speed_kmh,
            wind_source=r.wind_source,
            is_circular_fallback=bool(r.is_circular_fallback),
            severity_score=severity.score,
            severity_level=severity.level,
            fuel_group=r.fuel_group,
            igbp_name=r.igbp_name,
            created_at=r.created_at,
        ))
    return result
