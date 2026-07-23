"""
NiDa — NASA FIRMS API Client
Fetches active fire detections for Algeria's bounding box from ALL
available NASA/NOAA satellite sources and merges them.

FIRMS API format:
    {BASE_URL}/{MAP_KEY}/{SOURCE}/{west,south,east,north}/{day_range}[/{date}]

Example:
    https://firms.modaps.eosdis.nasa.gov/api/area/csv/KEY/VIIRS_SNPP_NRT/-8.7,18.9,11.9,37.1/1

IMPORTANT — multi-satellite coverage:
There are three independent VIIRS platforms in orbit (Suomi-NPP, NOAA-20,
NOAA-21), each with its own overpass time and its own FIRMS source name.
A fire detected by NOAA-20 will NOT appear in a VIIRS_SNPP_NRT-only query,
even though both instruments are identical VIIRS sensors. Querying only
one source can silently miss real, active fires. NiDa therefore queries
all three VIIRS sources by default; MODIS is included as a lower-resolution
cross-check. This was discovered during Phase 1 validation against the
July 2026 Algeria fire season and is documented in the paper as a design
requirement, not an optional enhancement.
"""

import asyncio
import io
import logging
from datetime import date
from typing import Optional

import httpx
import pandas as pd

from backend.config import settings

logger = logging.getLogger("nida.firms")

# All active NASA/NOAA satellite sources providing NRT fire detection.
# VIIRS 375m sources (preferred — see module docstring for why all three matter):
VIIRS_SOURCES = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]
# MODIS 1km sources (coarser, but useful as an independent cross-check):
MODIS_SOURCES = ["MODIS_NRT"]

ALL_SOURCES = VIIRS_SOURCES + MODIS_SOURCES

# Columns returned by the FIRMS VIIRS CSV
VIIRS_COLUMNS = [
    "latitude", "longitude", "bright_ti4", "scan", "track",
    "acq_date", "acq_time", "satellite", "instrument", "confidence",
    "version", "bright_ti5", "frp", "daynight",
]
# Columns returned by the FIRMS MODIS CSV (different brightness field names)
MODIS_COLUMNS = [
    "latitude", "longitude", "brightness", "scan", "track",
    "acq_date", "acq_time", "satellite", "instrument", "confidence",
    "version", "bright_t31", "frp", "daynight",
]


class FIRMSError(Exception):
    """Raised when the FIRMS API returns an error or unusable data."""


async def fetch_fires(
    day_range: Optional[int] = None,
    for_date: Optional[date] = None,
    sources: Optional[list] = None,
) -> pd.DataFrame:
    """
    Fetch active fire detections for Algeria from ALL NASA FIRMS sources
    (VIIRS S-NPP, VIIRS NOAA-20, VIIRS NOAA-21, MODIS) and merge them into
    a single deduplicated DataFrame with a unified schema.

    Args:
        day_range: Number of days of data to fetch (1-10). Defaults to settings.
        for_date:  Specific date (YYYY-MM-DD). If None, most recent data.
        sources:   Override which FIRMS source names to query. Defaults to
                   ALL_SOURCES (all VIIRS satellites + MODIS).

    Returns:
        DataFrame of fire detections (may be empty if no fires), with a
        unified 'brightness' column (bright_ti4 for VIIRS, brightness for MODIS)
        and a 'source' column recording which FIRMS product it came from.

    Raises:
        FIRMSError: Only if EVERY source fails. Partial failures are logged
                    and skipped so one bad source doesn't blank the results.
    """
    day_range = day_range or settings.FIRMS_DAY_RANGE
    sources = sources or ALL_SOURCES

    results = await asyncio.gather(
        *[_fetch_one_source(src, day_range, for_date) for src in sources],
        return_exceptions=True,
    )

    frames = []
    errors = []
    for src, result in zip(sources, results):
        if isinstance(result, Exception):
            logger.warning(f"Source {src} failed: {result}")
            errors.append((src, result))
            continue
        frames.append(result)

    if not frames and errors:
        raise FIRMSError(f"All FIRMS sources failed: {errors}")

    if not frames:
        return pd.DataFrame(columns=VIIRS_COLUMNS + ["source"])

    merged = pd.concat(frames, ignore_index=True)
    merged = _deduplicate(merged)

    # Precise national-boundary filter: the FIRMS bbox necessarily includes
    # parts of Morocco, Tunisia, Libya, Mali, and Niger; drop those points.
    from backend.geo.boundary import filter_to_algeria
    merged = filter_to_algeria(merged)

    # Land cover false-positive filter: drop detections in barren desert
    # with no burnable fuel nearby (overwhelmingly gas flares / industrial
    # heat in Algeria's southern oil regions, not wildfires).
    from backend.config import settings as _settings
    if _settings.LANDCOVER_FILTER_ENABLED and not merged.empty:
        from backend.geo.landcover import is_likely_false_positive
        before = len(merged)
        keep_mask = [
            not is_likely_false_positive(lat, lon)
            for lat, lon in zip(merged["latitude"], merged["longitude"])
        ]
        merged = merged[keep_mask].reset_index(drop=True)
        dropped = before - len(merged)
        if dropped:
            logger.info(
                f"Land cover filter: dropped {dropped} detections in barren "
                f"desert (likely gas flares / industrial heat, not wildfire)."
            )
    logger.info(
        f"Merged {len(frames)} source(s) -> {len(merged)} unique detections "
        f"(queried: {', '.join(sources)})"
    )
    return merged


