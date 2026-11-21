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
    parser.add_argument(
        "paths", nargs="+", type=Path, help="JSON or YAML files, or dashboard bundle directories, to check"
    )
    parser.add_argument(
        "--kind",
        choices=[k.value for k in SchemaKind],
        help='document type (default: detect from the top-level "schema" property)',
    )
    args = parser.parse_args(argv)
    kind = SchemaKind(args.kind) if args.kind else None

    failed = 0
    for path in args.paths:
        if path.is_dir() and (path / "tabfree.json").is_file():
            from tabfree_schemas.bundle import check_bundle_dir

            report = check_bundle_dir(path)
            print(f"{'ok  ' if report.ok else 'FAIL'} {path} (bundle: {report.components} components)")
            for error in report.errors:
                print(f"     error: {error}")
            for warning in report.warnings:
                print(f"     warning: {warning}")
            failed += 0 if report.ok else 1
            continue
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
