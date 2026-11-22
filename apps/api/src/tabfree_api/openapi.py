"""Write the API reference (BUILD_PLAN P7.6): the OpenAPI document the server serves at /api/openapi.json.

    uv run python -m tabfree_api.openapi docs/reference/openapi.json

Built from a development app on an empty in-memory database, so it needs no configuration and is the
same on every machine (keys sorted). The documentation site renders it.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def document() -> dict[str, Any]:
    from pydantic import SecretStr

    from tabfree_api.db import connect, upgrade
    from tabfree_api.db.seed import create_site
    from tabfree_api.main import create_app
    from tabfree_api.settings import Settings

    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite:///{Path(tmp) / 'openapi.db'}"
        engine = connect(url)
        upgrade(engine)
        create_site(engine, "default", "TabFree")
        settings = Settings(
            environment="test", database_url=SecretStr(url), site_id="default", session_db=Path(tmp) / "s.db"
        )
        spec = create_app(settings).openapi()
        engine.dispose()
    return dict(spec)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    text = json.dumps(document(), indent=2, sort_keys=True) + "\n"
    if args:
        out = Path(args[0])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