async def _fetch_one_source(
    source: str, day_range: int, for_date: Optional[date]
) -> pd.DataFrame:
    """Fetch and parse a single FIRMS source."""
    url = (
        f"{settings.FIRMS_BASE_URL}/{settings.FIRMS_MAP_KEY}/"
        f"{source}/{settings.ALGERIA_BBOX}/{day_range}"
    )
    if for_date:
        url += f"/{for_date.isoformat()}"

    logger.info(f"Fetching FIRMS data: source={source} "
                f"bbox={settings.ALGERIA_BBOX} days={day_range}")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise FIRMSError(f"Network error contacting FIRMS ({source}): {exc}") from exc

    if response.status_code != 200:
        raise FIRMSError(
            f"FIRMS returned HTTP {response.status_code} for {source}: {response.text[:200]}"
        )

    text = response.text.strip()
    is_modis = source in MODIS_SOURCES
    df = _parse_csv(text, is_modis=is_modis)
    df["source"] = source
    return df


def _parse_csv(text: str, is_modis: bool = False) -> pd.DataFrame:
    """Parse a FIRMS CSV response into a validated DataFrame with a
    unified 'brightness' column regardless of VIIRS/MODIS origin."""
    expected_cols = MODIS_COLUMNS if is_modis else VIIRS_COLUMNS

    # FIRMS returns errors as plain text, not HTTP error codes
    if text.startswith("Invalid"):
        raise FIRMSError(f"FIRMS rejected the request: {text[:200]}")

    if not text or text.count("\n") == 0:
        logger.info("No active fires detected for this source.")
        return pd.DataFrame(columns=expected_cols)

    try:
        df = pd.read_csv(io.StringIO(text))
    except Exception as exc:
        raise FIRMSError(f"Failed to parse FIRMS CSV: {exc}") from exc

    missing = {"latitude", "longitude", "acq_date", "confidence"} - set(df.columns)
    if missing:
        raise FIRMSError(f"FIRMS CSV missing expected columns: {missing}")

    # Unify brightness field name: VIIRS uses bright_ti4, MODIS uses brightness
    if "brightness" not in df.columns and "bright_ti4" in df.columns:
        df["brightness"] = df["bright_ti4"]
    elif "brightness" not in df.columns:
        df["brightness"] = None

    df = _filter_quality(df)
    logger.info(f"Parsed {len(df)} fire detections after quality filtering.")
    return df


def _filter_quality(df: pd.DataFrame) -> pd.DataFrame:
    """
    Quality filter based on confidence classes.

    VIIRS confidence values: 'l' (low), 'n' (nominal), 'h' (high) — string.
    MODIS confidence is numeric 0-100.
    We keep nominal/high (VIIRS) or >=30 (MODIS) to minimize false alarms,
    consistent with documented VIIRS nighttime classification anomalies
    (see paper Section 2.2).
    """
    before = len(df)
    if pd.api.types.is_numeric_dtype(df["confidence"]):
        df = df[df["confidence"] >= 30]
    else:
        df = df[df["confidence"].astype(str).isin(["n", "h", "nominal", "high"])]
    dropped = before - len(df)
    if dropped:
        logger.info(f"Dropped {dropped} low-confidence detections.")
    return df.reset_index(drop=True)


def _deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove near-duplicate detections across overlapping satellite swaths.

    Two different satellites can both detect the same fire within the same
    minute at nearly the same coordinates. We round lat/lon to ~110m
    precision (4 decimal places) and dedupe on (lat, lon, acq_date, acq_time)
    so we don't double-count or double-alert on the same physical fire.
    """
    if df.empty:
        return df
    df = df.copy()
    df["_lat_r"] = df["latitude"].round(3)
    df["_lon_r"] = df["longitude"].round(3)
    before = len(df)
    df = df.drop_duplicates(subset=["_lat_r", "_lon_r", "acq_date", "acq_time"])
    df = df.drop(columns=["_lat_r", "_lon_r"])
    dropped = before - len(df)
    if dropped:
        logger.info(f"Deduplicated {dropped} overlapping cross-satellite detections.")
    return df.reset_index(drop=True)
