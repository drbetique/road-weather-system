"""
Road Weather Monitoring System - API Backend
FastAPI application providing REST endpoints for the dashboard
"""

import json
import logging
import secrets
import time
from datetime import datetime, timedelta, date
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    BackgroundTasks,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Import our modules
# NOTE: sys.path must be extended BEFORE importing any sibling module in src/,
# otherwise `database` resolves to the top-level database/ directory (schema only).
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import DatabaseManager
from config import REGION_NAME, REGION_LAT, REGION_LON, REGION_STATIONS
from digitraffic_client import DigitrafficClient, StationMetadata, WeatherReading
from treatment_algorithm import (
    TreatmentDecisionEngine, 
    TreatmentRecommendation,
    TreatmentUrgency,
    TreatmentType,
    analyze_area
)
from fmi_client import FMIClient, WeatherForecast, RoadWeatherForecast

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global clients (initialized on startup)
digitraffic_client: Optional[DigitrafficClient] = None
fmi_client: Optional[FMIClient] = None
db: Optional[DatabaseManager] = None
cached_stations: List[StationMetadata] = []
last_update: Optional[datetime] = None


# Pydantic models for API responses
class StationResponse(BaseModel):
    id: int
    name: str
    latitude: float
    longitude: float
    distance_km: float
    collection_status: str
    state: Optional[str]


class WeatherReadingResponse(BaseModel):
    station_id: int
    station_name: str
    timestamp: str
    air_temp: Optional[float]
    road_temp: Optional[float]
    dew_point: Optional[float]
    humidity: Optional[float]
    friction: Optional[float]
    road_condition: Optional[str]
    warning: Optional[str]
    salt_amount: Optional[float]
    wind_speed: Optional[float]
    visibility_km: Optional[float]
    precipitation: Optional[float]


class RecommendationResponse(BaseModel):
    station_id: int
    station_name: str
    timestamp: str
    urgency: str
    urgency_level: int
    treatment_type: str
    salt_amount_gm2: Optional[float]
    confidence: float
    next_check_minutes: int
    reasoning: List[str]


class ForecastResponse(BaseModel):
    timestamp: str
    temperature: Optional[float]
    humidity: Optional[float]
    wind_speed: Optional[float]
    precipitation: Optional[float]
    dew_point: Optional[float]


class RiskPeriodResponse(BaseModel):
    start: str
    end: str
    max_risk_level: int
    risk_types: List[str]
    factors: List[str]


class DashboardSummary(BaseModel):
    timestamp: str
    location: Dict[str, Any]
    stations_count: int
    readings_count: int
    critical_count: int
    treatment_needed_count: int
    highest_urgency: str
    priority_station: Optional[str]
    forecast_risk_level: int
    upcoming_risks: List[RiskPeriodResponse]


class AlertResponse(BaseModel):
    id: str
    station_id: int
    station_name: str
    alert_type: str
    severity: str
    message: str
    created_at: str
    is_active: bool


# Lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize clients on startup"""
    global digitraffic_client, fmi_client, cached_stations
    
    logger.info("Initializing API clients...")
    digitraffic_client = DigitrafficClient()
    fmi_client = FMIClient()
    global db
    db = DatabaseManager()
    db.connect()
    # Reads below depend on the current column names, so migrate before the
    # first request rather than waiting for the next collection cycle.
    db.migrate()
    
    # Pre-fetch stations
    try:
        cached_stations = digitraffic_client.get_nearby_stations(radius_km=50)
        logger.info(f"Cached {len(cached_stations)} stations")
    except Exception as e:
        logger.error(f"Failed to cache stations: {e}")
    
    yield
    
    # Cleanup
    logger.info("Shutting down API...")


# Create FastAPI app
app = FastAPI(
    title="Road Weather Monitoring System API",
    description="API for road weather monitoring and winter maintenance optimization",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware for dashboard.
# Requests normally arrive server-to-server from the Next.js proxy (no Origin
# header, so CORS does not apply). This allowlist only covers direct browser
# calls. Never widen it back to "*": combined with allow_credentials it makes
# Starlette echo any caller's Origin.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["X-API-Key", "Content-Type"],
)


