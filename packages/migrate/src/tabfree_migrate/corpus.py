"""Verify the test corpus against ``corpus/manifest.json`` (BUILD_PLAN P0.5).

Corpus files are untrusted input: XML is parsed with defusedxml, and package members are
size-checked before they are read into memory. Nothing is ever extracted to disk or executed.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree as SafeElementTree
from defusedxml.common import DefusedXmlException

#: Manifest kinds stored as plain XML, with the root element each must have.
XML_ROOTS = {"twb": "workbook", "tds": "datasource"}
#: Packaged kinds: (inner file suffix, expected root element of the inner file).
PACKAGES = {"twbx": (".twb", "workbook"), "tdsx": (".tds", "datasource")}
#: Refuse to inflate package members larger than this (zip-bomb guard).
MAX_MEMBER_BYTES = 256 * 1024 * 1024


@dataclass
class CorpusReport:
    checked: int = 0
    skipped: int = 0
    problems: list[str] = field(default_factory=list)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def xml_root_tag(data: bytes) -> str:
    """Return the root element name of untrusted XML (entities and external refs forbidden)."""
    root = SafeElementTree.fromstring(data, forbid_dtd=False, forbid_entities=True, forbid_external=True)
    return str(root.tag)


def _package_member(path: Path, suffix: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        members = [m for m in archive.infolist() if m.filename.lower().endswith(suffix)]
        if not members:
            raise ValueError(f"no {suffix} file inside the package")
        member = min(members, key=lambda m: m.filename.count("/"))  # prefer the top level
        if member.file_size > MAX_MEMBER_BYTES:
            raise ValueError(f"{member.filename} is larger than {MAX_MEMBER_BYTES} bytes")
        return archive.read(member)


def _check_entry(repo_root: Path, entry: dict[str, Any]) -> list[str]:
    rel = str(entry["path"])
    path = repo_root / rel
    if not path.is_file():
        return [f"{rel}: listed in the manifest but missing"]
    problems: list[str] = []
    expected_hash = entry.get("sha256")
    if expected_hash and sha256_of(path) != expected_hash:
        problems.append(f"{rel}: sha256 does not match the manifest")
    expected_size = entry.get("bytes")
    if isinstance(expected_size, int) and path.stat().st_size != expected_size:
        problems.append(f"{rel}: size does not match the manifest")

    kind = entry.get("kind")
    # Upstream test suites ship deliberately broken files; those must keep failing to parse.
    expect_invalid = bool(entry.get("expect_invalid"))
    structural: list[str] = []
    try:
        if kind in XML_ROOTS:
            tag = xml_root_tag(path.read_bytes())
            if tag != XML_ROOTS[kind]:
                structural.append(f"{rel}: root element is <{tag}>, expected <{XML_ROOTS[kind]}>")
        elif kind in PACKAGES:
            suffix, root = PACKAGES[kind]
            tag = xml_root_tag(_package_member(path, suffix))
            if tag != root:
                structural.append(f"{rel}: inner {suffix} root is <{tag}>, expected <{root}>")
        else:
            return problems
    except (ParseError, DefusedXmlException, zipfile.BadZipFile, ValueError) as exc:
        structural.append(f"{rel}: {type(exc).__name__}: {exc}")
    if expect_invalid:
        if not structural:
            problems.append(f"{rel}: marked expect_invalid but parses cleanly")
    else:
        problems.extend(structural)
    return problems


def verify_corpus(repo_root: Path) -> CorpusReport:
    """Check every manifest entry that has a local file; entries without one are skipped."""
    manifest = json.loads((repo_root / "corpus" / "manifest.json").read_text(encoding="utf-8"))
    report = CorpusReport()
    for entry in manifest.get("files", []):
        if not entry.get("path") or entry.get("external"):
            report.skipped += 1
            continue
        report.checked += 1
        report.problems.extend(_check_entry(repo_root, entry))
    return report
