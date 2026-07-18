"""
NiDa — Fires Endpoints
GET  /api/v1/fires         -> list stored fire events
POST /api/v1/fires/ingest  -> fetch latest FIRMS data and store it
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.schemas import FireOut, IngestResult
from backend.data_layer.firms_client import FIRMSError, fetch_fires
from backend.db.database import FireEvent, get_db

logger = logging.getLogger("nida.api.fires")

router = APIRouter()


@router.get("/fires", response_model=List[FireOut])
def list_fires(
    db: Session = Depends(get_db),
    acq_date: Optional[str] = Query(None, description="Filter by acquisition date YYYY-MM-DD"),
    min_frp: Optional[float] = Query(None, description="Minimum Fire Radiative Power (MW)"),
    limit: int = Query(500, le=5000),
):
    """Return stored fire events, newest first."""
    q = db.query(FireEvent)
    if acq_date:
        q = q.filter(FireEvent.acq_date == acq_date)
    if min_frp is not None:
        q = q.filter(FireEvent.frp >= min_frp)
    return q.order_by(FireEvent.ingested_at.desc()).limit(limit).all()


@router.get("/fires/geojson")
def fires_geojson(db: Session = Depends(get_db)):
    """
    Raw satellite detections (pre-clustering) as GeoJSON points, each
    tagged with acq_date. Powers the dashboard's time-slider layer,
    letting users see individual VIIRS/MODIS detections day by day
    rather than only the aggregated fire-event clusters.
    """
    rows = db.query(FireEvent).all()
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [r.longitude, r.latitude]},
        "properties": {
            "acq_date": r.acq_date,
            "acq_time": r.acq_time,
            "frp": r.frp,
            "confidence": r.confidence,
            "satellite": r.satellite,
            "daynight": r.daynight,
        },
    } for r in rows]
    return {"type": "FeatureCollection", "features": features}


@router.post("/fires/ingest", response_model=IngestResult)
async def ingest_fires(db: Session = Depends(get_db)):
    """
    Fetch the latest VIIRS detections for Algeria from NASA FIRMS
    and store new records (deduplicated on lat/lon/date/time).
    """
    try:
        return await ingest_fires_impl(db)
    except FIRMSError as exc:
        logger.error(f"FIRMS ingestion failed: {exc}")
        raise HTTPException(status_code=502, detail=str(exc))


async def ingest_fires_impl(db: Session) -> IngestResult:
    """
    Core ingest logic, shared by the HTTP route above and the automated
    scheduler (backend/scheduler.py). Raises FIRMSError on total failure;
    callers decide how to surface that (HTTP 502 for the route, a logged
    SchedulerRun failure for the automated job).
    """
    df = await fetch_fires()

    new, dupes = 0, 0
    for _, row in df.iterrows():
        exists = (
            db.query(FireEvent)
            .filter(
                FireEvent.latitude == float(row["latitude"]),
                FireEvent.longitude == float(row["longitude"]),
                FireEvent.acq_date == str(row["acq_date"]),
                FireEvent.acq_time == str(row["acq_time"]),
            )
            .first()
        )
        if exists:
            dupes += 1
            continue

        db.add(FireEvent(
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            brightness=float(row.get("brightness") or row.get("bright_ti4") or 0) or None,
            frp=float(row.get("frp") or 0) or None,
            confidence=str(row.get("confidence") or ""),
            satellite=str(row.get("satellite") or ""),
            acq_date=str(row["acq_date"]),
            acq_time=str(row["acq_time"]),
            daynight=str(row.get("daynight") or ""),
        ))
        new += 1

    db.commit()
    logger.info(f"Ingest complete: {new} new, {dupes} duplicates skipped.")
    return IngestResult(fetched=len(df), new_records=new, duplicates_skipped=dupes)
