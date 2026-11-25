"""LangGraph workflow: schema linker -> SQL writer -> guard -> executor -> reviewer -> answer.

                    ┌──────────────── revise / error / blocked ───────────────┐
                    v                                                         │
  link_schema -> write_sql -> guard -> execute -> review -> finalize -> END   │
                                │          │         │                        │
                                └──────────┴─────────┴────────────────────────┘

The reviewer is a second LLM agent that inspects the executed SQL and a
preview of its result. If it finds a problem, its notes are fed back to the
writer for another attempt, up to `max_attempts`.
"""

from __future__ import annotations

import operator
import time
from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import Settings
from ..db import Database, QueryResult
from ..llm import LLM
from ..safety import check_sql
from ..schema_catalog import SchemaCatalog
from . import prompts
from .tools import REVIEW_QUERY_FN, SUBMIT_SQL_FN, select_tables_fn


class AgentState(TypedDict, total=False):
    question: str
    generate_answer: bool
    tables: list[str]
    table_notes: str
    sql: str
    attempts: int
    guard_ok: bool
    result: dict[str, Any]
    review: dict[str, Any]
    history: Annotated[list[dict[str, Any]], operator.add]
    trace: Annotated[list[dict[str, Any]], operator.add]
    status: str
    answer: str


def _timed(name: str, fn: Callable[[AgentState], dict[str, Any]]):
    def wrapper(state: AgentState) -> dict[str, Any]:
        start = time.perf_counter()
        update = fn(state)
        update.setdefault("trace", [])
        update["trace"] = update["trace"] + [
            {"node": name, "ms": round((time.perf_counter() - start) * 1000, 1)}
        ]
        return update

    return wrapper


class SQLAgent:
    def __init__(
        self,
        llm: LLM,
        db: Database,
        catalog: SchemaCatalog,
        settings: Settings,
    ) -> None:
        self.llm = llm
        self.db = db
        self.catalog = catalog
        self.settings = settings
        self.graph = self._build()

    # ------------------------------------------------------------------ nodes

    def _link_schema(self, state: AgentState) -> dict[str, Any]:
        names = self.catalog.table_names
        args = self.llm.call_function(
            prompts.LINKER_SYSTEM,
            prompts.linker_prompt(state["question"], self.catalog.summary()),
            select_tables_fn(names),
        )
        tables = [t for t in args.get("tables", []) if t in self.catalog.tables]
        # Fall back to the full schema if the linker returns nothing usable.
        return {"tables": tables or names, "table_notes": args.get("notes", "")}

    def _write_sql(self, state: AgentState) -> dict[str, Any]:
        args = self.llm.call_function(
            prompts.WRITER_SYSTEM,
            prompts.writer_prompt(
                state["question"],
                self.catalog.ddl(state["tables"]),
                state.get("table_notes", ""),
                state.get("history", []),
            ),
            SUBMIT_SQL_FN,
        )
        return {"sql": args.get("sql", ""), "attempts": state.get("attempts", 0) + 1}

    def _guard(self, state: AgentState) -> dict[str, Any]:
        verdict = check_sql(state["sql"], set(self.catalog.table_names), self.settings.max_rows)
        if verdict.ok:
            return {"sql": verdict.sql, "guard_ok": True}
        reason = f"Blocked by SQL guard: {verdict.reason}"
        return {
            "guard_ok": False,
            "result": QueryResult(error=reason).to_dict(),
            "history": [{"sql": state["sql"], "error": reason}],
        }

    def _execute(self, state: AgentState) -> dict[str, Any]:
        result = self.db.execute(state["sql"])
        update: dict[str, Any] = {"result": result.to_dict()}
        if not result.ok:
            update["history"] = [{"sql": state["sql"], "error": result.error}]
        return update

    def _review(self, state: AgentState) -> dict[str, Any]:
        args = self.llm.call_function(
            prompts.REVIEWER_SYSTEM,
            prompts.reviewer_prompt(
                state["question"], state["sql"], state["result"], self.settings.preview_rows
            ),
            REVIEW_QUERY_FN,
        )
        review = {"verdict": args.get("verdict", "approve"), "issues": args.get("issues", [])}
        update: dict[str, Any] = {"review": review}
        if review["verdict"] == "revise":
            update["history"] = [{"sql": state["sql"], "issues": review["issues"]}]
        return update

    def _finalize(self, state: AgentState) -> dict[str, Any]:
        result = state.get("result") or {}
        review = state.get("review") or {}
        if result.get("error"):
            status = "failed"
        elif review.get("verdict") == "revise":
            status = "unverified"  # ran out of attempts while the reviewer still had issues
        else:
            status = "answered"

        answer = ""
        if state.get("generate_answer", True):
            answer = self.llm.complete(
                prompts.ANSWER_SYSTEM,
                prompts.answer_prompt(
                    state["question"], state.get("sql", ""), result, self.settings.preview_rows
                ),
            )
        return {"status": status, "answer": answer}

    # ---------------------------------------------------------------- routing

    def _can_retry(self, state: AgentState) -> bool:
        return state.get("attempts", 0) < self.settings.max_attempts

    def _after_guard(self, state: AgentState) -> str:
        if state.get("guard_ok"):
            return "execute"
        return "write_sql" if self._can_retry(state) else "finalize"

    def _after_execute(self, state: AgentState) -> str:
        if state["result"].get("error"):
            return "write_sql" if self._can_retry(state) else "finalize"
        return "review" if self.settings.enable_reviewer else "finalize"

    def _after_review(self, state: AgentState) -> str:
        if state["review"]["verdict"] == "approve":
            return "finalize"
        return "write_sql" if self._can_retry(state) else "finalize"

    # ------------------------------------------------------------------ graph

    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("link_schema", _timed("link_schema", self._link_schema))
        g.add_node("write_sql", _timed("write_sql", self._write_sql))
        g.add_node("guard", _timed("guard", self._guard))
        g.add_node("execute", _timed("execute", self._execute))
        g.add_node("review", _timed("review", self._review))
        g.add_node("finalize", _timed("finalize", self._finalize))

        g.add_edge(START, "link_schema")
        g.add_edge("link_schema", "write_sql")
        g.add_edge("write_sql", "guard")
        g.add_conditional_edges("guard", self._after_guard, ["execute", "write_sql", "finalize"])
        g.add_conditional_edges("execute", self._after_execute, ["review", "write_sql", "finalize"])
        g.add_conditional_edges("review", self._after_review, ["finalize", "write_sql"])
        g.add_edge("finalize", END)
        return g.compile()

    # -------------------------------------------------------------------- run

    def run(self, question: str, generate_answer: bool = True) -> dict[str, Any]:
        start = time.perf_counter()
        state = self.graph.invoke(
            {
                "question": question,
                "generate_answer": generate_answer,
                "attempts": 0,
                "history": [],
                "trace": [],
            },
            # Each attempt is ~3 steps (write, guard, execute/review), plus linker and finalize.
            {"recursion_limit": 4 * self.settings.max_attempts + 6},
        )
        state["latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return state
