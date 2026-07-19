"""
NiDa — Known Industrial-Source Filter

Suppresses satellite detections that fall on known industrial thermal
sources (power stations, cement works, refineries, flares, industrial
zones) mapped in OpenStreetMap. Unlike the self-learning persistence
filter, this works from the very first run because it uses a pre-built
catalogue of facility locations -- no detection history required.

The catalogue (algeria_industrial_sites.json) is produced once by
scripts/build_industrial_sites.py and shipped with the project, so no
runtime network access is needed. Data (c) OpenStreetMap contributors
(ODbL), attributed on the /terms page.

Reliability / recall-first design
---------------------------------
The match buffer is deliberately SMALL (facility-footprint scale, default
1.0 km). A real wildfire merely *near* an industrial site must not be
discarded, so we suppress a detection only when it sits essentially on top
of a known facility. If the catalogue file is missing or empty, the filter
is a safe no-op (it never guesses). The buffer is configurable.
"""

import json
import logging
import math
from functools import lru_cache
from pathlib import Path
from typing import List, Tuple

from backend.config import settings

logger = logging.getLogger("nida.geo.industrial")

_DATA_PATH = Path(__file__).parent / "algeria_industrial_sites.json"


@lru_cache(maxsize=1)
def _load_sites() -> Tuple[Tuple[float, float], ...]:
    """Load industrial-site coordinates. Cached; safe no-op if absent."""
    if not _DATA_PATH.exists():
        logger.info("Industrial-site catalogue not found; industrial filter is inactive.")
        return tuple()
    try:
        data = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
        sites = tuple((float(s["lat"]), float(s["lon"])) for s in data.get("sites", []))
        logger.info(f"Loaded {len(sites)} known industrial sites for false-positive filtering.")
        return sites
    except (ValueError, KeyError, OSError) as exc:
        logger.warning(f"Failed to load industrial-site catalogue: {exc}")
        return tuple()


# Coarse spatial index: bucket sites into ~0.1 deg cells so each detection
# only compares against nearby sites, not the whole catalogue.
_BUCKET_DEG = 0.1


@lru_cache(maxsize=1)
def _bucketed():
    buckets: dict = {}
    for lat, lon in _load_sites():
        key = (int(lat / _BUCKET_DEG), int(lon / _BUCKET_DEG))
        buckets.setdefault(key, []).append((lat, lon))
    return buckets


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def is_industrial_source(lat: float, lon: float) -> bool:
    """
    True if the point lies within the match buffer of a known industrial
    site. Checks only sites in the neighbouring spatial buckets.
    """
    buckets = _bucketed()
    if not buckets:
        return False
    buffer_km = settings.INDUSTRIAL_FILTER_BUFFER_KM
    ci, cj = int(lat / _BUCKET_DEG), int(lon / _BUCKET_DEG)
    for i in (ci - 1, ci, ci + 1):
        for j in (cj - 1, cj, cj + 1):
            for slat, slon in buckets.get((i, j), ()):
                if _haversine_km(lat, lon, slat, slon) <= buffer_km:
                    return True
    return False


def filter_industrial_sources(detections):
    """
    Drop detections sitting on known industrial sites. Returns
    (kept_df, dropped_count). Safe no-op when disabled, when the
    catalogue is empty, or when there are no detections.
    """
    if not settings.INDUSTRIAL_FILTER_ENABLED or detections.empty:
        return detections, 0
    if not _bucketed():
        return detections, 0

    keep_mask = [
        not is_industrial_source(lat, lon)
        for lat, lon in zip(detections["latitude"], detections["longitude"])
    ]
    kept = detections[keep_mask].reset_index(drop=True)
    dropped = len(detections) - len(kept)
    if dropped:
        logger.info(
            f"Industrial-source filter: dropped {dropped} detection(s) on known "
            f"industrial sites (power plants, cement works, refineries, etc.)."
        )
    return kept, dropped
