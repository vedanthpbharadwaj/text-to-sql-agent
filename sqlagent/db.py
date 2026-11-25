"""Read-only query execution against PostgreSQL."""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool


@dataclass
class QueryResult:
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    elapsed_ms: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def to_jsonable(value: Any) -> Any:
    """Convert database values into JSON-friendly Python values."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    return value


class Database:
    """Thin wrapper around a connection pool that runs one read-only SELECT at a time.

    Every query runs inside a READ ONLY transaction with a statement timeout,
    on top of the read-only role configured in db/init/02_readonly_role.sh.
    """

    def __init__(
        self,
        dsn: str,
        statement_timeout_ms: int = 30_000,
        max_rows: int = 1_000,
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        self.statement_timeout_ms = statement_timeout_ms
        self.max_rows = max_rows
        self.pool = ConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs={"autocommit": False},
            open=True,
        )

    def close(self) -> None:
        self.pool.close()

    def execute(self, query: str, max_rows: int | None = None) -> QueryResult:
        limit = max_rows or self.max_rows
        start = time.perf_counter()
        try:
            with self.pool.connection() as conn:
                conn.read_only = True
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            sql.SQL("SET LOCAL statement_timeout = {}").format(
                                sql.Literal(self.statement_timeout_ms)
                            )
                        )
                        cur.execute(query)
                        if cur.description is None:
                            return QueryResult(
                                error="Query returned no result set.",
                                elapsed_ms=_ms(start),
                            )
                        columns = [d.name for d in cur.description]
                        fetched = cur.fetchmany(limit + 1)
                truncated = len(fetched) > limit
                rows = [[to_jsonable(v) for v in r] for r in fetched[:limit]]
                return QueryResult(
                    columns=columns,
                    rows=rows,
                    row_count=len(rows),
                    truncated=truncated,
                    elapsed_ms=_ms(start),
                )
        except psycopg.Error as exc:
            message = (exc.diag.message_primary if exc.diag else None) or str(exc)
            hint = exc.diag.message_hint if exc.diag else None
            if hint:
                message = f"{message} (hint: {hint})"
            return QueryResult(error=message.strip(), elapsed_ms=_ms(start))


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
