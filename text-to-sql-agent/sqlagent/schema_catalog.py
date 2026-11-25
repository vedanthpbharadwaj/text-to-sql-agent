"""Introspects the database once and renders schema context for the prompts.

On a 40M-row table we never run COUNT(*) here; row counts come from
pg_class.reltuples, and sample values are only collected for small,
low-cardinality text columns.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

# Text columns whose distinct values help the model write correct filters.
SAMPLE_VALUE_COLUMNS = {
    ("taxi_zones", "borough"),
    ("taxi_zones", "service_zone"),
    ("trips", "taxi_type"),
    ("vendors", "vendor_name"),
    ("payment_types", "description"),
    ("rate_codes", "description"),
}


@dataclass
class Column:
    name: str
    data_type: str
    description: str = ""
    sample_values: list[str] = field(default_factory=list)


@dataclass
class Table:
    name: str
    description: str = ""
    approx_rows: int = 0
    columns: list[Column] = field(default_factory=list)


class SchemaCatalog:
    def __init__(self, tables: dict[str, Table]) -> None:
        self.tables = tables

    @property
    def table_names(self) -> list[str]:
        return list(self.tables)

    # ---------- loading ----------

    @classmethod
    def from_dsn(cls, dsn: str) -> SchemaCatalog:
        with psycopg.connect(dsn) as conn:
            return cls.from_connection(conn)

    @classmethod
    def from_connection(cls, conn: psycopg.Connection) -> SchemaCatalog:
        tables: dict[str, Table] = {}
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.relname,
                       COALESCE(obj_description(c.oid, 'pg_class'), ''),
                       GREATEST(c.reltuples, 0)::bigint
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                ORDER BY c.relname
                """
            )
            for name, desc, approx in cur.fetchall():
                tables[name] = Table(name=name, description=desc, approx_rows=approx)

            cur.execute(
                """
                SELECT c.table_name, c.column_name, c.data_type,
                       COALESCE(col_description(
                           format('%I.%I', c.table_schema, c.table_name)::regclass,
                           c.ordinal_position), '')
                FROM information_schema.columns c
                WHERE c.table_schema = 'public'
                ORDER BY c.table_name, c.ordinal_position
                """
            )
            for table, column, dtype, desc in cur.fetchall():
                if table in tables:
                    tables[table].columns.append(Column(column, dtype, desc))

            for table, column in sorted(SAMPLE_VALUE_COLUMNS):
                if table not in tables:
                    continue
                if not any(c.name == column for c in tables[table].columns):
                    continue
                # Lookup tables are tiny; for trips.taxi_type a sampled scan is enough.
                source = (
                    f"{table} TABLESAMPLE SYSTEM (1)"
                    if tables[table].approx_rows > 100_000
                    else table
                )
                cur.execute(
                    f'SELECT DISTINCT "{column}" FROM {source} '
                    f'WHERE "{column}" IS NOT NULL ORDER BY 1 LIMIT 25'
                )
                values = [str(r[0]) for r in cur.fetchall()]
                for col in tables[table].columns:
                    if col.name == column:
                        col.sample_values = values
        return cls(tables)

    # ---------- rendering ----------

    def summary(self) -> str:
        """One line per table, used by the schema linker."""
        lines = []
        for t in self.tables.values():
            cols = ", ".join(c.name for c in t.columns)
            lines.append(f"- {t.name} (~{t.approx_rows:,} rows): {t.description}\n  columns: {cols}")
        return "\n".join(lines)

    def ddl(self, table_names: list[str] | None = None) -> str:
        """Annotated CREATE TABLE text for the selected tables, used by the SQL writer."""
        names = table_names or self.table_names
        blocks = []
        for name in names:
            t = self.tables.get(name)
            if t is None:
                continue
            col_lines = []
            for i, c in enumerate(t.columns):
                notes = []
                if c.description:
                    notes.append(c.description)
                if c.sample_values:
                    notes.append("values: " + ", ".join(repr(v) for v in c.sample_values))
                comma = "," if i < len(t.columns) - 1 else ""
                comment = f"  -- {'; '.join(notes)}" if notes else ""
                col_lines.append(f"    {c.name} {c.data_type}{comma}{comment}")
            header = f"-- {t.description} (~{t.approx_rows:,} rows)\n" if t.description else ""
            blocks.append(f"{header}CREATE TABLE {t.name} (\n" + "\n".join(col_lines) + "\n);")
        return "\n\n".join(blocks)
