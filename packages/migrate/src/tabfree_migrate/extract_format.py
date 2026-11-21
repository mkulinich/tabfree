"""Tell extract files apart by their first bytes, never by name (BUILD_PLAN P2.8).

- ``.hyper`` files start with ``Hyper`` (Hyper 10.5+ format, read by ``tableauhyperapi``).
- Legacy ``.tde`` files (Tableau Data Engine, before 10.5) start with
  ``20 02 01 62 a0 1e ab 07``; every ``.tde`` in the corpus has it. The current Hyper API cannot
  read them: see :mod:`tabfree_migrate.tde`.
- ``.twbx``/``.tdsx`` packages are zip files (``PK\\x03\\x04``) that may contain either.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

HYPER_MAGIC = b"Hyper"
TDE_MAGIC = b" \x02\x01b\xa0\x1e\xab\x07"
ZIP_MAGIC = b"PK\x03\x04"
SNIFF_BYTES = 16


class ExtractFormat(StrEnum):
    HYPER = "hyper"
    TDE = "tde"
    PACKAGE = "package"
    UNKNOWN = "unknown"


def detect_format(head: bytes) -> ExtractFormat:
    if head.startswith(HYPER_MAGIC):
        return ExtractFormat.HYPER
    if head.startswith(TDE_MAGIC):
        return ExtractFormat.TDE
    if head.startswith(ZIP_MAGIC):
        return ExtractFormat.PACKAGE
    return ExtractFormat.UNKNOWN


def sniff(path: Path) -> ExtractFormat:
    with path.open("rb") as handle:
        return detect_format(handle.read(SNIFF_BYTES))
