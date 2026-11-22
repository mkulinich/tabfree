"""The migration factory inside the server (BUILD_PLAN Phase 5).

``tabfree-migrate factory`` writes a factory folder offline. This package takes it from there:

- :mod:`.importer` loads it into a site: governance (users, groups, projects, rules, models) and one
  migration item per Tableau dashboard, holding the converted bundle as a draft;
- :mod:`.repair` runs the Claude repair agent on an item's draft: validate → render → compare with
  Tableau's PNG → fix, up to N iterations;
- :mod:`.verify` produces the verification reports: access, RLS, data and visual parity;
- :mod:`.fidelity` scores an item;
- :mod:`.routes` is the migration console's API (site administrators only), and ``python -m
  tabfree_api.migration`` the command line for batch runs.
"""
