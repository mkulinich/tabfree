"""Service configuration, read from ``TABFREE_*`` environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TABFREE_", extra="ignore")

    environment: str = "dev"
