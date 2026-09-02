from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class EmailAttachment:
    filename: str
    content_type: str
    size_bytes: int
    local_path: Path


@dataclass(slots=True)
class EmailMessage:
    message_id: str
    subject: str
    sender: str
    received_at: datetime
    body_text: str = ""
    mailbox: str = ""
    attachments: list[EmailAttachment] = field(default_factory=list)


@runtime_checkable
class MailboxClient(Protocol):
    """One interface over Microsoft Graph, Gmail, and the local demo folder.

    Attachments are written to disk before the workflow starts so a run can be replayed
    from the audit trail without re-fetching from the mail provider.
    """

    async def fetch_messages(self, limit: int = 25) -> list[EmailMessage]: ...

    async def mark_processed(self, message_id: str) -> None: ...
