"""Service configuration, read from ``TABFREE_*`` environment variables."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TABFREE_", extra="ignore")

    environment: str = "dev"
    #: Public URL of this service; cookies are Secure when it is https.
    base_url: str = "http://localhost:8000"
    #: Projects, dashboards, permissions and users (Postgres from Phase 3).
    site_file: Path = REPO_ROOT / "apps" / "api" / "dev" / "site.yaml"
    #: SQLite file for sessions (Postgres from Phase 3).
    session_db: Path = Path("tabfree-sessions.db")
    session_hours: float = 8.0
    #: Where queries run: "sample" is DuckDB with generated data; "aws" uses each model's connection.
    engine: Literal["sample", "aws"] = "sample"
    #: Built runtime bundles (npm run build -w @tabfree/runtime).
    runtime_dir: Path = REPO_ROOT / "packages" / "runtime" / "dist" / "bundle"

    # OpenID Connect. The secret comes from the environment (inject it with `secret run`), never a file.
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_scopes: str = "openid email profile"
    oidc_username_claim: str = "preferred_username"
    oidc_groups_claim: str = "groups"

    #: Sign in as any site-file user without a password. Development only.
    dev_login: bool = False
    #: Mount the local dashboard preview (/preview). Development only: it has no authentication.
    preview: bool = False

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")
