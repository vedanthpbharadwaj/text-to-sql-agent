"""Generate eval/benchmark.jsonl: 220 questions with gold SQL.

45 hand-written questions cover joins, windows, percentiles and CTEs; the
rest come from parameterised templates (months, boroughs, zones, weekdays)
so the benchmark exercises the same reasoning across many filter values.

Every question states exactly which output columns it expects, because the
evaluation compares result sets, not SQL text.

    python eval/build_benchmark.py            # rewrites eval/benchmark.jsonl
"""

from __future__ import annotations

import calendar
import json
from pathlib import Path

YEAR = 2023
OUT = Path(__file__).parent / "benchmark.jsonl"
BOROUGHS = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def month_range(m: int) -> tuple[str, str]:
    start = f"{YEAR}-{m:02d}-01"
    end = f"{YEAR + (m == 12)}-{m % 12 + 1:02d}-01"
    return start, end


def mname(m: int) -> str:
    return calendar.month_name[m]


HAND_WRITTEN: list[tuple[str, str, str]] = [
    # (difficulty, question, gold_sql)
    ("easy", "How many trips are in the database in total?",
     "SELECT COUNT(*) FROM trips"),
    ("easy", "How many taxi zones are there in each borough? Return the borough and the number of zones.",
     "SELECT borough, COUNT(*) FROM taxi_zones GROUP BY borough"),
    ("easy", "What was the highest total amount recorded for a single trip?",
     "SELECT MAX(total_amount) FROM trips"),
    ("easy", "How many distinct pickup zones appear in the trip data?",
     "SELECT COUNT(DISTINCT pickup_location_id) FROM trips"),
    ("easy", "What is the average number of passengers per trip? Round to 2 decimals.",
     "SELECT ROUND(AVG(passenger_count)::numeric, 2) FROM trips"),
    ("easy", "How many trips had zero passengers recorded?",
     "SELECT COUNT(*) FROM trips WHERE passenger_count = 0"),
    ("easy", "How many trips had a trip distance of exactly 0 miles?",
     "SELECT COUNT(*) FROM trips WHERE trip_distance = 0"),
    ("easy", "What is the total amount of tolls paid across all trips? Round to 2 decimals.",
     "SELECT ROUND(SUM(tolls_amount)::numeric, 2) FROM trips"),
    ("easy", "How many trips were charged the JFK rate code?",
     "SELECT COUNT(*) FROM trips WHERE rate_code_id = 2"),
    ("easy", "How many green taxi trips are in the data?",
     "SELECT COUNT(*) FROM trips WHERE taxi_type = 'green'"),
    ("medium", "How many trips were there for each payment type? Return the payment type description and the trip count.",
     """SELECT p.description, COUNT(*) FROM trips t
        JOIN payment_types p ON p.payment_type_id = t.payment_type_id GROUP BY p.description"""),
    ("medium", "For trips longer than 1 mile, what is the average per-trip fare per mile (fare amount divided by trip distance)? Round to 2 decimals.",
     "SELECT ROUND(AVG(fare_amount / trip_distance::numeric), 2) FROM trips WHERE trip_distance > 1"),
    ("medium", "Which pickup borough had the highest average tip amount on credit card trips? Return the borough and the average tip rounded to 2 decimals.",
     """SELECT z.borough, ROUND(AVG(t.tip_amount)::numeric, 2) AS avg_tip FROM trips t
        JOIN taxi_zones z ON z.location_id = t.pickup_location_id
        WHERE t.payment_type_id = 1 GROUP BY z.borough ORDER BY avg_tip DESC LIMIT 1"""),
    ("medium", "List the 10 dropoff zones with the highest average total amount, among zones with at least 1,000 dropoffs. Return the zone and the average total amount rounded to 2 decimals.",
     """SELECT z.zone, ROUND(AVG(t.total_amount)::numeric, 2) AS avg_total FROM trips t
        JOIN taxi_zones z ON z.location_id = t.dropoff_location_id
        GROUP BY z.zone HAVING COUNT(*) >= 1000 ORDER BY avg_total DESC LIMIT 10"""),
    ("medium", "How many trips started and ended in the same zone?",
     "SELECT COUNT(*) FROM trips WHERE pickup_location_id = dropoff_location_id"),
    ("medium", "How many trips were picked up in Manhattan and dropped off in Brooklyn?",
     """SELECT COUNT(*) FROM trips t
        JOIN taxi_zones pu ON pu.location_id = t.pickup_location_id
        JOIN taxi_zones dz ON dz.location_id = t.dropoff_location_id
        WHERE pu.borough = 'Manhattan' AND dz.borough = 'Brooklyn'"""),
    ("hard", "Among pickups at JFK Airport, LaGuardia Airport and Newark Airport, what share did each airport have? Return the airport zone and its percentage of these airport pickups rounded to 2 decimals.",
     """SELECT z.zone, ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) FROM trips t
        JOIN taxi_zones z ON z.location_id = t.pickup_location_id
        WHERE z.zone IN ('JFK Airport', 'LaGuardia Airport', 'Newark Airport') GROUP BY z.zone"""),
    ("medium", "What was the average trip duration in minutes for trips from JFK Airport to Times Sq/Theatre District? Round to 1 decimal.",
     """SELECT ROUND(AVG(EXTRACT(EPOCH FROM t.dropoff_datetime - t.pickup_datetime) / 60)::numeric, 1)
        FROM trips t
        JOIN taxi_zones pu ON pu.location_id = t.pickup_location_id
        JOIN taxi_zones dz ON dz.location_id = t.dropoff_location_id
        WHERE pu.zone = 'JFK Airport' AND dz.zone = 'Times Sq/Theatre District'"""),
    ("medium", "On which day of the week were the most trips taken? Return the ISO day-of-week number (1 = Monday ... 7 = Sunday) and the trip count.",
     """SELECT EXTRACT(ISODOW FROM pickup_datetime) AS dow, COUNT(*) AS trips FROM trips
        GROUP BY dow ORDER BY trips DESC LIMIT 1"""),
    ("medium", "How many trips lasted longer than 60 minutes?",
     "SELECT COUNT(*) FROM trips WHERE dropoff_datetime - pickup_datetime > INTERVAL '60 minutes'"),
    ("easy", "What is the average total amount for trips with the negotiated fare rate code? Round to 2 decimals.",
     "SELECT ROUND(AVG(total_amount)::numeric, 2) FROM trips WHERE rate_code_id = 5"),
    ("medium", "How many cash trips had a total amount greater than $100?",
     "SELECT COUNT(*) FROM trips WHERE payment_type_id = 2 AND total_amount > 100"),
    ("medium", "Which vendor recorded the most trips? Return the vendor name and the trip count.",
     """SELECT v.vendor_name, COUNT(*) AS trips FROM trips t JOIN vendors v ON v.vendor_id = t.vendor_id
        GROUP BY v.vendor_name ORDER BY trips DESC LIMIT 1"""),
    ("medium", "What percentage of trips were charged an airport fee greater than zero? Round to 2 decimals.",
     "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE airport_fee > 0) / COUNT(*), 2) FROM trips"),
    ("hard", "What is the average congestion surcharge for trips picked up in Manhattan versus trips picked up anywhere else? Return two rows labelled 'Manhattan' and 'Other' with the average rounded to 2 decimals.",
     """SELECT CASE WHEN z.borough = 'Manhattan' THEN 'Manhattan' ELSE 'Other' END AS area,
               ROUND(AVG(t.congestion_surcharge)::numeric, 2)
        FROM trips t JOIN taxi_zones z ON z.location_id = t.pickup_location_id GROUP BY area"""),
    ("easy", "What was the average trip distance for each taxi type? Return the taxi type and the average distance rounded to 2 decimals.",
     "SELECT taxi_type, ROUND(AVG(trip_distance)::numeric, 2) FROM trips GROUP BY taxi_type"),
    ("medium", f"How many trips were there in each month of {YEAR}? Return the month number (1-12) and the trip count.",
     f"""SELECT EXTRACT(MONTH FROM pickup_datetime) AS month, COUNT(*) FROM trips
        WHERE pickup_datetime >= '{YEAR}-01-01' AND pickup_datetime < '{YEAR + 1}-01-01' GROUP BY month"""),
    ("medium", "On weekdays (Monday to Friday), how many trips were picked up in Midtown Center between 7:00 and 10:00 (7:00 inclusive, 10:00 exclusive)?",
     """SELECT COUNT(*) FROM trips t JOIN taxi_zones z ON z.location_id = t.pickup_location_id
        WHERE z.zone = 'Midtown Center' AND EXTRACT(ISODOW FROM t.pickup_datetime) BETWEEN 1 AND 5
          AND EXTRACT(HOUR FROM t.pickup_datetime) >= 7 AND EXTRACT(HOUR FROM t.pickup_datetime) < 10"""),
    ("medium", "For credit card trips with a positive fare dropped off at LaGuardia Airport, what was the average per-trip tip percentage (tip divided by fare, times 100)? Round to 2 decimals.",
     """SELECT ROUND(AVG(100.0 * t.tip_amount / t.fare_amount), 2) FROM trips t
        JOIN taxi_zones z ON z.location_id = t.dropoff_location_id
        WHERE z.zone = 'LaGuardia Airport' AND t.payment_type_id = 1 AND t.fare_amount > 0"""),
    ("hard", "For each of the five boroughs (Manhattan, Brooklyn, Queens, Bronx, Staten Island), which pickup zone had the most trips? Return the borough, the zone and the trip count.",
     """WITH c AS (
          SELECT z.borough, z.zone, COUNT(*) AS trips,
                 ROW_NUMBER() OVER (PARTITION BY z.borough ORDER BY COUNT(*) DESC) AS rn
          FROM trips t JOIN taxi_zones z ON z.location_id = t.pickup_location_id
          WHERE z.borough IN ('Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island')
          GROUP BY z.borough, z.zone)
        SELECT borough, zone, trips FROM c WHERE rn = 1"""),
    ("hard", "What is the median trip distance for yellow taxi trips? Round to 2 decimals.",
     """SELECT ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY trip_distance))::numeric, 2)
        FROM trips WHERE taxi_type = 'yellow'"""),
    ("hard", "What is the 90th percentile fare amount for each taxi type? Return the taxi type and the value rounded to 2 decimals.",
     """SELECT taxi_type, ROUND((PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY fare_amount))::numeric, 2)
        FROM trips GROUP BY taxi_type"""),
    ("hard", "Which 5 pickup and dropoff zone pairs (where the two zones differ) had the most trips? Return the pickup zone, the dropoff zone and the trip count.",
     """SELECT pu.zone, dz.zone, COUNT(*) AS trips FROM trips t
        JOIN taxi_zones pu ON pu.location_id = t.pickup_location_id
        JOIN taxi_zones dz ON dz.location_id = t.dropoff_location_id
        WHERE t.pickup_location_id <> t.dropoff_location_id
        GROUP BY pu.zone, dz.zone ORDER BY trips DESC LIMIT 5"""),
    ("hard", f"For each month of {YEAR}, what was the cumulative number of trips since the start of the year? Return the month number and the cumulative trip count.",
     f"""SELECT month, SUM(trips) OVER (ORDER BY month) FROM (
          SELECT EXTRACT(MONTH FROM pickup_datetime) AS month, COUNT(*) AS trips FROM trips
          WHERE pickup_datetime >= '{YEAR}-01-01' AND pickup_datetime < '{YEAR + 1}-01-01'
          GROUP BY month) m"""),
    ("hard", "Which hour of the day has the highest average per-trip tip percentage (tip divided by fare, times 100) on credit card trips with a positive fare? Return the hour (0-23) and the average rounded to 2 decimals.",
     """SELECT EXTRACT(HOUR FROM pickup_datetime) AS hour, ROUND(AVG(100.0 * tip_amount / fare_amount), 2) AS pct
        FROM trips WHERE payment_type_id = 1 AND fare_amount > 0 GROUP BY hour ORDER BY pct DESC LIMIT 1"""),
    ("hard", "What percentage of trips dropped off at Newark Airport used the Newark rate code? Round to 2 decimals.",
     """SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE t.rate_code_id = 3) / COUNT(*), 2) FROM trips t
        JOIN taxi_zones z ON z.location_id = t.dropoff_location_id WHERE z.zone = 'Newark Airport'"""),
    ("hard", "For trips picked up at JFK Airport, which 5 dropoff boroughs received the most trips? Return the borough, the trip count and the average total amount rounded to 2 decimals.",
     """SELECT dz.borough, COUNT(*) AS trips, ROUND(AVG(t.total_amount)::numeric, 2) FROM trips t
        JOIN taxi_zones pu ON pu.location_id = t.pickup_location_id
        JOIN taxi_zones dz ON dz.location_id = t.dropoff_location_id
        WHERE pu.zone = 'JFK Airport' GROUP BY dz.borough ORDER BY trips DESC LIMIT 5"""),
    ("hard", "For trips that both started and ended in Manhattan and lasted at least 1 minute, what was the average per-trip speed in miles per hour? Round to 2 decimals.",
     """SELECT ROUND(AVG(t.trip_distance / (EXTRACT(EPOCH FROM t.dropoff_datetime - t.pickup_datetime) / 3600))::numeric, 2)
        FROM trips t
        JOIN taxi_zones pu ON pu.location_id = t.pickup_location_id
        JOIN taxi_zones dz ON dz.location_id = t.dropoff_location_id
        WHERE pu.borough = 'Manhattan' AND dz.borough = 'Manhattan'
          AND t.dropoff_datetime - t.pickup_datetime >= INTERVAL '1 minute'"""),
    ("hard", f"How many days in {YEAR} had more trips than the average number of trips per day across {YEAR}?",
     f"""WITH daily AS (
          SELECT pickup_datetime::date AS day, COUNT(*) AS trips FROM trips
          WHERE pickup_datetime >= '{YEAR}-01-01' AND pickup_datetime < '{YEAR + 1}-01-01' GROUP BY day)
        SELECT COUNT(*) FROM daily WHERE trips > (SELECT AVG(trips) FROM daily)"""),
    ("hard", f"Which month of {YEAR} had the highest share of cash payments? Return the month number and the cash percentage rounded to 2 decimals.",
     f"""SELECT EXTRACT(MONTH FROM pickup_datetime) AS month,
               ROUND(100.0 * COUNT(*) FILTER (WHERE payment_type_id = 2) / COUNT(*), 2) AS cash_pct
        FROM trips WHERE pickup_datetime >= '{YEAR}-01-01' AND pickup_datetime < '{YEAR + 1}-01-01'
        GROUP BY month ORDER BY cash_pct DESC LIMIT 1"""),
    ("hard", "For each taxi type, which pickup borough had the most trips? Return the taxi type, the borough and the trip count.",
     """WITH c AS (
          SELECT t.taxi_type, z.borough, COUNT(*) AS trips,
                 ROW_NUMBER() OVER (PARTITION BY t.taxi_type ORDER BY COUNT(*) DESC) AS rn
          FROM trips t JOIN taxi_zones z ON z.location_id = t.pickup_location_id
          GROUP BY t.taxi_type, z.borough)
        SELECT taxi_type, borough, trips FROM c WHERE rn = 1"""),
    ("medium", "What is the average fare amount on weekends versus weekdays? Return two rows labelled 'weekend' and 'weekday' with the average rounded to 2 decimals.",
     """SELECT CASE WHEN EXTRACT(ISODOW FROM pickup_datetime) IN (6, 7) THEN 'weekend' ELSE 'weekday' END AS period,
               ROUND(AVG(fare_amount)::numeric, 2) FROM trips GROUP BY period"""),
    ("hard", "Which 3 Brooklyn dropoff zones received the most trips that were picked up in Manhattan? Return the zone and the trip count.",
     """SELECT dz.zone, COUNT(*) AS trips FROM trips t
        JOIN taxi_zones pu ON pu.location_id = t.pickup_location_id
        JOIN taxi_zones dz ON dz.location_id = t.dropoff_location_id
        WHERE pu.borough = 'Manhattan' AND dz.borough = 'Brooklyn'
        GROUP BY dz.zone ORDER BY trips DESC LIMIT 3"""),
    ("hard", "Rank the five boroughs (Manhattan, Brooklyn, Queens, Bronx, Staten Island) by total revenue (sum of total amount by pickup borough). Return the borough, the total revenue rounded to 2 decimals, and the rank (1 = highest).",
     """SELECT z.borough, ROUND(SUM(t.total_amount)::numeric, 2),
               RANK() OVER (ORDER BY SUM(t.total_amount) DESC)
        FROM trips t JOIN taxi_zones z ON z.location_id = t.pickup_location_id
        WHERE z.borough IN ('Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island')
        GROUP BY z.borough"""),
    ("hard", "For credit card trips, what percentage had no tip at all, by pickup borough? Return the borough and the percentage rounded to 2 decimals, for the five boroughs Manhattan, Brooklyn, Queens, Bronx and Staten Island.",
     """SELECT z.borough, ROUND(100.0 * COUNT(*) FILTER (WHERE t.tip_amount = 0) / COUNT(*), 2)
        FROM trips t JOIN taxi_zones z ON z.location_id = t.pickup_location_id
        WHERE t.payment_type_id = 1
          AND z.borough IN ('Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island')
        GROUP BY z.borough"""),
]


