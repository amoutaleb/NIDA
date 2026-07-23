"""
NiDa — System Status Endpoints

GET  /api/v1/system/status    -> scheduler state + recent automated run history
POST /api/v1/system/run-now   -> trigger an immediate pipeline run (does not
                                  wait for the next scheduled interval)
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db.database import SchedulerRun, get_db
from backend.scheduler import get_status, run_full_pipeline

logger = logging.getLogger("nida.api.system")

router = APIRouter()


class RunHistoryOut(BaseModel):
    run_at: datetime
    fires_fetched: int
    clusters_found: int
    alerts_created: int
    success: bool
    error_message: Optional[str] = None
    duration_seconds: Optional[float] = None

    class Config:
        from_attributes = True


class SystemStatusOut(BaseModel):
    enabled: bool
    poll_interval_hours: int
    running: bool
    last_run_at: Optional[datetime] = None
    last_success: Optional[bool] = None
    recent_runs: List[RunHistoryOut] = []


@router.get("/system/status", response_model=SystemStatusOut)
def system_status(db: Session = Depends(get_db)):
    """
    Scheduler health snapshot: whether automated polling is enabled,
    whether a run is in progress right now, and the outcome of the last
    10 automated runs (fetched counts, duration, success/failure) --
    a reliability audit trail for the paper's evaluation section.
    """
    state = get_status()
    rows = (
        db.query(SchedulerRun)
        .order_by(SchedulerRun.run_at.desc())
        .limit(10)
        .all()
    )
    return SystemStatusOut(
        **state,
        recent_runs=[
            RunHistoryOut(
                run_at=r.run_at,
                fires_fetched=r.fires_fetched,
                clusters_found=r.clusters_found,
                alerts_created=r.alerts_created,
                success=bool(r.success),
                error_message=r.error_message,
                duration_seconds=r.duration_seconds,
            )
            for r in rows
        ],
    )


@router.post("/system/run-now")
async def run_now():
    """
    Trigger an immediate full pipeline run (ingest -> cluster -> dispatch)
    without waiting for the next scheduled interval. Used by the
    dashboard's manual refresh button.
    """
    await run_full_pipeline()
    return {"status": "completed", "detail": get_status()}
