"""
Road Weather Monitoring System configuration.
"""

import os
from dataclasses import dataclass
from typing import List

# Centre of the monitored region. Stations are discovered within
# STATION_SEARCH_RADIUS_KM of this point, and forecasts are fetched for it.
# Override per deployment with REGION_NAME, REGION_LAT and REGION_LON.
REGION_NAME = os.getenv("REGION_NAME", "Monitored region")
REGION_LAT = float(os.getenv("REGION_LAT", "60.8143"))
REGION_LON = float(os.getenv("REGION_LON", "23.6208"))

# Search radius for nearby stations (km)
STATION_SEARCH_RADIUS_KM = 50

# Digitraffic API endpoints
DIGITRAFFIC_BASE_URL = "https://tie.digitraffic.fi/api"
WEATHER_STATIONS_URL = f"{DIGITRAFFIC_BASE_URL}/weather/v1/stations"
WEATHER_DATA_URL = f"{DIGITRAFFIC_BASE_URL}/weather/v1/stations/data"
MAINTENANCE_URL = f"{DIGITRAFFIC_BASE_URL}/maintenance/v1/tracking/routes"
FORECAST_URL = f"{DIGITRAFFIC_BASE_URL}/weather/v1/forecasts"

# FMI API endpoints
FMI_BASE_URL = "https://opendata.fmi.fi/wfs"
FMI_STORED_QUERY = "fmi::forecast::harmonie::surface::point::multipointcoverage"

# Key sensor IDs for winter maintenance decision-making
@dataclass
class SensorConfig:
    """Configuration for critical road weather sensors"""
    # Temperature sensors
    AIR_TEMP = "ILMA"                    # Air temperature (°C)
    ROAD_TEMP_1 = "TIE_1"                # Road surface temp sensor 1 (°C)
    ROAD_TEMP_2 = "TIE_2"                # Road surface temp sensor 2 (°C)
    GROUND_TEMP = "MAA_1"                # Ground temperature (°C)
    
    # Moisture and precipitation
    DEW_POINT = "KASTEPISTE"             # Dew point (°C)
    HUMIDITY = "ILMAN_KOSTEUS"           # Air humidity (%)
    PRECIPITATION = "SADE"               # Precipitation status
    PRECIP_INTENSITY = "SADE_INTENSITEETTI"  # mm/h
    PRECIP_SUM = "SADESUMMA"             # Accumulated precipitation (mm)
    
    # Road condition sensors
    FREEZING_POINT = "JÄÄTYMISPISTE_1"   # Freezing point (°C)
    ROAD_CONDITION = "KELI_1"            # Road condition code
    FRICTION = "KITKA1"                  # Friction coefficient (µ)
    WARNING = "VAROITUS_1"               # Warning status
    
    # Salt monitoring
    SALT_AMOUNT = "SUOLAN_MÄÄRÄ_1"       # Salt amount (g/m²)
    SALT_CONCENTRATION = "SUOLAN_VÄKEVYYS_1"  # Salt concentration (g/l)
    
    # Wind
    WIND_SPEED = "KESKITUULI"            # Average wind (m/s)
    WIND_GUST = "MAKSIMITUULI"           # Max wind gust (m/s)
    WIND_DIR = "TUULENSUUNTA"            # Wind direction (°)
    
    # Visibility
    VISIBILITY = "NÄKYVYYS_KM"           # Visibility (km)


# Road condition codes (KELI values)
ROAD_CONDITIONS = {
    1: "Dry",
    2: "Moist", 
    3: "Wet",
    4: "Wet and salty",
    5: "Frost",
    6: "Partly icy",
    7: "Icy",
    8: "Probably moist and salty",
    9: "Dry and salty"
}

# Warning codes (VAROITUS values)
WARNING_CODES = {
    0: "OK",
    1: "Beware",
    2: "Ice warning",
    3: "Frost warning",
    4: "Heavy rain",
    5: "Snowfall"
}

# Treatment thresholds for salt application decision
@dataclass
class TreatmentThresholds:
    """Thresholds for winter maintenance decisions"""
    # Temperature thresholds
    ROAD_TEMP_CRITICAL = 0.0          # Below this, ice risk increases
    ROAD_TEMP_WARNING = 3.0           # Pre-treatment consideration
    DEW_POINT_MARGIN = 2.0            # If road temp within this of dew point
    
    # Friction thresholds (lower = more slippery)
    FRICTION_CRITICAL = 0.30          # Immediate treatment needed
    FRICTION_WARNING = 0.45           # Consider treatment
    FRICTION_GOOD = 0.60              # Acceptable conditions
    
    # Salt residual thresholds
    SALT_MIN_EFFECTIVE = 5.0          # Minimum g/m² for protection
    SALT_MAX_RECOMMENDED = 20.0       # Environmental consideration
    
    # Precipitation thresholds
    PRECIP_LIGHT = 0.5                # mm/h
    PRECIP_MODERATE = 2.0             # mm/h
    PRECIP_HEAVY = 5.0                # mm/h


# Stations near the default region centre (populated dynamically at runtime)
# These are approximate IDs based on geographic proximity
REGION_STATIONS = [
    4008,   # vt10_Tammela_Heinivaho (closest, ~15km)
    4003,   # vt2_Humppila_Koenjoki (~25km)
    2011,   # vt9_Loimaa_Mellilä (~35km)
    2012,   # vt10_Koski_TL (~40km)
]

# Database configuration
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "road_weather"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
}

# Data collection intervals
POLLING_INTERVAL_SECONDS = 300  # 5 minutes (Digitraffic updates ~every 5 min)
FORECAST_UPDATE_INTERVAL = 3600  # 1 hour

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