def templated() -> list[tuple[str, str, str, str]]:
    """Return (template_id, difficulty, question, sql)."""
    q: list[tuple[str, str, str, str]] = []

    for taxi in ("yellow", "green"):
        for m in range(1, 13):
            s, e = month_range(m)
            q.append((f"monthly_count_{taxi}", "easy",
                      f"How many {taxi} taxi trips were there in {mname(m)} {YEAR}?",
                      f"SELECT COUNT(*) FROM trips WHERE taxi_type = '{taxi}' "
                      f"AND pickup_datetime >= '{s}' AND pickup_datetime < '{e}'"))

    for m in range(1, 13):
        s, e = month_range(m)
        q.append(("card_share", "medium",
                  f"What percentage of all trips in {mname(m)} {YEAR} were paid by credit card? Round to 2 decimals.",
                  f"SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE payment_type_id = 1) / COUNT(*), 2) FROM trips "
                  f"WHERE pickup_datetime >= '{s}' AND pickup_datetime < '{e}'"))

    for m in range(1, 13):
        s, e = month_range(m)
        q.append(("top_pickup_zones", "medium",
                  f"List the 5 pickup zones with the most trips in {mname(m)} {YEAR}, with their trip counts.",
                  f"SELECT z.zone, COUNT(*) AS trips FROM trips t "
                  f"JOIN taxi_zones z ON z.location_id = t.pickup_location_id "
                  f"WHERE t.pickup_datetime >= '{s}' AND t.pickup_datetime < '{e}' "
                  f"GROUP BY z.zone ORDER BY trips DESC LIMIT 5"))

    for b in BOROUGHS:
        q.append(("avg_fare_borough", "easy",
                  f"What was the average fare amount for trips picked up in {b}? Round to 2 decimals.",
                  f"SELECT ROUND(AVG(t.fare_amount)::numeric, 2) FROM trips t "
                  f"JOIN taxi_zones z ON z.location_id = t.pickup_location_id WHERE z.borough = '{b}'"))

    for i, d in enumerate(DAYS, 1):
        q.append(("tip_pct_weekday", "medium",
                  f"For credit card trips with a positive fare, what was the average per-trip tip percentage "
                  f"(tip divided by fare, times 100) on {d}s? Round to 2 decimals.",
                  f"SELECT ROUND(AVG(100.0 * tip_amount / fare_amount), 2) FROM trips "
                  f"WHERE payment_type_id = 1 AND fare_amount > 0 AND EXTRACT(ISODOW FROM pickup_datetime) = {i}"))

    for n in range(1, 7):
        q.append(("avg_distance_passengers", "easy",
                  f"What is the average trip distance in miles for trips with exactly {n} passenger{'s' if n > 1 else ''}? Round to 2 decimals.",
                  f"SELECT ROUND(AVG(trip_distance)::numeric, 2) FROM trips WHERE passenger_count = {n}"))

    for b in BOROUGHS:
        q.append(("busiest_hour_borough", "medium",
                  f"In {b}, which hour of the day (0-23) had the most pickups? Return the hour and the number of trips.",
                  f"SELECT EXTRACT(HOUR FROM t.pickup_datetime) AS hour, COUNT(*) AS trips FROM trips t "
                  f"JOIN taxi_zones z ON z.location_id = t.pickup_location_id WHERE z.borough = '{b}' "
                  f"GROUP BY hour ORDER BY trips DESC LIMIT 1"))

    for qn in range(1, 5):
        s, _ = month_range(3 * qn - 2)
        _, e = month_range(3 * qn)
        q.append(("quarterly_revenue", "medium",
                  f"What was the total amount collected (sum of total amount) across all trips in Q{qn} {YEAR}? Round to 2 decimals.",
                  f"SELECT ROUND(SUM(total_amount)::numeric, 2) FROM trips "
                  f"WHERE pickup_datetime >= '{s}' AND pickup_datetime < '{e}'"))

    for m in range(1, 13):
        s, e = month_range(m)
        q.append(("jfk_pickups_month", "medium",
                  f"How many trips in {mname(m)} {YEAR} were picked up at JFK Airport?",
                  f"SELECT COUNT(*) FROM trips t JOIN taxi_zones z ON z.location_id = t.pickup_location_id "
                  f"WHERE z.zone = 'JFK Airport' AND t.pickup_datetime >= '{s}' AND t.pickup_datetime < '{e}'"))

    for method in ("Credit card", "Cash", "No charge", "Dispute"):
        q.append(("count_payment_method", "easy",
                  f"How many trips were paid with the payment type '{method}'?",
                  f"SELECT COUNT(*) FROM trips t JOIN payment_types p ON p.payment_type_id = t.payment_type_id "
                  f"WHERE p.description = '{method}'"))

    for b in BOROUGHS:
        q.append(("avg_duration_borough", "medium",
                  f"What was the average trip duration in minutes for trips picked up in {b}? Round to 1 decimal.",
                  f"SELECT ROUND(AVG(EXTRACT(EPOCH FROM t.dropoff_datetime - t.pickup_datetime) / 60)::numeric, 1) "
                  f"FROM trips t JOIN taxi_zones z ON z.location_id = t.pickup_location_id WHERE z.borough = '{b}'"))

    pairs = [
        ("JFK Airport", "Times Sq/Theatre District"), ("LaGuardia Airport", "Midtown Center"),
        ("Penn Station/Madison Sq West", "JFK Airport"), ("Times Sq/Theatre District", "LaGuardia Airport"),
        ("East Village", "Williamsburg (North Side)"), ("Upper East Side South", "Midtown Center"),
        ("Midtown Center", "Upper East Side South"), ("Astoria", "LaGuardia Airport"),
        ("Central Park", "Penn Station/Madison Sq West"), ("Midtown Center", "Newark Airport"),
    ]
    for a, b in pairs:
        q.append(("zone_pair_count", "medium",
                  f"How many trips went from {a} to {b}?",
                  f"SELECT COUNT(*) FROM trips t "
                  f"JOIN taxi_zones pu ON pu.location_id = t.pickup_location_id "
                  f"JOIN taxi_zones dz ON dz.location_id = t.dropoff_location_id "
                  f"WHERE pu.zone = '{a}' AND dz.zone = '{b}'"))

    for a in ("Times Sq/Theatre District", "Midtown Center"):
        for b in ("JFK Airport", "LaGuardia Airport", "Newark Airport"):
            q.append(("zone_pair_avg_total", "medium",
                      f"What was the average total amount for trips from {a} to {b}? Round to 2 decimals.",
                      f"SELECT ROUND(AVG(t.total_amount)::numeric, 2) FROM trips t "
                      f"JOIN taxi_zones pu ON pu.location_id = t.pickup_location_id "
                      f"JOIN taxi_zones dz ON dz.location_id = t.dropoff_location_id "
                      f"WHERE pu.zone = '{a}' AND dz.zone = '{b}'"))

    for m in range(1, 13):
        s, e = month_range(m)
        q.append(("avg_distance_month", "easy",
                  f"What was the average trip distance in {mname(m)} {YEAR}? Round to 2 decimals.",
                  f"SELECT ROUND(AVG(trip_distance)::numeric, 2) FROM trips "
                  f"WHERE pickup_datetime >= '{s}' AND pickup_datetime < '{e}'"))

    for m in range(1, 13):
        s, e = month_range(m)
        q.append(("avg_card_tip_month", "medium",
                  f"What was the average tip amount on credit card trips in {mname(m)} {YEAR}? Round to 2 decimals.",
                  f"SELECT ROUND(AVG(tip_amount)::numeric, 2) FROM trips WHERE payment_type_id = 1 "
                  f"AND pickup_datetime >= '{s}' AND pickup_datetime < '{e}'"))

    for m in range(1, 13):
        s, e = month_range(m)
        q.append(("busiest_day_month", "medium",
                  f"Which date in {mname(m)} {YEAR} had the most trips? Return the date and the trip count.",
                  f"SELECT pickup_datetime::date AS day, COUNT(*) AS trips FROM trips "
                  f"WHERE pickup_datetime >= '{s}' AND pickup_datetime < '{e}' "
                  f"GROUP BY day ORDER BY trips DESC LIMIT 1"))

    for b in BOROUGHS:
        q.append(("dropoffs_borough", "easy",
                  f"How many trips were dropped off in {b}?",
                  f"SELECT COUNT(*) FROM trips t JOIN taxi_zones z ON z.location_id = t.dropoff_location_id "
                  f"WHERE z.borough = '{b}'"))

    for b in BOROUGHS:
        q.append(("green_share_borough", "medium",
                  f"What percentage of trips picked up in {b} were green taxi trips? Round to 2 decimals.",
                  f"SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE t.taxi_type = 'green') / COUNT(*), 2) FROM trips t "
                  f"JOIN taxi_zones z ON z.location_id = t.pickup_location_id WHERE z.borough = '{b}'"))

    for vendor in ("Creative Mobile Technologies", "Curb Mobility"):
        q.append(("avg_passengers_vendor", "medium",
                  f"What is the average passenger count for trips recorded by {vendor}? Round to 2 decimals.",
                  f"SELECT ROUND(AVG(t.passenger_count)::numeric, 2) FROM trips t "
                  f"JOIN vendors v ON v.vendor_id = t.vendor_id WHERE v.vendor_name = '{vendor}'"))

    for d in (10, 20, 30, 50):
        q.append(("long_trips", "easy",
                  f"How many trips were at least {d} miles long?",
                  f"SELECT COUNT(*) FROM trips WHERE trip_distance >= {d}"))

    for m in range(2, 13):
        ps, _ = month_range(m - 1)
        s, e = month_range(m)
        q.append(("mom_change", "hard",
                  f"By what percentage did the total number of trips change from {mname(m - 1)} to {mname(m)} {YEAR}? "
                  f"Return a single number rounded to 2 decimals (negative for a decrease).",
                  f"WITH c AS (SELECT COUNT(*) FILTER (WHERE pickup_datetime < '{s}') AS prev, "
                  f"COUNT(*) FILTER (WHERE pickup_datetime >= '{s}') AS cur FROM trips "
                  f"WHERE pickup_datetime >= '{ps}' AND pickup_datetime < '{e}') "
                  f"SELECT ROUND(100.0 * (cur - prev) / prev, 2) FROM c"))

    return q


def main() -> None:
    rows = []
    for i, (difficulty, question, sql) in enumerate(HAND_WRITTEN, 1):
        rows.append({"id": f"hw_{i:03d}", "difficulty": difficulty, "category": "hand_written",
                     "question": question, "gold_sql": " ".join(sql.split())})
    counters: dict[str, int] = {}
    for template, difficulty, question, sql in templated():
        counters[template] = counters.get(template, 0) + 1
        rows.append({"id": f"{template}_{counters[template]:02d}", "difficulty": difficulty,
                     "category": template, "question": question, "gold_sql": " ".join(sql.split())})

    assert len(rows) == 220, f"expected 220 questions, got {len(rows)}"
    assert len({r["id"] for r in rows}) == len(rows), "duplicate ids"
    assert len({r["question"] for r in rows}) == len(rows), "duplicate questions"

    with OUT.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    by_diff: dict[str, int] = {}
    for r in rows:
        by_diff[r["difficulty"]] = by_diff.get(r["difficulty"], 0) + 1
    print(f"Wrote {len(rows)} questions to {OUT} ({by_diff})")


if __name__ == "__main__":
    main()
