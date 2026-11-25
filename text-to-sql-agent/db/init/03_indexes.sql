-- Indexes for the most common analytic filters. Created at init so the
-- sample loader benefits; the full TLC loader drops and rebuilds them
-- around the bulk COPY for speed.
CREATE INDEX IF NOT EXISTS idx_trips_pickup_datetime ON trips (pickup_datetime);
CREATE INDEX IF NOT EXISTS idx_trips_pickup_location ON trips (pickup_location_id);
CREATE INDEX IF NOT EXISTS idx_trips_dropoff_location ON trips (dropoff_location_id);
CREATE INDEX IF NOT EXISTS idx_trips_payment_type ON trips (payment_type_id);
CREATE INDEX IF NOT EXISTS idx_trips_taxi_type ON trips (taxi_type);
