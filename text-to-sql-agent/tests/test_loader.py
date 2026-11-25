"""Unit test for the TLC parquet normaliser (no download needed)."""

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")

_spec = importlib.util.spec_from_file_location(
    "load_tlc_data", Path(__file__).resolve().parent.parent / "scripts" / "load_tlc_data.py"
)
loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loader)


def _batch(prefix: str, extra: dict) -> pa.RecordBatch:
    data = {
        "VendorID": [1, 2, 2],
        f"{prefix}_pickup_datetime": [dt.datetime(2023, 3, 1, 8), dt.datetime(2023, 3, 31, 23),
                                      dt.datetime(2023, 2, 28, 23)],  # last row is outside March
        f"{prefix}_dropoff_datetime": [dt.datetime(2023, 3, 1, 8, 20)] * 3,
        "passenger_count": [1.0, None, 2.0],
        "trip_distance": [2.5, 10.1, 1.0],
        "RatecodeID": [1.0, 2.0, 1.0],
        "store_and_fwd_flag": ["N", "N", "N"],
        "PULocationID": [161, 132, 79],
        "DOLocationID": [230, 161, 79],
        "payment_type": [1, 2, 1],
        "fare_amount": [12.1, 70.0, 6.5],
        "extra": [2.5, 0.0, 1.0],
        "mta_tax": [0.5, 0.5, 0.5],
        "tip_amount": [3.0, 0.0, 1.0],
        "tolls_amount": [0.0, 6.94, 0.0],
        "improvement_surcharge": [1.0, 1.0, 1.0],
        "total_amount": [21.6, 80.19, 10.0],
        "congestion_surcharge": [2.5, 2.5, 0.0],
        **extra,
    }
    return pa.RecordBatch.from_pydict(data)


def test_yellow_batch_is_normalised_and_filtered():
    batch = _batch("tpep", {"Airport_fee": [0.0, 1.75, 0.0]})  # capitalised in older files
    table = loader.normalize_batch(batch, "yellow", 2023, 3)

    assert table.column_names == loader.TARGET_COLUMNS
    assert table.num_rows == 2  # Feb 28 row dropped
    rows = table.to_pylist()
    assert rows[0]["taxi_type"] == "yellow"
    assert rows[0]["pickup_location_id"] == 161
    assert rows[1]["passenger_count"] is None
    assert rows[1]["rate_code_id"] == 2
    assert rows[1]["airport_fee"] == 1.75


def test_green_batch_has_null_airport_fee():
    batch = _batch("lpep", {"trip_type": [1, 1, 2], "ehail_fee": [None, None, None]})
    table = loader.normalize_batch(batch, "green", 2023, 3)
    assert table.num_rows == 2
    assert all(r["airport_fee"] is None for r in table.to_pylist())
    assert {r["taxi_type"] for r in table.to_pylist()} == {"green"}