# ---------------------------------------------------------------------------
# API key authentication
# ---------------------------------------------------------------------------
# The service is publicly reachable on Render, so every /api/* route requires a
# shared secret. The Next.js proxy holds it server-side and injects the header;
# it is never exposed to the browser.
#
# Fails closed: if API_KEY is unset the API refuses to serve data rather than
# silently running wide open. /health stays public for Render health checks.
API_KEY = os.getenv("API_KEY", "")

if not API_KEY:
    logger.critical(
        "API_KEY is not set. All /api/* requests will be rejected with 503. "
        "Set API_KEY in the Render dashboard to enable the API."
    )


def _is_valid_key(candidate: Optional[str]) -> bool:
    """Constant-time comparison to avoid leaking the key via timing."""
    if not API_KEY or not candidate:
        return False
    # Compare as bytes: secrets.compare_digest raises TypeError on str inputs
    # containing non-ASCII characters, which would surface as a 500 instead of
    # a clean 401 for a header like "X-API-Key: café".
    return secrets.compare_digest(
        candidate.encode("utf-8"), API_KEY.encode("utf-8")
    )


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    path = request.url.path

    # Preflight is handled by CORSMiddleware; public paths need no key.
    if request.method == "OPTIONS" or not path.startswith("/api/"):
        return await call_next(request)

    if not API_KEY:
        return JSONResponse(
            status_code=503,
            content={"detail": "API key not configured on the server"},
        )

    if not _is_valid_key(request.headers.get("X-API-Key")):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing X-API-Key header"},
        )

    return await call_next(request)



# ---------------------------------------------------------------------------
# Station list recovery
# ---------------------------------------------------------------------------
# cached_stations is filled once during startup. Digitraffic intermittently
# answers 503, and without a retry that single failure leaves the instance
# reporting zero stations for its whole lifetime -- on a free instance that
# sleeps after 15 minutes idle, potentially an hour. Every endpoint reads the
# list through ensure_stations() so a transient upstream failure heals itself
# on the next request instead of requiring a restart.
_last_station_attempt = 0.0
_STATION_RETRY_COOLDOWN_S = 60.0


def ensure_stations(radius_km: float = 50) -> List[StationMetadata]:
    """Return the cached stations, refetching once per cooldown if empty."""
    global cached_stations, _last_station_attempt

    if cached_stations:
        return cached_stations

    now = time.monotonic()
    if now - _last_station_attempt < _STATION_RETRY_COOLDOWN_S:
        # A sustained outage must not turn every request into an upstream call.
        return cached_stations
    _last_station_attempt = now

    try:
        cached_stations = digitraffic_client.get_nearby_stations(radius_km=radius_km)
        logger.info(f"Recovered station list: {len(cached_stations)} stations")
    except Exception as e:
        logger.warning(f"Station list still unavailable, will retry: {e}")

    return cached_stations


# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "stations_cached": len(cached_stations),
    }


# Stations endpoints
@app.get("/api/stations", response_model=List[StationResponse])
async def get_stations(
    radius_km: float = Query(50, ge=1, le=200, description="Search radius in km"),
    refresh: bool = Query(False, description="Force refresh from API")
):
    """Get road weather stations near the region centre"""
    global cached_stations
    
    if refresh or not cached_stations:
        try:
            cached_stations = digitraffic_client.get_nearby_stations(radius_km=radius_km)
        except Exception as e:
            logger.error(f"Failed to fetch stations: {e}")
            raise HTTPException(status_code=503, detail="Failed to fetch station data")
    
    return [
        StationResponse(
            id=s.id,
            name=s.name,
            latitude=s.latitude,
            longitude=s.longitude,
            distance_km=s.distance_km,
            collection_status=s.collection_status,
            state=s.state
        )
        for s in cached_stations
        if s.distance_km <= radius_km
    ]


