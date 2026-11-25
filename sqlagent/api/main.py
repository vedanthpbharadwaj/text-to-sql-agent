"""FastAPI service exposing the text-to-SQL agent."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ..config import get_settings
from ..factory import build_agent

logger = logging.getLogger("sqlagent.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=1000)
    include_answer: bool = True


class AskResponse(BaseModel):
    question: str
    status: str = Field(description="answered | unverified | failed")
    answer: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    attempts: int
    review_issues: list[str]
    error: str | None
    latency_ms: float
    trace: list[dict[str, Any]]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if getattr(app.state, "agent", None) is None:  # tests may inject a fake agent
        app.state.agent = build_agent(settings)
    app.state.settings = settings
    yield
    db = getattr(app.state.agent, "db", None)
    if db is not None and hasattr(db, "close"):
        db.close()


app = FastAPI(
    title="Text-to-SQL Analytics Assistant",
    description="Ask questions about NYC taxi trips in plain English.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/schema")
def schema(request: Request) -> dict[str, Any]:
    catalog = request.app.state.agent.catalog
    return {
        name: {
            "description": t.description,
            "approx_rows": t.approx_rows,
            "columns": [{"name": c.name, "type": c.data_type} for c in t.columns],
        }
        for name, t in catalog.tables.items()
    }


@app.post("/ask", response_model=AskResponse)
async def ask(body: AskRequest, request: Request) -> AskResponse:
    agent = request.app.state.agent
    settings = request.app.state.settings
    try:
        state = await run_in_threadpool(agent.run, body.question, body.include_answer)
    except Exception as exc:  # LLM/API failures
        logger.exception("agent run failed")
        raise HTTPException(status_code=502, detail=f"Agent error: {exc}") from exc

    result = state.get("result") or {}
    review = state.get("review") or {}
    logger.info(
        "question=%r status=%s attempts=%s latency_ms=%s",
        body.question, state.get("status"), state.get("attempts"), state.get("latency_ms"),
    )
    return AskResponse(
        question=body.question,
        status=state.get("status", "failed"),
        answer=state.get("answer", ""),
        sql=state.get("sql", ""),
        columns=result.get("columns", []),
        rows=result.get("rows", [])[: settings.response_rows],
        row_count=result.get("row_count", 0),
        truncated=result.get("truncated", False),
        attempts=state.get("attempts", 0),
        review_issues=review.get("issues", []) if review.get("verdict") == "revise" else [],
        error=result.get("error"),
        latency_ms=state.get("latency_ms", 0.0),
        trace=state.get("trace", []),
    )
