from __future__ import annotations

from .agents import SQLAgent
from .config import Settings
from .db import Database
from .llm import OpenAILLM
from .schema_catalog import SchemaCatalog


def build_agent(settings: Settings) -> SQLAgent:
    """Wire up the database pool, schema catalog, LLM client and agent graph."""
    db = Database(
        settings.database_url,
        statement_timeout_ms=settings.statement_timeout_ms,
        max_rows=settings.max_rows,
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
    )
    catalog = SchemaCatalog.from_dsn(settings.database_url)
    llm = OpenAILLM(
        settings.openai_model,
        api_key=settings.openai_api_key,
        temperature=settings.openai_temperature,
    )
    return SQLAgent(llm=llm, db=db, catalog=catalog, settings=settings)
