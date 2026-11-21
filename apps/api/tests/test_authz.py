"""P3.3: the Tableau-compatible permission engine, table-driven.

Each case states who asks, for what, on which resource, and the expected answer with the rule
that decides it (Tableau's documented evaluation order; see tabfree_api.authz and ADR-006).
"""

from __future__ import annotations

import pytest

from tabfree_api.authz import (
    APPLICABLE,
    CEILINGS,
    Capability,
    ContentEntry,
    Principal,
    Project,
    Rule,
    SiteRole,
    capabilities,
    evaluate,
    max_role,
    parse_rules,
)

C = Capability
R = SiteRole


def who(user: str, *groups: str, role: SiteRole = R.CREATOR) -> Principal:
    return Principal(user, frozenset(groups), role)


RULES = parse_rules(
    [
        {
            "group": "A",
            "capabilities": {"view": "allow", "download_full_data": "allow", "overwrite": "allow"},
        },
        {"group": "B", "capabilities": {"view": "deny"}},
        {"user": "uma", "capabilities": {"view": "allow"}},
        {"user": "dan", "capabilities": {"view": "deny"}},
    ]
)
LEAD = Rule("lena", None, {C.PROJECT_LEADER: "allow"})
LEAD_GROUP = Rule(None, "Leads", {C.PROJECT_LEADER: "allow"})
PROJECTS = {
    # Top level, customizable: content keeps its own rules.
    "open": Project("open", "Open", "customizable", {"dashboard": RULES, "project": (LEAD,)}, owner="olga"),
    # Nested under "open", locked to itself only.
    "team": Project("team", "Team", "locked_not_nested", {"dashboard": RULES}, parent="open"),
    # Top level, locked including nested projects.
    "vault": Project("vault", "Vault", "locked", {"dashboard": RULES, "project": (LEAD_GROUP,)}),
    "inner": Project("inner", "Inner", "customizable", {"dashboard": ()}, parent="vault"),
}
OWN = parse_rules([{"group": "B", "capabilities": {"view": "allow"}}])


def dash(project: str, rules: tuple[Rule, ...] = RULES, owner: str | None = "ola") -> ContentEntry:
    return ContentEntry("d", project, owner, rules)


CASES = [
    # (principal, capability, resource, expected, reason fragment)
    (who("x", "A"), C.VIEW, dash("open"), True, "group 'A' allowed"),
    (who("x", "A", "B"), C.VIEW, dash("open"), False, "group 'B' denied"),  # deny wins among groups
    (who("uma", "B"), C.VIEW, dash("open"), True, "user rule (allow)"),  # user rule beats groups
    (who("dan", "A"), C.VIEW, dash("open"), False, "user rule (deny)"),
    (who("x", "C"), C.VIEW, dash("open"), False, "unspecified"),
    # Site role first: rules and ownership cannot exceed it.
    (who("x", "A", role=R.VIEWER), C.DOWNLOAD_FULL, dash("open"), False, "site role viewer"),
    (who("x", "A", role=R.EXPLORER), C.DOWNLOAD_FULL, dash("open"), True, "group 'A' allowed"),
    (who("x", "A", role=R.EXPLORER), C.OVERWRITE, dash("open"), False, "site role explorer"),
    (who("x", "A", role=R.EXPLORER_CAN_PUBLISH), C.OVERWRITE, dash("open"), True, "group 'A'"),
    (who("ola", role=R.EXPLORER), C.SET_PERMISSIONS, dash("open"), False, "site role explorer"),
    (who("ola", role=R.EXPLORER), C.WEB_EDIT, dash("open"), True, "content owner"),
    (who("u", role=R.UNLICENSED), C.VIEW, dash("open"), False, "site role unlicensed"),
    (who("root", role=R.SITE_ADMINISTRATOR), C.DELETE, dash("vault"), True, "site administrator"),
    # Owners and leaders.
    (who("ola", "B"), C.VIEW, dash("open"), True, "content owner"),
    (who("ola"), C.SET_PERMISSIONS, dash("open"), True, "content owner"),
    (
        who("ola"),
        C.SET_PERMISSIONS,
        dash("vault"),
        False,
        "locked project 'Vault'",
    ),  # locked: no Set Permissions
    (who("ola"), C.DELETE, dash("vault"), True, "content owner"),
    (who("olga", "B"), C.DELETE, dash("team"), True, "owner of project 'Open'"),  # applies downward
    (who("lena", "B"), C.SET_PERMISSIONS, dash("team"), True, "leader of project 'Open'"),
    (who("y", "Leads", "B"), C.VIEW, dash("inner"), True, "leader of project 'Vault'"),
    (who("lena", role=R.VIEWER), C.DELETE, dash("open"), False, "site role viewer"),
    # Locks.
    (who("x", "B"), C.VIEW, dash("open", OWN), True, "its own rules"),  # customizable: own rules
    (who("x", "B"), C.VIEW, dash("team", OWN), False, "locked project 'Team'"),
    (who("x", "B"), C.VIEW, dash("inner", OWN), False, "locked project 'Vault'"),  # locked incl. nested
    (who("x", "A"), C.VIEW, dash("inner", ()), True, "locked project 'Vault'"),
    # Capabilities that do not exist for the resource kind.
    (who("x", "A"), C.CONNECT, dash("open"), False, "not a capability"),
]


