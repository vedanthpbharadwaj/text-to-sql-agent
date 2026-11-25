import pytest

from sqlagent.safety import check_sql

ALLOWED = {"trips", "taxi_zones", "payment_types"}


@pytest.mark.parametrize(
    "query",
    [
        "DROP TABLE trips",
        "DELETE FROM trips",
        "UPDATE trips SET fare_amount = 0",
        "INSERT INTO trips (taxi_type) VALUES ('x')",
        "TRUNCATE trips",
        "ALTER TABLE trips ADD COLUMN x int",
        "CREATE TABLE x AS SELECT * FROM trips",
        "GRANT ALL ON trips TO public",
        "SELECT 1 FROM trips; DROP TABLE trips",
        "SELECT * INTO backup FROM trips",
        "SELECT * FROM trips FOR UPDATE",
        "COPY trips TO STDOUT",
        "VACUUM trips",
        "SET statement_timeout = 0",
        "WITH d AS (DELETE FROM trips RETURNING *) SELECT * FROM d",
    ],
)
def test_destructive_queries_are_blocked(query):
    result = check_sql(query, ALLOWED, 1000)
    assert not result.ok
    assert result.reason


@pytest.mark.parametrize(
    "query",
    [
        "SELECT pg_sleep(30)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT set_config('statement_timeout', '0', false)",
        "SELECT dblink('host=evil', 'select 1')",
    ],
)
def test_dangerous_functions_are_blocked(query):
    assert not check_sql(query, ALLOWED, 1000).ok


def test_unknown_tables_are_blocked():
    result = check_sql("SELECT * FROM pg_shadow", ALLOWED, 1000)
    assert not result.ok
    assert "Allowed tables" in result.reason
    assert not check_sql("SELECT * FROM other_schema.trips", ALLOWED, 1000).ok


def test_limit_is_added_when_missing():
    result = check_sql("SELECT taxi_type FROM trips", ALLOWED, 500)
    assert result.ok
    assert "LIMIT 500" in result.sql


def test_existing_limit_is_kept():
    result = check_sql("SELECT taxi_type FROM trips LIMIT 5;", ALLOWED, 500)
    assert result.ok
    assert result.sql == "SELECT taxi_type FROM trips LIMIT 5"


@pytest.mark.parametrize(
    "query",
    [
        "WITH c AS (SELECT taxi_type, COUNT(*) n FROM trips GROUP BY 1) SELECT * FROM c",
        "SELECT z.borough, COUNT(*) FROM trips t JOIN taxi_zones z ON z.location_id = t.pickup_location_id GROUP BY 1",
        "SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY fare_amount) FROM trips",
        "SELECT COUNT(*) FILTER (WHERE payment_type_id = 1) FROM trips",
        "SELECT 1 FROM trips UNION ALL SELECT 2 FROM taxi_zones",
        "SELECT EXTRACT(ISODOW FROM pickup_datetime), ROW_NUMBER() OVER (ORDER BY fare_amount) FROM trips",
    ],
)
def test_valid_analytic_queries_pass(query):
    assert check_sql(query, ALLOWED, 1000).ok


def test_empty_and_unparseable():
    assert not check_sql("", ALLOWED, 10).ok
    assert not check_sql("SELEC FROM WHERE", ALLOWED, 10).ok
