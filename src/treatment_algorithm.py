"""
Treatment Decision Algorithm
Determines optimal timing and amount for winter maintenance treatments
Based on road weather conditions and forecasts
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional, Tuple

from config import TreatmentThresholds, ROAD_CONDITIONS, WARNING_CODES
from digitraffic_client import WeatherReading

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TreatmentUrgency(Enum):
    """Urgency levels for treatment decisions"""
    NONE = 0		# No treatment needed
    MONITOR = 1		# Continue monitoring
    PREVENTIVE = 2	# Preventive treatment recommended
    STANDARD = 3	# Standard treatment needed
    URGENT = 4		# Immediate treatment needed
    CRITICAL = 5	# Emergency response needed

    def __gt__(self, other):
        return self.value > other.value

    def __lt__(self, other):
        return self.value < other.value

    def __ge__(self, other):
        return self.value >= other.value

    def __le__(self, other):
        return self.value <= other.value


class TreatmentType(Enum):
    """Types of winter maintenance treatments"""
    NONE = "none"
    SALT_DRY = "dry_salt"           # Dry salt spreading
    SALT_BRINE = "salt_brine"       # Pre-wetted salt or brine
    SAND = "sand"                   # Sand/grit for traction
    SALT_SAND_MIX = "salt_sand"     # Combined treatment
    PLOWING = "plowing"             # Snow removal
    PLOW_AND_SALT = "plow_salt"     # Combined plowing and salting


@dataclass
class TreatmentRecommendation:
    """Treatment recommendation for a road segment"""
    station_id: int
    station_name: str
    timestamp: datetime
    urgency: TreatmentUrgency
    treatment_type: TreatmentType
    salt_amount_gm2: Optional[float]  # Recommended salt amount g/m²
    reasoning: List[str]              # Explanation of decision factors
    confidence: float                 # 0.0-1.0 confidence in recommendation
    next_check_minutes: int           # When to re-evaluate
    
    def to_dict(self) -> dict:
        return {
            "station_id": self.station_id,
            "station_name": self.station_name,
            "timestamp": self.timestamp.isoformat(),
            "urgency": self.urgency.name,
            "urgency_level": self.urgency.value,
            "treatment_type": self.treatment_type.value,
            "salt_amount_gm2": self.salt_amount_gm2,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "next_check_minutes": self.next_check_minutes,
        }


class TreatmentDecisionEngine:
    """
    Decision engine for winter maintenance treatments
    
    Uses multiple factors:
    1. Current road surface temperature
    2. Dew point and frost risk
    3. Current friction coefficient
    4. Existing salt residual
    5. Precipitation status and forecast
    6. Warning indicators from stations
    """
    
    def __init__(self, thresholds: TreatmentThresholds = None):
        self.thresholds = thresholds or TreatmentThresholds()
    
    def analyze(self, reading: WeatherReading, station_name: str = "") -> TreatmentRecommendation:
        """
        Analyze current conditions and generate treatment recommendation
        """
        reasoning = []
        urgency = TreatmentUrgency.NONE
        treatment_type = TreatmentType.NONE
        salt_amount = None
        confidence = 0.8  # Base confidence
        next_check = 60   # Default 60 minutes
        
        # Use average of road temp sensors if both available
        road_temp = self._get_road_temp(reading)
        
        # === Factor 1: Road Temperature Analysis ===
        if road_temp is not None:
            if road_temp <= self.thresholds.ROAD_TEMP_CRITICAL:
                reasoning.append(f"Road temp {road_temp}°C is at/below freezing point")
                urgency = max(urgency, TreatmentUrgency.STANDARD)
                next_check = 15
            elif road_temp <= self.thresholds.ROAD_TEMP_WARNING:
                reasoning.append(f"Road temp {road_temp}°C approaching freezing")
                urgency = max(urgency, TreatmentUrgency.PREVENTIVE)
                next_check = 30
            else:
                reasoning.append(f"Road temp {road_temp}°C is safe")
        
        # === Factor 2: Dew Point / Frost Risk ===
        if road_temp is not None and reading.dew_point is not None:
            dew_margin = road_temp - reading.dew_point
            if dew_margin <= 0:
                reasoning.append(f"Condensation/frost active (road {road_temp}°C <= dew point {reading.dew_point}°C)")
                urgency = max(urgency, TreatmentUrgency.URGENT)
                next_check = 10
            elif dew_margin <= self.thresholds.DEW_POINT_MARGIN:
                reasoning.append(f"Frost risk: road temp only {dew_margin:.1f}°C above dew point")
                urgency = max(urgency, TreatmentUrgency.PREVENTIVE)
                next_check = 20
        
        # === Factor 3: Friction Analysis ===
        if reading.friction is not None:
            if reading.friction < self.thresholds.FRICTION_CRITICAL:
                reasoning.append(f"Critical friction {reading.friction:.2f} (slippery conditions)")
                urgency = max(urgency, TreatmentUrgency.CRITICAL)
                next_check = 5
            elif reading.friction < self.thresholds.FRICTION_WARNING:
                reasoning.append(f"Low friction {reading.friction:.2f} (reduced traction)")
                urgency = max(urgency, TreatmentUrgency.STANDARD)
                next_check = 15
            elif reading.friction >= self.thresholds.FRICTION_GOOD:
                reasoning.append(f"Good friction {reading.friction:.2f}")
        
        # === Factor 4: Existing Salt Residual ===
        if reading.salt_amount is not None:
            if reading.salt_amount >= self.thresholds.SALT_MIN_EFFECTIVE:
                reasoning.append(f"Salt residual {reading.salt_amount:.1f} g/m² provides protection")
                # Reduce urgency if salt is present and conditions aren't critical
                if urgency == TreatmentUrgency.PREVENTIVE:
                    urgency = TreatmentUrgency.MONITOR
            else:
                reasoning.append(f"Low salt residual {reading.salt_amount:.1f} g/m²")
        
        # === Factor 5: Warning Indicators ===
        if reading.warning is not None and reading.warning > 0:
            warning_text = WARNING_CODES.get(reading.warning, f"Code {reading.warning}")
            reasoning.append(f"Station warning: {warning_text}")
            if reading.warning in [2, 3]:  # Ice or frost warning
                urgency = max(urgency, TreatmentUrgency.STANDARD)
        
        # === Factor 6: Road Condition Status ===
        if reading.road_condition is not None:
            condition_text = ROAD_CONDITIONS.get(reading.road_condition, f"Code {reading.road_condition}")
            reasoning.append(f"Road condition: {condition_text}")
            
            if reading.road_condition in [5, 6, 7]:  # Frost, partly icy, icy
                urgency = max(urgency, TreatmentUrgency.URGENT)
            elif reading.road_condition in [2, 3]:  # Moist or wet
                # Check if freezing is possible
                if road_temp is not None and road_temp <= self.thresholds.ROAD_TEMP_WARNING:
                    urgency = max(urgency, TreatmentUrgency.PREVENTIVE)
        
        # === Factor 7: Precipitation ===
        if reading.precip_intensity is not None and reading.precip_intensity > 0:
            if reading.precip_intensity >= self.thresholds.PRECIP_HEAVY:
                reasoning.append(f"Heavy precipitation {reading.precip_intensity:.1f} mm/h")
                if road_temp is not None and road_temp <= 2.0:
                    urgency = max(urgency, TreatmentUrgency.URGENT)
                    treatment_type = TreatmentType.PLOW_AND_SALT
            elif reading.precip_intensity >= self.thresholds.PRECIP_MODERATE:
                reasoning.append(f"Moderate precipitation {reading.precip_intensity:.1f} mm/h")
        
        # === Determine Treatment Type and Salt Amount ===
        treatment_type, salt_amount = self._determine_treatment(
            urgency, road_temp, reading.friction, reading.salt_amount, reading.precip_intensity
        )
        
        # Adjust confidence based on data completeness
        confidence = self._calculate_confidence(reading)
        
        return TreatmentRecommendation(
            station_id=reading.station_id,
            station_name=station_name,
            timestamp=reading.timestamp,
            urgency=urgency,
            treatment_type=treatment_type,
            salt_amount_gm2=salt_amount,
            reasoning=reasoning,
            confidence=confidence,
            next_check_minutes=next_check,
        )
    
    def _get_road_temp(self, reading: WeatherReading) -> Optional[float]:
        """Get representative road temperature"""
        temps = [t for t in [reading.road_temp_1, reading.road_temp_2] if t is not None]
        if temps:
            return sum(temps) / len(temps)
        return None
    
    def _determine_treatment(
        self,
        urgency: TreatmentUrgency,
        road_temp: Optional[float],
        friction: Optional[float],
        current_salt: Optional[float],
        precip: Optional[float]
    ) -> Tuple[TreatmentType, Optional[float]]:
        """Determine treatment type and salt amount based on conditions"""
        
        if urgency == TreatmentUrgency.NONE:
            return TreatmentType.NONE, None
        
        if urgency == TreatmentUrgency.MONITOR:
            return TreatmentType.NONE, None
        
        # Calculate target salt amount
        base_salt = 10.0  # Base amount g/m²
        
        if road_temp is not None:
            if road_temp < -10:
                base_salt = 20.0  # More salt needed at very low temps
            elif road_temp < -5:
                base_salt = 15.0
            elif road_temp < 0:
                base_salt = 12.0
        
        # Reduce if existing salt present
        if current_salt is not None and current_salt > 0:
            base_salt = max(0, base_salt - current_salt * 0.5)
        
        # Cap at environmental maximum
        salt_amount = min(base_salt, self.thresholds.SALT_MAX_RECOMMENDED)
        
        # Determine treatment type
        if precip is not None and precip >= self.thresholds.PRECIP_MODERATE:
            treatment_type = TreatmentType.PLOW_AND_SALT
        elif urgency >= TreatmentUrgency.URGENT:
            treatment_type = TreatmentType.SALT_BRINE  # Faster acting
        elif urgency == TreatmentUrgency.PREVENTIVE:
            treatment_type = TreatmentType.SALT_BRINE  # Preventive brine
            salt_amount = salt_amount * 0.7  # Reduce for preventive
        else:
            treatment_type = TreatmentType.SALT_DRY
        
        # Very low friction might need sand
        if friction is not None and friction < 0.25:
            treatment_type = TreatmentType.SALT_SAND_MIX
        
        return treatment_type, round(salt_amount, 1)
    
    def _calculate_confidence(self, reading: WeatherReading) -> float:
        """Calculate confidence based on available data"""
        confidence = 1.0
        
        # Reduce confidence for missing critical sensors
        if reading.road_temp_1 is None and reading.road_temp_2 is None:
            confidence -= 0.2
        if reading.dew_point is None:
            confidence -= 0.1
        if reading.friction is None:
            confidence -= 0.15
        if reading.road_condition is None:
            confidence -= 0.1
        
        return max(0.3, confidence)  # Minimum 30% confidence


def analyze_area(readings: List[WeatherReading], station_names: dict = None) -> List[TreatmentRecommendation]:
    """Analyze multiple stations and return recommendations"""
    engine = TreatmentDecisionEngine()
    recommendations = []
    
    for reading in readings:
        name = station_names.get(reading.station_id, f"Station {reading.station_id}") if station_names else ""
        rec = engine.analyze(reading, name)
        recommendations.append(rec)
    
    # Sort by urgency (highest first)
    recommendations.sort(key=lambda r: r.urgency.value, reverse=True)
    
    return recommendations


def main():
    """Test the decision engine with sample data"""
    from digitraffic_client import DigitrafficClient
    
    client = DigitrafficClient()
    nearby = client.get_nearby_stations(50)
    station_names = {s.id: s.name for s in nearby}
    
    station_ids = [s.id for s in nearby[:5]]
    readings = client.get_station_data(station_ids)
    
    print("\n=== Treatment Recommendations for Forssa Area ===")
    print("=" * 60)
    
    recommendations = analyze_area(readings, station_names)
    
    for rec in recommendations:
        print(f"\n📍 {rec.station_name}")
        print(f"   Urgency: {rec.urgency.name} (level {rec.urgency.value})")
        print(f"   Treatment: {rec.treatment_type.value}")
        if rec.salt_amount_gm2:
            print(f"   Salt amount: {rec.salt_amount_gm2} g/m²")
        print(f"   Confidence: {rec.confidence:.0%}")
        print(f"   Re-check in: {rec.next_check_minutes} minutes")
        print(f"   Reasoning:")
        for reason in rec.reasoning:
            print(f"     • {reason}")


if __name__ == "__main__":
    main()
