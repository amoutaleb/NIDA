"""
NiDa — Alerts Endpoints
GET /api/v1/alerts -> list recent alerts (Phase 3 populates these automatically)
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.api.schemas import AlertOut
from backend.db.database import Alert, get_db

router = APIRouter()


@router.get("/alerts", response_model=List[AlertOut])
def list_alerts(
    db: Session = Depends(get_db),
    level: Optional[str] = Query(None, description="critical / warning / advisory"),
    limit: int = Query(100, le=1000),
):
    """Return recent alerts, newest first."""
    q = db.query(Alert)
    if level:
        q = q.filter(Alert.level == level)
    return q.order_by(Alert.created_at.desc()).limit(limit).all()
