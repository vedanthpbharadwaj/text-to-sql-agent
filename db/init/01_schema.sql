-- NYC TLC taxi trip schema.
-- Column comments are read by the schema linker and SQL writer agents,
-- so they double as documentation for the LLM.

CREATE TABLE IF NOT EXISTS taxi_zones (
    location_id   INTEGER PRIMARY KEY,
    borough       TEXT NOT NULL,
    zone          TEXT NOT NULL,
    service_zone  TEXT
);
COMMENT ON TABLE  taxi_zones IS 'TLC taxi zone lookup. Join trips.pickup_location_id or trips.dropoff_location_id to location_id.';
COMMENT ON COLUMN taxi_zones.borough IS 'Borough name: Manhattan, Brooklyn, Queens, Bronx, Staten Island, EWR, Unknown, N/A';
COMMENT ON COLUMN taxi_zones.zone IS 'Zone name, e.g. JFK Airport, Times Sq/Theatre District, Midtown Center';
COMMENT ON COLUMN taxi_zones.service_zone IS 'Yellow Zone, Boro Zone, Airports, EWR or N/A';

CREATE TABLE IF NOT EXISTS vendors (
    vendor_id    INTEGER PRIMARY KEY,
    vendor_name  TEXT NOT NULL
);
COMMENT ON TABLE vendors IS 'Technology provider that recorded the trip.';

CREATE TABLE IF NOT EXISTS payment_types (
    payment_type_id  INTEGER PRIMARY KEY,
    description      TEXT NOT NULL
);
COMMENT ON TABLE payment_types IS 'How the passenger paid. 1 = Credit card, 2 = Cash. Tips are only recorded for credit card trips.';

CREATE TABLE IF NOT EXISTS rate_codes (
    rate_code_id  INTEGER PRIMARY KEY,
    description   TEXT NOT NULL
);
COMMENT ON TABLE rate_codes IS 'Final rate code in effect at the end of the trip.';

CREATE TABLE IF NOT EXISTS trips (
    trip_id               BIGSERIAL PRIMARY KEY,
    taxi_type             TEXT NOT NULL,
    vendor_id             INTEGER,
    pickup_datetime       TIMESTAMP NOT NULL,
    dropoff_datetime      TIMESTAMP NOT NULL,
    passenger_count       INTEGER,
    trip_distance         DOUBLE PRECISION,
    rate_code_id          INTEGER,
    pickup_location_id    INTEGER,
    dropoff_location_id   INTEGER,
    payment_type_id       INTEGER,
    fare_amount           NUMERIC(10, 2),
    extra                 NUMERIC(10, 2),
    mta_tax               NUMERIC(10, 2),
    tip_amount            NUMERIC(10, 2),
    tolls_amount          NUMERIC(10, 2),
    improvement_surcharge NUMERIC(10, 2),
    congestion_surcharge  NUMERIC(10, 2),
    airport_fee           NUMERIC(10, 2),
    total_amount          NUMERIC(10, 2)
);
COMMENT ON TABLE  trips IS 'One row per yellow or green taxi trip (NYC TLC trip records, 2023).';
COMMENT ON COLUMN trips.taxi_type IS '''yellow'' or ''green''';
COMMENT ON COLUMN trips.vendor_id IS 'References vendors.vendor_id';
COMMENT ON COLUMN trips.pickup_datetime IS 'Meter engaged time (local NYC time). Use for all date filters.';
COMMENT ON COLUMN trips.dropoff_datetime IS 'Meter disengaged time. Trip duration = dropoff_datetime - pickup_datetime.';
COMMENT ON COLUMN trips.passenger_count IS 'Driver-entered passenger count; may be NULL or 0.';
COMMENT ON COLUMN trips.trip_distance IS 'Trip distance in miles reported by the taximeter.';
COMMENT ON COLUMN trips.rate_code_id IS 'References rate_codes.rate_code_id (2 = JFK, 3 = Newark, 5 = Negotiated fare).';
COMMENT ON COLUMN trips.pickup_location_id IS 'References taxi_zones.location_id';
COMMENT ON COLUMN trips.dropoff_location_id IS 'References taxi_zones.location_id';
COMMENT ON COLUMN trips.payment_type_id IS 'References payment_types.payment_type_id (1 = Credit card, 2 = Cash).';
COMMENT ON COLUMN trips.fare_amount IS 'Time-and-distance fare in USD.';
COMMENT ON COLUMN trips.tip_amount IS 'Tip in USD. Only populated for credit card payments; cash tips are not recorded.';
COMMENT ON COLUMN trips.tolls_amount IS 'Total tolls paid in USD.';
COMMENT ON COLUMN trips.congestion_surcharge IS 'NYS congestion surcharge in USD.';
COMMENT ON COLUMN trips.airport_fee IS 'Airport pickup fee in USD (LaGuardia and JFK pickups).';
COMMENT ON COLUMN trips.total_amount IS 'Total charged to passengers in USD (excludes cash tips).';

-- Static lookup values from the TLC data dictionary.
INSERT INTO vendors VALUES
    (1, 'Creative Mobile Technologies'),
    (2, 'Curb Mobility'),
    (6, 'Myle Technologies'),
    (7, 'Helix')
ON CONFLICT DO NOTHING;

INSERT INTO payment_types VALUES
    (0, 'Flex Fare trip'),
    (1, 'Credit card'),
    (2, 'Cash'),
    (3, 'No charge'),
    (4, 'Dispute'),
    (5, 'Unknown'),
    (6, 'Voided trip')
ON CONFLICT DO NOTHING;

INSERT INTO rate_codes VALUES
    (1, 'Standard rate'),
    (2, 'JFK'),
    (3, 'Newark'),
    (4, 'Nassau or Westchester'),
    (5, 'Negotiated fare'),
    (6, 'Group ride'),
    (99, 'Unknown')
ON CONFLICT DO NOTHING;