@app.get("/api/stations/{station_id}", response_model=StationResponse)
async def get_station(station_id: int):
    """Get specific station details"""
    station = next((s for s in ensure_stations() if s.id == station_id), None)
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    
    return StationResponse(
        id=station.id,
        name=station.name,
        latitude=station.latitude,
        longitude=station.longitude,
        distance_km=station.distance_km,
        collection_status=station.collection_status,
        state=station.state
    )


# Weather data endpoints
@app.get("/api/weather/current", response_model=List[WeatherReadingResponse])
async def get_current_weather(
    station_ids: Optional[str] = Query(None, description="Comma-separated station IDs"),
    limit: int = Query(10, ge=1, le=50, description="Max stations to return")
):
    """Get current weather conditions"""
    global last_update
    
    # Parse station IDs
    ids = None
    if station_ids:
        ids = [int(x.strip()) for x in station_ids.split(",")]
    else:
        ids = [s.id for s in ensure_stations()[:limit]]
    
    try:
        readings = digitraffic_client.get_station_data(ids)
        last_update = datetime.utcnow()
    except Exception as e:
        logger.error(f"Failed to fetch weather data: {e}")
        raise HTTPException(status_code=503, detail="Failed to fetch weather data")
    
    station_names = {s.id: s.name for s in ensure_stations()}
    
    return [
        WeatherReadingResponse(
            station_id=r.station_id,
            station_name=station_names.get(r.station_id, f"Station {r.station_id}"),
            timestamp=r.timestamp.isoformat(),
            air_temp=r.air_temp,
            road_temp=r.road_temp_1,
            dew_point=r.dew_point,
            humidity=r.humidity,
            friction=r.friction,
            road_condition=r.road_condition_text,
            warning=r.warning_text,
            salt_amount=r.salt_amount,
            wind_speed=r.wind_speed,
            visibility_km=r.visibility_km,
            precipitation=r.precip_intensity
        )
        for r in readings
    ]


# Recommendations endpoints
@app.get("/api/recommendations", response_model=List[RecommendationResponse])
async def get_recommendations(
    station_ids: Optional[str] = Query(None, description="Comma-separated station IDs"),
    min_urgency: int = Query(0, ge=0, le=5, description="Minimum urgency level")
):
    """Get treatment recommendations"""
    # Parse station IDs
    ids = None
    if station_ids:
        ids = [int(x.strip()) for x in station_ids.split(",")]
    else:
        ids = [s.id for s in ensure_stations()[:10]]
    
    try:
        readings = digitraffic_client.get_station_data(ids)
    except Exception as e:
        logger.error(f"Failed to fetch weather data: {e}")
        raise HTTPException(status_code=503, detail="Failed to fetch weather data")
    
    station_names = {s.id: s.name for s in ensure_stations()}
    recommendations = analyze_area(readings, station_names)
    
    # Filter by urgency
    filtered = [r for r in recommendations if r.urgency.value >= min_urgency]
    
    return [
        RecommendationResponse(
            station_id=r.station_id,
            station_name=r.station_name,
            timestamp=r.timestamp.isoformat(),
            urgency=r.urgency.name,
            urgency_level=r.urgency.value,
            treatment_type=r.treatment_type.value,
            salt_amount_gm2=r.salt_amount_gm2,
            confidence=r.confidence,
            next_check_minutes=r.next_check_minutes,
            reasoning=r.reasoning
        )
        for r in filtered
    ]


# Forecast endpoints
@app.get("/api/forecast", response_model=List[ForecastResponse])
async def get_forecast(
    hours: int = Query(24, ge=1, le=48, description="Hours ahead to forecast"),
    lat: float = Query(REGION_LAT, description="Latitude"),
    lon: float = Query(REGION_LON, description="Longitude")
):
    """Get weather forecast from FMI"""
    try:
        forecasts = fmi_client.get_forecast(lat=lat, lon=lon, hours_ahead=hours)
    except Exception as e:
        logger.error(f"Failed to fetch forecast: {e}")
        raise HTTPException(status_code=503, detail="Failed to fetch forecast data")
    
    return [
        ForecastResponse(
            timestamp=f.timestamp.isoformat(),
            temperature=f.temperature,
            humidity=f.humidity,
            wind_speed=f.wind_speed,
            precipitation=f.precipitation_1h,
            dew_point=f.dew_point
        )
        for f in forecasts
    ]


