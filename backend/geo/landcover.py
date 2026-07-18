"""
NiDa — Land Cover Classification (MODIS MCD12C1, IGBP scheme)

Classifies each fire detection/cluster by the vegetation type at its
location, using NASA's MODIS Land Cover Type product (MCD12C1, IGBP
17-class scheme, 0.05 deg / ~5.5km global grid, cropped to Algeria and
shipped as a ~7KB static file -- no runtime API, mirroring the Algeria
boundary approach).

Two uses:
  1. False-positive filtering: VIIRS/MODIS thermal detections in BARREN /
     sparsely-vegetated desert are very likely industrial heat sources
     (gas flares -- Algeria's southeast is a major oil/gas region) rather
     than wildfires. There is no vegetation there to burn. This is a
     second line of defence behind the NASA Static Thermal Anomalies
     mask, catching flares the STA mask has not yet catalogued.
  2. Fire-type enrichment: forest vs shrubland vs grassland vs cropland
     burn very differently. Tagging each cluster supports descriptive
     statistics in the paper and future fuel-aware severity refinement.

IMPORTANT resolution caveat (documented for the paper): at ~5.5km the
grid cannot resolve fine coastal/mountain land-use mosaics. A single
cell over a coastal city may read "urban" even where adjacent forest
burns. We therefore (a) sample a small NEIGHBOURHOOD and take the most
vegetation-relevant class present, not just the exact cell, and (b) use
land cover to EXCLUDE only the unambiguous barren-desert case, treating
all other classes as advisory context rather than hard filters -- so a
real fire is never dropped merely because the coarse grid mislabelled
its cell.
"""

import logging
from functools import lru_cache
from pathlib import Path

import numpy as np

logger = logging.getLogger("nida.geo.landcover")

_DATA_PATH = Path(__file__).parent / "algeria_landcover.npz"

# IGBP 17-class legend
IGBP_CLASSES = {
    0: "Water", 1: "Evergreen Needleleaf Forest", 2: "Evergreen Broadleaf Forest",
    3: "Deciduous Needleleaf Forest", 4: "Deciduous Broadleaf Forest", 5: "Mixed Forest",
    6: "Closed Shrubland", 7: "Open Shrubland", 8: "Woody Savanna", 9: "Savanna",
    10: "Grassland", 11: "Permanent Wetland", 12: "Cropland", 13: "Urban",
    14: "Cropland/Natural Vegetation Mosaic", 15: "Snow and Ice", 16: "Barren or Sparsely Vegetated",
}

# Simplified fuel groupings used for tagging and (later) severity.
FOREST_CLASSES = {1, 2, 3, 4, 5}
SHRUB_CLASSES = {6, 7}
SAVANNA_CLASSES = {8, 9}
GRASS_CROP_CLASSES = {10, 12, 14}
# Classes with negligible wildfire fuel. Barren is the desert/flare case.
NON_FUEL_CLASSES = {0, 11, 13, 15, 16}


def _fuel_group(igbp: int) -> str:
    if igbp in FOREST_CLASSES:
        return "forest"
    if igbp in SHRUB_CLASSES:
        return "shrubland"
    if igbp in SAVANNA_CLASSES:
        return "savanna"
    if igbp in GRASS_CROP_CLASSES:
        return "grassland_cropland"
    if igbp == 16:
        return "barren"
    return "other"


@lru_cache(maxsize=1)
def _load():
    data = np.load(_DATA_PATH)
    return (
        data["grid"],
        float(data["lon_min"]),
        float(data["lat_max"]),
        float(data["res"]),
    )


def _cell(lat: float, lon: float):
    grid, lon_min, lat_max, res = _load()
    rows, cols = grid.shape
    row = int((lat_max - lat) / res)
    col = int((lon - lon_min) / res)
    if 0 <= row < rows and 0 <= col < cols:
        return grid, row, col
    return None, None, None


def classify_point(lat: float, lon: float, neighborhood: int = 1) -> dict:
    """
    Classify a location's land cover. Samples a (2*neighborhood+1)^2 cell
    window and reports both the exact-cell class and the dominant
    VEGETATION class in the window (so a real fire adjacent to a
    coarse-grid 'urban'/'barren' cell is still recognised as vegetated
    if any burnable fuel sits nearby).

    Returns dict with: igbp_class, igbp_name, fuel_group, is_vegetated,
    and window_has_fuel.
    """
    grid, row, col = _cell(lat, lon)
    if grid is None:
        return {
            "igbp_class": None, "igbp_name": "Unknown", "fuel_group": "unknown",
            "is_vegetated": True, "window_has_fuel": True,  # fail-safe: don't filter
        }

    exact = int(grid[row, col])
    rows, cols = grid.shape
    r0, r1 = max(0, row - neighborhood), min(rows, row + neighborhood + 1)
    c0, c1 = max(0, col - neighborhood), min(cols, col + neighborhood + 1)
    window = grid[r0:r1, c0:c1].flatten().tolist()

    window_has_fuel = any(v not in NON_FUEL_CLASSES for v in window)

    # Dominant vegetation class in the window (fall back to exact cell)
    veg_cells = [v for v in window if v not in NON_FUEL_CLASSES]
    if veg_cells:
        dominant = max(set(veg_cells), key=veg_cells.count)
    else:
        dominant = exact

    return {
        "igbp_class": exact,
        "igbp_name": IGBP_CLASSES.get(exact, "Unknown"),
        "fuel_group": _fuel_group(dominant if veg_cells else exact),
        "is_vegetated": exact not in NON_FUEL_CLASSES,
        "window_has_fuel": window_has_fuel,
    }


def is_likely_false_positive(lat: float, lon: float) -> bool:
    """
    True only for the unambiguous desert/flare case: the exact cell is
    barren AND no burnable fuel exists anywhere in the surrounding
    neighbourhood. Deliberately conservative -- a fire is excluded only
    when there is genuinely nothing to burn nearby, never merely because
    the coarse grid mislabelled a single vegetated cell.
    """
    info = classify_point(lat, lon, neighborhood=1)
    if info["igbp_class"] is None:
        return False
    return info["igbp_class"] == 16 and not info["window_has_fuel"]
