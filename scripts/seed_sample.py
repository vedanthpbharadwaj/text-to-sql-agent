"""Seed a small synthetic dataset with the same schema as the real TLC data.

Used for local development without downloading gigabytes of parquet, and by
CI to run the integration tests and validate every benchmark gold query.
The distributions are rough approximations of real taxi behaviour (airport
trips are long, tips only on card payments, rush-hour peaks, etc.).

    python scripts/seed_sample.py --rows 200000
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys

import psycopg

# (location_id, borough, zone, service_zone) — a subset of the real TLC lookup.
ZONES = [
    (1, "EWR", "Newark Airport", "EWR"),
    (7, "Queens", "Astoria", "Boro Zone"),
    (41, "Manhattan", "Central Harlem", "Boro Zone"),
    (43, "Manhattan", "Central Park", "Yellow Zone"),
    (48, "Manhattan", "Clinton East", "Yellow Zone"),
    (65, "Brooklyn", "Downtown Brooklyn/MetroTech", "Boro Zone"),
    (68, "Manhattan", "East Chelsea", "Yellow Zone"),
    (69, "Bronx", "East Concourse/Concourse Village", "Boro Zone"),
    (74, "Manhattan", "East Harlem North", "Boro Zone"),
    (79, "Manhattan", "East Village", "Yellow Zone"),
    (82, "Queens", "Elmhurst", "Boro Zone"),
    (87, "Manhattan", "Financial District North", "Yellow Zone"),
    (95, "Queens", "Forest Hills", "Boro Zone"),
    (97, "Brooklyn", "Fort Greene", "Boro Zone"),
    (107, "Manhattan", "Gramercy", "Yellow Zone"),
    (129, "Queens", "Jackson Heights", "Boro Zone"),
    (132, "Queens", "JFK Airport", "Airports"),
    (138, "Queens", "LaGuardia Airport", "Airports"),
    (140, "Manhattan", "Lenox Hill East", "Yellow Zone"),
    (142, "Manhattan", "Lincoln Square East", "Yellow Zone"),
    (161, "Manhattan", "Midtown Center", "Yellow Zone"),
    (162, "Manhattan", "Midtown East", "Yellow Zone"),
    (168, "Bronx", "Mott Haven/Port Morris", "Boro Zone"),
    (170, "Manhattan", "Murray Hill", "Yellow Zone"),
    (181, "Brooklyn", "Park Slope", "Boro Zone"),
    (186, "Manhattan", "Penn Station/Madison Sq West", "Yellow Zone"),
    (206, "Staten Island", "Saint George/New Brighton", "Boro Zone"),
    (221, "Staten Island", "Stapleton", "Boro Zone"),
    (230, "Manhattan", "Times Sq/Theatre District", "Yellow Zone"),
    (234, "Manhattan", "Union Sq", "Yellow Zone"),
    (236, "Manhattan", "Upper East Side North", "Yellow Zone"),
    (237, "Manhattan", "Upper East Side South", "Yellow Zone"),
    (239, "Manhattan", "Upper West Side South", "Yellow Zone"),
    (247, "Bronx", "West Concourse", "Boro Zone"),
    (255, "Brooklyn", "Williamsburg (North Side)", "Boro Zone"),
    (264, "Unknown", "NV", "N/A"),
]
ZONE_BY_ID = {z[0]: z for z in ZONES}
AIRPORTS = {1, 132, 138}

# Relative pickup weights per taxi type.
YELLOW_WEIGHTS = {
    z[0]: (30 if z[3] == "Yellow Zone" else 14 if z[0] in (132, 138) else 1 if z[0] == 1
           else 0.6 if z[0] == 264 else 2)
    for z in ZONES
}
GREEN_WEIGHTS = {
    z[0]: (20 if z[3] == "Boro Zone" else 1 if z[0] in AIRPORTS else 2) for z in ZONES
}
HOUR_WEIGHTS = [3, 2, 1.5, 1, 1, 1.5, 3, 5, 6, 6, 6, 6, 6.5, 6.5, 7, 7.5, 8, 9, 9, 8, 7, 6.5, 6, 4.5]
MONTH_WEIGHTS = [8, 7.5, 9, 9, 9.5, 9, 8, 8, 8.5, 9.5, 9, 9.5]

COLUMNS = [
    "taxi_type", "vendor_id", "pickup_datetime", "dropoff_datetime", "passenger_count",
    "trip_distance", "rate_code_id", "pickup_location_id", "dropoff_location_id",
    "payment_type_id", "fare_amount", "extra", "mta_tax", "tip_amount", "tolls_amount",
    "improvement_surcharge", "congestion_surcharge", "airport_fee", "total_amount",
]


def make_trip(rng: random.Random, year: int) -> tuple:
    taxi_type = "yellow" if rng.random() < 0.93 else "green"
    weights = YELLOW_WEIGHTS if taxi_type == "yellow" else GREEN_WEIGHTS
    pu = rng.choices(list(weights), list(weights.values()))[0]
    if pu in AIRPORTS:
        do = rng.choices(list(YELLOW_WEIGHTS), list(YELLOW_WEIGHTS.values()))[0]
    elif rng.random() < 0.04:
        do = rng.choice([132, 138, 1])
    else:
        do = rng.choices(list(weights), list(weights.values()))[0]

    month = rng.choices(range(1, 13), MONTH_WEIGHTS)[0]
    days = (dt.date(year + (month == 12), month % 12 + 1, 1) - dt.date(year, month, 1)).days
    pickup = dt.datetime(year, month, rng.randint(1, days),
                         rng.choices(range(24), HOUR_WEIGHTS)[0],
                         rng.randint(0, 59), rng.randint(0, 59))

    airport_trip = pu in AIRPORTS or do in AIRPORTS
    if rng.random() < 0.01:
        distance = 0.0
    elif airport_trip:
        distance = round(rng.uniform(9, 22), 2)
    else:
        distance = round(min(rng.lognormvariate(0.6, 0.75), 40), 2)
    speed_mph = rng.uniform(6, 22) if not airport_trip else rng.uniform(15, 30)
    minutes = max(1.0, distance / speed_mph * 60 + rng.uniform(1, 6))
    dropoff = pickup + dt.timedelta(minutes=minutes)

    rate_code = 1
    if pu == 132 or do == 132:
        rate_code = 2 if rng.random() < 0.6 else 1
    elif do == 1:
        rate_code = 3
    elif rng.random() < 0.01:
        rate_code = 5
    elif rng.random() < 0.003:
        rate_code = 99

    if rate_code == 2:
        fare = 70.0
    else:
        fare = round(3.0 + 3.5 * distance + 0.35 * minutes, 2)
        if rate_code == 3:
            fare += 20.0

    payment = rng.choices([1, 2, 3, 4, 0], [74, 21, 2, 1.5, 1.5])[0]
    if payment == 1 and rng.random() > 0.12:
        tip = round(fare * rng.uniform(0.1, 0.3), 2)
    else:
        tip = 0.0

    extra = rng.choice([0.0, 1.0, 2.5]) if taxi_type == "yellow" else rng.choice([0.0, 1.0])
    mta_tax = 0.5
    improvement = 1.0
    tolls = round(rng.choice([0.0, 6.94]), 2) if airport_trip else 0.0
    pu_manhattan = ZONE_BY_ID[pu][1] == "Manhattan"
    do_manhattan = ZONE_BY_ID[do][1] == "Manhattan"
    congestion = 2.5 if (pu_manhattan or do_manhattan) and taxi_type == "yellow" else 0.0
    airport_fee = 1.75 if taxi_type == "yellow" and pu in (132, 138) else (0.0 if taxi_type == "yellow" else None)
    total = round(fare + extra + mta_tax + tip + tolls + improvement + congestion + (airport_fee or 0), 2)

    r = rng.random()
    passengers = None if r < 0.02 else 0 if r < 0.03 else rng.choices([1, 2, 3, 4, 5, 6], [70, 15, 5, 3, 4, 3])[0]

    return (
        taxi_type, rng.choice([1, 2, 2]), pickup, dropoff, passengers, distance, rate_code,
        pu, do, payment, fare, extra, mta_tax, tip, tolls, improvement, congestion, airport_fee, total,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.environ.get("ADMIN_DATABASE_URL"))
    parser.add_argument("--rows", type=int, default=200_000)
    parser.add_argument("--year", type=int, default=2023)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    if not args.dsn:
        parser.error("Provide --dsn or set ADMIN_DATABASE_URL")

    rng = random.Random(args.seed)
    with psycopg.connect(args.dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO taxi_zones VALUES (%s, %s, %s, %s)
                   ON CONFLICT (location_id) DO NOTHING""",
                ZONES,
            )
            cur.execute("TRUNCATE trips RESTART IDENTITY")
            with cur.copy(f"COPY trips ({', '.join(COLUMNS)}) FROM STDIN") as copy:
                for _ in range(args.rows):
                    copy.write_row(make_trip(rng, args.year))
        conn.commit()
        conn.autocommit = True
        conn.execute("ANALYZE")
    print(f"Seeded {args.rows:,} synthetic trips and {len(ZONES)} zones")
    return 0


if __name__ == "__main__":
    sys.exit(main())