@app.get("/api/forecast/risks", response_model=List[RiskPeriodResponse])
async def get_forecast_risks(
    hours: int = Query(24, ge=1, le=48, description="Hours ahead to analyze"),
    min_risk: int = Query(2, ge=1, le=5, description="Minimum risk level to include")
):
    """Get upcoming risk periods from forecast"""
    try:
        forecasts = fmi_client.get_forecast(hours_ahead=hours)
        road_forecasts = fmi_client.analyze_road_weather_forecast(forecasts)
        risk_periods = fmi_client.get_risk_periods(road_forecasts, min_risk_level=min_risk)
    except Exception as e:
        logger.error(f"Failed to analyze forecast: {e}")
        raise HTTPException(status_code=503, detail="Failed to analyze forecast")
    
    return [
        RiskPeriodResponse(
            start=p['start'].isoformat(),
            end=p['end'].isoformat(),
            max_risk_level=p['max_risk_level'],
            risk_types=p['risk_types'],
            factors=p['factors'][:5]  # Limit factors
        )
        for p in risk_periods
    ]


# Dashboard summary endpoint
@app.get("/api/dashboard/summary", response_model=DashboardSummary)
async def get_dashboard_summary():
    """Get summary data for dashboard"""
    try:
        station_ids = [s.id for s in ensure_stations()[:10]]
        readings = digitraffic_client.get_station_data(station_ids)
        
        station_names = {s.id: s.name for s in ensure_stations()}
        recommendations = analyze_area(readings, station_names) or []
        
        forecasts = fmi_client.get_forecast(hours_ahead=24)
        road_forecasts = fmi_client.analyze_road_weather_forecast(forecasts) or []
        risk_periods = fmi_client.get_risk_periods(road_forecasts, min_risk_level=2) or []
        
        critical_count = sum(1 for r in recommendations if r.urgency.value >= TreatmentUrgency.URGENT.value)
        treatment_count = sum(1 for r in recommendations if r.urgency.value >= TreatmentUrgency.PREVENTIVE.value)
        max_forecast_risk = max((rf.risk_level for rf in road_forecasts), default=0)
        
        return DashboardSummary(
            timestamp=datetime.utcnow().isoformat(),
            location={"name": REGION_NAME, "lat": REGION_LAT, "lon": REGION_LON},
            stations_count=len(ensure_stations()),
            readings_count=len(readings),
            critical_count=critical_count,
            treatment_needed_count=treatment_count,
            highest_urgency=recommendations[0].urgency.name if recommendations else "NONE",
            priority_station=recommendations[0].station_name if recommendations else None,
            forecast_risk_level=max_forecast_risk,
            upcoming_risks=[
                RiskPeriodResponse(
                    start=p['start'] if isinstance(p['start'], str) else p['start'].isoformat(),
                    end=p['end'] if isinstance(p['end'], str) else p['end'].isoformat(),
                    max_risk_level=p['max_risk_level'],
                    risk_types=p['risk_types'],
                    factors=p['factors'][:3]
                )
                for p in risk_periods[:3]
            ]
        )
    except Exception as e:
        logger.error(f"Failed to generate dashboard summary: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Failed to generate summary")


