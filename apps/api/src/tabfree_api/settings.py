"""Service configuration, read from ``TABFREE_*`` environment variables."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TABFREE_", extra="ignore")

    environment: str = "dev"
    #: Public URL of this service; cookies are Secure when it is https.
    base_url: str = "http://localhost:8000"
    #: Projects, dashboards, permissions and users (Postgres from Phase 3).
    site_file: Path = REPO_ROOT / "apps" / "api" / "dev" / "site.yaml"
    #: SQLite file for sessions when there is no database (Phase 1 mode).
    session_db: Path = Path("tabfree-sessions.db")
    #: The metadata database (P3.1), e.g. postgresql+psycopg://user:pass@host/tabfree. When set, content,
    #: permissions and sessions come from it and ``site_file`` is not read. It carries a password:
    #: inject it (``secret run -e TABFREE_DATABASE_URL=...``), never write it to a file.
    database_url: SecretStr | None = None
    #: The site this process serves (multi-site routing arrives with /site/<id> URLs).
    site_id: str = "default"
    #: Apply migrations at start-up (dev and test only; deployments run ``python -m tabfree_api.db upgrade``).
    database_auto_migrate: bool = False
    #: Seconds a process may serve a cached view of permissions after a change made elsewhere.
    permission_cache_s: float = Field(default=2.0, ge=0)
    session_hours: float = 8.0
    #: Where queries run: "sample" is DuckDB with generated data; "aws" uses each model's connection.
    engine: Literal["sample", "aws"] = "sample"

    # ---- query service (P2.7) -------------------------------------------------------------------
    #: Most rows any query returns; more are cut off and the result is flagged ``truncated``.
    query_row_limit: int = Field(default=10_000, ge=1)
    #: Most bytes of a result (approximate JSON size of the rows); more rows are cut off and flagged.
    query_max_bytes: int = Field(default=16 * 1024 * 1024, ge=1024)
    #: Per-query engine timeout; a query running longer is cancelled on the engine.
    query_timeout_s: float = Field(default=60.0, gt=0)
    #: Result cache entries kept in memory (0 turns the cache off) and how long they stay fresh.
    result_cache_entries: int = Field(default=2_000, ge=0)
    result_cache_ttl_s: float = Field(default=300.0, gt=0)
    #: Athena result reuse (exact repeats in the same workgroup, at most this old); 0 turns it off.
    athena_result_reuse_minutes: int = Field(default=60, ge=0, le=10_080)
    #: Allow Athena result reuse for queries with ExecutionParameters (RLS, filter values). Off until
    #: P2.10 confirms on real Athena that reuse matches on the parameter values as well as the SQL.
    athena_reuse_with_parameters: bool = False
    #: Athena workgroup per site id; the site file's ``query.athenaWorkgroup`` wins over this.
    athena_workgroups: dict[str, str] = Field(default_factory=dict)
    #: Bytes scanned a site may spend per UTC day (None: no budget); the site file can override it.
    daily_scan_budget_bytes: int | None = Field(default=None, ge=0)
    #: Redshift does not report bytes scanned per query, so each one is charged this estimate.
    redshift_scan_estimate_bytes: int = Field(default=100 * 1024 * 1024, ge=0)
    #: Engine queries running at once per site (Athena allows 200 active queries per account).
    max_concurrent_queries: int = Field(default=20, ge=1)
    #: How long a query waits for a free slot before it is refused with 429.
    queue_timeout_s: float = Field(default=10.0, ge=0)

    # ---- exports (P3.7) -----------------------------------------------------------------------------
    #: Most rows and bytes a data export (CSV or Excel) may carry.
    export_row_limit: int = Field(default=1_000_000, ge=1)
    export_max_bytes: int = Field(default=256 * 1024 * 1024, ge=1024)
    #: PNG/PDF exports: "playwright" renders in headless Chromium (needs tabfree-api[render]); "none" refuses.
    export_renderer: Literal["none", "playwright"] = "none"
    #: The URL the renderer opens (the portal as seen from the export worker); defaults to base_url.
    export_base_url: str | None = None

    #: Built runtime bundles (npm run build -w @tabfree/runtime).
    runtime_dir: Path = REPO_ROOT / "packages" / "runtime" / "dist" / "bundle"
    #: Separate origin that serves dashboard bundles (P3.6), e.g. https://usercontent.tabfree.example.
    #: Point it at this service. With it set, the portal page gets a strict CSP and dashboards load from
    #: signed, short-lived /frame URLs there; without it they load as inline srcdoc (development).
    usercontent_origin: str | None = None
    #: HMAC key for /frame URLs. Set it when several processes serve the API (any one may verify).
    frame_signing_key: SecretStr | None = None
    #: The built portal (npm run build -w @tabfree/portal); without it, / serves a minimal page.
    portal_dir: Path = REPO_ROOT / "apps" / "portal" / "dist"

    # OpenID Connect. The secret comes from the environment (inject it with `secret run`), never a file.
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_scopes: str = "openid email profile"
    oidc_username_claim: str = "preferred_username"
    oidc_groups_claim: str = "groups"
    #: ID-token claims copied into user attributes for RLS (``USERATTRIBUTE('region')``), e.g. ["region"].
    oidc_attribute_claims: list[str] = Field(default_factory=list)

    #: Sign in as any site-file user without a password. Development only.
    dev_login: bool = False
    #: Mount the local dashboard preview (/preview). Development only: it has no authentication.
    preview: bool = False

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")
