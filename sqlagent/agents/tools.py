"""JSON schemas for the OpenAI function calls each agent must make."""

from __future__ import annotations

from typing import Any


def select_tables_fn(table_names: list[str]) -> dict[str, Any]:
    return {
        "name": "select_tables",
        "description": "Choose the tables needed to answer the question.",
        "parameters": {
            "type": "object",
            "properties": {
                "tables": {
                    "type": "array",
                    "items": {"type": "string", "enum": table_names},
                    "description": "Every table the SQL query will need, including lookup tables for joins.",
                },
                "notes": {
                    "type": "string",
                    "description": "Short notes on join keys or filters the SQL writer should use.",
                },
            },
            "required": ["tables", "notes"],
        },
    }


SUBMIT_SQL_FN: dict[str, Any] = {
    "name": "submit_sql",
    "description": "Submit one PostgreSQL SELECT query that answers the question.",
    "parameters": {
        "type": "object",
        "properties": {
            "reasoning": {
                "type": "string",
                "description": "Brief plan: tables, joins, filters, aggregation.",
            },
            "sql": {
                "type": "string",
                "description": "A single read-only PostgreSQL SELECT statement.",
            },
        },
        "required": ["reasoning", "sql"],
    },
}


REVIEW_QUERY_FN: dict[str, Any] = {
    "name": "review_query",
    "description": "Judge whether the executed query correctly answers the question.",
    "parameters": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["approve", "revise"],
                "description": "approve if the query and result answer the question exactly; otherwise revise.",
            },
            "issues": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concrete problems to fix. Empty when approving.",
            },
        },
        "required": ["verdict", "issues"],
    },
}
