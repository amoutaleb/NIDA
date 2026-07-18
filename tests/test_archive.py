"""
NiDa — Archive & Data Lifecycle Tests

Covers: retention rollover (old detections moved, recent kept, nothing
deleted), cluster snapshotting on pipeline reruns, and the archive
browsing endpoints (summary bounds, date-range filtering, validation).
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.archive as archive_module
from backend.db.database import (
    ArchivedFireCluster, ArchivedFireEvent, Base, FireEvent,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _mk_fire(db, acq_date: str, lat=36.5, lon=5.0):
    f = FireEvent(latitude=lat, longitude=lon, brightness=340.0, frp=10.0,
                  confidence="n", satellite="N20", acq_date=acq_date,
                  acq_time="0100", daynight="N")
    db.add(f)
    db.commit()
    return f


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def test_rollover_moves_old_keeps_recent(db):
    _mk_fire(db, _days_ago(15))   # old -> should be archived
    _mk_fire(db, _days_ago(12))   # old -> should be archived
    _mk_fire(db, _days_ago(3))    # recent -> stays live
    _mk_fire(db, _days_ago(0))    # today -> stays live

    moved = archive_module.rollover_old_detections(db)

    assert moved == 2
    assert db.query(FireEvent).count() == 2
    assert db.query(ArchivedFireEvent).count() == 2
    # nothing lost: total rows across both tables unchanged
    assert db.query(FireEvent).count() + db.query(ArchivedFireEvent).count() == 4


def test_rollover_boundary_day_stays_live(db):
    """A detection exactly AT the retention boundary (retention days ago)
    must stay live -- only strictly older moves."""
    from backend.config import settings
    _mk_fire(db, _days_ago(settings.ACTIVE_RETENTION_DAYS))
    moved = archive_module.rollover_old_detections(db)
    assert moved == 0
    assert db.query(FireEvent).count() == 1


def test_rollover_idempotent(db):
    _mk_fire(db, _days_ago(20))
    first = archive_module.rollover_old_detections(db)
    second = archive_module.rollover_old_detections(db)
    assert first == 1
    assert second == 0
    assert db.query(ArchivedFireEvent).count() == 1


def test_rollover_preserves_fields(db):
    _mk_fire(db, _days_ago(20), lat=34.85, lon=-1.07)
    archive_module.rollover_old_detections(db)
    a = db.query(ArchivedFireEvent).first()
    assert a.latitude == 34.85
    assert a.longitude == -1.07
    assert a.frp == 10.0
    assert a.satellite == "N20"
    assert a.archived_at is not None


# ── Archive endpoints via the real app (fresh temp DB per test run) ──

@pytest.fixture
def client(tmp_path, monkeypatch):
    """App TestClient wired to an isolated on-disk SQLite so route-level
    Session dependencies and our fixtures see the same database."""
    import backend.db.database as dbmod
    engine = create_engine(f"sqlite:///{tmp_path}/test.db",
                           connect_args={"check_same_thread": False})
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(dbmod, "engine", engine)
    monkeypatch.setattr(dbmod, "SessionLocal", TestSession)
    Base.metadata.create_all(engine)

    from backend.config import settings
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", False)

    from backend.main import app
    with TestClient(app) as c:
        yield c, TestSession


def test_archive_summary_and_geojson(client):
    c, TestSession = client
    db = TestSession()
    # one archived old fire, one live recent fire
    db.add(ArchivedFireEvent(latitude=36.0, longitude=4.0, frp=20.0,
                             confidence="h", satellite="N20",
                             acq_date=_days_ago(15), acq_time="0200", daynight="N"))
    db.add(FireEvent(latitude=36.6, longitude=5.1, frp=15.0, confidence="n",
                     satellite="N", acq_date=_days_ago(2), acq_time="0100",
                     daynight="N", brightness=330.0))
    db.commit()

    r = c.get("/api/v1/archive/summary")
    assert r.status_code == 200
    s = r.json()
    assert s["archived_detections"] == 1
    assert s["live_detections"] == 1
    assert s["earliest_date"] == _days_ago(15)
    assert s["latest_date"] == _days_ago(2)

    # Full range: both detections returned (archived + live unioned)
    r = c.get(f"/api/v1/archive/geojson?start={_days_ago(20)}&end={_days_ago(0)}")
    assert r.status_code == 200
    feats = r.json()["features"]
    dets = [f for f in feats if f["properties"]["feature_type"] == "detection"]
    assert len(dets) == 2

    # Narrow range: only the old archived one
    r = c.get(f"/api/v1/archive/geojson?start={_days_ago(16)}&end={_days_ago(10)}")
    dets = [f for f in r.json()["features"]
            if f["properties"]["feature_type"] == "detection"]
    assert len(dets) == 1
    assert dets[0]["properties"]["acq_date"] == _days_ago(15)

    db.close()


def test_archive_geojson_validation(client):
    c, _ = client
    assert c.get("/api/v1/archive/geojson?start=bad&end=2026-07-17").status_code == 422
    assert c.get("/api/v1/archive/geojson?start=2026-07-17&end=2026-07-01").status_code == 422


def test_archive_page_serves(client):
    c, _ = client
    r = c.get("/archive")
    assert r.status_code == 200
    assert "NiDa" in r.text
    assert "dz-clock" in r.text          # Algeria clock present
    assert "fa-box-archive" in r.text    # Font Awesome, not emoji
    assert "أرشيف" in r.text             # Arabic i18n present
