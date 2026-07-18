"""
NiDa — Alert Engine Tests

Includes the canonical regional-targeting scenario that motivated the
system design: a fire near Béjaïa must alert a device in Béjaïa and
must NOT alert a device in Oran (~430 km away).
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.database import Alert, Base, Device, FireClusterModel
from backend.notifications.alert_engine import run_alert_engine
from backend.notifications.messages import build_alert_message


@pytest.fixture
def db():
    """Fresh in-memory database per test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _bejaia_cluster(db, semi_major=12.0, semi_minor=6.0, orientation=90.0):
    """A fire cluster just west of Béjaïa city, ellipse pointing east."""
    c = FireClusterModel(
        centroid_lat=36.75, centroid_lon=4.95,
        point_count=20, max_frp=250.0, mean_frp=120.0,
        has_high_confidence=1,
        semi_major_km=semi_major, semi_minor_km=semi_minor,
        orientation_deg=orientation, lw_ratio=2.0,
        wind_speed_kmh=15.0, wind_source="open-meteo",
        is_circular_fallback=0,
    )
    db.add(c)
    db.commit()
    return c


def test_bejaia_device_alerted_oran_device_not(db):
    """THE core scenario: regional targeting."""
    _bejaia_cluster(db)

    bejaia_device = Device(fcm_token="token-bejaia-user-000001",
                           latitude=36.752, longitude=5.05, language="en")
    oran_device = Device(fcm_token="token-oran-user-00000002",
                         latitude=35.6971, longitude=-0.6308, language="en")
    db.add_all([bejaia_device, oran_device])
    db.commit()

    summary = run_alert_engine(db)

    assert summary.devices_evaluated == 2
    assert summary.alerts_created == 1  # ONLY the Béjaïa device

    alerts = db.query(Alert).all()
    assert len(alerts) == 1
    assert alerts[0].device_id == bejaia_device.id
    # Oran device has no alerts
    oran_alerts = db.query(Alert).filter(Alert.device_id == oran_device.id).all()
    assert oran_alerts == []


def test_downwind_device_alerted_upwind_not(db):
    """Directional behavior: ellipse points east (orientation=90).
    A device 8km east (downwind) is inside; a device 8km west (upwind,
    beyond the backing reach) is not."""
    _bejaia_cluster(db, semi_major=12.0, semi_minor=4.0, orientation=90.0)

    downwind = Device(fcm_token="token-downwind-000000001",
                      latitude=36.75, longitude=5.04, language="en")   # ~8km east
    upwind = Device(fcm_token="token-upwind-00000000001",
                    latitude=36.75, longitude=4.86, language="en")     # ~8km west
    db.add_all([downwind, upwind])
    db.commit()

    summary = run_alert_engine(db)

    downwind_alerts = db.query(Alert).filter(Alert.device_id == downwind.id).count()
    upwind_alerts = db.query(Alert).filter(Alert.device_id == upwind.id).count()
    assert downwind_alerts == 1
    assert upwind_alerts == 0


def test_no_duplicate_alerts_on_rerun(db):
    """Running the engine twice must not re-alert the same device for
    the same cluster at the same level."""
    _bejaia_cluster(db)
    db.add(Device(fcm_token="token-bejaia-user-000001",
                  latitude=36.752, longitude=5.05, language="en"))
    db.commit()

    first = run_alert_engine(db)
    second = run_alert_engine(db)

    assert first.alerts_created == 1
    assert second.alerts_created == 0
    assert db.query(Alert).count() == 1


def test_arabic_message_generated_for_arabic_device(db):
    _bejaia_cluster(db)
    db.add(Device(fcm_token="token-arabic-user-0000001",
                  latitude=36.752, longitude=5.05, language="ar"))
    db.commit()

    run_alert_engine(db)
    alert = db.query(Alert).first()
    assert "NiDa" in alert.message
    assert "حريق" in alert.message  # Arabic word for fire


def test_alert_records_distance(db):
    _bejaia_cluster(db)
    db.add(Device(fcm_token="token-bejaia-user-000001",
                  latitude=36.752, longitude=5.05, language="en"))
    db.commit()

    run_alert_engine(db)
    alert = db.query(Alert).first()
    assert alert.distance_km is not None
    assert 5 <= alert.distance_km <= 12  # ~9km from centroid


def test_dry_run_mode_marks_alerts(db):
    """Without Firebase credentials (test environment), alerts must be
    marked dispatched=3 (dry-run), not 1 (sent) or 2 (failed)."""
    _bejaia_cluster(db)
    db.add(Device(fcm_token="token-bejaia-user-000001",
                  latitude=36.752, longitude=5.05, language="en"))
    db.commit()

    summary = run_alert_engine(db)
    assert summary.alerts_dry_run == 1
    assert summary.alerts_sent == 0
    alert = db.query(Alert).first()
    assert alert.dispatched == 3


def test_message_structure_follows_warning_lexicon():
    """Message must contain: source, hazard, distance, direction, action."""
    msg = build_alert_message(
        level="critical",
        device_lat=36.752, device_lon=5.05,
        cluster_lat=36.75, cluster_lon=4.95,
        distance_km=8.9, language="en",
    )
    assert "NiDa" in msg                    # authoritative source
    assert "wildfire" in msg.lower()        # hazard
    assert "8.9 km" in msg                  # location: distance
    assert "W" in msg                       # location: direction (fire is west of device)
    assert "Leave now" in msg  # protective action
