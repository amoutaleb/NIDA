"""
NiDa — Alert Engine

For each computed fire cluster, determines WHICH registered devices fall
inside the cluster's directional alert ellipse and at WHAT severity tier,
builds a Warning Lexicon message in the device's language, and dispatches
it via FCM.

This module implements the regional-targeting requirement that motivated
the ellipse model: a device in Oran is never alerted about a fire in
Béjaïa, and a device downwind of a fire is alerted at greater distance
than an equidistant device upwind (see geo/ellipse.py).

Deduplication: a (device, cluster) pair is alerted at most once per
severity level -- re-running the engine after new satellite passes only
notifies a device again if its severity ESCALATED (e.g. advisory ->
warning as the fire grows or wind shifts toward them).
"""

import logging
from dataclasses import dataclass, field
from typing import List

from sqlalchemy.orm import Session

from backend.db.database import Alert, Device, FireClusterModel
from backend.geo.distance import haversine_km
from backend.geo.ellipse import AlertEllipse, severity_at_point
from backend.notifications.fcm import send_push
from backend.notifications.messages import build_alert_message

logger = logging.getLogger("nida.alert_engine")

_LEVEL_RANK = {"advisory": 1, "warning": 2, "critical": 3}

_TITLES = {
    "en": {"critical": "🔴 Evacuate — Wildfire", "warning": "🟠 Wildfire Warning", "advisory": "🟡 Wildfire Advisory"},
    "ar": {"critical": "🔴 إخلاء — حريق غابات", "warning": "🟠 تحذير حريق غابات", "advisory": "🟡 تنبيه حريق غابات"},
    "fr": {"critical": "🔴 Évacuez — Feu de forêt", "warning": "🟠 Alerte feu de forêt", "advisory": "🟡 Avis feu de forêt"},
}


@dataclass
class EngineRunSummary:
    clusters_evaluated: int = 0
    devices_evaluated: int = 0
    alerts_created: int = 0
    alerts_sent: int = 0
    alerts_dry_run: int = 0
    alerts_failed: int = 0
    alerts_by_level: dict = field(default_factory=lambda: {"critical": 0, "warning": 0, "advisory": 0})


def _cluster_to_ellipse(c: FireClusterModel) -> AlertEllipse:
    """Rebuild the AlertEllipse from stored cluster fields."""
    return AlertEllipse(
        centroid_lat=c.centroid_lat,
        centroid_lon=c.centroid_lon,
        semi_major_km=c.semi_major_km,
        semi_minor_km=c.semi_minor_km,
        orientation_deg=c.orientation_deg,
        lw_ratio=c.lw_ratio,
        wind_speed_kmh=c.wind_speed_kmh,
        wind_source=c.wind_source,
        is_circular_fallback=bool(c.is_circular_fallback),
    )


def _already_alerted_at_or_above(db: Session, device_id: int, cluster_id: int, level: str) -> bool:
    """True if this device already received an alert for this cluster at
    this severity level or higher (prevents duplicate/downgraded spam)."""
    existing = (
        db.query(Alert)
        .filter(Alert.device_id == device_id, Alert.fire_event_id == cluster_id)
        .all()
    )
    if not existing:
        return False
    max_existing = max(_LEVEL_RANK.get(a.level, 0) for a in existing)
    return max_existing >= _LEVEL_RANK[level]


def run_alert_engine(db: Session) -> EngineRunSummary:
    """
    Evaluate every active device against every current fire cluster's
    directional ellipse; create + dispatch alerts for devices inside.
    """
    summary = EngineRunSummary()

    clusters = db.query(FireClusterModel).all()
    devices = db.query(Device).filter(Device.active == 1).all()
    summary.clusters_evaluated = len(clusters)
    summary.devices_evaluated = len(devices)

    if not clusters or not devices:
        logger.info(f"Alert engine: nothing to do "
                    f"({len(clusters)} clusters, {len(devices)} active devices).")
        return summary

    for cluster in clusters:
        ellipse = _cluster_to_ellipse(cluster)

        for device in devices:
            level = severity_at_point(ellipse, device.latitude, device.longitude)
            if level is None:
                continue  # device is outside this cluster's alert zone entirely

            if _already_alerted_at_or_above(db, device.id, cluster.id, level):
                continue  # no duplicate / downgraded re-alerts

            distance = haversine_km(
                cluster.centroid_lat, cluster.centroid_lon,
                device.latitude, device.longitude,
            )

            lang = device.language if device.language in ("en", "ar", "fr") else "en"
            message = build_alert_message(
                level=level,
                device_lat=device.latitude,
                device_lon=device.longitude,
                cluster_lat=cluster.centroid_lat,
                cluster_lon=cluster.centroid_lon,
                distance_km=distance,
                language=lang,
            )
            title = _TITLES[lang][level]

            result = send_push(
                fcm_token=device.fcm_token,
                title=title,
                body=message,
                data={
                    "cluster_id": cluster.id,
                    "level": level,
                    "fire_lat": cluster.centroid_lat,
                    "fire_lon": cluster.centroid_lon,
                    "distance_km": round(distance, 1),
                },
            )

            if result.dry_run:
                dispatched_state = 3
                summary.alerts_dry_run += 1
            elif result.success:
                dispatched_state = 1
                summary.alerts_sent += 1
            else:
                dispatched_state = 2
                summary.alerts_failed += 1

            db.add(Alert(
                fire_event_id=cluster.id,
                device_id=device.id,
                level=level,
                message=message,
                latitude=cluster.centroid_lat,
                longitude=cluster.centroid_lon,
                radius_km=cluster.semi_major_km,
                distance_km=distance,
                dispatched=dispatched_state,
            ))
            summary.alerts_created += 1
            summary.alerts_by_level[level] += 1

    db.commit()
    logger.info(
        f"Alert engine run: {summary.alerts_created} alerts "
        f"({summary.alerts_by_level}) | sent={summary.alerts_sent} "
        f"dry_run={summary.alerts_dry_run} failed={summary.alerts_failed}"
    )
    return summary
