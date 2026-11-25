"""Download NYC TLC trip records and bulk-load them into Postgres.

Default: every 2023 yellow and green taxi file (tens of millions of rows).

    python scripts/load_tlc_data.py --year 2023 --types yellow green
    python scripts/load_tlc_data.py --months 1 2 3          # quicker partial load

Must run with the admin DSN (ADMIN_DATABASE_URL), not the read-only role.
Parquet files are streamed in batches, converted to CSV in memory with
pyarrow, and loaded with COPY, so memory stays flat regardless of file size.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import sys
import time
import urllib.request
from pathlib import Path

import psycopg
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

BASE_URL = "https://d37ci6vzurychx.cloudfront.net"
ZONE_URL = f"{BASE_URL}/misc/taxi_zone_lookup.csv"

TARGET_COLUMNS = [
    "taxi_type", "vendor_id", "pickup_datetime", "dropoff_datetime", "passenger_count",
    "trip_distance", "rate_code_id", "pickup_location_id", "dropoff_location_id",
    "payment_type_id", "fare_amount", "extra", "mta_tax", "tip_amount", "tolls_amount",
    "improvement_surcharge", "congestion_surcharge", "airport_fee", "total_amount",
]

# target column -> source column name (lower-cased) per taxi type
SOURCE_COLUMNS = {
    "yellow": {
        "vendor_id": "vendorid",
        "pickup_datetime": "tpep_pickup_datetime",
        "dropoff_datetime": "tpep_dropoff_datetime",
        "airport_fee": "airport_fee",
    },
    "green": {
        "vendor_id": "vendorid",
        "pickup_datetime": "lpep_pickup_datetime",
        "dropoff_datetime": "lpep_dropoff_datetime",
        "airport_fee": None,  # green files have no airport fee column
    },
}
COMMON_SOURCES = {
    "passenger_count": "passenger_count",
    "trip_distance": "trip_distance",
    "rate_code_id": "ratecodeid",
    "pickup_location_id": "pulocationid",
    "dropoff_location_id": "dolocationid",
    "payment_type_id": "payment_type",
    "fare_amount": "fare_amount",
    "extra": "extra",
    "mta_tax": "mta_tax",
    "tip_amount": "tip_amount",
    "tolls_amount": "tolls_amount",
    "improvement_surcharge": "improvement_surcharge",
    "congestion_surcharge": "congestion_surcharge",
    "total_amount": "total_amount",
}
INT_COLUMNS = {"vendor_id", "passenger_count", "rate_code_id", "pickup_location_id",
               "dropoff_location_id", "payment_type_id"}

INDEXES = {
    "idx_trips_pickup_datetime": "trips (pickup_datetime)",
    "idx_trips_pickup_location": "trips (pickup_location_id)",
    "idx_trips_dropoff_location": "trips (dropoff_location_id)",
    "idx_trips_payment_type": "trips (payment_type_id)",
    "idx_trips_taxi_type": "trips (taxi_type)",
}


def download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  downloading {url}")
    with urllib.request.urlopen(url) as resp, open(tmp, "wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    tmp.rename(dest)
    return dest


def load_zones(conn: psycopg.Connection, data_dir: Path) -> None:
    path = download(ZONE_URL, data_dir / "taxi_zone_lookup.csv")
    table = pacsv.read_csv(path)
    with conn.cursor() as cur:
        for row in table.to_pylist():
            cur.execute(
                """INSERT INTO taxi_zones (location_id, borough, zone, service_zone)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (location_id) DO UPDATE
                   SET borough = EXCLUDED.borough, zone = EXCLUDED.zone,
                       service_zone = EXCLUDED.service_zone""",
                (row["LocationID"], row["Borough"] or "Unknown",
                 row["Zone"] or "Unknown", row["service_zone"]),
            )
    conn.commit()
    print(f"  loaded {table.num_rows} taxi zones")


def normalize_batch(batch: pa.RecordBatch, taxi_type: str, year: int, month: int) -> pa.Table:
    cols = {name.lower(): batch.column(i) for i, name in enumerate(batch.schema.names)}
    mapping = {**COMMON_SOURCES, **SOURCE_COLUMNS[taxi_type]}
    n = batch.num_rows

    out: dict[str, pa.Array] = {"taxi_type": pa.array([taxi_type] * n, pa.string())}
    for target in TARGET_COLUMNS[1:]:
        source = mapping.get(target)
        arr = cols.get(source) if source else None
        if arr is None:
            arr = pa.nulls(n, pa.float64())
        if target in INT_COLUMNS:
            arr = pc.cast(arr, pa.int64(), safe=False)
        out[target] = arr
    table = pa.table(out)

    # TLC files contain a few trips with timestamps outside the file's month; drop them.
    start = pa.scalar(_ts(year, month), pa.timestamp("us"))
    end = pa.scalar(_ts(year + (month == 12), month % 12 + 1), pa.timestamp("us"))
    pickup = pc.cast(table["pickup_datetime"], pa.timestamp("us"))
    keep = pc.and_(pc.greater_equal(pickup, start), pc.less(pickup, end))
    return table.filter(keep)


def _ts(year: int, month: int) -> dt.datetime:
    return dt.datetime(year, month, 1)


def copy_file(conn: psycopg.Connection, path: Path, taxi_type: str, year: int, month: int,
              batch_size: int) -> int:
    loaded = 0
    pf = pq.ParquetFile(path)
    copy_sql = f"COPY trips ({', '.join(TARGET_COLUMNS)}) FROM STDIN WITH (FORMAT csv)"
    with conn.cursor() as cur:
        with cur.copy(copy_sql) as copy:
            for batch in pf.iter_batches(batch_size=batch_size):
                table = normalize_batch(batch, taxi_type, year, month)
                if table.num_rows == 0:
                    continue
                buf = io.BytesIO()
                pacsv.write_csv(table, buf, pacsv.WriteOptions(include_header=False))
                copy.write(buf.getvalue())
                loaded += table.num_rows
    conn.commit()
    return loaded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", default=os.environ.get("ADMIN_DATABASE_URL"),
                        help="Admin Postgres DSN (default: $ADMIN_DATABASE_URL)")
    parser.add_argument("--year", type=int, default=2023)
    parser.add_argument("--months", type=int, nargs="+", default=list(range(1, 13)))
    parser.add_argument("--types", nargs="+", default=["yellow", "green"], choices=["yellow", "green"])
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--batch-size", type=int, default=500_000)
    parser.add_argument("--truncate", action="store_true", help="Empty the trips table first")
    args = parser.parse_args()

    if not args.dsn:
        parser.error("Provide --dsn or set ADMIN_DATABASE_URL")

    started = time.time()
    with psycopg.connect(args.dsn) as conn:
        print("Loading taxi zones")
        load_zones(conn, args.data_dir)

        with conn.cursor() as cur:
            if args.truncate:
                cur.execute("TRUNCATE trips RESTART IDENTITY")
            print("Dropping indexes for bulk load")
            for name in INDEXES:
                cur.execute(f"DROP INDEX IF EXISTS {name}")
        conn.commit()

        total = 0
        for taxi_type in args.types:
            for month in args.months:
                fname = f"{taxi_type}_tripdata_{args.year}-{month:02d}.parquet"
                path = download(f"{BASE_URL}/trip-data/{fname}", args.data_dir / fname)
                t0 = time.time()
                n = copy_file(conn, path, taxi_type, args.year, month, args.batch_size)
                total += n
                print(f"  {fname}: {n:,} rows in {time.time() - t0:.0f}s (running total {total:,})")

        print("Rebuilding indexes and statistics")
        with conn.cursor() as cur:
            for name, target in INDEXES.items():
                cur.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")
            conn.commit()
        conn.autocommit = True
        conn.execute("ANALYZE trips")
        count = conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]

    print(f"Done: {count:,} rows in trips ({(time.time() - started) / 60:.1f} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
