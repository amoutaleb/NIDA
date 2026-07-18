"""
NiDa — Device Registration & Alert Dispatch Endpoints

POST /api/v1/devices/register  -> register/update a device (location + FCM token)
GET  /api/v1/devices           -> list registered devices
POST /api/v1/alerts/dispatch   -> run the alert engine against current clusters
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.db.database import Device, get_db
from backend.notifications.alert_engine import run_alert_engine

logger = logging.getLogger("nida.api.devices")

router = APIRouter()


class DeviceIn(BaseModel):
    fcm_token: str = Field(..., min_length=10, max_length=512)
    latitude: float = Field(..., ge=18.9, le=37.1)
    longitude: float = Field(..., ge=-8.7, le=11.9)
    language: str = Field("en", pattern="^(en|ar|fr)$")


class DeviceOut(BaseModel):
    id: int
    latitude: float
    longitude: float
    language: str
    registered_at: datetime
    active: bool

    class Config:
        from_attributes = True


class DispatchSummaryOut(BaseModel):
    clusters_evaluated: int
    devices_evaluated: int
    alerts_created: int
    alerts_sent: int
    alerts_dry_run: int
    alerts_failed: int
    alerts_by_level: dict


@router.post("/devices/register", response_model=DeviceOut, status_code=201)
def register_device(device: DeviceIn, db: Session = Depends(get_db)):
    """
    Register a device or update its location if the FCM token is already
    known (the mobile app calls this on startup and on significant
    location change).
    """
    existing = db.query(Device).filter(Device.fcm_token == device.fcm_token).first()
    if existing:
        existing.latitude = device.latitude
        existing.longitude = device.longitude
        existing.language = device.language
        existing.last_seen_at = datetime.utcnow()
        existing.active = 1
        db.commit()
        db.refresh(existing)
        return DeviceOut(
            id=existing.id, latitude=existing.latitude, longitude=existing.longitude,
            language=existing.language, registered_at=existing.registered_at,
            active=bool(existing.active),
        )

    record = Device(
        fcm_token=device.fcm_token,
        latitude=device.latitude,
        longitude=device.longitude,
        language=device.language,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return DeviceOut(
        id=record.id, latitude=record.latitude, longitude=record.longitude,
        language=record.language, registered_at=record.registered_at,
        active=bool(record.active),
    )


@router.get("/devices", response_model=List[DeviceOut])
def list_devices(db: Session = Depends(get_db)):
    rows = db.query(Device).filter(Device.active == 1).all()
    return [
        DeviceOut(
            id=r.id, latitude=r.latitude, longitude=r.longitude,
            language=r.language, registered_at=r.registered_at,
            active=bool(r.active),
        )
        for r in rows
    ]


@router.post("/alerts/dispatch", response_model=DispatchSummaryOut)
def dispatch_alerts(db: Session = Depends(get_db)):
    """
    Run the alert engine: evaluate all active devices against all current
    fire cluster ellipses, create tiered alerts for devices inside zones,
    and dispatch via FCM (or dry-run if Firebase credentials are absent).
    """
    summary = run_alert_engine(db)
    return DispatchSummaryOut(
        clusters_evaluated=summary.clusters_evaluated,
        devices_evaluated=summary.devices_evaluated,
        alerts_created=summary.alerts_created,
        alerts_sent=summary.alerts_sent,
        alerts_dry_run=summary.alerts_dry_run,
        alerts_failed=summary.alerts_failed,
        alerts_by_level=summary.alerts_by_level,
    )
