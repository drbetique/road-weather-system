
-- Road Weather Monitoring System Database Schema
-- Version: 1.0

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- PostGIS removed: not available on Railway

-- Stations table: metadata about road weather stations
CREATE TABLE IF NOT EXISTS stations (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    -- location column removed: PostGIS not available
    distance_km DOUBLE PRECISION,
    collection_status VARCHAR(50),
    state VARCHAR(50),
    road_number VARCHAR(20),
    road_section VARCHAR(50),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for geographic queries
-- GIST index removed: PostGIS not available
CREATE INDEX IF NOT EXISTS idx_stations_distance ON stations(distance_km);

-- Weather readings table: time-series sensor data
CREATE TABLE IF NOT EXISTS weather_readings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    station_id INTEGER REFERENCES stations(id),
    measured_at TIMESTAMP WITH TIME ZONE NOT NULL,
    received_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Temperature sensors
    air_temp DOUBLE PRECISION,
    road_temp_1 DOUBLE PRECISION,
    road_temp_2 DOUBLE PRECISION,
    ground_temp DOUBLE PRECISION,
    dew_point DOUBLE PRECISION,
    freezing_point DOUBLE PRECISION,
    
    -- Humidity and precipitation
    humidity DOUBLE PRECISION,
    precipitation_status INTEGER,
    precipitation_intensity DOUBLE PRECISION,
    precipitation_sum DOUBLE PRECISION,
    
    -- Road condition sensors
    road_condition INTEGER,
    friction DOUBLE PRECISION,
    warning_status INTEGER,
    
    -- Salt monitoring
    salt_amount DOUBLE PRECISION,
    salt_concentration DOUBLE PRECISION,
    
    -- Wind
    wind_speed DOUBLE PRECISION,
    wind_gust DOUBLE PRECISION,
    wind_direction DOUBLE PRECISION,
    
    -- Visibility
    visibility_km DOUBLE PRECISION
);

-- Indexes for time-series queries
CREATE INDEX IF NOT EXISTS idx_readings_station_time 
    ON weather_readings(station_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_readings_measured_at 
    ON weather_readings(measured_at DESC);
-- A station repeats the same measured_at until it measures again; this makes a repeat
-- a skipped insert instead of a duplicate row.
CREATE UNIQUE INDEX IF NOT EXISTS uq_weather_readings_station_time 
    ON weather_readings(station_id, measured_at);

-- Partitioning by month for better performance (optional, for production)
-- CREATE TABLE weather_readings_2026_02 PARTITION OF weather_readings
--     FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');

-- Treatment recommendations table
CREATE TABLE IF NOT EXISTS treatment_recommendations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    station_id INTEGER REFERENCES stations(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Recommendation details
    urgency_level INTEGER NOT NULL,  -- 0-5
    urgency_name VARCHAR(20) NOT NULL,
    treatment_type VARCHAR(30) NOT NULL,
    salt_amount_recommended DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    next_check_minutes INTEGER,
    
    -- Analysis factors (JSON for flexibility)
    reasoning JSONB,
    
    -- Source reading
    weather_reading_id UUID REFERENCES weather_readings(id)
);

CREATE INDEX IF NOT EXISTS idx_recommendations_station_time 
    ON treatment_recommendations(station_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recommendations_urgency 
    ON treatment_recommendations(urgency_level DESC, created_at DESC);

-- Treatment actions table: actual treatments performed
CREATE TABLE IF NOT EXISTS treatment_actions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    station_id INTEGER REFERENCES stations(id),
    recommendation_id UUID REFERENCES treatment_recommendations(id),
    
    -- Action details
    action_type VARCHAR(30) NOT NULL,
    salt_amount_applied DOUBLE PRECISION,
    sand_amount_applied DOUBLE PRECISION,
    
    -- Timing
    planned_at TIMESTAMP WITH TIME ZONE,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    
    -- Operator info
    operator_id VARCHAR(50),
    vehicle_id VARCHAR(50),
    notes TEXT,
    
    -- Status
    status VARCHAR(20) DEFAULT 'planned',  -- planned, in_progress, completed, cancelled
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_actions_station_time 
    ON treatment_actions(station_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_actions_status 
    ON treatment_actions(status, planned_at);

-- FMI forecasts table
CREATE TABLE IF NOT EXISTS weather_forecasts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    station_id INTEGER REFERENCES stations(id),
    forecast_time TIMESTAMP WITH TIME ZONE NOT NULL,
    fetched_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Forecast data
    air_temp DOUBLE PRECISION,
    precipitation DOUBLE PRECISION,
    precipitation_type VARCHAR(20),  -- rain, snow, sleet
    wind_speed DOUBLE PRECISION,
    wind_direction DOUBLE PRECISION,
    humidity DOUBLE PRECISION,
    cloud_cover DOUBLE PRECISION,
    
    -- Source
    source VARCHAR(20) DEFAULT 'FMI',
    model VARCHAR(30)  -- HARMONIE, etc.
);

CREATE INDEX IF NOT EXISTS idx_forecasts_station_time 
    ON weather_forecasts(station_id, forecast_time);

-- Alerts table: active alerts for dashboard
CREATE TABLE IF NOT EXISTS alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    station_id INTEGER REFERENCES stations(id),
    
    alert_type VARCHAR(30) NOT NULL,  -- frost_warning, ice_detected, low_friction, etc.
    severity VARCHAR(20) NOT NULL,     -- info, warning, critical
    message TEXT NOT NULL,
    
    -- Timing
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    acknowledged_at TIMESTAMP WITH TIME ZONE,
    resolved_at TIMESTAMP WITH TIME ZONE,
    
    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    acknowledged_by VARCHAR(50)
);

CREATE INDEX IF NOT EXISTS idx_alerts_active 
    ON alerts(is_active, severity, created_at DESC);

-- System logs table
CREATE TABLE IF NOT EXISTS system_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    level VARCHAR(10) NOT NULL,
    component VARCHAR(50),
    message TEXT,
    details JSONB
);

