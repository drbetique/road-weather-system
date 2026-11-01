"""
Digitraffic API Client
Fetches road weather station data, forecasts, and maintenance information
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from math import radians, cos, sin, asin, sqrt
import requests

from config import (
    WEATHER_STATIONS_URL,
    WEATHER_DATA_URL,
    REGION_LAT,
    REGION_LON,
    STATION_SEARCH_RADIUS_KM,
    SensorConfig,
    ROAD_CONDITIONS,
    WARNING_CODES,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class StationMetadata:
    id: int
    name: str
    latitude: float
    longitude: float
    distance_km: float
    collection_status: str
    state: Optional[str]


@dataclass
class WeatherReading:
    station_id: int
    timestamp: datetime
    air_temp: Optional[float] = None
    road_temp_1: Optional[float] = None
    road_temp_2: Optional[float] = None
    ground_temp: Optional[float] = None
    dew_point: Optional[float] = None
    humidity: Optional[float] = None
    freezing_point: Optional[float] = None
    road_condition: Optional[int] = None
    road_condition_text: Optional[str] = None
    friction: Optional[float] = None
    warning: Optional[int] = None
    warning_text: Optional[str] = None
    salt_amount: Optional[float] = None
    salt_concentration: Optional[float] = None
    precipitation: Optional[float] = None
    precip_intensity: Optional[float] = None
    wind_speed: Optional[float] = None
    wind_gust: Optional[float] = None
    wind_direction: Optional[float] = None
    visibility_km: Optional[float] = None


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    return c * 6371


class DigitrafficClient:

    def __init__(self, user_agent: str = "RoadWeatherMonitor/1.0"):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })
        self._stations_cache: Optional[List[StationMetadata]] = None
        self._cached_data: Optional[List[WeatherReading]] = None
        self._cache_time: Optional[datetime] = None
        self._cache_ttl_minutes: int = 5

    def _fetch_json(self, url: str) -> Dict[str, Any]:
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Failed to fetch {url}: {e}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch {url}: {e}")
            raise

    def _is_cache_valid(self) -> bool:
        if self._cached_data is None or self._cache_time is None:
            return False
        age = datetime.utcnow() - self._cache_time
        return age < timedelta(minutes=self._cache_ttl_minutes)

    def get_all_stations(self) -> List[StationMetadata]:
        data = self._fetch_json(WEATHER_STATIONS_URL)
        stations = []

        for feature in data.get("features", []):
            props = feature.get("properties", {})
            coords = feature.get("geometry", {}).get("coordinates", [0, 0])
            lon, lat = coords[0], coords[1]
            distance = haversine_distance(REGION_LAT, REGION_LON, lat, lon)

            station = StationMetadata(
                id=props.get("id"),
                name=props.get("name", "Unknown"),
                latitude=lat,
                longitude=lon,
                distance_km=round(distance, 1),
                collection_status=props.get("collectionStatus", "UNKNOWN"),
                state=props.get("state"),
            )
            stations.append(station)

        stations.sort(key=lambda s: s.distance_km)
        self._stations_cache = stations
        return stations

    def get_nearby_stations(self, radius_km: float = STATION_SEARCH_RADIUS_KM) -> List[StationMetadata]:
        if self._stations_cache is None:
            self.get_all_stations()

        nearby = [s for s in self._stations_cache
                  if s.distance_km <= radius_km
                  and s.collection_status == "GATHERING"]

        logger.info(f"Found {len(nearby)} active stations within {radius_km}km of the region centre")
        return nearby

    def get_station_data(self, station_ids: Optional[List[int]] = None) -> List[WeatherReading]:
        # Return cached data if still fresh
        if self._is_cache_valid():
            logger.info(f"Returning cached weather data ({len(self._cached_data)} stations)")
            if station_ids:
                return [r for r in self._cached_data if r.station_id in station_ids]
            return self._cached_data

        # Fetch fresh data from Digitraffic
        data = self._fetch_json(WEATHER_DATA_URL)
        readings = []

        for station in data.get("stations", []):
            station_id = station.get("id")

            sensors = {}
            for sensor in station.get("sensorValues", []):
                sensors[sensor.get("name")] = {
                    "value": sensor.get("value"),
                    "unit": sensor.get("unit"),
                    "time": sensor.get("measuredTime"),
                    "description_en": sensor.get("sensorValueDescriptionEn"),
                }

            timestamp_str = station.get("dataUpdatedTime")
            if timestamp_str:
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            else:
                timestamp = datetime.utcnow()

            road_condition_val = self._get_sensor_value(sensors, SensorConfig.ROAD_CONDITION)
            warning_val = self._get_sensor_value(sensors, SensorConfig.WARNING)

            reading = WeatherReading(
                station_id=station_id,
                timestamp=timestamp,
                air_temp=self._get_sensor_value(sensors, SensorConfig.AIR_TEMP),
                road_temp_1=self._get_sensor_value(sensors, SensorConfig.ROAD_TEMP_1),
                road_temp_2=self._get_sensor_value(sensors, SensorConfig.ROAD_TEMP_2),
                ground_temp=self._get_sensor_value(sensors, SensorConfig.GROUND_TEMP),
                dew_point=self._get_sensor_value(sensors, SensorConfig.DEW_POINT),
                humidity=self._get_sensor_value(sensors, SensorConfig.HUMIDITY),
                freezing_point=self._get_sensor_value(sensors, SensorConfig.FREEZING_POINT),
                road_condition=int(road_condition_val) if road_condition_val else None,
                road_condition_text=ROAD_CONDITIONS.get(int(road_condition_val)) if road_condition_val else None,
                friction=self._get_sensor_value(sensors, SensorConfig.FRICTION),
                warning=int(warning_val) if warning_val else None,
                warning_text=WARNING_CODES.get(int(warning_val)) if warning_val else None,
                salt_amount=self._get_sensor_value(sensors, SensorConfig.SALT_AMOUNT),
                salt_concentration=self._get_sensor_value(sensors, SensorConfig.SALT_CONCENTRATION),
                precipitation=self._get_sensor_value(sensors, SensorConfig.PRECIPITATION),
                precip_intensity=self._get_sensor_value(sensors, SensorConfig.PRECIP_INTENSITY),
                wind_speed=self._get_sensor_value(sensors, SensorConfig.WIND_SPEED),
                wind_gust=self._get_sensor_value(sensors, SensorConfig.WIND_GUST),
                wind_direction=self._get_sensor_value(sensors, SensorConfig.WIND_DIR),
                visibility_km=self._get_sensor_value(sensors, SensorConfig.VISIBILITY),
            )
            readings.append(reading)

        # Save to cache
        self._cached_data = readings
        self._cache_time = datetime.utcnow()
        logger.info(f"Fetched fresh weather data for {len(readings)} stations, cached for {self._cache_ttl_minutes} minutes")

        if station_ids:
            return [r for r in readings if r.station_id in station_ids]
        return readings

    def _get_sensor_value(self, sensors: Dict, sensor_name: str) -> Optional[float]:
        sensor = sensors.get(sensor_name)
        if sensor:
            return sensor.get("value")
        return None


def main():
    client = DigitrafficClient()

    print("\n=== Stations near the region centre (within 50km) ===")
    nearby = client.get_nearby_stations(50)
    for station in nearby[:10]:
        print(f"  {station.id}: {station.name} ({station.distance_km}km) - {station.collection_status}")

    print("\n=== Current Weather Data ===")
    station_ids = [s.id for s in nearby[:5]]
    readings = client.get_station_data(station_ids)

    for reading in readings:
        station = next((s for s in nearby if s.id == reading.station_id), None)
        station_name = station.name if station else f"Station {reading.station_id}"

        print(f"\n{station_name}:")
        print(f"  Air temp: {reading.air_temp}°C")
        print(f"  Road temp: {reading.road_temp_1}°C")
        print(f"  Dew point: {reading.dew_point}°C")
        print(f"  Humidity: {reading.humidity}%")
        print(f"  Road condition: {reading.road_condition_text}")
        print(f"  Friction: {reading.friction}")
        print(f"  Warning: {reading.warning_text}")
        print(f"  Salt amount: {reading.salt_amount} g/m²")
        print(f"  Wind: {reading.wind_speed} m/s")
        print(f"  Visibility: {reading.visibility_km} km")


if __name__ == "__main__":
    main()