# Map data endpoint (for map visualization)
@app.get("/api/map/stations")
async def get_map_stations():
    """Get station data formatted for map display"""
    try:
        station_ids = [s.id for s in ensure_stations()[:15]]
        readings = digitraffic_client.get_station_data(station_ids)
        station_names = {s.id: s.name for s in ensure_stations()}
        recommendations = analyze_area(readings, station_names)
        
        rec_by_station = {r.station_id: r for r in recommendations}
        reading_by_station = {r.station_id: r for r in readings}
        
        markers = []
        for station in ensure_stations()[:15]:
            reading = reading_by_station.get(station.id)
            rec = rec_by_station.get(station.id)
            
            markers.append({
                "id": station.id,
                "name": station.name,
                "lat": station.latitude,
                "lon": station.longitude,
                "distance_km": station.distance_km,
                "air_temp": reading.air_temp if reading else None,
                "road_temp": reading.road_temp_1 if reading else None,
                "friction": reading.friction if reading else None,
                "urgency": rec.urgency.name if rec else "UNKNOWN",
                "urgency_level": rec.urgency.value if rec else 0,
                "treatment": rec.treatment_type.value if rec else "none",
            })
        
        return {
            "center": {"lat": REGION_LAT, "lon": REGION_LON},
            "markers": markers
        }
    except Exception as e:
        logger.error(f"Failed to get map data: {e}")
        raise HTTPException(status_code=503, detail="Failed to get map data")


# Export endpoint
@app.get("/api/export/current")
async def export_current_data():
    """Export current data as JSON for external systems"""
    try:
        station_ids = [s.id for s in ensure_stations()[:10]]
        readings = digitraffic_client.get_station_data(station_ids)
        station_names = {s.id: s.name for s in ensure_stations()}
        recommendations = analyze_area(readings, station_names)
        
        return {
            "export_time": datetime.utcnow().isoformat(),
            "location": REGION_NAME,
            "stations": [
                {
                    "id": s.id,
                    "name": s.name,
                    "lat": s.latitude,
                    "lon": s.longitude,
                    "distance_km": s.distance_km
                }
                for s in ensure_stations()[:10]
            ],
            "readings": [
                {
                    "station_id": r.station_id,
                    "timestamp": r.timestamp.isoformat(),
                    "air_temp": r.air_temp,
                    "road_temp": r.road_temp_1,
                    "friction": r.friction,
                    "salt_amount": r.salt_amount,
                    "road_condition": r.road_condition_text,
                    "warning": r.warning_text
                }
                for r in readings
            ],
            "recommendations": [r.to_dict() for r in recommendations]
        }
    except Exception as e:
        logger.error(f"Failed to export data: {e}")
        raise HTTPException(status_code=503, detail="Failed to export data")
    
    
@app.post("/api/collect")
def run_collection(request: Request):
    """
    Run one data collection cycle and persist the results.

    This is what makes the history views fill up over time. The standalone
    collector service (src/collector.py) needs an always-on worker, which the
    Render free tier does not offer, so an external scheduler calls this
    endpoint on a cron instead. It is behind the same X-API-Key check as every
    other /api route.

    Defined with `def` rather than `async def` so FastAPI runs it in a worker
    thread: the collector performs blocking HTTP calls and would otherwise
    stall the event loop.
    """
    if not db or not db.conn:
        raise HTTPException(
            status_code=503,
            detail="No database configured; set DATABASE_URL to collect history",
        )

    from collector import DataCollector

    collector = DataCollector(db_manager=db, register_signals=False)
    if not collector.initialize():
        raise HTTPException(status_code=503, detail="Collector failed to initialise")

    started = datetime.utcnow()
    try:
        collector.run_collection_cycle()
    except Exception as e:
        logger.error(f"Collection cycle failed: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Collection cycle failed")

    return {
        "status": "ok",
        "started_at": started.isoformat(),
        "duration_seconds": round((datetime.utcnow() - started).total_seconds(), 1),
        "stations": len(collector.stations),
    }


def _parse_range(start: Optional[str], end: Optional[str]):
    """Validate an optional YYYY-MM-DD range, returning (start, end) dates."""
    if not start or not end:
        return None
    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
    except ValueError:
        raise HTTPException(status_code=400, detail="Dates must be YYYY-MM-DD")
    if e < s:
        raise HTTPException(status_code=400, detail="end must not precede start")
    return s, e


