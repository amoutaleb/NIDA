"""
NiDa — Archive Browsing Endpoints

GET /api/v1/archive/summary  -> available date range + row counts
GET /api/v1/archive/geojson  -> detections + cluster snapshots for a date range

The archive spans BOTH archived rows (older than the retention window)
and still-live rows, so a queried range that straddles the rollover
boundary returns a seamless picture. This same endpoint will later serve
the Phase 5 (2021-2023) validation dataset once bulk-imported.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.db.database import (
    ArchivedFireCluster, ArchivedFireEvent, FireEvent, get_db,
)

logger = logging.getLogger("nida.api.archive")

router = APIRouter()


def _validate_date(d: str, name: str) -> str:
    import re
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        raise HTTPException(status_code=422, detail=f"{name} must be YYYY-MM-DD")
    return d


@router.get("/archive/summary")
def archive_summary(db: Session = Depends(get_db)):
    """Date coverage and volume of the historical record (archived +
    live), so the archive page can bound its date pickers."""
    arch_min = db.query(func.min(ArchivedFireEvent.acq_date)).scalar()
    arch_max = db.query(func.max(ArchivedFireEvent.acq_date)).scalar()
    live_min = db.query(func.min(FireEvent.acq_date)).scalar()
    live_max = db.query(func.max(FireEvent.acq_date)).scalar()

    all_dates = [d for d in (arch_min, arch_max, live_min, live_max) if d]
    return {
        "earliest_date": min(all_dates) if all_dates else None,
        "latest_date": max(all_dates) if all_dates else None,
        "archived_detections": db.query(ArchivedFireEvent).count(),
        "live_detections": db.query(FireEvent).count(),
        "cluster_snapshots": db.query(ArchivedFireCluster).count(),
    }


@router.get("/archive/geojson")
def archive_geojson(
    start: str = Query(..., description="Start date YYYY-MM-DD (inclusive)"),
    end: str = Query(..., description="End date YYYY-MM-DD (inclusive)"),
    include_snapshots: bool = Query(True),
    max_points: int = Query(20000, le=50000),
    db: Session = Depends(get_db),
):
    """
    Historical fire picture for a date range: raw detections (archived
    and live, unioned) plus optionally the 3-hourly cluster snapshots
    whose creation fell inside the range.
    """
    start = _validate_date(start, "start")
    end = _validate_date(end, "end")
    if start > end:
        raise HTTPException(status_code=422, detail="start must be <= end")

    features = []

    # Detections: archived + still-live, same schema
    arch_rows = (
        db.query(ArchivedFireEvent)
        .filter(ArchivedFireEvent.acq_date >= start, ArchivedFireEvent.acq_date <= end)
        .limit(max_points)
        .all()
    )
    remaining = max_points - len(arch_rows)
    live_rows = (
        db.query(FireEvent)
        .filter(FireEvent.acq_date >= start, FireEvent.acq_date <= end)
        .limit(max(remaining, 0))
        .all()
    ) if remaining > 0 else []

    for r in list(arch_rows) + list(live_rows):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r.longitude, r.latitude]},
            "properties": {
                "feature_type": "detection",
                "acq_date": r.acq_date,
                "acq_time": r.acq_time,
                "frp": r.frp,
                "confidence": r.confidence,
                "satellite": r.satellite,
            },
        })

    snapshots_count = 0
    if include_snapshots:
        snaps = (
            db.query(ArchivedFireCluster)
            .filter(
                func.date(ArchivedFireCluster.cluster_created_at) >= start,
                func.date(ArchivedFireCluster.cluster_created_at) <= end,
            )
            .all()
        )
        snapshots_count = len(snaps)
        for s in snaps:
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [s.centroid_lon, s.centroid_lat]},
                "properties": {
                    "feature_type": "cluster_snapshot",
                    "live_cluster_id": s.live_cluster_id,
                    "point_count": s.point_count,
                    "max_frp": round(s.max_frp or 0, 1),
                    "severity_level": s.severity_level,
                    "severity_score": s.severity_score,
                    "wind_speed_kmh": s.wind_speed_kmh,
                    "snapshot_at": s.snapshot_at.isoformat() if s.snapshot_at else None,
                },
            })

    logger.info(
        f"Archive query {start}..{end}: {len(arch_rows)} archived + "
        f"{len(live_rows)} live detections, {snapshots_count} snapshots."
    )
    return {"type": "FeatureCollection", "features": features}
