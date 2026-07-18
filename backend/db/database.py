"""
NiDa — Database Models & Connection
Phase 1 uses SQLite for zero-setup local development.
Phase 2 switches to PostgreSQL + PostGIS via DATABASE_URL (no code changes).
"""

from datetime import datetime

from sqlalchemy import (
    Column, DateTime, Float, Integer, String, create_engine
)
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class FireEvent(Base):
    """A single VIIRS fire detection ingested from NASA FIRMS."""
    __tablename__ = "fire_events"

    id = Column(Integer, primary_key=True, index=True)
    latitude = Column(Float, nullable=False, index=True)
    longitude = Column(Float, nullable=False, index=True)
    brightness = Column(Float)            # bright_ti4 (Kelvin)
    frp = Column(Float)                   # Fire Radiative Power (MW)
    confidence = Column(String(10))       # 'n' / 'h'
    satellite = Column(String(10))
    acq_date = Column(String(10), index=True)
    acq_time = Column(String(4))
    daynight = Column(String(1))
    cluster_id = Column(Integer, nullable=True, index=True)   # Phase 2
    severity_score = Column(Float, nullable=True)             # Phase 2
    ingested_at = Column(DateTime, default=datetime.utcnow)


class FireClusterModel(Base):
    """A clustered fire event (Phase 2 output) with its directional alert ellipse."""
    __tablename__ = "fire_clusters"

    id = Column(Integer, primary_key=True, index=True)
    centroid_lat = Column(Float, nullable=False)
    centroid_lon = Column(Float, nullable=False)
    point_count = Column(Integer)
    max_frp = Column(Float)
    mean_frp = Column(Float)
    has_high_confidence = Column(Integer, default=0)
    # Ellipse geometry
    semi_major_km = Column(Float)
    semi_minor_km = Column(Float)
    orientation_deg = Column(Float)
    lw_ratio = Column(Float)
    wind_speed_kmh = Column(Float, nullable=True)
    wind_source = Column(String(30), nullable=True)
    is_circular_fallback = Column(Integer, default=0)
    fuel_group = Column(String(30), nullable=True)   # forest/shrubland/savanna/grassland_cropland/other
    igbp_name = Column(String(50), nullable=True)     # dominant land cover class name
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class Device(Base):
    """A registered user device (Phase 3): location + FCM push token.
    Location is the device's last known position, updated by the mobile
    app. Alert targeting tests this position against each fire cluster's
    directional ellipse."""
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    fcm_token = Column(String(512), unique=True, nullable=False, index=True)
    latitude = Column(Float, nullable=False, index=True)
    longitude = Column(Float, nullable=False, index=True)
    language = Column(String(5), default="en")     # 'en' / 'ar' / 'fr'
    registered_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)
    active = Column(Integer, default=1)


class Alert(Base):
    """An alert generated for a (device, fire cluster) pair by the Phase 3
    alert engine. dispatched tracks FCM delivery state."""
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    fire_event_id = Column(Integer, index=True)        # FireClusterModel.id
    device_id = Column(Integer, index=True, nullable=True)
    level = Column(String(20))            # 'critical' / 'warning' / 'advisory'
    message = Column(String(500))
    latitude = Column(Float)              # cluster centroid
    longitude = Column(Float)
    radius_km = Column(Float)             # ellipse semi-major (for reference)
    distance_km = Column(Float, nullable=True)  # device distance from centroid
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    dispatched = Column(Integer, default=0)   # 0 = pending, 1 = sent, 2 = failed, 3 = dry-run


class UserReport(Base):
    """A citizen-submitted fire report (POST /report)."""
    __tablename__ = "user_reports"

    id = Column(Integer, primary_key=True, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    description = Column(String(500))
    reporter_contact = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    verified = Column(Integer, default=0)


class ArchivedFireEvent(Base):
    """A fire detection older than ACTIVE_RETENTION_DAYS, moved (not
    deleted) out of the live table by the automated rollover job. Same
    schema as FireEvent plus the archival timestamp."""
    __tablename__ = "archived_fire_events"

    id = Column(Integer, primary_key=True, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    brightness = Column(Float)
    frp = Column(Float)
    confidence = Column(String(10))
    satellite = Column(String(10))
    acq_date = Column(String(10), index=True)
    acq_time = Column(String(4))
    daynight = Column(String(1))
    ingested_at = Column(DateTime)
    archived_at = Column(DateTime, default=datetime.utcnow)


class ArchivedFireCluster(Base):
    """A snapshot of a computed fire cluster, taken every pipeline run
    just before the live cluster table is rebuilt. Freezes the cluster's
    state (zone geometry, wind, severity) AS IT WAS at that moment --
    giving the archive page a 3-hourly historical record of how each
    fire evolved, which the live table (rebuilt each run) cannot provide."""
    __tablename__ = "archived_fire_clusters"

    id = Column(Integer, primary_key=True, index=True)
    live_cluster_id = Column(Integer)          # the id it had in the live table
    centroid_lat = Column(Float, nullable=False)
    centroid_lon = Column(Float, nullable=False)
    point_count = Column(Integer)
    max_frp = Column(Float)
    mean_frp = Column(Float)
    has_high_confidence = Column(Integer, default=0)
    semi_major_km = Column(Float)
    semi_minor_km = Column(Float)
    orientation_deg = Column(Float)
    lw_ratio = Column(Float)
    wind_speed_kmh = Column(Float, nullable=True)
    wind_source = Column(String(30), nullable=True)
    is_circular_fallback = Column(Integer, default=0)
    severity_level = Column(String(20))        # frozen at snapshot time
    severity_score = Column(Float)
    cluster_created_at = Column(DateTime)      # when the live cluster was computed
    snapshot_at = Column(DateTime, default=datetime.utcnow, index=True)


class SchedulerRun(Base):
    """History of automated pipeline runs (ingest -> cluster -> dispatch),
    written by backend/scheduler.py every time it fires. Powers the
    /api/v1/system/status endpoint and gives the paper's evaluation
    section a real uptime/reliability audit trail."""
    __tablename__ = "scheduler_runs"

    id = Column(Integer, primary_key=True, index=True)
    run_at = Column(DateTime, default=datetime.utcnow, index=True)
    fires_fetched = Column(Integer, default=0)
    clusters_found = Column(Integer, default=0)
    alerts_created = Column(Integer, default=0)
    success = Column(Integer, default=1)   # 0 = failed
    error_message = Column(String(500), nullable=True)
    duration_seconds = Column(Float, nullable=True)


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
