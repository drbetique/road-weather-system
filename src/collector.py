"""
Data Collector Service
Background service that periodically collects data from APIs and stores in database
"""

import logging
import time
import signal
import sys
from datetime import datetime
from typing import Optional

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
load_dotenv(env_path)

from config import POLLING_INTERVAL_SECONDS, REGION_LAT, REGION_LON
from digitraffic_client import DigitrafficClient
from fmi_client import FMIClient
from treatment_algorithm import analyze_area
from database import DatabaseManager, Station

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DataCollector:
    """Background service for collecting road weather data"""
    
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.digitraffic = DigitrafficClient()
        self.fmi = FMIClient()
        self.db = db_manager
        self.running = False
        self.stations = []
        self.station_names = {}
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
    
    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info("Shutdown signal received")
        self.running = False
    
    def initialize(self):
        """Initialize collector - fetch stations and setup database"""
        logger.info("Initializing data collector...")
        
        # Fetch nearby stations
        try:
            self.stations = self.digitraffic.get_nearby_stations(radius_km=50)
            self.station_names = {s.id: s.name for s in self.stations}
            logger.info(f"Found {len(self.stations)} stations")
        except Exception as e:
            logger.error(f"Failed to fetch stations: {e}")
            return False
        
        # Initialize database if available
        if self.db:
            try:
                self.db.connect()
                self.db.initialize_schema()
                
                # Store station metadata
                for station in self.stations:
                    db_station = Station(
                        id=station.id,
                        name=station.name,
                        latitude=station.latitude,
                        longitude=station.longitude,
                        distance_km=station.distance_km,
                        collection_status=station.collection_status,
                        state=station.state
                    )
                    self.db.insert_station(db_station)
                
                logger.info("Database initialized")
            except Exception as e:
                logger.warning(f"Database initialization failed: {e}")
                self.db = None
        
        return True
    
    def collect_weather_data(self):
        """Collect current weather data from all stations"""
        station_ids = [s.id for s in self.stations[:15]]  # Top 15 closest
        
        try:
            readings = self.digitraffic.get_station_data(station_ids)
            logger.info(f"Collected data from {len(readings)} stations")
            
            # Store in database if available
            if self.db:
                for reading in readings:
                    self.db.insert_reading(reading)
            
            return readings
        except Exception as e:
            logger.error(f"Failed to collect weather data: {e}")
            return []
    
    def collect_forecasts(self):
        """Collect weather forecasts from FMI"""
        try:
            forecasts = self.fmi.get_forecast(
                lat=REGION_LAT,
                lon=REGION_LON,
                hours_ahead=24
            )
            logger.info(f"Collected {len(forecasts)} forecast points")
            return forecasts
        except Exception as e:
            logger.error(f"Failed to collect forecasts: {e}")
            return []
    
    def generate_recommendations(self, readings):
        """Generate treatment recommendations from current data"""
        try:
            recommendations = analyze_area(readings, self.station_names)
            
            # Store in database if available
            if self.db:
                for rec in recommendations:
                    self.db.insert_recommendation(rec)
            
            # Log any urgent recommendations
            urgent = [r for r in recommendations if r.urgency.value >= 4]
            if urgent:
                logger.warning(f"⚠️ {len(urgent)} URGENT recommendations:")
                for r in urgent:
                    logger.warning(f"  - {r.station_name}: {r.urgency.name} - {r.treatment_type.value}")
            
            return recommendations
        except Exception as e:
            logger.error(f"Failed to generate recommendations: {e}")
            return []
    
    def run_collection_cycle(self):
        """Run one complete data collection cycle"""
        cycle_start = datetime.now()
        logger.info(f"Starting collection cycle at {cycle_start}")
        
        # Collect weather data
        readings = self.collect_weather_data()
        
        # Generate recommendations
        if readings:
            recommendations = self.generate_recommendations(readings)
            
            # Log summary
            treatment_needed = sum(1 for r in recommendations if r.urgency.value >= 2)
            critical = sum(1 for r in recommendations if r.urgency.value >= 4)
            
            logger.info(f"Cycle complete: {len(readings)} readings, "
                       f"{treatment_needed} need treatment, {critical} critical")
        
        # Collect forecasts (less frequently)
        if hasattr(self, '_forecast_counter'):
            self._forecast_counter += 1
        else:
            self._forecast_counter = 0
        
        if self._forecast_counter % 12 == 0:  # Every 12 cycles (~1 hour)
            forecasts = self.collect_forecasts()
            if forecasts:
                road_forecasts = self.fmi.analyze_road_weather_forecast(forecasts)
                risk_periods = self.fmi.get_risk_periods(road_forecasts)
                if risk_periods:
                    logger.info(f"📅 {len(risk_periods)} upcoming risk periods detected")
        
        cycle_duration = (datetime.now() - cycle_start).total_seconds()
        logger.info(f"Cycle completed in {cycle_duration:.1f}s")
    
    def run(self, interval_seconds: int = POLLING_INTERVAL_SECONDS):
        """Run the collector service"""
        logger.info(f"Starting collector service (interval: {interval_seconds}s)")
        self.running = True
        
        while self.running:
            try:
                self.run_collection_cycle()
            except Exception as e:
                logger.error(f"Error in collection cycle: {e}")
            
            # Sleep until next cycle
            if self.running:
                time.sleep(interval_seconds)
        
        logger.info("Collector service stopped")
        
        # Cleanup
        if self.db:
            self.db.close()


def main():
    """Run the data collector"""
    print("""
    ╔════════════════════════════════════════════════════════════╗
    ║     Road Weather Monitoring System - Data Collector        ║
    ║                                                            ║
    ║  Collecting data from Digitraffic and FMI APIs            ║
    ║  Press Ctrl+C to stop                                      ║
    ╚════════════════════════════════════════════════════════════╝
    """)
    
    # Initialize database (optional)
    db = None
    try:
        db = DatabaseManager()
    except Exception as e:
        logger.warning(f"Database not available: {e}")
    
    # Create and run collector
    collector = DataCollector(db_manager=db)
    
    if not collector.initialize():
        logger.error("Failed to initialize collector")
        sys.exit(1)
    
    # Run with 5-minute interval
    collector.run(interval_seconds=300)


if __name__ == "__main__":
    main()
