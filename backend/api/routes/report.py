"""
NiDa — Citizen Fire Reports
POST /api/v1/report -> citizen submits a fire sighting with GPS coordinates
GET  /api/v1/reports -> list submitted reports
"""

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.schemas import ReportIn, ReportOut
from backend.db.database import UserReport, get_db

router = APIRouter()


@router.post("/report", response_model=ReportOut, status_code=201)
def submit_report(report: ReportIn, db: Session = Depends(get_db)):
    """
    Accept a citizen-submitted fire report.
    Coordinates are validated to fall within Algeria's bounding box
    by the ReportIn schema.
    """
    record = UserReport(
        latitude=report.latitude,
        longitude=report.longitude,
        description=report.description,
        reporter_contact=report.reporter_contact,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("/reports", response_model=List[ReportOut])
def list_reports(db: Session = Depends(get_db), limit: int = 100):
    return (
        db.query(UserReport)
        .order_by(UserReport.created_at.desc())
        .limit(limit)
        .all()
    )
