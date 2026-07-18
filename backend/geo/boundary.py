"""
NiDa — Algeria National Boundary Filter

The FIRMS API only supports rectangular bounding-box queries, but any
rectangle enclosing Algeria necessarily also contains territory in
Morocco, Tunisia, Libya, Mali, Niger, and Western Sahara. During live
validation against the July 2026 fire season, a 624 MW fire cluster
near Marrakech (Morocco) passed the bbox filter and was severity-ranked
as if it were an Algerian event.

This module performs true point-in-polygon filtering against Algeria's
national boundary (Natural Earth data, simplified to ~1.1 km border
precision -- 346 vertices), applied after the bbox pre-filter. The
two-stage approach (cheap bbox at the API level, precise polygon
locally) is documented in the paper as a deliberate design pattern.
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

import pandas as pd
from shapely.geometry import Point, shape
from shapely.prepared import prep

logger = logging.getLogger("nida.geo.boundary")

_BOUNDARY_PATH = Path(__file__).parent / "algeria_boundary.geojson"


@lru_cache(maxsize=1)
def _algeria_polygon():
    """Load and cache the (shapely-prepared) Algeria polygon."""
    with open(_BOUNDARY_PATH) as f:
        feature = json.load(f)
    polygon = shape(feature["geometry"])
    return prep(polygon)  # prepared geometry: much faster repeated contains()


def is_in_algeria(lat: float, lon: float) -> bool:
    """True if the point lies within Algeria's national boundary."""
    return _algeria_polygon().contains(Point(lon, lat))


def filter_to_algeria(df: pd.DataFrame) -> pd.DataFrame:
    """
    Drop rows whose coordinates fall outside Algeria's national boundary.
    Expects 'latitude' and 'longitude' columns.
    """
    if df.empty:
        return df
    before = len(df)
    poly = _algeria_polygon()
    mask = [
        poly.contains(Point(lon, lat))
        for lat, lon in zip(df["latitude"], df["longitude"])
    ]
    out = df[mask].reset_index(drop=True)
    dropped = before - len(out)
    if dropped:
        logger.info(
            f"Boundary filter: dropped {dropped} detections outside Algeria "
            f"(bbox includes parts of neighboring countries)."
        )
    return out