CREATE INDEX IF NOT EXISTS idx_logs_time ON system_logs(timestamp DESC);

-- Views for common queries

-- Current conditions view
CREATE OR REPLACE VIEW current_conditions AS
SELECT DISTINCT ON (wr.station_id)
    s.id AS station_id,
    s.name AS station_name,
    s.distance_km,
    wr.measured_at,
    wr.air_temp,
    wr.road_temp_1,
    wr.road_temp_2,
    wr.dew_point,
    wr.humidity,
    wr.friction,
    wr.road_condition,
    wr.salt_amount,
    wr.warning_status,
    wr.precipitation_intensity,
    wr.visibility_km
FROM weather_readings wr
JOIN stations s ON wr.station_id = s.id
ORDER BY wr.station_id, wr.measured_at DESC;

-- Latest recommendations view
CREATE OR REPLACE VIEW latest_recommendations AS
SELECT DISTINCT ON (tr.station_id)
    s.id AS station_id,
    s.name AS station_name,
    s.distance_km,
    tr.created_at,
    tr.urgency_level,
    tr.urgency_name,
    tr.treatment_type,
    tr.salt_amount_recommended,
    tr.confidence,
    tr.reasoning
FROM treatment_recommendations tr
JOIN stations s ON tr.station_id = s.id
ORDER BY tr.station_id, tr.created_at DESC;

-- Daily statistics view
CREATE OR REPLACE VIEW daily_statistics AS
SELECT 
    station_id,
    DATE(measured_at) AS date,
    COUNT(*) AS reading_count,
    AVG(air_temp) AS avg_air_temp,
    MIN(air_temp) AS min_air_temp,
    MAX(air_temp) AS max_air_temp,
    AVG(road_temp_1) AS avg_road_temp,
    MIN(road_temp_1) AS min_road_temp,
    AVG(friction) AS avg_friction,
    MIN(friction) AS min_friction,
    SUM(precipitation_intensity) AS total_precipitation,
    AVG(salt_amount) AS avg_salt_amount
FROM weather_readings
GROUP BY station_id, DATE(measured_at);

-- Functions

-- Function to get treatment history for a station
CREATE OR REPLACE FUNCTION get_treatment_history(
    p_station_id INTEGER,
    p_days INTEGER DEFAULT 7
)
RETURNS TABLE (
    action_date DATE,
    action_count INTEGER,
    total_salt_kg DOUBLE PRECISION,
    treatment_types TEXT[]
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        DATE(ta.completed_at) AS action_date,
        COUNT(*)::INTEGER AS action_count,
        SUM(ta.salt_amount_applied) AS total_salt_kg,
        ARRAY_AGG(DISTINCT ta.action_type) AS treatment_types
    FROM treatment_actions ta
    WHERE ta.station_id = p_station_id
      AND ta.completed_at >= NOW() - (p_days || ' days')::INTERVAL
      AND ta.status = 'completed'
    GROUP BY DATE(ta.completed_at)
    ORDER BY action_date DESC;
END;
$$ LANGUAGE plpgsql;

-- Function to calculate salt usage efficiency
CREATE OR REPLACE FUNCTION calculate_salt_efficiency(
    p_station_id INTEGER,
    p_start_date TIMESTAMP,
    p_end_date TIMESTAMP
)
RETURNS TABLE (
    total_salt_used DOUBLE PRECISION,
    avg_friction_before DOUBLE PRECISION,
    avg_friction_after DOUBLE PRECISION,
    improvement_ratio DOUBLE PRECISION
) AS $$
BEGIN
    -- This would be implemented based on actual treatment-to-improvement correlation
    RETURN QUERY
    SELECT 
        SUM(ta.salt_amount_applied),
        0.0::DOUBLE PRECISION,  -- Placeholder
        0.0::DOUBLE PRECISION,  -- Placeholder
        0.0::DOUBLE PRECISION   -- Placeholder
    FROM treatment_actions ta
    WHERE ta.station_id = p_station_id
      AND ta.completed_at BETWEEN p_start_date AND p_end_date;
END;
$$ LANGUAGE plpgsql;
