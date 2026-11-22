"""``tabfree``: author TabFree dashboards from a folder, with Claude Code or any editor (P4.2).

    tabfree login https://tabfree.example.com    # sign in (browser, OAuth with PKCE)
    tabfree pull sales-overview                  # files into ./sales-overview/
    tabfree push sales-overview                  # your changes into your private draft
    tabfree validate sales-overview              # the full validation pipeline
    tabfree preview sales-overview               # open the draft's live preview in the portal
    tabfree publish sales-overview -m "message"  # review the diff, then confirm
    tabfree kit .                                # CLAUDE.md, .mcp.json and the authoring skill

A pulled folder has ``.tabfree.json`` (server, dashboard, draft id) next to the bundle files.
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from collections.abc import Sequence
from importlib import resources
from pathlib import Path
from typing import Any

from tabfree_cli.client import CliError, Credentials, Mcp, connect, login

BUNDLE = ("tabfree.json", "index.html", "README.md")
STATE = ".tabfree.json"


def _state(folder: Path) -> dict[str, Any]:
    path = folder / STATE
    if not path.is_file():
        raise CliError(f"{folder} was not pulled with tabfree (no {STATE}); run `tabfree pull DASHBOARD`")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _save_state(folder: Path, data: dict[str, Any]) -> None:
    (folder / STATE).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def pull(mcp: Mcp, dashboard: str, folder: Path) -> Path:
    result = mcp.call("get_dashboard", dashboard=dashboard)
    data = result["structuredContent"]
    folder.mkdir(parents=True, exist_ok=True)
    for path, text in data["files"].items():
        if path in BUNDLE:
            (folder / path).write_text(text, encoding="utf-8")
    drafts = data.get("openDrafts") or []
    _save_state(folder, {"url": mcp.url, "dashboard": dashboard, "draft": drafts[0] if drafts else None})
    return folder


def push(mcp: Mcp, folder: Path) -> dict[str, Any]:
    state = _state(folder)
    edits = [
        {"path": path, "op": "write", "content": (folder / path).read_text(encoding="utf-8")}
        for path in BUNDLE
        if (folder / path).is_file()
    ]
    target = {"draft": state["draft"]} if state.get("draft") else {"dashboard": state["dashboard"]}
    result = mcp.call("edit_dashboard_files", edits=edits, **target)
    structured: dict[str, Any] = result["structuredContent"]
    state["draft"] = structured["draft"]["id"]
    _save_state(folder, state)
    return structured


def _print_validation(report: dict[str, Any]) -> None:
    for check in report["checks"]:
        print(f"{check['name']:>14}: {check['status']}")
        for message in check["messages"]:
            print(f"{'':>16}{message}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tabfree", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("login", help="sign in to a TabFree server in the browser")
    p.add_argument("url")
    p = commands.add_parser("pull", help="download a dashboard (or your open draft of it)")
    p.add_argument("dashboard")
    p.add_argument("folder", nargs="?")
    for name, text in (
        ("push", "upload your changes to your draft"),
        ("validate", "run the validation pipeline"),
        ("preview", "open the draft's live preview"),
        ("diff", "show the draft's changes"),
    ):
        p = commands.add_parser(name, help=text)
        p.add_argument("folder", nargs="?", default=".")
    p = commands.add_parser("publish", help="publish the draft after reviewing the diff")
    p.add_argument("folder", nargs="?", default=".")
    p.add_argument("-m", "--message", default="")
    p.add_argument("--yes", action="store_true", help="do not ask (for scripts that already showed the diff)")
    p = commands.add_parser("kit", help="write CLAUDE.md, .mcp.json and the authoring skill into a folder")
    p.add_argument("folder", nargs="?", default=".")
    p.add_argument("--url", help="the TabFree server (default: the one you signed in to)")
    args = parser.parse_args(argv)

    try:
        if args.command == "login":
            creds = login(args.url)
            print(f"Signed in to {creds.url}; saved to {creds.save()}")
        elif args.command == "kit":
            url = args.url or (Credentials.load() or Credentials("")).url
            for path in write_kit(Path(args.folder), url or "https://tabfree.example.com"):
                print(f"wrote {path}")
        elif args.command == "pull":
            mcp = connect()
            folder = pull(mcp, args.dashboard, Path(args.folder or args.dashboard))
            print(f"pulled {args.dashboard} into {folder}/")
        else:
            folder = Path(args.folder)
            state = _state(folder)
            mcp = connect(state["url"])
            if args.command == "push":
                result = push(mcp, folder)
                print(f"draft {result['draft']['id']} updated")
                _print_validation(result["validation"])
                return 0 if result["validation"]["ok"] else 1
            draft = state.get("draft")
            if not draft:
                raise CliError("no draft yet: run `tabfree push` first")
            if args.command == "validate":
                report = mcp.call("validate_dashboard", draft=draft)["structuredContent"]
                _print_validation(report)
                return 0 if report["ok"] else 1
            if args.command == "diff":
                print(mcp.call("diff_revisions", draft=draft)["content"][0]["text"])
            elif args.command == "preview":
                url = f"{state['url'].rstrip('/')}/drafts/{draft}"
                print(url)
                webbrowser.open(url)
            elif args.command == "publish":
                request = mcp.call("request_publish", draft=draft, message=args.message)
                print(request["content"][0]["text"].split("Confirmation code:")[0].strip())
                print(mcp.call("diff_revisions", draft=draft)["content"][0]["text"])
                if (
                    not args.yes
                    and input("Publish these changes for everyone who can see the dashboard? [y/N] ")
                    .strip()
                    .lower()
                    != "y"
                ):
                    print("Not published. The draft stays as it is.")
                    return 1
                code = request["structuredContent"]["confirmation"]
                revision = mcp.call("confirm_publish", draft=draft, confirmation=code)["structuredContent"][
                    "revision"
                ]
                state["draft"] = None
                _save_state(folder, state)
                print(f"published revision {revision}")
    except CliError as exc:
        print(f"tabfree: {exc}", file=sys.stderr)
        return 2
    return 0


def write_kit(folder: Path, url: str) -> list[Path]:
    """CLAUDE.md, .mcp.json and the authoring skill, for building dashboards with Claude Code."""
    kit = resources.files("tabfree_cli").joinpath("kit")
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    claude_md = folder / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text(kit.joinpath("CLAUDE.md").read_text(encoding="utf-8"), encoding="utf-8")
        written.append(claude_md)
    mcp_json = folder / ".mcp.json"
    config: dict[str, Any] = json.loads(mcp_json.read_text(encoding="utf-8")) if mcp_json.exists() else {}
    config.setdefault("mcpServers", {})["tabfree"] = {"type": "http", "url": f"{url.rstrip('/')}/mcp"}
    mcp_json.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    written.append(mcp_json)
    skill = folder / ".claude" / "skills" / "tabfree-dashboard" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        kit.joinpath("skills", "tabfree-dashboard", "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    written.append(skill)
    return written


if __name__ == "__main__":
    raise SystemExit(main())
