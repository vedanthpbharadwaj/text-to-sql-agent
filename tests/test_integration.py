"""Integration tests against a real Postgres seeded with scripts/seed_sample.py.

Run with TEST_DATABASE_URL pointing at the read-only role, e.g.
    TEST_DATABASE_URL=postgresql://analyst_ro:analyst_ro@localhost:5432/nyc_taxi pytest
"""

import json
from pathlib import Path

import pytest

from sqlagent.agents import SQLAgent
from sqlagent.config import Settings
from sqlagent.db import Database
from sqlagent.schema_catalog import SchemaCatalog
from tests.conftest import FakeLLM

BENCHMARK = Path(__file__).resolve().parent.parent / "eval" / "benchmark.jsonl"


@pytest.fixture
def db(pg_dsn):
    database = Database(pg_dsn, max_rows=100, statement_timeout_ms=5_000)
    yield database
    database.close()


@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE trips",
        "DELETE FROM trips",
        "UPDATE trips SET fare_amount = 0",
        "CREATE TABLE hack (id int)",
        "INSERT INTO vendors VALUES (99, 'x')",
    ],
)
def test_read_only_role_rejects_writes(db, statement):
    """Second safety layer: even raw SQL that bypasses the guard cannot write."""
    result = db.execute(statement)
    assert result.error is not None


def test_statement_timeout(pg_dsn):
    database = Database(pg_dsn, statement_timeout_ms=200)
    try:
        result = database.execute("SELECT pg_sleep(2)")
        assert result.error and "timeout" in result.error.lower()
    finally:
        database.close()


def test_truncation_flag(db):
    result = db.execute("SELECT trip_id FROM trips", max_rows=10)
    assert result.ok and result.row_count == 10 and result.truncated


def test_catalog_introspection(pg_dsn):
    catalog = SchemaCatalog.from_dsn(pg_dsn)
    assert {"trips", "taxi_zones", "payment_types", "rate_codes", "vendors"} <= set(catalog.tables)
    ddl = catalog.ddl(["taxi_zones"])
    assert "borough" in ddl and "'Manhattan'" in ddl


def test_all_gold_queries_execute(db):
    for line in BENCHMARK.read_text().splitlines():
        item = json.loads(line)
        result = db.execute(item["gold_sql"])
        assert result.ok, f"{item['id']}: {result.error}"
        assert result.row_count > 0, f"{item['id']} returned no rows"


def test_agent_end_to_end_with_real_db(pg_dsn, db):
    catalog = SchemaCatalog.from_dsn(pg_dsn)
    llm = FakeLLM({
        "select_tables": [{"tables": ["trips", "taxi_zones"], "notes": ""}],
        "submit_sql": [
            {"reasoning": "", "sql": "SELECT COUNT(*) FROM trip"},  # typo -> guard rejects unknown table -> retry
            {"reasoning": "", "sql": "SELECT z.borough, COUNT(*) AS trips FROM trips t "
                                     "JOIN taxi_zones z ON z.location_id = t.pickup_location_id "
                                     "GROUP BY z.borough ORDER BY trips DESC"},
        ],
        "review_query": [{"verdict": "approve", "issues": []}],
    })
    agent = SQLAgent(llm=llm, db=db, catalog=catalog, settings=Settings(openai_api_key="x"))
    state = agent.run("Trips by pickup borough?")

    assert state["status"] == "answered"
    assert state["attempts"] == 2
    assert "Unknown or disallowed table" in state["history"][0]["error"]
    assert state["result"]["columns"] == ["borough", "trips"]
    assert state["result"]["rows"][0][0] == "Manhattan"
