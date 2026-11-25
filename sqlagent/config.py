from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The agent only ever connects with the read-only role.
    database_url: str = "postgresql://analyst_ro:analyst_ro@localhost:5432/nyc_taxi"

    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    openai_temperature: float = 0.0

    # Agent loop
    max_attempts: int = 3
    enable_reviewer: bool = True

    # Query execution limits
    statement_timeout_ms: int = 30_000
    max_rows: int = 1_000  # LIMIT injected into queries that have none
    preview_rows: int = 20  # rows shown to the reviewer / answer agents
    response_rows: int = 100  # rows returned by the API

    pool_min_size: int = 1
    pool_max_size: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
