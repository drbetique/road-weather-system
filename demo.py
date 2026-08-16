#!/usr/bin/env python3
"""
Road Weather Monitoring System - Demo Script
Demonstrates real-time data fetching and treatment recommendations

This script:
1. Connects to Digitraffic API
2. Finds road weather stations near Forssa
3. Fetches current conditions
4. Generates treatment recommendations
"""

import json
import sys
import os
from datetime import datetime

# Add src directory to path (relative to this file, so it works anywhere)
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from digitraffic_client import DigitrafficClient, StationMetadata, WeatherReading
from treatment_algorithm import TreatmentDecisionEngine, analyze_area, TreatmentUrgency
from config import REGION_LAT, REGION_LON


def print_header(text: str):
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)


def print_station_table(stations: list):
    """Print stations in a formatted table"""
    print(f"\n{'ID':<8} {'Name':<35} {'Distance':<12} {'Status':<15}")
    print("-" * 70)
    for s in stations:
        status = "✓ Active" if s.collection_status == "GATHERING" else s.collection_status
        print(f"{s.id:<8} {s.name[:34]:<35} {s.distance_km:>6.1f} km    {status:<15}")


def print_weather_summary(reading: WeatherReading, station_name: str):
    """Print weather reading summary"""
    print(f"\n  📊 {station_name}")
    print(f"     ├─ Air temp:      {reading.air_temp or 'N/A':>8} °C")
    print(f"     ├─ Road temp:     {reading.road_temp_1 or 'N/A':>8} °C")
    print(f"     ├─ Dew point:     {reading.dew_point or 'N/A':>8} °C")
    print(f"     ├─ Humidity:      {reading.humidity or 'N/A':>8} %")
    print(f"     ├─ Friction:      {reading.friction or 'N/A':>8}")
    print(f"     ├─ Salt amount:   {reading.salt_amount or 'N/A':>8} g/m²")
    print(f"     ├─ Condition:     {reading.road_condition_text or 'N/A'}")
    print(f"     └─ Warning:       {reading.warning_text or 'None'}")


def get_urgency_emoji(urgency: TreatmentUrgency) -> str:
    """Get emoji for urgency level"""
    return {
        TreatmentUrgency.NONE: "✅",
        TreatmentUrgency.MONITOR: "👀",
        TreatmentUrgency.PREVENTIVE: "⚠️",
        TreatmentUrgency.STANDARD: "🔶",
        TreatmentUrgency.URGENT: "🔴",
        TreatmentUrgency.CRITICAL: "🚨",
    }.get(urgency, "❓")


def main():
    print_header("ROAD WEATHER MONITORING SYSTEM - FORSSA AREA")
    print(f"  Location: Forssa, Finland ({REGION_LAT}°N, {REGION_LON}°E)")
    print(f"  Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Initialize client
    client = DigitrafficClient()
    
    # Step 1: Find nearby stations
    print_header("STEP 1: Finding Road Weather Stations Near Forssa")
    nearby_stations = client.get_nearby_stations(radius_km=50)
    
    print(f"\n  Found {len(nearby_stations)} active stations within 50km")
    print_station_table(nearby_stations[:10])
    
    if not nearby_stations:
        print("\n  ❌ No stations found. Check network connection.")
        return
    
    # Step 2: Fetch current weather data
    print_header("STEP 2: Fetching Current Weather Data")
    
    # Get data for 5 closest stations
    target_stations = nearby_stations[:5]
    station_ids = [s.id for s in target_stations]
    station_names = {s.id: s.name for s in target_stations}
    
    readings = client.get_station_data(station_ids)
    
    print(f"\n  Retrieved data from {len(readings)} stations")
    
    for reading in readings:
        name = station_names.get(reading.station_id, f"Station {reading.station_id}")
        print_weather_summary(reading, name)
    
    # Step 3: Generate treatment recommendations
    print_header("STEP 3: Treatment Recommendations")
    
    recommendations = analyze_area(readings, station_names)
    
    for rec in recommendations:
        emoji = get_urgency_emoji(rec.urgency)
        print(f"\n  {emoji} {rec.station_name}")
        print(f"     Urgency:    {rec.urgency.name}")
        print(f"     Treatment:  {rec.treatment_type.value}")
        if rec.salt_amount_gm2:
            print(f"     Salt dose:  {rec.salt_amount_gm2} g/m²")
        print(f"     Confidence: {rec.confidence:.0%}")
        print(f"     Re-check:   {rec.next_check_minutes} min")
        print(f"     Analysis:")
        for reason in rec.reasoning:
            print(f"       • {reason}")
    
    # Step 4: Summary
    print_header("SUMMARY")
    
    critical_count = sum(1 for r in recommendations if r.urgency.value >= TreatmentUrgency.URGENT.value)
    treatment_count = sum(1 for r in recommendations if r.urgency.value >= TreatmentUrgency.PREVENTIVE.value)
    
    print(f"""
    Stations analyzed:     {len(readings)}
    Requiring treatment:   {treatment_count}
    Critical situations:   {critical_count}
    
    Highest urgency:       {recommendations[0].urgency.name if recommendations else 'N/A'}
    Priority station:      {recommendations[0].station_name if recommendations else 'N/A'}
    """)
    
    # Export data as JSON
    print_header("DATA EXPORT")
    
    export_data = {
        "timestamp": datetime.now().isoformat(),
        "location": {"name": "Forssa", "lat": REGION_LAT, "lon": REGION_LON},
        "stations_analyzed": len(readings),
        "recommendations": [r.to_dict() for r in recommendations]
    }
    
    output_dir = os.path.join(PROJECT_ROOT, "data")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "latest_analysis.json")
    with open(output_path, "w") as f:
        json.dump(export_data, f, indent=2)
    
    print(f"  Analysis exported to: {output_path}")
    print("\n  ✅ Demo complete!")


if __name__ == "__main__":
    main()
