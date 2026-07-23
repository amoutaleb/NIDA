"""
NiDa — Static (Industrial) Thermal Source Detection

Wildfires are transient and spatially dynamic: they ignite, spread across
neighbouring ground, and burn out. Industrial heat sources -- cement
kilns, power stations, refineries, gas flares -- are the opposite: the
same fixed point radiates day after day, at a stable and comparatively
modest intensity, without spreading. This module exploits that contrast
to identify and suppress industrial false positives, which the land-cover
filter cannot catch in the populated north (those facilities sit on land
classified as urban or cropland, not barren desert).

Method
------
Detections are binned onto a ~400 m grid, matching the resolution of
NASA's Static Thermal Anomalies (STA) mask. NASA builds that mask by
flagging cells with repeated detections over time; the mask itself is not
distributed for reuse, so NiDa computes an equivalent classification from
its own data. A cell is classified as a STATIC INDUSTRIAL SOURCE only
when all three of the following hold:

  1. PERSISTENCE  -- detections on at least `PERSISTENCE_MIN_DISTINCT_DAYS`
     distinct acquisition dates. A fixed installation registers every day;
     a given 400 m cell of a moving fire front usually does not.

  2. LOW INTENSITY -- peak Fire Radiative Power in the cell does not
     exceed `STATIC_MAX_FRP_MW`. Industrial sources radiate steadily at
     modest power (typically well under 100 MW), whereas the wildfires
     this system exists to warn about reach hundreds to thousands of MW.

  3. SPATIAL ISOLATION -- few neighbouring cells are also alight
     (at most `STATIC_MAX_NEIGHBOUR_CELLS` of the surrounding eight).
     A spreading fire illuminates a contiguous patch of ground; a factory
     is a solitary hot pixel.

Requiring all three is a deliberate recall-first choice: a large or
fast-moving fire fails criteria 2 and 3 even if it burns in one place for
several days, so it can never be mistaken for infrastructure. The cost is
that a small, low-power, stationary *real* fire persisting for days may be
suppressed; that trade is acceptable because such a fire is both rare and
far less dangerous than the class of events NiDa targets.

Analysis is performed over the incoming detection batch combined with
stored history, so the classification works from the first run rather than
requiring days of accumulated archive.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Set, Tuple

from sqlalchemy.orm import Session

from backend.config import settings
from backend.db.database import ArchivedFireEvent, FireEvent

logger = logging.getLogger("nida.geo.persistence")

# ~400 m grid at Algerian latitudes (0.004 deg ~ 0.44 km of latitude),
# matching the cell size NASA uses for static thermal anomalies.
GRID_DEG = 0.004

_Cell = Tuple[int, int]


def _cell(lat: float, lon: float) -> _Cell:
    """Grid-cell index for a coordinate."""
    return (int(lat / GRID_DEG), int(lon / GRID_DEG))


def _neighbour_count(cell: _Cell, occupied: Set[_Cell]) -> int:
    """How many of the eight surrounding cells also contain detections."""
    i, j = cell
    return sum(
        (i + di, j + dj) in occupied
        for di in (-1, 0, 1) for dj in (-1, 0, 1)
        if not (di == 0 and dj == 0)
    )


def build_static_source_cells(db: Session, detections=None) -> Set[_Cell]:
    """
    Classify grid cells as static industrial sources using the three-part
    signature described in the module docstring. Considers stored history
    plus, when supplied, the incoming detection batch -- so the filter is
    effective on the very first run.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=settings.PERSISTENCE_LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")

    cell_days: Dict[_Cell, Set[str]] = defaultdict(set)
    cell_peak_frp: Dict[_Cell, float] = defaultdict(float)

    def _record(lat, lon, acq_date, frp):
        if not acq_date or acq_date < cutoff:
            return
        c = _cell(lat, lon)
        cell_days[c].add(acq_date)
        if frp is not None:
            cell_peak_frp[c] = max(cell_peak_frp[c], float(frp))

    # Stored history (live + archived tables).
    for lat, lon, acq_date, frp in db.query(
        FireEvent.latitude, FireEvent.longitude, FireEvent.acq_date, FireEvent.frp
    ).all():
        _record(lat, lon, acq_date, frp)
    for lat, lon, acq_date, frp in db.query(
        ArchivedFireEvent.latitude, ArchivedFireEvent.longitude,
        ArchivedFireEvent.acq_date, ArchivedFireEvent.frp
    ).all():
        _record(lat, lon, acq_date, frp)

    # Incoming batch, so the very first run has something to work with.
    if detections is not None and not detections.empty:
        has_frp = "frp" in detections.columns
        has_date = "acq_date" in detections.columns
        for row in detections.itertuples(index=False):
            _record(
                row.latitude, row.longitude,
                getattr(row, "acq_date", None) if has_date else None,
                getattr(row, "frp", None) if has_frp else None,
            )

    occupied = set(cell_days.keys())
    min_days = settings.PERSISTENCE_MIN_DISTINCT_DAYS
    max_frp = settings.STATIC_MAX_FRP_MW
    max_neighbours = settings.STATIC_MAX_NEIGHBOUR_CELLS

    static_cells = {
        c for c, days in cell_days.items()
        if len(days) >= min_days                       # 1. persistent
        and cell_peak_frp[c] <= max_frp                # 2. low intensity
        and _neighbour_count(c, occupied) <= max_neighbours  # 3. isolated
    }

    if static_cells:
        logger.info(
            f"Static-source analysis: {len(static_cells)} grid cell(s) classified as "
            f"industrial (>={min_days} distinct days, peak FRP <={max_frp} MW, "
            f"<={max_neighbours} lit neighbours)."
        )
    return static_cells


def filter_static_sources(db: Session, detections):
    """
    Drop detections falling in cells classified as static industrial
    sources. Returns (kept_df, dropped_count). Safe no-op when disabled,
    when there are no detections, or when nothing qualifies as static.
    """
    if not settings.PERSISTENCE_FILTER_ENABLED or detections.empty:
        return detections, 0

    static_cells = build_static_source_cells(db, detections)
    if not static_cells:
        return detections, 0

    keep_mask = [
        _cell(lat, lon) not in static_cells
        for lat, lon in zip(detections["latitude"], detections["longitude"])
    ]
    kept = detections[keep_mask].reset_index(drop=True)
    dropped = len(detections) - len(kept)
    if dropped:
        logger.info(
            f"Static-source filter: dropped {dropped} detection(s) at persistent, "
            f"low-intensity, spatially isolated locations (industrial heat, not wildfire)."
        )
    return kept, dropped


def is_static_source(db: Session, lat: float, lon: float) -> bool:
    """Convenience predicate: is this location a known static source?"""
    return _cell(lat, lon) in build_static_source_cells(db)