def _window(column: str, days: int, rng):
    """
    SQL fragment and params restricting `column` to the requested window.

    An explicit start/end pair takes precedence over the rolling `days`
    window, so a custom range in the dashboard queries the dates actually
    picked rather than the last N days counted back from today.
    """
    if rng:
        s, e = rng
        return (
            f"{column} >= %s::date AND {column} < %s::date + 1",
            [s.isoformat(), e.isoformat()],
        )
    return f"{column} >= NOW() - (%s || ' days')::INTERVAL", [days]


def _window_dates(days: int, rng) -> List[date]:
    """Dates covered by the window, newest first, for the no-database path."""
    if rng:
        s, e = rng
        span = min((e - s).days + 1, 366)
        return [e - timedelta(days=i) for i in range(span)]
    today = date.today()
    return [today - timedelta(days=i) for i in range(days)]


@app.get("/api/weather/history")
async def get_weather_history(
    days: int = Query(7, ge=1, le=366, description="Days of history"),
    station_id: Optional[int] = Query(None, description="Filter by station ID"),
    start: Optional[str] = Query(None, description="Range start, YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="Range end, YYYY-MM-DD"),
):
    """
    Temperature trends over past N days, grouped by day.
    Returns min/avg/max air_temp and road_temp per day per station.
    """
    rng = _parse_range(start, end)

    if not db or not db.conn:
        # No database configured: return correctly shaped rows with null
        # measurements so the dashboard renders its "no historical data"
        # state rather than failing.
        return [
            {
                "date": d.isoformat(),
                "station_id": station_id or 0,
                "station_name": "All stations",
                "avg_air_temp": None,
                "min_air_temp": None,
                "max_air_temp": None,
                "avg_road_temp": None,
                "min_road_temp": None,
                "reading_count": 0,
            }
            for d in _window_dates(days, rng)
        ]

    sql = """
        SELECT
            DATE(wr.measured_at) AS date,
            wr.station_id,
            s.name AS station_name,
            ROUND(AVG(wr.air_temp)::numeric, 1)    AS avg_air_temp,
            ROUND(MIN(wr.air_temp)::numeric, 1)    AS min_air_temp,
            ROUND(MAX(wr.air_temp)::numeric, 1)    AS max_air_temp,
            ROUND(AVG(wr.road_temp_1)::numeric, 1) AS avg_road_temp,
            ROUND(MIN(wr.road_temp_1)::numeric, 1) AS min_road_temp,
            COUNT(*)                               AS reading_count
        FROM weather_readings wr
        JOIN stations s ON s.id = wr.station_id
        WHERE {window}
    """
    _w, params = _window("wr.measured_at", days, rng)
    sql = sql.format(window=_w)
    if station_id:
        sql += " AND wr.station_id = %s"
        params.append(station_id)
    sql += " GROUP BY DATE(wr.measured_at), wr.station_id, s.name ORDER BY date DESC"

    try:
        with db.conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        logger.error(f"History query failed: {e}")
        raise HTTPException(status_code=503, detail="Failed to fetch history")


@app.get("/api/treatments/history")
async def get_treatment_history(
    days: int = Query(7, ge=1, le=366, description="Days of history"),
    station_id: Optional[int] = Query(None, description="Filter by station ID"),
    start: Optional[str] = Query(None, description="Range start, YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="Range end, YYYY-MM-DD"),
):
    """
    Treatment recommendation history grouped by day and urgency level.
    Used for the treatment history bar chart.
    """
    rng = _parse_range(start, end)

    if not db or not db.conn:
        return [
            {
                "date": d.isoformat(),
                "urgency_name": "NONE",
                "count": 0,
                "avg_confidence": 0,
            }
            for d in _window_dates(days, rng)
        ]

    sql = """
        SELECT
            DATE(tr.created_at)         AS date,
            tr.urgency_name,
            COUNT(*)                    AS count,
            ROUND(AVG(tr.confidence)::numeric, 2) AS avg_confidence
        FROM treatment_recommendations tr
        WHERE {window}
    """
    _w, params = _window("tr.created_at", days, rng)
    sql = sql.format(window=_w)
    if station_id:
        sql += " AND tr.station_id = %s"
        params.append(station_id)
    sql += " GROUP BY DATE(tr.created_at), tr.urgency_name ORDER BY date DESC, tr.urgency_name"

    try:
        with db.conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        logger.error(f"Treatment history query failed: {e}")
        raise HTTPException(status_code=503, detail="Failed to fetch treatment history")


@app.get("/api/risks/history")
async def get_risk_history(
    days: int = Query(30, ge=1, le=366, description="Days of history"),
    start: Optional[str] = Query(None, description="Range start, YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="Range end, YYYY-MM-DD"),
):
    """
    Risk period frequency by day.
    Counts how many URGENT/CRITICAL recommendations were generated each day.
    Used for risk frequency analysis chart.
    """
    rng = _parse_range(start, end)

    if not db or not db.conn:
        return [
            {
                "date": d.isoformat(),
                "urgent_count": 0,
                "critical_count": 0,
                "total_count": 0,
            }
            for d in _window_dates(days, rng)
        ]

    sql = """
        SELECT
            DATE(created_at) AS date,
            COUNT(*) FILTER (WHERE urgency_level >= 4) AS urgent_count,
            COUNT(*) FILTER (WHERE urgency_level >= 5) AS critical_count,
            COUNT(*)                                   AS total_count
        FROM treatment_recommendations
        WHERE {window}
        GROUP BY DATE(created_at)
        ORDER BY date DESC
    """
    _w, params = _window("created_at", days, rng)
    sql = sql.format(window=_w)
    try:
        with db.conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        logger.error(f"Risk history query failed: {e}")
        raise HTTPException(status_code=503, detail="Failed to fetch risk history")


@app.get("/api/weather/compare")
async def get_week_comparison(
    station_id: Optional[int] = Query(None, description="Filter by station ID")
):
    """
    This week vs last week comparison.
    Returns daily averages for both periods for the comparison chart.
    """
    if not db or not db.conn:
        from datetime import date, timedelta
        today = date.today()
        result = []
        for i in range(7):
            day = today - timedelta(days=i)
            result.append({
                "day_of_week": day.strftime("%a"),
                "this_week_avg_air": None,
                "this_week_min_road": None,
                "last_week_avg_air": None,
                "last_week_min_road": None,
            })
        return result

    sql = """
        SELECT
            TO_CHAR(measured_at, 'Dy') AS day_of_week,
            EXTRACT(DOW FROM measured_at) AS dow,
            ROUND(AVG(air_temp) FILTER (
                WHERE measured_at >= DATE_TRUNC('week', NOW())
            )::numeric, 1) AS this_week_avg_air,
            ROUND(MIN(road_temp_1) FILTER (
                WHERE measured_at >= DATE_TRUNC('week', NOW())
            )::numeric, 1) AS this_week_min_road,
            ROUND(AVG(air_temp) FILTER (
                WHERE measured_at >= DATE_TRUNC('week', NOW()) - INTERVAL '7 days'
                  AND measured_at < DATE_TRUNC('week', NOW())
            )::numeric, 1) AS last_week_avg_air,
            ROUND(MIN(road_temp_1) FILTER (
                WHERE measured_at >= DATE_TRUNC('week', NOW()) - INTERVAL '7 days'
                  AND measured_at < DATE_TRUNC('week', NOW())
            )::numeric, 1) AS last_week_min_road
        FROM weather_readings
        WHERE measured_at >= DATE_TRUNC('week', NOW()) - INTERVAL '7 days'
    """
    params = []
    if station_id:
        sql += " AND station_id = %s"
        params.append(station_id)
    sql += " GROUP BY TO_CHAR(measured_at, 'Dy'), EXTRACT(DOW FROM measured_at) ORDER BY dow"

    try:
        with db.conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        logger.error(f"Comparison query failed: {e}")
        raise HTTPException(status_code=503, detail="Failed to fetch comparison data")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
    