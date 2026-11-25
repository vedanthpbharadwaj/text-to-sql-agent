"""Static checks applied to every LLM-generated query before it reaches Postgres.

This is the first of two layers. The second is the database itself: the agent
connects as a read-only role inside READ ONLY transactions (see db.py and
db/init/02_readonly_role.sh), so even a query that slipped past these checks
could not modify data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

DIALECT = "postgres"

# sqlglot logs a warning when it falls back to parsing unknown syntax as a
# Command; we reject those anyway, so keep the logs quiet.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

# Statement / clause types that must never appear anywhere in the tree.
_FORBIDDEN_NODE_NAMES = [
    "Insert", "Update", "Delete", "Merge", "Drop", "Create", "Alter", "AlterTable",
    "TruncateTable", "Command", "Into", "Lock", "Grant", "Revoke", "Copy", "Set",
    "Transaction", "Commit", "Rollback", "Use", "Pragma", "LoadData",
]
FORBIDDEN_NODES = tuple(
    getattr(exp, name) for name in _FORBIDDEN_NODE_NAMES if hasattr(exp, name)
)

# Functions that can read the filesystem, stall the server, or change settings.
FORBIDDEN_FUNCTIONS = {
    "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "lo_import", "lo_export", "lo_get", "lo_put",
    "dblink", "dblink_exec", "set_config",
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "pg_advisory_lock", "pg_advisory_xact_lock",
    "query_to_xml", "query_to_json",
}


@dataclass
class GuardResult:
    ok: bool
    sql: str
    reason: str | None = None


def check_sql(query: str, allowed_tables: set[str], max_rows: int) -> GuardResult:
    """Validate that `query` is a single read-only SELECT over allowed tables.

    Returns the (possibly LIMIT-augmented) SQL when the query passes.
    """
    text = (query or "").strip().rstrip(";").strip()
    if not text:
        return GuardResult(False, query, "Empty query.")

    try:
        statements = [s for s in sqlglot.parse(text, read=DIALECT) if s is not None]
    except ParseError as exc:
        return GuardResult(False, query, f"SQL could not be parsed: {_first_line(exc)}")

    if len(statements) != 1:
        return GuardResult(False, query, "Only a single SQL statement is allowed.")

    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        return GuardResult(
            False, query, f"Only SELECT queries are allowed, got {tree.key.upper()}."
        )

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return GuardResult(
                False, query, f"Forbidden operation in query: {node.key.upper()}."
            )
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in FORBIDDEN_FUNCTIONS:
                return GuardResult(False, query, f"Function {name}() is not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    allowed = {t.lower() for t in allowed_tables}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not name or name in cte_names:
            continue
        schema = (table.db or "").lower()
        if schema not in ("", "public") or name not in allowed:
            return GuardResult(
                False,
                query,
                f"Unknown or disallowed table '{table.sql(dialect=DIALECT)}'. "
                f"Allowed tables: {', '.join(sorted(allowed))}.",
            )

    if tree.args.get("limit") is None:
        tree = tree.limit(max_rows)
        return GuardResult(True, tree.sql(dialect=DIALECT, pretty=True))
    return GuardResult(True, text)


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0][:300]
