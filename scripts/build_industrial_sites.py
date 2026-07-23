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
import urllib.parse
from pathlib import Path

import httpx

OUTPUT = Path(__file__).resolve().parents[1] / "backend" / "geo" / "algeria_industrial_sites.json"

# Two focused queries instead of one heavy one. The single combined query
# (especially landuse=industrial ways over all Algeria) is slow enough to
# trigger gateway timeouts (HTTP 504). Splitting keeps each request light.
QUERY_POWER_WORKS = """
[out:json][timeout:120];
area["ISO3166-1"="DZ"][admin_level=2]->.dz;
(
  node["power"="plant"](area.dz);
  way["power"="plant"](area.dz);
  node["power"="generator"]["generator:source"!="solar"](area.dz);
  way["power"="generator"]["generator:source"!="solar"](area.dz);
  node["man_made"="works"](area.dz);
  way["man_made"="works"](area.dz);
  node["man_made"="flare"](area.dz);
  way["man_made"="flare"](area.dz);
);
out center tags;
"""

QUERY_INDUSTRIAL = """
[out:json][timeout:120];
area["ISO3166-1"="DZ"][admin_level=2]->.dz;
(
  way["landuse"="industrial"](area.dz);
  node["industrial"="oil"](area.dz);
  way["industrial"="oil"](area.dz);
);
out center tags;
"""

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "NiDa-wildfire-research/1.0 (industrial-source filter builder)",
    "Accept": "application/json",
}


def _run_query(query: str) -> dict:
    """Try each endpoint until one returns 200. Returns {} if all fail."""
    payload = "data=" + urllib.parse.quote(query)
    for url in ENDPOINTS:
        try:
            print(f"  querying {url} ...")
            r = httpx.post(url, content=payload.encode("utf-8"),
                           headers=HEADERS, timeout=180)
            if r.status_code == 200:
                return r.json()
            print(f"    HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            print(f"    failed: {str(exc)[:80]}")
        time.sleep(3)
    return {}


def _extract(data: dict) -> list:
    out = []
    for el in data.get("elements", []):
        if el.get("type") == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center") or {}
            lat, lon = center.get("lat"), center.get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("operator") or ""
        kind = (tags.get("power") or tags.get("man_made")
                or tags.get("industrial") or tags.get("landuse") or "industrial")
        out.append({"lat": round(float(lat), 5), "lon": round(float(lon), 5),
                    "kind": kind, "name": name[:60]})
    return out


def main():
    all_sites = []
    print("Fetching power plants, works, and flares ...")
    all_sites += _extract(_run_query(QUERY_POWER_WORKS))
    print("Fetching industrial zones and oil sites ...")
    all_sites += _extract(_run_query(QUERY_INDUSTRIAL))

    # De-duplicate near-identical coordinates (~100 m).
    seen, unique = set(), []
    for s in all_sites:
        key = (round(s["lat"], 3), round(s["lon"], 3))
        if key not in seen:
            seen.add(key)
            unique.append(s)

    if not unique:
        print("ERROR: no sites returned from any endpoint. Keeping existing file.",
              file=sys.stderr)
        sys.exit(1)

    OUTPUT.write_text(json.dumps({
        "source": "OpenStreetMap contributors (ODbL), via Overpass API",
        "count": len(unique),
        "sites": unique,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {len(unique)} industrial sites to {OUTPUT}")


if __name__ == "__main__":
    main()
