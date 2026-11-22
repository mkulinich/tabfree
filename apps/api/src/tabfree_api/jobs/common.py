"""What jobs share: acting as a user, signed links, recipients."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from tabfree_api.auth import SessionInfo
from tabfree_api.authz import Capability


class NoSuchUser(LookupError):  # noqa: N818 - reads as a sentence
    pass


def session_info(site: Any, username: str, *, ttl_s: float = 900) -> SessionInfo:
    """A short-lived identity for a user, so jobs run with exactly their permissions and RLS."""
    user = site.users.get(username)
    if user is None:
        raise NoSuchUser(username)
    return SessionInfo(
        user.username,
        user.fullname,
        frozenset(user.groups),
        user.site_role,
        time.time() + ttl_s,
        email=user.email,
        attributes=dict(user.attributes),
    )


def can_view(site: Any, info: SessionInfo, dashboard: str) -> bool:
    return bool(site.can(info.principal(), Capability.VIEW, dashboard))


def link_key(settings: Any) -> bytes:
    if settings.link_signing_key is not None:
        return bytes(settings.link_signing_key.get_secret_value().encode())
    seed = settings.database_url.get_secret_value() if settings.database_url is not None else "tabfree-dev"
    return hashlib.sha256(f"tabfree-links:{seed}".encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def sign(settings: Any, *parts: str) -> str:
    """A token carrying ``parts`` with an HMAC; the parts are not secret, only unforgeable."""
    body = "\x1f".join(parts)
    mac = hmac.new(link_key(settings), body.encode(), hashlib.sha256).digest()[:16]
    return f"{_b64(body.encode())}.{_b64(mac)}"


def verify(settings: Any, token: str) -> list[str] | None:
    try:
        body_b64, mac_b64 = token.split(".", 1)
        body = base64.urlsafe_b64decode(body_b64 + "=" * (-len(body_b64) % 4))
        mac = base64.urlsafe_b64decode(mac_b64 + "=" * (-len(mac_b64) % 4))
    except (ValueError, TypeError):
        return None
    expected = hmac.new(link_key(settings), body, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(mac, expected):
        return None
    return body.decode().split("\x1f")


@dataclass(frozen=True)
class Recipient:
    #: The TabFree user, when the recipient is one (rendered with their access).
    username: str | None
    address: str


def is_internal(settings: Any, address: str) -> bool:
    domains = [d.lower().lstrip("@") for d in settings.mail_internal_domains]
    return not domains or address.rsplit("@", 1)[-1].lower() in domains


def expand(site: Any, recipients: Iterable[Mapping[str, Any]]) -> tuple[list[Recipient], list[str]]:
    """Users, group members and addresses → recipients (deduplicated), and problems to report."""
    out: dict[str, Recipient] = {}
    problems: list[str] = []
    by_email = {u.email.lower(): u.username for u in site.users.values() if u.email}
    for entry in recipients:
        if "user" in entry:
            user = site.users.get(str(entry["user"]))
            if user is None or not user.email:
                problems.append(f"user {entry['user']!r} has no email address")
                continue
            out.setdefault(user.email.lower(), Recipient(user.username, user.email))
        elif "group" in entry:
            members = [u for u in site.users.values() if str(entry["group"]) in u.groups]
            for user in members:
                if user.email:
                    out.setdefault(user.email.lower(), Recipient(user.username, user.email))
        elif "email" in entry:
            address = str(entry["email"]).strip()
            if "@" not in address or any(c in address for c in "\r\n,;<> "):
                problems.append(f"invalid address {address!r}")
                continue
            out.setdefault(address.lower(), Recipient(by_email.get(address.lower()), address))
    return list(out.values()), problems
