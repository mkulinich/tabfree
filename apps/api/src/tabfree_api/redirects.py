"""Old Tableau URLs keep working (BUILD_PLAN P3.9).

Tableau Server links look like ``/#/site/<site>/views/<workbook>/<sheet>`` (the portal),
``/#/views/<workbook>/<sheet>`` (the default site), ``/t/<site>/views/<workbook>/<sheet>`` and
``/views/<workbook>/<sheet>`` (embedding and share links), often with ``?:iid=1``-style options. The
migration records each view's path against the dashboard that replaces it; these routes send people
there. A link to a dashboard the viewer may not see answers 404, exactly like an unknown link.

The ``#`` forms never reach a server (browsers keep the fragment), so the portal resolves them with
``GET /api/redirects/resolve``.
"""

from __future__ import annotations

import re
import urllib.parse

_FORMS = (
    re.compile(r"^/?#?/?site/[^/]+/(views/.+)$"),
    re.compile(r"^/?#?/?t/[^/]+/(views/.+)$"),
    re.compile(r"^/?#?/?(views/.+)$"),
)


def normalise(link: str) -> str | None:
    """``views/<workbook>/<sheet>`` from any Tableau view URL or path; None when it is not one."""
    if "://" in link:
        parts = urllib.parse.urlsplit(link)
        link = parts.path + ("#" + parts.fragment if parts.fragment else "")
    link = link.strip()
    if link.startswith("/#") or link.startswith("#"):
        link = link.split("#", 1)[1]
    link = link.split("?", 1)[0].rstrip("/")
    for form in _FORMS:
        match = form.match(link)
        if match:
            segments = [urllib.parse.unquote(s) for s in match.group(1).split("/")]
            if len(segments) < 3 or any(not s or s in (".", "..") for s in segments):
                return None
            return "/".join(segments[:3])  # views/<workbook>/<sheet>; anything after is a custom view
    return None
