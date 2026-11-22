"""Outgoing email (subscriptions, alerts, job failures): Amazon SES, or the log in development."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Protocol

log = logging.getLogger("tabfree.mail")


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class Message:
    to: Sequence[str]
    subject: str
    text: str
    html: str | None = None
    attachments: Sequence[Attachment] = ()
    #: Extra headers, e.g. List-Unsubscribe.
    headers: Mapping[str, str] = field(default_factory=dict)


class Mailer(Protocol):
    def send(self, message: Message) -> str:
        """Send; returns the provider's message id."""
        ...


def mime(message: Message, sender: str) -> EmailMessage:
    mail = EmailMessage()
    mail["From"] = sender
    mail["To"] = ", ".join(message.to)
    mail["Subject"] = message.subject.replace("\r", " ").replace("\n", " ")[:250]
    for key, value in message.headers.items():
        mail[key] = value.replace("\r", " ").replace("\n", " ")
    mail.set_content(message.text)
    if message.html:
        mail.add_alternative(message.html, subtype="html")
    for attachment in message.attachments:
        maintype, _, subtype = attachment.content_type.partition("/")
        mail.add_attachment(attachment.data, maintype=maintype, subtype=subtype, filename=attachment.filename)
    return mail


class LogMailer:
    """Development and tests: logs a line per message (never the attachments) and keeps them."""

    def __init__(self, sender: str = "tabfree@localhost") -> None:
        self.sender = sender
        self.sent: list[Message] = []

    def send(self, message: Message) -> str:
        self.sent.append(message)
        log.info(
            "mail to=%d subject=%r attachments=%d", len(message.to), message.subject, len(message.attachments)
        )
        return f"log-{len(self.sent)}"


class SesMailer:
    """Amazon SES (v2 API), with the task role's credentials."""

    def __init__(self, sender: str, region: str, client: Any = None) -> None:
        self.sender = sender
        if client is None:
            import boto3  # the ``tabfree-api[aws]`` extra

            client = boto3.client("sesv2", region_name=region)
        self.client = client

    def send(self, message: Message) -> str:
        raw = mime(message, self.sender).as_bytes()
        response = self.client.send_email(
            FromEmailAddress=self.sender,
            Destination={"ToAddresses": list(message.to)},
            Content={"Raw": {"Data": raw}},
        )
        return str(response.get("MessageId", ""))


def from_settings(settings: Any) -> Mailer:
    if settings.mail_backend == "ses":
        if not settings.ses_region:
            raise RuntimeError("TABFREE_SES_REGION is required with TABFREE_MAIL_BACKEND=ses")
        return SesMailer(settings.mail_from, settings.ses_region)
    return LogMailer(settings.mail_from)
