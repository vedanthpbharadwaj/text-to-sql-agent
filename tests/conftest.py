from __future__ import annotations

import os
from typing import Any

import pytest

from sqlagent.config import Settings
from sqlagent.db import QueryResult
from sqlagent.schema_catalog import Column, SchemaCatalog, Table


class FakeLLM:
    """Returns scripted function-call arguments, keyed by function name."""

    def __init__(self, responses: dict[str, list[dict[str, Any]]], answer: str = "There were 42 trips.") -> None:
        self.responses = {k: list(v) for k, v in responses.items()}
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    def call_function(self, system: str, user: str, function: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((function["name"], user))
        queue = self.responses.get(function["name"])
        if not queue:
            raise AssertionError(f"Unexpected call to {function['name']}")
        return queue.pop(0)

    def complete(self, system: str, user: str) -> str:
        self.calls.append(("complete", user))
        return self.answer


class FakeDB:
    """Returns a QueryResult per call; records every SQL string it receives."""

    def __init__(self, results: list[QueryResult]) -> None:
        self.results = list(results)
        self.executed: list[str] = []

    def execute(self, query: str, max_rows: int | None = None) -> QueryResult:
        self.executed.append(query)
        return self.results.pop(0)

    def close(self) -> None:
        pass


@pytest.fixture
def catalog() -> SchemaCatalog:
    trips = Table(
        "trips", "One row per trip", 1000,
        [Column("trip_id", "bigint"), Column("taxi_type", "text", sample_values=["green", "yellow"]),
         Column("pickup_datetime", "timestamp"), Column("pickup_location_id", "integer"),
         Column("fare_amount", "numeric")],
    )
    zones = Table(
        "taxi_zones", "Zone lookup", 265,
        [Column("location_id", "integer"), Column("borough", "text"), Column("zone", "text")],
    )
    return SchemaCatalog({"trips": trips, "taxi_zones": zones})


@pytest.fixture
def settings() -> Settings:
    return Settings(openai_api_key="test", max_attempts=3, enable_reviewer=True)


@pytest.fixture
def pg_dsn() -> str:
    """Read-only DSN for integration tests (seeded with scripts/seed_sample.py)."""
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")
    return dsn
