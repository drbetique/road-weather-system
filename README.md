# Road Weather Monitoring System

A Road Weather Monitoring System for Extending Road Infrastructure Lifespan and Minimizing Environmental Impact Through Optimized Winter Maintenance Using Digitraffic Open Data.

## Project Overview

This system integrates real-time road weather data from Fintraffic's Digitraffic API and weather forecasts from the Finnish Meteorological Institute (FMI) to provide intelligent treatment recommendations for winter road maintenance. The goal is to reduce salt usage while maintaining road safety, thereby protecting both infrastructure and the environment.

**Author**: Victor Betiku  
**Data Sources**: Fintraffic Digitraffic, Finnish Meteorological Institute (FMI)

## Features

- **Real-time Data Ingestion**: Automatic collection of road weather data from Digitraffic API
- **Weather Forecasting**: 24-hour forecasts from FMI Open Data integration
- **Treatment Decision Algorithm**: Multi-factor analysis for optimal salt application timing
- **Risk Assessment**: Identifies upcoming frost, ice, and snow risk periods
- **Web Dashboard**: Real-time monitoring interface with maps and charts
- **REST API**: Full API for integration with external systems
- **Database Storage**: PostgreSQL with PostGIS for historical analysis

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Web Dashboard                             │
│                    (HTML/JS/Tailwind/Leaflet)                   │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Nginx Proxy                              │
│                    (Static files + API proxy)                    │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                        FastAPI Backend                           │
│              (REST API + Business Logic)                         │
└─────────────────────────────────────────────────────────────────┘
         │                      │                      │
         ▼                      ▼                      ▼
┌─────────────┐      ┌─────────────────┐      ┌─────────────┐
│ Digitraffic │      │   PostgreSQL    │      │     FMI     │
│     API     │      │   + PostGIS     │      │   Open Data │
└─────────────┘      └─────────────────┘      └─────────────┘
```

## Project Structure

```
road-weather-system/
├── src/
│   ├── config.py              # Configuration and thresholds
│   ├── digitraffic_client.py  # Digitraffic API client
│   ├── fmi_client.py          # FMI weather forecast client
│   ├── treatment_algorithm.py # Decision engine
│   ├── database.py            # Database models and operations
│   ├── api.py                 # FastAPI backend
│   └── collector.py           # Background data collector
├── dashboard/
│   └── index.html             # Web dashboard
├── database/
│   └── schema.sql             # PostgreSQL schema
├── tests/
├── docs/
├── demo.py                    # Standalone demo script
├── docker-compose.yml         # Full stack deployment
├── Dockerfile                 # Container build
├── nginx.conf                 # Nginx configuration
├── requirements.txt           # Python dependencies
├── .env.example               # Environment template
└── README.md
```

## Quick Start

### Option 1: Docker (Recommended for Production)

```bash
# Navigate to project directory
cd road-weather-system

# Create environment file
cp .env.example .env
# Edit .env with your database password

# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Access dashboard at http://localhost
# Access API at http://localhost:8000
```

### Option 2: Local Development

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Run demo (no database required)
python demo.py

# Or start API server
cd src
uvicorn api:app --reload --host 0.0.0.0 --port 8000

# Open dashboard/index.html in browser
# Update API_BASE in index.html if needed
```

### Option 3: Standalone Demo

```bash
# Just run the demo to see the system in action
python demo.py
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/stations` | GET | List nearby stations |
| `/api/stations/{id}` | GET | Station details |
| `/api/weather/current` | GET | Current weather readings |
| `/api/recommendations` | GET | Treatment recommendations |
| `/api/forecast` | GET | Weather forecast |
| `/api/forecast/risks` | GET | Upcoming risk periods |
| `/api/dashboard/summary` | GET | Dashboard summary data |
| `/api/map/stations` | GET | Map marker data |
| `/api/export/current` | GET | Export current data (JSON) |

## Treatment Decision Algorithm

The algorithm evaluates 7 factors to generate treatment recommendations:

1. **Road Surface Temperature**: Risk increases below 3°C, critical at 0°C
2. **Dew Point Analysis**: Frost risk when road temp within 2°C of dew point
3. **Friction Coefficient**: Critical below 0.30, warning below 0.45
4. **Salt Residual**: Checks existing protection (target >5 g/m²)
5. **Station Warnings**: Responds to ice and frost warnings
6. **Road Condition Status**: Monitors for frost, ice, wet conditions
7. **Precipitation**: Adjusts treatment for rain/snow

### Urgency Levels

| Level | Name | Description |
|-------|------|-------------|
| 0 | NONE | No treatment needed |
| 1 | MONITOR | Continue monitoring |
| 2 | PREVENTIVE | Preventive treatment recommended |
| 3 | STANDARD | Standard treatment needed |
| 4 | URGENT | Immediate treatment required |
| 5 | CRITICAL | Emergency response needed |

## Key Sensors Monitored

| Sensor | Finnish Name | Description | Unit |
|--------|--------------|-------------|------|
| ILMA | Air temp | Air temperature | °C |
| TIE_1 | Road temp | Road surface temperature | °C |
| KASTEPISTE | Dew point | Dew point temperature | °C |
| KITKA | Friction | Friction coefficient | μ |
| KELI | Road condition | Road condition code | - |
| SUOLAN_MÄÄRÄ | Salt amount | Salt on road surface | g/m² |
| VAROITUS | Warning | Warning status | - |

## Circular Economy Alignment

This project supports circular economy principles by:

- **Eliminating waste**: Optimizing salt usage to prevent over-application
- **Circulating materials**: Extending road infrastructure lifespan
- **Regenerating nature**: Reducing environmental salt contamination

## Configuration

The monitored region is set per deployment through environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `REGION_NAME` | `Monitored region` | Display name returned by the API |
| `REGION_LAT` | `60.8143` | Latitude of the region centre |
| `REGION_LON` | `23.6208` | Longitude of the region centre |

Stations are discovered within `STATION_SEARCH_RADIUS_KM` of the centre. Other
tuning lives in `src/config.py`:

```python
STATION_SEARCH_RADIUS_KM = 50

# Treatment thresholds
ROAD_TEMP_CRITICAL = 0.0      # °C
FRICTION_CRITICAL = 0.30      # coefficient
SALT_MIN_EFFECTIVE = 5.0      # g/m²

# Data collection
POLLING_INTERVAL_SECONDS = 300  # 5 minutes
```

## API References

- **Digitraffic Road Weather**: https://www.digitraffic.fi/en/road-traffic/
- **FMI Open Data**: https://en.ilmatieteenlaitos.fi/open-data

## Development Roadmap

- [x] Digitraffic API integration
- [x] Treatment decision algorithm
- [x] FMI forecast integration
- [x] REST API backend
- [x] Web dashboard
- [x] Database schema
- [x] Docker deployment
- [ ] Historical trend analysis
- [ ] Route-based treatment planning
- [ ] Mobile app for maintenance crews
- [ ] Integration with city maintenance systems

## Data attribution

Road weather data: Fintraffic / digitraffic.fi, licensed under CC BY 4.0.
Forecast data: Finnish Meteorological Institute open data, licensed under CC BY 4.0.

## License

Copyright © 2026 Victor Betiku. All rights reserved. See [LICENSE](LICENSE).

## Author

**Victor Betiku**
