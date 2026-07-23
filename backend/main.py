"""
NiDa — National Integrated Disaster Alert
FastAPI Backend — Main Entry Point

Run with:
    uvicorn backend.main:app --reload
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import alerts, archive, clusters, devices, evacuation, fireweather, fires, report, system
from backend.config import settings
from backend.db.database import create_tables
from backend.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("nida")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("NiDa backend starting up...")
    create_tables()
    logger.info("Database tables verified.")
    logger.info(f"FIRMS source : {settings.FIRMS_SOURCE}")
    logger.info(f"Algeria bbox : {settings.ALGERIA_BBOX}")
    logger.info(f"Alert radius : {settings.ALERT_RADIUS_KM} km")
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("NiDa backend shutting down.")


app = FastAPI(
    title="NiDa — National Integrated Disaster Alert",
    description=(
        "Satellite-driven wildfire early warning system for Algeria's "
        "wildland-urban interface (WUI) communities. Ingests NASA FIRMS "
        "VIIRS 375m data, applies geospatial risk scoring, and dispatches "
        "location-based push notifications."
    ),
    version="1.0.0",
    contact={"name": "NiDa Research Team", "url": "https://github.com/amoutaleb/NIDA"},
    license_info={"name": "MIT"},
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(fires.router, prefix="/api/v1", tags=["Fires"])
app.include_router(clusters.router, prefix="/api/v1", tags=["Clusters"])
app.include_router(alerts.router, prefix="/api/v1", tags=["Alerts"])
app.include_router(devices.router, prefix="/api/v1", tags=["Devices"])
app.include_router(report.router, prefix="/api/v1", tags=["Reports"])
app.include_router(system.router, prefix="/api/v1", tags=["System"])
app.include_router(archive.router, prefix="/api/v1", tags=["Archive"])
app.include_router(evacuation.router, prefix="/api/v1", tags=["Evacuation"])
app.include_router(fireweather.router, prefix="/api/v1", tags=["Fire Weather"])


@app.get("/", tags=["Health"])
async def root():
    return {
        "system": "NiDa — National Integrated Disaster Alert",
        "version": "1.0.0",
        "status": "operational",
        "docs": "/docs",
        "dashboard": "/map",
    }


@app.get("/map", tags=["Dashboard"])
async def dashboard():
    """Live wildfire map dashboard."""
    from pathlib import Path
    from fastapi.responses import HTMLResponse
    html_path = Path(__file__).parent / "static" / "dashboard.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/archive", tags=["Dashboard"])
async def archive_page():
    """Historical fire archive browser."""
    from pathlib import Path
    from fastapi.responses import HTMLResponse
    html_path = Path(__file__).parent / "static" / "archive.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/terms", tags=["Dashboard"])
async def terms_page():
    """Terms of use and data-source attributions."""
    from pathlib import Path
    from fastapi.responses import HTMLResponse
    html_path = Path(__file__).parent / "static" / "terms.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}
