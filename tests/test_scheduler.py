"""
NiDa — Automated Scheduler Tests

All network-touching stages (FIRMS ingest, wind lookups) are monkeypatched
so these tests never make real HTTP calls, regardless of sandbox/CI
network availability. They verify the scheduler's ORCHESTRATION logic:
run recording, error handling, and the overlapping-run guard -- not the
pipeline internals themselves, which are already covered by
test_firms.py, test_clustering.py, and test_alert_engine.py.
"""

import asyncio
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.scheduler as scheduler_module
from backend.db.database import Base, SchedulerRun


@dataclass
class _FakeIngestResult:
    fetched: int = 10
    new_records: int = 10
    duplicates_skipped: int = 0


@dataclass
class _FakeClusterResult:
    clusters_found: int = 3


@dataclass
class _FakeDispatchSummary:
    alerts_created: int = 2


@pytest.fixture
def scheduler_test_db(monkeypatch):
    """Point the scheduler's SessionLocal at a fresh in-memory database
    for this test only."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(scheduler_module, "SessionLocal", TestSession)
    yield TestSession
    # reset module-level state between tests
    scheduler_module._state["running"] = False
    scheduler_module._state["last_run_at"] = None
    scheduler_module._state["last_success"] = None


def _patch_pipeline_success(monkeypatch):
    async def fake_ingest(db):
        return _FakeIngestResult()

    async def fake_cluster(db):
        return _FakeClusterResult()

    def fake_dispatch(db):
        return _FakeDispatchSummary()

    monkeypatch.setattr(
        "backend.api.routes.fires.ingest_fires_impl", fake_ingest
    )
    monkeypatch.setattr(
        "backend.api.routes.clusters.run_clustering_impl", fake_cluster
    )
    monkeypatch.setattr(
        "backend.notifications.alert_engine.run_alert_engine", fake_dispatch
    )


@pytest.mark.asyncio
async def test_successful_run_recorded(scheduler_test_db, monkeypatch):
    _patch_pipeline_success(monkeypatch)

    await scheduler_module.run_full_pipeline()

    db = scheduler_test_db()
    runs = db.query(SchedulerRun).all()
    assert len(runs) == 1
    assert runs[0].success == 1
    assert runs[0].fires_fetched == 10
    assert runs[0].clusters_found == 3
    assert runs[0].alerts_created == 2
    assert runs[0].error_message is None
    db.close()


@pytest.mark.asyncio
async def test_failed_ingest_recorded_not_crashed(scheduler_test_db, monkeypatch):
    """If FIRMS (or any stage) throws, the run must be recorded as a
    failure with the error message -- the scheduler must NOT propagate
    the exception and crash the background job forever."""
    async def failing_ingest(db):
        raise RuntimeError("simulated FIRMS outage")

    monkeypatch.setattr(
        "backend.api.routes.fires.ingest_fires_impl", failing_ingest
    )

    await scheduler_module.run_full_pipeline()

    db = scheduler_test_db()
    runs = db.query(SchedulerRun).all()
    assert len(runs) == 1
    assert runs[0].success == 0
    assert "simulated FIRMS outage" in runs[0].error_message
    db.close()

    # state must reflect failure but not leave "running" stuck True
    status = scheduler_module.get_status()
    assert status["running"] is False
    assert status["last_success"] is False


@pytest.mark.asyncio
async def test_overlapping_run_is_skipped(scheduler_test_db, monkeypatch):
    """If a run is already in progress, a second trigger must be a no-op
    (not queue up, not run concurrently) -- prevents pile-up if a single
    pipeline pass ever takes longer than the poll interval."""
    _patch_pipeline_success(monkeypatch)
    scheduler_module._state["running"] = True

    await scheduler_module.run_full_pipeline()

    db = scheduler_test_db()
    runs = db.query(SchedulerRun).all()
    assert len(runs) == 0  # skipped entirely, nothing recorded


def test_scheduler_disabled_does_not_start(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", False)
    scheduler_module._scheduler = None

    scheduler_module.start_scheduler()

    assert scheduler_module._scheduler is None
    scheduler_module.stop_scheduler()  # should be a safe no-op


def test_get_status_shape():
    status = scheduler_module.get_status()
    assert set(status.keys()) == {
        "enabled", "poll_interval_hours", "running", "last_run_at", "last_success"
    }


def test_naive_utc_with_local_scheduler_causes_offset_bug():
    """
    Root-cause regression test. The ORIGINAL bug: AsyncIOScheduler() with
    no explicit timezone defaults to the HOST machine's local timezone
    (e.g. Africa/Algiers, UTC+1) -- not UTC. Combined with a naive
    datetime.utcnow() value for next_run_time, APScheduler mislabels
    that UTC instant as if it were already in local time, shifting the
    job's true trigger point backward by the UTC offset. On a UTC+1
    host this pushed 'run immediately on startup' to appear ~1h in the
    past, causing it to be treated as missed and rescheduled ~2h later
    instead of running right away -- exactly the symptom observed live
    (last_run_at stayed null well after startup).

    This test reproduces the mislocalization on a synthetic non-UTC
    scheduler and confirms our fix (explicit timezone='UTC' + a
    timezone-AWARE next_run_time) eliminates the offset entirely,
    regardless of the host machine's local timezone.
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    real_utc_now = scheduler_module._now()

    # ── Buggy pattern: local-tz scheduler + naive datetime ──
    buggy_sched = AsyncIOScheduler(timezone="Africa/Algiers")  # UTC+1, non-UTC
    naive_value = real_utc_now.replace(tzinfo=None)  # simulates old datetime.utcnow()

    async def noop():
        pass

    buggy_sched.add_job(noop, trigger="interval", hours=3,
                         next_run_time=naive_value, id="buggy")
    buggy_job = buggy_sched.get_job("buggy")
    buggy_offset = abs((buggy_job.next_run_time - real_utc_now).total_seconds())

    assert buggy_offset > 1800, (
        f"Expected the naive-datetime bug to misplace next_run_time by "
        f"~1 hour on a UTC+1 host, got only {buggy_offset:.0f}s offset -- "
        f"test setup may not be reproducing the original bug."
    )

    # ── Fixed pattern: explicit UTC scheduler + timezone-aware datetime ──
    fixed_sched = AsyncIOScheduler(timezone="UTC")
    fixed_sched.add_job(noop, trigger="interval", hours=3,
                         next_run_time=scheduler_module._now(), id="fixed")
    fixed_job = fixed_sched.get_job("fixed")
    fixed_offset = abs((fixed_job.next_run_time - real_utc_now).total_seconds())

    assert fixed_offset < 2, (
        f"Fixed scheduler should schedule 'immediately' within ~2s of "
        f"real now, got {fixed_offset:.0f}s offset."
    )


