"""``tabfree-validate``: validate dashboard manifests, semantic models and Tableau IR files."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import yaml

from tabfree_schemas.validate import SchemaKind, SchemaValidationError, load_document, validate


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tabfree-validate",
        description="Validate TabFree documents (tabfree.json manifests, semantic models, IR).",
    )
    parser.add_argument("paths", nargs="+", type=Path, help="JSON or YAML files to check")
    parser.add_argument(
        "--kind",
        choices=[k.value for k in SchemaKind],
        help='document type (default: detect from the top-level "schema" property)',
    )
    args = parser.parse_args(argv)
    kind = SchemaKind(args.kind) if args.kind else None

    failed = 0
    for path in args.paths:
        try:
            resolved = validate(load_document(path), kind)
        except SchemaValidationError as exc:
            failed += 1
            print(f"FAIL {path} ({exc.kind.value})")
            for error in exc.errors:
                print(f"     {error}")
        except (OSError, ValueError, yaml.YAMLError) as exc:
            failed += 1
            print(f"FAIL {path}: {exc}")
        else:
            print(f"ok   {path} ({resolved.value})")
    return 1 if failed else 0
