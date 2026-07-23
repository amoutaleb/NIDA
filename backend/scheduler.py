"""
NiDa — Automated Pipeline Scheduler

Runs the full ingest -> cluster -> dispatch pipeline automatically on a
fixed interval (FIRMS_POLL_INTERVAL_HOURS, default 3h, matching the
research proposal's Section 4.2 data pipeline specification). Before
this module existed, the pipeline only advanced when a human manually
ran curl commands or clicked the dashboard's refresh button -- meaning
the "live" system was not actually live between manual triggers.

Design notes:
  - Uses APScheduler's AsyncIOScheduler, integrated into FastAPI's
    lifespan so it starts/stops cleanly with the server process.
  - A concurrency guard (_state["running"]) prevents overlapping runs if
    one pipeline pass takes longer than the poll interval.
  - Every run (success or failure) is recorded to the scheduler_runs
    table, giving a persistent, queryable reliability record --
    directly useful for the paper's evaluation section (e.g. uptime,
    mean run duration, failure rate over the validation period).
  - A failure in one stage (e.g. FIRMS temporarily down) is caught and
    logged rather than crashing the process; the next scheduled run
    tries again.
  - SCHEDULER_ENABLED=false disables this entirely (used for tests/CI
    so pytest runs never trigger real network calls in the background).
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.config import settings
from backend.db.database import SchedulerRun, SessionLocal

logger = logging.getLogger("nida.scheduler")

_scheduler: Optional[AsyncIOScheduler] = None
_state = {"running": False, "last_run_at": None, "last_success": None}


def _now():
    return datetime.now(timezone.utc)


async def run_full_pipeline() -> None:
    """
    One complete automated cycle: ingest new FIRMS data, recluster,
    dispatch alerts to any devices newly inside a fire's zone. Records
    the outcome to SchedulerRun regardless of success or failure.
    """
    if _state["running"]:
        logger.warning("Pipeline run already in progress; skipping this trigger "
                        "(previous run is taking longer than the poll interval).")
        return

    # Imported here (not at module load) to avoid a circular import:
    # these route modules import backend.main indirectly via the app,
    # and backend.main imports this scheduler module to start it.
    from backend.api.routes.clusters import run_clustering_impl
    from backend.api.routes.fires import ingest_fires_impl
    from backend.notifications.alert_engine import run_alert_engine

    _state["running"] = True
    start = time.time()
    db = SessionLocal()
    error_message = None
    fires_fetched = clusters_found = alerts_created = 0

    try:
        ingest_result = await ingest_fires_impl(db)
        fires_fetched = ingest_result.fetched

        cluster_result = await run_clustering_impl(db)
        clusters_found = cluster_result.clusters_found

        dispatch_summary = run_alert_engine(db)
        alerts_created = dispatch_summary.alerts_created

        # Data lifecycle: move detections older than the retention window
        # to the archive (moved, never deleted). Runs last so it can never
        # interfere with the alerting stages of the same cycle.
        from backend.archive import rollover_old_detections
        rollover_old_detections(db)

    except Exception as exc:
        error_message = str(exc)[:500]
        logger.exception("Automated pipeline run failed")

    finally:
        duration = time.time() - start
        try:
            db.add(SchedulerRun(
                run_at=_now(),
                fires_fetched=fires_fetched,
                clusters_found=clusters_found,
                alerts_created=alerts_created,
                success=0 if error_message else 1,
                error_message=error_message,
                duration_seconds=duration,
            ))
            db.commit()
        except Exception:
            logger.exception("Failed to record SchedulerRun history entry")
        finally:
            db.close()

        _state["running"] = False
        _state["last_run_at"] = _now()
        _state["last_success"] = error_message is None

        logger.info(
            f"Automated pipeline run finished in {duration:.1f}s "
            f"(fires={fires_fetched}, clusters={clusters_found}, "
            f"alerts={alerts_created}, success={error_message is None})"
        )


def start_scheduler() -> None:
    """Start the recurring job. Also fires one immediate run on startup
    so the system has current data right away rather than waiting a
    full poll interval after a fresh deploy."""
    global _scheduler
    if not settings.SCHEDULER_ENABLED:
        logger.info("Automated scheduler disabled (SCHEDULER_ENABLED=false).")
        return
    if _scheduler is not None:
        return  # already running

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        run_full_pipeline,
        trigger="interval",
        hours=settings.FIRMS_POLL_INTERVAL_HOURS,
        next_run_time=_now(),   # run once immediately, then every interval
        id="nida_pipeline",
        max_instances=1,
        coalesce=True,
        # CRITICAL: without this, APScheduler's default 1-second misfire
        # tolerance silently drops the "run immediately on startup" job
        # if there's ANY delay (DB init, module imports, etc.) between
        # computing next_run_time and the scheduler's loop actually
        # ticking -- which there always is. Discovered when live
        # validation showed last_run_at staying null indefinitely after
        # startup. misfire_grace_time=None means "always run it, however
        # late" for this job specifically.
        misfire_grace_time=None,
    )
    _scheduler.start()
    logger.info(
        f"Automated scheduler started: pipeline runs every "
        f"{settings.FIRMS_POLL_INTERVAL_HOURS}h (first run triggered now)."
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Automated scheduler stopped.")


def get_status() -> dict:
    """Current in-memory scheduler state, for the /system/status endpoint."""
    return {
        "enabled": settings.SCHEDULER_ENABLED,
        "poll_interval_hours": settings.FIRMS_POLL_INTERVAL_HOURS,
        "running": _state["running"],
        "last_run_at": _state["last_run_at"],
        "last_success": _state["last_success"],
    }
