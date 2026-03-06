"""
FMI (Finnish Meteorological Institute) API Client
Fetches weather forecasts for road weather prediction
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import urllib.request
import urllib.parse
import urllib.error

from config import REGION_LAT, REGION_LON

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FMI WFS API configuration
FMI_WFS_URL = "https://opendata.fmi.fi/wfs"
FMI_STORED_QUERY = "fmi::forecast::harmonie::surface::point::simple"

# Namespaces for XML parsing
NAMESPACES = {
    'wfs': 'http://www.opengis.net/wfs/2.0',
    'gml': 'http://www.opengis.net/gml/3.2',
    'BsWfs': 'http://xml.fmi.fi/schema/wfs/2.0',
}


@dataclass
class WeatherForecast:
    """Weather forecast data point"""
    timestamp: datetime
    latitude: float
    longitude: float

    # Forecast parameters
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    wind_speed: Optional[float] = None
    wind_direction: Optional[float] = None
    wind_gust: Optional[float] = None
    precipitation_1h: Optional[float] = None
    precipitation_type: Optional[str] = None
    cloud_cover: Optional[float] = None
    pressure: Optional[float] = None
    dew_point: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "latitude": self.latitude,
            "longitude": self.longitude,
            "temperature": self.temperature,
            "humidity": self.humidity,
            "wind_speed": self.wind_speed,
            "wind_direction": self.wind_direction,
            "wind_gust": self.wind_gust,
            "precipitation_1h": self.precipitation_1h,
            "precipitation_type": self.precipitation_type,
            "cloud_cover": self.cloud_cover,
            "pressure": self.pressure,
            "dew_point": self.dew_point,
        }


@dataclass
class RoadWeatherForecast:
    """Processed forecast for road weather decisions"""
    timestamp: datetime

    # Risk indicators
    frost_risk: bool = False
    ice_risk: bool = False
    snow_risk: bool = False

    # Temperatures
    air_temp: Optional[float] = None
    estimated_road_temp: Optional[float] = None
    dew_point: Optional[float] = None

    # Precipitation
    precipitation_mm: Optional[float] = None
    precipitation_type: Optional[str] = None

    # Wind
    wind_speed: Optional[float] = None

    # Risk level (0-5)
    risk_level: int = 0
    risk_factors: List[str] = None

    def __post_init__(self):
        if self.risk_factors is None:
            self.risk_factors = []


class FMIClient:
    """Client for FMI Open Data WFS API"""

    def __init__(self):
        self.base_url = FMI_WFS_URL

    def _build_url(
        self,
        lat: float,
        lon: float,
        start_time: datetime = None,
        end_time: datetime = None,
        timestep: int = 60
    ) -> str:
        """Build WFS request URL"""

        if start_time is None:
            start_time = datetime.utcnow()
        if end_time is None:
            end_time = start_time + timedelta(hours=24)

        params = {
            'service': 'WFS',
            'version': '2.0.0',
            'request': 'getFeature',
            'storedquery_id': FMI_STORED_QUERY,
            'latlon': f'{lat},{lon}',
            'starttime': start_time.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'endtime': end_time.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'timestep': str(timestep),
            'parameters': 'temperature,humidity,windspeedms,winddirection,windgust,precipitation1h,totalcloudcover,pressure,dewpoint'
        }

        return f"{self.base_url}?{urllib.parse.urlencode(params)}"

    def _fetch_xml(self, url: str) -> Optional[ET.Element]:
        """Fetch and parse XML from URL"""
        try:
            request = urllib.request.Request(url, headers={
                'User-Agent': 'RoadWeatherMonitor/1.0'
            })
            with urllib.request.urlopen(request, timeout=30) as response:
                content = response.read()
                return ET.fromstring(content)
        except urllib.error.URLError as e:
            logger.error(f"Failed to fetch FMI data: {e}")
            return None
        except ET.ParseError as e:
            logger.error(f"Failed to parse FMI XML: {e}")
            return None

    def get_forecast(
        self,
        lat: float = REGION_LAT,
        lon: float = REGION_LON,
        hours_ahead: int = 24
    ) -> List[WeatherForecast]:
        """Fetch weather forecast for location"""

        start_time = datetime.utcnow()
        end_time = start_time + timedelta(hours=hours_ahead)

        url = self._build_url(lat, lon, start_time, end_time)
        logger.info(f"Fetching FMI forecast for ({lat}, {lon})")

        root = self._fetch_xml(url)
        if root is None:
            return []

        return self._parse_forecast_response(root, lat, lon)

    def _parse_forecast_response(
        self,
        root: ET.Element,
        lat: float,
        lon: float
    ) -> List[WeatherForecast]:
        """Parse FMI WFS response XML"""

        forecasts = {}

        for element in root.iter():
            if 'BsWfsElement' in element.tag:
                time_elem = element.find('.//BsWfs:Time', NAMESPACES)
                param_elem = element.find('.//BsWfs:ParameterName', NAMESPACES)
                value_elem = element.find('.//BsWfs:ParameterValue', NAMESPACES)

                if time_elem is None or param_elem is None or value_elem is None:
                    continue

                try:
                    timestamp = datetime.fromisoformat(time_elem.text.replace('Z', '+00:00'))
                    param_name = param_elem.text
                    value_text = value_elem.text

                    if value_text and value_text.lower() != 'nan':
                        value = float(value_text)
                    else:
                        value = None

                    if timestamp not in forecasts:
                        forecasts[timestamp] = {
                            'timestamp': timestamp,
                            'latitude': lat,
                            'longitude': lon,
                        }

                    param_mapping = {
                        'temperature': 'temperature',
                        'Temperature': 'temperature',
                        'humidity': 'humidity',
                        'Humidity': 'humidity',
                        'windspeedms': 'wind_speed',
                        'WindSpeedMS': 'wind_speed',
                        'winddirection': 'wind_direction',
                        'WindDirection': 'wind_direction',
                        'windgust': 'wind_gust',
                        'WindGust': 'wind_gust',
                        'precipitation1h': 'precipitation_1h',
                        'Precipitation1h': 'precipitation_1h',
                        'totalcloudcover': 'cloud_cover',
                        'TotalCloudCover': 'cloud_cover',
                        'pressure': 'pressure',
                        'Pressure': 'pressure',
                        'dewpoint': 'dew_point',
                        'DewPoint': 'dew_point',
                    }

                    field = param_mapping.get(param_name)
                    if field and value is not None:
                        forecasts[timestamp][field] = value

                except (ValueError, AttributeError):
                    continue

        result = []
        for timestamp in sorted(forecasts.keys()):
            data = forecasts[timestamp]
            forecast = WeatherForecast(
                timestamp=data['timestamp'],
                latitude=data['latitude'],
                longitude=data['longitude'],
                temperature=data.get('temperature'),
                humidity=data.get('humidity'),
                wind_speed=data.get('wind_speed'),
                wind_direction=data.get('wind_direction'),
                wind_gust=data.get('wind_gust'),
                precipitation_1h=data.get('precipitation_1h'),
                cloud_cover=data.get('cloud_cover'),
                pressure=data.get('pressure'),
                dew_point=data.get('dew_point'),
            )
            result.append(forecast)

        logger.info(f"Parsed {len(result)} forecast points")
        return result

    def analyze_road_weather_forecast(
        self,
        forecasts: List[WeatherForecast]
    ) -> List[RoadWeatherForecast]:
        """Analyze forecasts for road weather risks"""

        road_forecasts = []

        for fc in forecasts:
            risk_factors = []
            risk_level = 0

            frost_risk = False
            ice_risk = False
            snow_risk = False

            estimated_road_temp = fc.temperature
            if fc.temperature is not None and fc.cloud_cover is not None:
                if fc.cloud_cover < 30 and fc.temperature < 5:
                    estimated_road_temp = fc.temperature - 2.0

            if fc.temperature is not None and fc.dew_point is not None:
                if fc.temperature <= 3.0:
                    if fc.temperature - fc.dew_point < 2.0:
                        frost_risk = True
                        risk_factors.append(f"Frost risk: temp {fc.temperature}°C near dew point {fc.dew_point}°C")
                        risk_level = max(risk_level, 3)

            if estimated_road_temp is not None and estimated_road_temp <= 0:
                ice_risk = True
                risk_factors.append(f"Ice risk: estimated road temp {estimated_road_temp:.1f}°C")
                risk_level = max(risk_level, 4)

            precip_type = None
            if fc.precipitation_1h is not None and fc.precipitation_1h > 0:
                if fc.temperature is not None:
                    if fc.temperature <= 0:
                        precip_type = "snow"
                        snow_risk = True
                        risk_factors.append(f"Snow: {fc.precipitation_1h:.1f}mm/h at {fc.temperature}°C")
                        risk_level = max(risk_level, 4)
                    elif fc.temperature <= 2:
                        precip_type = "sleet"
                        risk_factors.append(f"Sleet risk: {fc.precipitation_1h:.1f}mm/h at {fc.temperature}°C")
                        risk_level = max(risk_level, 3)
                    else:
                        precip_type = "rain"
                        if fc.temperature <= 5:
                            risk_factors.append(f"Rain with low temp: {fc.precipitation_1h:.1f}mm/h at {fc.temperature}°C")
                            risk_level = max(risk_level, 2)

            if fc.wind_speed is not None and fc.wind_speed > 10:
                risk_factors.append(f"High wind: {fc.wind_speed:.1f} m/s")
                risk_level = max(risk_level, 1)

            road_forecast = RoadWeatherForecast(
                timestamp=fc.timestamp,
                frost_risk=frost_risk,
                ice_risk=ice_risk,
                snow_risk=snow_risk,
                air_temp=fc.temperature,
                estimated_road_temp=estimated_road_temp,
                dew_point=fc.dew_point,
                precipitation_mm=fc.precipitation_1h,
                precipitation_type=precip_type,
                wind_speed=fc.wind_speed,
                risk_level=risk_level,
                risk_factors=risk_factors,
            )
            road_forecasts.append(road_forecast)

        return road_forecasts

    def get_risk_periods(
        self,
        road_forecasts: List[RoadWeatherForecast],
        min_risk_level: int = 2
    ) -> List[Dict[str, Any]]:
        """Identify periods of elevated risk"""

        risk_periods = []
        current_period = None

        for fc in road_forecasts:
            if fc.risk_level >= min_risk_level:
                if current_period is None:
                    current_period = {
                        'start': fc.timestamp,
                        'end': fc.timestamp,
                        'max_risk_level': fc.risk_level,
                        'risk_types': set(),
                        'factors': [],
                    }

                current_period['end'] = fc.timestamp
                current_period['max_risk_level'] = max(
                    current_period['max_risk_level'], fc.risk_level
                )

                if fc.frost_risk:
                    current_period['risk_types'].add('frost')
                if fc.ice_risk:
                    current_period['risk_types'].add('ice')
                if fc.snow_risk:
                    current_period['risk_types'].add('snow')

                current_period['factors'].extend(fc.risk_factors or [])

            else:
                if current_period is not None:
                    current_period['risk_types'] = list(current_period['risk_types'])
                    current_period['factors'] = list(set(current_period['factors']))
                    current_period['start'] = current_period['start'].isoformat()
                    current_period['end'] = current_period['end'].isoformat()
                    risk_periods.append(current_period)
                    current_period = None

        # Close the last period if still open
        if current_period is not None:
            current_period['risk_types'] = list(current_period['risk_types'])
            current_period['factors'] = list(set(current_period['factors']))
            current_period['start'] = current_period['start'].isoformat()
            current_period['end'] = current_period['end'].isoformat()
            risk_periods.append(current_period)

        return risk_periods


def main():
    """Test FMI client"""
    client = FMIClient()

    print("\n=== FMI Weather Forecast for Forssa ===")
    print(f"Location: {REGION_LAT}°N, {REGION_LON}°E")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    forecasts = client.get_forecast(hours_ahead=24)

    if not forecasts:
        print("\n❌ Failed to fetch forecast. FMI API may be unavailable.")
        return

    print(f"\n📊 Fetched {len(forecasts)} forecast points")

    print("\n--- Hourly Forecast ---")
    for fc in forecasts[:6]:
        print(f"\n  {fc.timestamp.strftime('%H:%M')}:")
        print(f"    Temp: {fc.temperature}°C")
        print(f"    Humidity: {fc.humidity}%")
        print(f"    Wind: {fc.wind_speed} m/s")
        print(f"    Precip: {fc.precipitation_1h} mm/h")
        print(f"    Dew point: {fc.dew_point}°C")

    print("\n--- Road Weather Risk Analysis ---")
    road_forecasts = client.analyze_road_weather_forecast(forecasts)
    risk_periods = client.get_risk_periods(road_forecasts, min_risk_level=2)

    if risk_periods:
        print(f"\n⚠️ Found {len(risk_periods)} risk period(s):")
        for period in risk_periods:
            print(f"\n  {period['start']} - {period['end']}")
            print(f"    Risk level: {period['max_risk_level']}/5")
            print(f"    Types: {', '.join(period['risk_types'])}")
            for factor in period['factors'][:3]:
                print(f"    • {factor}")
    else:
        print("\n✅ No significant risk periods in the next 24 hours")


if __name__ == "__main__":
    main()