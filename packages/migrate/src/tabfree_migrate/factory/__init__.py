"""The migration factory (BUILD_PLAN Phase 5): the whole Tableau estate to TabFree, in waves.

Everything here is deterministic and offline. It reads what the read-only harvest wrote (inventory,
repository usage, workbook files, ground truth, converted extracts) and writes a factory folder:

- ``models/``: one semantic model per data source, with connections remapped to Athena or Redshift
  (:mod:`.remap`, :mod:`.models`), and ``decisions.json`` for sources that need a person's choice;
- ``dashboards/<id>/``: a bundle per Tableau dashboard (``tabfree.json`` from :mod:`.manifest`,
  ``index.html`` from :mod:`.baseline`) and its ``report.json`` (fidelity, :mod:`.fidelity`);
- ``site.yaml`` and ``governance.json``: users, groups, projects, owners and permissions in the
  site-file format the API imports, plus favorites, subscriptions, alerts and tags (:mod:`.governance`);
- ``waves.json``: which dashboard goes in which wave (:mod:`.waves`).

The API imports the folder (``python -m tabfree_api.migration import``), runs the Claude repair agent
and the verification reports, and shows the migration console.
"""
