"""
NiDa — Configuration
Loads all settings from environment variables (.env file).
"""

from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # NASA FIRMS
    FIRMS_MAP_KEY: str = ""
    FIRMS_BASE_URL: str = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
    FIRMS_SOURCE: str = "VIIRS_SNPP_NRT"
    FIRMS_DAY_RANGE: int = 3

    # Algeria bounding box: west,south,east,north
    ALGERIA_BBOX: str = "-8.7,18.9,11.9,37.1"

    # Alert configuration
    ALERT_RADIUS_KM: float = 10.0
    CRITICAL_RADIUS_KM: float = 5.0
    ADVISORY_RADIUS_KM: float = 20.0
    FIRMS_POLL_INTERVAL_HOURS: int = 3

    # Database
    DATABASE_URL: str = "sqlite:///./nida.db"

    # Wind data (dual-source)
    OPENWEATHERMAP_API_KEY: str = ""
    OPENMETEO_BASE_URL: str = "https://api.open-meteo.com/v1/forecast"
    OPENWEATHERMAP_BASE_URL: str = "https://api.openweathermap.org/data/2.5/weather"

    # Clustering (Phase 2)
    CLUSTER_EPS_KM: float = 1.5          # HDBSCAN search radius (Section 2.4 lit review)
    CLUSTER_MIN_SAMPLES: int = 3         # minimum points to form a fire cluster
    ELLIPSE_MAX_LW_RATIO: float = 8.0    # Anderson (1983)/FARSITE cap on L/W ratio
    ELLIPSE_BASE_RADIUS_KM: float = 2.0  # no-wind base radius before FRP scaling
    ELLIPSE_FRP_SCALE_KM: float = 0.01   # extra km of base radius per MW of FRP
    ELLIPSE_MAX_BASE_RADIUS_KM: float = 8.0  # cap on FRP-scaled base (minor axis)
    # Mid-flame wind adjustment factor: Anderson (1983) expects mid-flame wind,
    # not 10m open wind. WAF ~0.4 is a standard open-terrain conversion
    # (Baughman & Albini 1980; Andrews 2012, "Modeling wind adjustment factor").
    MIDFLAME_WIND_ADJUSTMENT: float = 0.4

    # Automated scheduling
    SCHEDULER_ENABLED: bool = True   # set false for tests/CI to avoid background network calls

    # Data lifecycle: detections older than this move from the live tables
    # to the archive tables (moved, never deleted). Configurable operational
    # parameter, not a hardcoded constant.
    ACTIVE_RETENTION_DAYS: int = 10

    # Land cover filtering (MODIS MCD12C1) — drop desert/gas-flare false
    # positives and tag each cluster's vegetation/fuel type.
    LANDCOVER_FILTER_ENABLED: bool = True

    # Evacuation routing (OpenRouteService — avoid-area directions)
    ORS_API_KEY: str = ""
    ORS_BASE_URL: str = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    # Only fire zones within this distance of the straight-line origin-
    # destination corridor are sent as avoid-polygons (keeps the ORS
    # payload small and avoids its "avoid area too large" rejection).
    EVACUATION_CORRIDOR_BUFFER_KM: float = 40.0
    EVACUATION_MAX_AVOID_ZONES: int = 15
    # Multi-route: how many candidate safe towns (in different directions)
    # to attempt routes to simultaneously.
    EVACUATION_ROUTE_CANDIDATES: int = 4

    # Firebase (Phase 3)
    FIREBASE_PROJECT_ID: str = "nida-4f068"
    FIREBASE_CREDENTIALS_PATH: str = "./firebase_service_account.json"

    # App
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
