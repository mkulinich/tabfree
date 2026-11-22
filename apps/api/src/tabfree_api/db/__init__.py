"""The TabFree metadata database (P3.1): schema, migrations and the stores built on them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event

from tabfree_api.db.schema import metadata

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def connect(url: str, **kwargs: Any) -> Engine:
    """An engine for ``url`` (``postgresql+psycopg://...`` or ``sqlite:///...``)."""
    engine = create_engine(url, pool_pre_ping=True, **kwargs)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def alembic_config(engine: Engine) -> Any:
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option(
        "sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%")
    )
    return config


def upgrade(engine: Engine, revision: str = "head") -> None:
    """Apply migrations up to ``revision``."""
    from alembic import command

    config = alembic_config(engine)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def downgrade(engine: Engine, revision: str) -> None:
    from alembic import command

    config = alembic_config(engine)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, revision)


__all__ = ["MIGRATIONS", "connect", "downgrade", "metadata", "upgrade"]