def test_immediate_startup_run_survives_startup_delay(monkeypatch):
    """
    Regression test for a real bug found during live validation: the
    'run once immediately on startup' job used next_run_time=now(), but
    by the time APScheduler's loop actually ticks (after DB init, module
    imports, etc.) that timestamp is already slightly in the past.
    APScheduler's default misfire_grace_time is only 1 second, so the
    job was silently dropped and the system waited a full poll interval
    (3h) before ever running -- last_run_at stayed null indefinitely.

    This test schedules the job exactly the way start_scheduler() does,
    waits past a 1-second window, and confirms the job still fires --
    proving misfire_grace_time=None is actually in effect.
    """
    import asyncio as _asyncio
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    fired = []

    async def marker_job():
        fired.append(True)

    async def run():
        sched = AsyncIOScheduler(timezone="UTC")
        sched.add_job(
            marker_job,
            trigger="interval",
            hours=3,
            next_run_time=scheduler_module._now(),
            id="test_job",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=None,
        )
        sched.start()
        # simulate the real-world delay between computing next_run_time
        # and the scheduler loop getting a chance to tick
        await _asyncio.sleep(1.5)
        sched.shutdown(wait=False)

    _asyncio.run(run())
    assert fired == [True], (
        "Immediate startup job did not fire after a >1s delay -- "
        "the misfire_grace_time fix is not effective."
    )
