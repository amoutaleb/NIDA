"""
One-time builder: fetch known industrial thermal sources for Algeria from
OpenStreetMap (via the Overpass API) and save them as a compact static
file that NiDa loads at runtime.

WHY THIS EXISTS
---------------
Satellite fire products flag persistent industrial heat sources -- power
stations, cement works, refineries, steel plants -- as "fires". These sit
on built-up industrial land with no vegetation to burn. OpenStreetMap
already maps these facilities (power=plant, man_made=works, etc.), so we
use their known locations to suppress the false positives immediately --
no detection history required (unlike the self-learning persistence
filter, which needs time to accumulate).

This script is run ONCE (or occasionally, to refresh) by a maintainer.
Its output, backend/geo/algeria_industrial_sites.json, is shipped with
the project so end users never need to call Overpass themselves.

USAGE
-----
    python scripts/build_industrial_sites.py

Requires network access to an Overpass API endpoint. Data (c)
OpenStreetMap contributors, ODbL -- already attributed on the /terms page.
"""

import json
import sys
import time
from pathlib import Path

import httpx

OUTPUT = Path(__file__).resolve().parents[1] / "backend" / "geo" / "algeria_industrial_sites.json"

# Facility types that commonly register as persistent thermal anomalies.
# Each is fetched as both nodes and ways (areas), using out center to get a
# single representative coordinate per feature.
OVERPASS_QUERY = """
[out:json][timeout:180];
area["ISO3166-1"="DZ"][admin_level=2]->.dz;
(
  node["power"="plant"](area.dz);
  way["power"="plant"](area.dz);
  node["power"="generator"]["generator:source"!="solar"](area.dz);
  way["power"="generator"]["generator:source"!="solar"](area.dz);
  node["man_made"="works"](area.dz);
  way["man_made"="works"](area.dz);
  node["man_made"="petroleum_well"](area.dz);
  node["man_made"="flare"](area.dz);
  way["man_made"="flare"](area.dz);
  node["landuse"="industrial"](area.dz);
  way["landuse"="industrial"](area.dz);
  node["industrial"="oil"](area.dz);
  way["industrial"="oil"](area.dz);
  way["industrial"="factory"](area.dz);
)
;
out center tags;
"""

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]


def fetch() -> dict:
    last_err = None
    for url in ENDPOINTS:
        try:
            print(f"Querying {url} ...")
            r = httpx.post(url, data={"data": OVERPASS_QUERY}, timeout=200)
            if r.status_code == 200:
                return r.json()
            print(f"  HTTP {r.status_code}: {r.text[:120]}")
            last_err = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001
            print(f"  failed: {exc}")
            last_err = str(exc)
        time.sleep(2)
    raise SystemExit(f"All Overpass endpoints failed. Last error: {last_err}")


def main():
    data = fetch()
    elements = data.get("elements", [])
    sites = []
    for el in elements:
        # node -> lat/lon directly; way -> center.{lat,lon}
        if el.get("type") == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center") or {}
            lat, lon = center.get("lat"), center.get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("operator") or ""
        kind = (
            tags.get("power") or tags.get("man_made")
            or tags.get("industrial") or tags.get("landuse") or "industrial"
        )
        sites.append({
            "lat": round(float(lat), 5),
            "lon": round(float(lon), 5),
            "kind": kind,
            "name": name[:60],
        })

    # De-duplicate near-identical coordinates.
    seen = set()
    unique = []
    for s in sites:
        key = (round(s["lat"], 3), round(s["lon"], 3))
        if key in seen:
            continue
        seen.add(key)
        unique.append(s)

    OUTPUT.write_text(json.dumps({
        "source": "OpenStreetMap contributors (ODbL), via Overpass API",
        "count": len(unique),
        "sites": unique,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(unique)} industrial sites to {OUTPUT}")
    if not unique:
        print("WARNING: no sites returned; the filter will be a no-op.", file=sys.stderr)


if __name__ == "__main__":
    main()
