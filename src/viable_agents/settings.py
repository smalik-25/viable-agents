"""Runtime secrets and connection strings, the only place ``os.environ`` is read.

Environment variables never override YAML config values; only secrets and
connection strings come from the environment, so a run's ``config_fingerprint``
stays honest. The kernel does not import this module.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://viable:viable@localhost:5433/viable_agents"
    anthropic_api_key: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_base_url: str = "https://us.cloud.langfuse.com"
    langfuse_tracing_enabled: bool = True


def load_settings() -> Settings:
    return Settings()
