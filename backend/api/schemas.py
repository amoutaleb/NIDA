"""
NiDa — API Schemas (Pydantic models)
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class FireOut(BaseModel):
    id: int
    latitude: float
    longitude: float
    brightness: Optional[float] = None
    frp: Optional[float] = None
    confidence: Optional[str] = None
    satellite: Optional[str] = None
    acq_date: Optional[str] = None
    acq_time: Optional[str] = None
    daynight: Optional[str] = None
    severity_score: Optional[float] = None

    class Config:
        from_attributes = True


class AlertOut(BaseModel):
    id: int
    fire_event_id: int
    level: str
    message: str
    latitude: float
    longitude: float
    radius_km: float
    created_at: datetime

    class Config:
        from_attributes = True


class ReportIn(BaseModel):
    latitude: float = Field(..., ge=18.9, le=37.1, description="Latitude within Algeria")
    longitude: float = Field(..., ge=-8.7, le=11.9, description="Longitude within Algeria")
    description: str = Field(..., min_length=5, max_length=500)
    reporter_contact: Optional[str] = Field(None, max_length=100)


class ReportOut(BaseModel):
    id: int
    latitude: float
    longitude: float
    description: str
    created_at: datetime

    class Config:
        from_attributes = True


class ClusterOut(BaseModel):
    id: int
    centroid_lat: float
    centroid_lon: float
    point_count: int
    max_frp: float
    mean_frp: float
    has_high_confidence: bool
    semi_major_km: float
    semi_minor_km: float
    orientation_deg: float
    lw_ratio: float
    wind_speed_kmh: Optional[float] = None
    wind_source: Optional[str] = None
    is_circular_fallback: bool
    severity_score: float
    severity_level: str
    fuel_group: Optional[str] = None
    igbp_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ClusterRunResult(BaseModel):
    input_points: int
    clusters_found: int
    noise_points_filtered: int
    clusters: List[ClusterOut]


class IngestResult(BaseModel):
    fetched: int
    new_records: int
    duplicates_skipped: int
