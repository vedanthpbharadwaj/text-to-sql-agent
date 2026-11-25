from fastapi.testclient import TestClient

from sqlagent.api.main import app


class StubAgent:
    def __init__(self, catalog):
        self.catalog = catalog
        self.db = None

    def run(self, question, generate_answer=True):
        return {
            "status": "answered",
            "answer": "There were 42 trips.",
            "sql": "SELECT COUNT(*) FROM trips LIMIT 1000",
            "attempts": 1,
            "result": {"columns": ["count"], "rows": [[42]], "row_count": 1,
                       "truncated": False, "error": None},
            "review": {"verdict": "approve", "issues": []},
            "latency_ms": 12.3,
            "trace": [{"node": "write_sql", "ms": 5.0}],
        }


def test_ask_and_schema(catalog):
    app.state.agent = StubAgent(catalog)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}

        resp = client.post("/ask", json={"question": "How many trips?"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "answered"
        assert body["rows"] == [[42]]
        assert body["sql"].startswith("SELECT")

        schema = client.get("/schema").json()
        assert "trips" in schema and schema["trips"]["approx_rows"] == 1000

        assert client.post("/ask", json={"question": ""}).status_code == 422
    app.state.agent = None
