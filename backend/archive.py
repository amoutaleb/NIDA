"""
NiDa — Data Lifecycle: Archive Rollover

Moves fire detections older than ACTIVE_RETENTION_DAYS (default 10) from
the live fire_events table into archived_fire_events. Data is MOVED,
never deleted -- the archive preserves the complete historical record
for the /archive browser and for the paper's Phase 5 evaluation, while
keeping the live table (and therefore the live /map and clustering
input) bounded and fast.

Cluster snapshots are handled separately: run_clustering_impl() copies
the outgoing cluster set into archived_fire_clusters on every pipeline
run, because the live cluster table is fully rebuilt each cycle and
would otherwise retain no history at all.

Retention is judged on acq_date (the satellite acquisition date), not
ingested_at, so a late-arriving detection of an old fire is still
classified by when the fire actually burned.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.config import settings
from backend.db.database import ArchivedFireEvent, FireEvent

logger = logging.getLogger("nida.archive")


def rollover_old_detections(db: Session) -> int:
    """
    Move fire detections with acq_date older than the retention window
    into the archive table. Returns the number of rows moved.
    """
    cutoff_date = (
        datetime.now(timezone.utc) - timedelta(days=settings.ACTIVE_RETENTION_DAYS)
    ).strftime("%Y-%m-%d")

    old_rows = db.query(FireEvent).filter(FireEvent.acq_date < cutoff_date).all()
    if not old_rows:
        return 0

    for r in old_rows:
        db.add(ArchivedFireEvent(
            latitude=r.latitude,
            longitude=r.longitude,
            brightness=r.brightness,
            frp=r.frp,
            confidence=r.confidence,
            satellite=r.satellite,
            acq_date=r.acq_date,
            acq_time=r.acq_time,
            daynight=r.daynight,
            ingested_at=r.ingested_at,
        ))
        db.delete(r)

    db.commit()
    logger.info(
        f"Archive rollover: moved {len(old_rows)} detections older than "
        f"{cutoff_date} (retention={settings.ACTIVE_RETENTION_DAYS} days) "
        f"to archived_fire_events."
    )
    return len(old_rows)
