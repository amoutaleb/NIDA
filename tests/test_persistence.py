"""
NiDa — Static (Industrial) Source Detection Tests

The decisive property is recall safety: an industrial installation must be
suppressed, but a genuine wildfire must survive even when it burns in the
same area for several days. These tests exercise each arm of the
three-part signature (persistence, low intensity, spatial isolation).
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.database import Base
from backend.geo.persistence import (
    _cell, build_static_source_cells, filter_static_sources,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _d(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _batch(rows):
    """rows: list of (lat, lon, acq_date, frp)"""
    return pd.DataFrame(
        [{"latitude": r[0], "longitude": r[1], "acq_date": r[2], "frp": r[3]} for r in rows]
    )


# ── the industrial case: persistent, low power, isolated ──

def test_factory_signature_is_flagged(db):
    """A cement plant: same cell every day, modest steady FRP, no spread."""
    rows = [(35.7500, 5.3000, _d(k), 8.0) for k in range(3)]
    cells = build_static_source_cells(db, _batch(rows))
    assert _cell(35.7500, 5.3000) in cells


def test_factory_detections_are_dropped(db):
    rows = [(35.7500, 5.3000, _d(k), 8.0) for k in range(3)]        # factory
    rows += [(36.8000, 4.5000, _d(0), 450.0)]                        # real fire
    kept, dropped = filter_static_sources(db, _batch(rows))
    assert dropped == 3
    assert set(kept["latitude"]) == {36.8000}


# ── recall safety: real fires must survive each way ──

def test_intense_fire_in_one_place_survives(db):
    """High FRP alone must protect a fire that burns days in one spot."""
    rows = [(36.7000, 4.0500, _d(k), 800.0) for k in range(3)]
    cells = build_static_source_cells(db, _batch(rows))
    assert _cell(36.7000, 4.0500) not in cells


def test_spreading_fire_survives(db):
    """A fire lighting many adjacent cells fails the isolation test even
    at low FRP and full persistence."""
    base_lat, base_lon = 36.6000, 4.2000
    rows = []
    for di in range(-1, 2):
        for dj in range(-1, 2):
            lat = base_lat + di * 0.004
            lon = base_lon + dj * 0.004
            rows += [(lat, lon, _d(k), 30.0) for k in range(3)]
    cells = build_static_source_cells(db, _batch(rows))
    assert _cell(base_lat, base_lon) not in cells


def test_brief_fire_survives(db):
    """A one-day flare-up is not persistent, so it is never flagged."""
    rows = [(36.9000, 5.1000, _d(0), 20.0)]
    cells = build_static_source_cells(db, _batch(rows))
    assert _cell(36.9000, 5.1000) not in cells


# ── safe degradation ──

def test_empty_batch_is_noop(db):
    kept, dropped = filter_static_sources(db, pd.DataFrame(
        {"latitude": [], "longitude": [], "acq_date": [], "frp": []}))
    assert dropped == 0


def test_disabled_is_noop(db, monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "PERSISTENCE_FILTER_ENABLED", False)
    rows = [(35.7500, 5.3000, _d(k), 8.0) for k in range(3)]
    kept, dropped = filter_static_sources(db, _batch(rows))
    assert dropped == 0


def test_threshold_not_above_fetch_window():
    """Regression: if the persistence threshold exceeds the number of days
    fetched, the filter can never trigger on a fresh database."""
    from backend.config import settings
    assert settings.PERSISTENCE_MIN_DISTINCT_DAYS <= settings.FIRMS_DAY_RANGE
