# NiDa: National Integrated Disaster Alert

NiDa is a satellite-driven wildfire early-warning system for Algeria. It
ingests thermal fire detections from NASA satellites every few hours,
groups them into fire events, models a wind-driven danger zone around each
one, and delivers targeted, multilingual alerts to people inside those
zones — along with evacuation routing that steers around the fires.

This repository is a research prototype accompanying an academic paper. It
is provided for research, evaluation, and educational use. **It is not an
official emergency service.** In an emergency in Algeria, contact Civil
Protection (14).

---

## What it does

- **Detects fires** from NASA FIRMS, combining three VIIRS satellites
  (Suomi-NPP, NOAA-20, NOAA-21) and MODIS, restricted to Algeria's borders.
- **Filters false positives** using MODIS land cover: thermal detections
  in barren desert (typically gas flares from oil/gas infrastructure, not
  wildfires) are removed, and each real fire is tagged with its vegetation
  type (forest, shrubland, grassland, etc.).
- **Groups detections into fire events** using density-based clustering
  (DBSCAN), discarding isolated detections as likely noise.
- **Models a wind-driven danger zone** for each fire: an ellipse elongated
  downwind using the Anderson (1983) length-to-width model, with live wind
  from Open-Meteo (OpenWeatherMap as a fallback). If wind data is
  unavailable, a circular zone is used instead.
- **Scores severity** (Critical / Warning / Advisory) from fire radiative
  power, detection confidence, and wind speed.
- **Targets alerts**: only devices inside a fire's zone are notified, in
  Arabic, French, or English, structured on the Warning Lexicon.
- **Suggests evacuation routes** that avoid active fire zones, to several
  safe towns in different directions, using OpenRouteService.
- **Runs itself** on a scheduler (default: every 3 hours) and archives data
  older than a configurable retention window.

An interactive trilingual web dashboard (`/map`) visualizes all of this,
with a historical archive browser (`/archive`) and an attributions page
(`/terms`).

---

## Requirements

- Python 3.11 or newer (developed on 3.12)
- Free API keys:
  - **NASA FIRMS** — https://firms.modaps.eosdis.nasa.gov/api/map_key/
  - **OpenWeatherMap** (free tier) — https://openweathermap.org/api
  - **OpenRouteService** — https://openrouteservice.org/dev/#/signup
  - **Open-Meteo** needs no key.
- Push notifications (Firebase) are optional. Without a Firebase service
  account, NiDa runs in dry-run mode: alerts are computed and logged but
  not actually sent. This is sufficient to run and evaluate the system.

---

## Setup

```bash
# 1. Clone and enter the project
git clone <repository-url>
cd nida

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r backend/requirements.txt

# 4. Configure environment
cp .env.example .env
# Open .env and fill in your FIRMS, OpenWeatherMap, and OpenRouteService keys.
# FIRMS_DAY_RANGE is already set to 3, so no change is needed to get started.
```

---

## Running

```bash
uvicorn backend.main:app --reload
```

Then open **http://localhost:8000/map** in a browser.

On startup the built-in scheduler runs the pipeline once immediately and
then repeats every few hours, so the map populates on its own within a
minute or so. To trigger an update manually you can also use the
**Update fire data** button on the dashboard, or:

```bash
curl -X POST http://localhost:8000/api/v1/system/run-now
```

To disable the background scheduler (for example while running tests), set
`SCHEDULER_ENABLED=false` in your `.env`.

---

## Project structure

```
backend/
  main.py                FastAPI app, routes, page serving, lifespan
  config.py              Environment-driven settings
  scheduler.py           Automated ingest -> cluster -> alert -> archive loop
  archive.py             Data-retention rollover
  data_layer/
    firms_client.py      NASA FIRMS ingestion, dedup, boundary + land-cover filtering
  geo/
    distance.py          Haversine / bearing / destination-point helpers
    clustering.py        DBSCAN grouping of detections into fire events
    ellipse.py           Anderson (1983) wind-driven alert ellipse
    severity.py          Composite severity scoring
    wind_client.py       Open-Meteo + OpenWeatherMap wind lookup
    boundary.py          Algeria national-boundary filtering
    landcover.py         MODIS land cover classification and filtering
    evacuation.py        Safe-town selection and avoid-area routing
  notifications/
    messages.py          Warning Lexicon message construction (EN/FR/AR)
    alert_engine.py      Zone-targeted alert dispatch and de-duplication
    fcm.py               Firebase Cloud Messaging (dry-run without credentials)
  api/routes/            HTTP endpoints (fires, clusters, alerts, evacuation, ...)
  db/database.py         SQLAlchemy models
  static/                Dashboard, archive, and terms pages
data/historical/         Sample detection data for local testing
tests/                   Test suite
```

---

## Tests

```bash
python -m pytest tests/
```

The suite covers the geometric core (distance, clustering, ellipse
calibration, severity), the data filters (boundary, land cover), the
alerting and scheduling logic, the archive lifecycle, and the evacuation
routing (with the external routing API mocked, so tests need no network
access or API keys).

---

## Configuration reference

Key settings in `.env` (see `.env.example` for the full list):

| Setting | Meaning | Default |
|---|---|---|
| `FIRMS_DAY_RANGE` | Days of detections fetched per poll | `3` |
| `FIRMS_POLL_INTERVAL_HOURS` | Scheduler interval | `3` |
| `SCHEDULER_ENABLED` | Run the automated pipeline | `true` |
| `ACTIVE_RETENTION_DAYS` | Age at which data moves to the archive | `10` |
| `LANDCOVER_FILTER_ENABLED` | Filter desert/flare false positives | `true` |
| `ALERT_RADIUS_KM` | Base alert radius | `10` |
| `EVACUATION_CORRIDOR_BUFFER_KM` | How near a route a fire must be to be avoided | `40` |

---

## Data sources

NiDa is built on freely available data and open tools: NASA FIRMS (fire
detections), NASA MODIS MCD12C1 (land cover), Open-Meteo and OpenWeatherMap
(wind), OpenRouteService (routing), OpenStreetMap (map tiles and road
data), Natural Earth (national boundary), Leaflet and Font Awesome (web
interface). Full attributions and terms are on the `/terms` page.

---

## Limitations

- Satellite thermal detections can miss fires hidden by cloud or dense
  canopy.
- A MODIS land cover filter removes most desert gas-flare false positives;
  because it is deliberately conservative, a few may still appear.
- Alert zones are modeled estimates, not measured fire perimeters.
- Land cover is at ~5.5 km resolution, which is coarse for fine coastal or
  mountain vegetation mosaics; the desert filter is deliberately
  conservative to avoid discarding real fires.
- Routing avoids only *detected* fire zones and depends on third-party road
  data. It requires the public OpenStreetMap tile server, which is not
  intended for production traffic; a real deployment needs a dedicated tile
  provider.

---

## License

Released under the **NiDa Research License** (see [LICENSE](LICENSE)):
free for research, academic, and educational use; no commercial use or
redistribution without permission; citation of the associated paper is
required. Author and citation details will be added upon publication.
