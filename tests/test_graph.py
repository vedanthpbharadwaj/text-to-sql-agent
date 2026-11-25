"""Agent graph tests with a scripted LLM and fake database (no network, no Postgres)."""

from sqlagent.agents import SQLAgent
from sqlagent.config import Settings
from sqlagent.db import QueryResult
from tests.conftest import FakeDB, FakeLLM

LINK = {"tables": ["trips"], "notes": ""}
OK_RESULT = QueryResult(columns=["count"], rows=[[42]], row_count=1)
APPROVE = {"verdict": "approve", "issues": []}


def make_agent(llm, db, catalog, settings):
    return SQLAgent(llm=llm, db=db, catalog=catalog, settings=settings)


def test_happy_path(catalog, settings):
    llm = FakeLLM({
        "select_tables": [LINK],
        "submit_sql": [{"reasoning": "count", "sql": "SELECT COUNT(*) FROM trips"}],
        "review_query": [APPROVE],
    })
    db = FakeDB([OK_RESULT])
    state = make_agent(llm, db, catalog, settings).run("How many trips?")

    assert state["status"] == "answered"
    assert state["attempts"] == 1
    assert state["result"]["rows"] == [[42]]
    assert state["answer"] == "There were 42 trips."
    assert "LIMIT" in db.executed[0]  # guard injected a LIMIT
    assert [t["node"] for t in state["trace"]] == [
        "link_schema", "write_sql", "guard", "execute", "review", "finalize"
    ]


def test_reviewer_feedback_triggers_rewrite(catalog, settings):
    llm = FakeLLM({
        "select_tables": [LINK],
        "submit_sql": [
            {"reasoning": "", "sql": "SELECT COUNT(*) FROM trips"},
            {"reasoning": "", "sql": "SELECT COUNT(*) FROM trips WHERE taxi_type = 'green'"},
        ],
        "review_query": [
            {"verdict": "revise", "issues": ["Question asks for green taxis only."]},
            APPROVE,
        ],
    })
    db = FakeDB([QueryResult(columns=["count"], rows=[[100]], row_count=1), OK_RESULT])
    state = make_agent(llm, db, catalog, settings).run("How many green trips?")

    assert state["status"] == "answered"
    assert state["attempts"] == 2
    assert "green" in db.executed[1]
    # The second writer prompt must contain the reviewer's feedback.
    writer_prompts = [u for name, u in llm.calls if name == "submit_sql"]
    assert "Question asks for green taxis only." in writer_prompts[1]


def test_guard_blocks_destructive_sql_before_db(catalog, settings):
    llm = FakeLLM({
        "select_tables": [LINK],
        "submit_sql": [
            {"reasoning": "", "sql": "DELETE FROM trips"},
            {"reasoning": "", "sql": "SELECT COUNT(*) FROM trips"},
        ],
        "review_query": [APPROVE],
    })
    db = FakeDB([OK_RESULT])
    state = make_agent(llm, db, catalog, settings).run("How many trips?")

    assert state["status"] == "answered"
    assert len(db.executed) == 1 and "DELETE" not in db.executed[0]
    assert "Blocked by SQL guard" in state["history"][0]["error"]


def test_database_error_is_fed_back(catalog, settings):
    llm = FakeLLM({
        "select_tables": [LINK],
        "submit_sql": [
            {"reasoning": "", "sql": "SELECT fare FROM trips"},
            {"reasoning": "", "sql": "SELECT fare_amount FROM trips"},
        ],
        "review_query": [APPROVE],
    })
    db = FakeDB([QueryResult(error='column "fare" does not exist'), OK_RESULT])
    state = make_agent(llm, db, catalog, settings).run("Show fares")

    assert state["status"] == "answered"
    writer_prompts = [u for name, u in llm.calls if name == "submit_sql"]
    assert 'column "fare" does not exist' in writer_prompts[1]


def test_gives_up_after_max_attempts(catalog):
    settings = Settings(openai_api_key="x", max_attempts=2)
    llm = FakeLLM({
        "select_tables": [LINK],
        "submit_sql": [{"reasoning": "", "sql": "SELECT bad FROM trips"}] * 2,
    })
    db = FakeDB([QueryResult(error="boom"), QueryResult(error="boom")])
    state = make_agent(llm, db, catalog, settings).run("?")

    assert state["status"] == "failed"
    assert state["attempts"] == 2
    assert len(state["history"]) == 2


def test_unverified_when_reviewer_never_approves(catalog):
    settings = Settings(openai_api_key="x", max_attempts=2)
    revise = {"verdict": "revise", "issues": ["wrong"]}
    llm = FakeLLM({
        "select_tables": [LINK],
        "submit_sql": [{"reasoning": "", "sql": "SELECT 1 FROM trips"}] * 2,
        "review_query": [revise, revise],
    })
    db = FakeDB([OK_RESULT, OK_RESULT])
    state = make_agent(llm, db, catalog, settings).run("?")
    assert state["status"] == "unverified"


def test_reviewer_disabled_single_shot(catalog):
    settings = Settings(openai_api_key="x", enable_reviewer=False, max_attempts=1)
    llm = FakeLLM({
        "select_tables": [LINK],
        "submit_sql": [{"reasoning": "", "sql": "SELECT COUNT(*) FROM trips"}],
    })
    db = FakeDB([OK_RESULT])
    state = make_agent(llm, db, catalog, settings).run("?", generate_answer=False)
    assert state["status"] == "answered"
    assert not any(name == "review_query" for name, _ in llm.calls)
    assert state["answer"] == ""


def test_linker_garbage_falls_back_to_all_tables(catalog, settings):
    llm = FakeLLM({
        "select_tables": [{"tables": ["nope"], "notes": ""}],
        "submit_sql": [{"reasoning": "", "sql": "SELECT COUNT(*) FROM trips"}],
        "review_query": [APPROVE],
    })
    state = make_agent(llm, FakeDB([OK_RESULT]), catalog, settings).run("?")
    assert set(state["tables"]) == {"trips", "taxi_zones"}