@pytest.mark.parametrize(("principal", "capability", "entry", "expected", "reason"), CASES)
def test_effective_permissions(
    principal: Principal, capability: Capability, entry: ContentEntry, expected: bool, reason: str
) -> None:
    verdict = evaluate(principal, capability, entry, PROJECTS)
    assert verdict.allowed is expected, verdict.reason
    assert reason in verdict.reason


def test_projects_themselves() -> None:
    rules = parse_rules([{"group": "A", "capabilities": {"view": "allow", "publish": "allow"}}])
    top = Project("top", "Top", "locked", {"project": rules})
    child = Project("child", "Child", "customizable", {"project": ()}, parent="top")
    projects = {"top": top, "child": child}
    assert evaluate(who("x", "A"), C.PUBLISH, top, projects).allowed
    assert not evaluate(who("x", "A", role=R.EXPLORER), C.PUBLISH, top, projects).allowed
    # The child's own (empty) rules are replaced by the parent's: locked including nested.
    assert evaluate(who("x", "A"), C.VIEW, child, projects).allowed
    loose = {"top": Project("top", "Top", "locked_not_nested", {"project": rules}), "child": child}
    assert not evaluate(who("x", "A"), C.VIEW, child, loose).allowed


def test_models_use_explorer_for_connect() -> None:
    rules = parse_rules([{"group": "A", "capabilities": {"view": "allow", "connect": "allow"}}])
    model = ContentEntry("m", None, None, rules, "model")
    assert evaluate(who("x", "A", role=R.EXPLORER), C.CONNECT, model, {}).allowed
    # TabFree deviation (ADR-006): Connect is ad-hoc querying, so Viewers never get it.
    assert not evaluate(who("x", "A", role=R.VIEWER), C.CONNECT, model, {}).allowed
    assert evaluate(who("x", "A", role=R.VIEWER), C.VIEW, model, {}).allowed


def test_ceilings_follow_tableau_site_role_maximums() -> None:
    viewer, explorer = CEILINGS[R.VIEWER]["dashboard"], CEILINGS[R.EXPLORER]["dashboard"]
    assert C.DOWNLOAD_SUMMARY in viewer
    assert C.DOWNLOAD_FULL not in viewer
    assert C.WEB_EDIT not in viewer
    assert {C.WEB_EDIT, C.DOWNLOAD_FULL, C.SHARE_CUSTOMIZED} <= explorer
    assert not explorer & {C.OVERWRITE, C.DELETE, C.SET_PERMISSIONS, C.MOVE}
    for role in (R.EXPLORER_CAN_PUBLISH, R.CREATOR, R.SITE_ADMINISTRATOR):
        assert CEILINGS[role]["dashboard"] == APPLICABLE["dashboard"]
    assert C.PUBLISH not in CEILINGS[R.EXPLORER]["project"]
    assert C.PUBLISH in CEILINGS[R.EXPLORER_CAN_PUBLISH]["project"]
    assert max_role(R.VIEWER, R.CREATOR, R.EXPLORER) is R.CREATOR


def test_capability_summary_for_the_ui() -> None:
    caps = capabilities(who("x", "A", role=R.EXPLORER), dash("open"), PROJECTS)
    assert caps["view"] is True
    assert caps["download_full_data"] is True
    assert caps["overwrite"] is False
    assert set(caps) == {str(c) for c in APPLICABLE["dashboard"]}


def test_cycles_in_parents_do_not_loop() -> None:
    loop = {
        "a": Project("a", "A", "customizable", {}, parent="b"),
        "b": Project("b", "B", "customizable", {}, parent="a"),
    }
    assert not evaluate(who("x"), C.VIEW, dash("a", ()), loop).allowed
