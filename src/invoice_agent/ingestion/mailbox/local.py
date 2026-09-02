from __future__ import annotations

import hashlib
import mimetypes
import shutil
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from invoice_agent.core.config import MailboxConfig, get_config
from invoice_agent.ingestion.mailbox.base import EmailAttachment, EmailMessage
from invoice_agent.ingestion.parser import SUPPORTED_SUFFIXES


class LocalFolderMailbox:
    """Filesystem mailbox for demos, tests, and CI.

    Each file in the root is one message; a sidecar `<name>.meta.json` may supply sender and
    subject. Processed files move to `processed/` so a re-run does not reprocess them.
    """

    def __init__(self, config: MailboxConfig | None = None) -> None:
        self.config = config or get_config().mailbox
        self.root = Path(self.config.local_root)
        self.processed_dir = self.root / "processed"

    async def fetch_messages(self, limit: int = 25) -> list[EmailMessage]:
        if not self.root.exists():
            logger.warning("Local mailbox root {} does not exist", self.root)
            return []

        self.processed_dir.mkdir(parents=True, exist_ok=True)
        messages: list[EmailMessage] = []

        for path in sorted(self.root.iterdir()):
            if len(messages) >= limit:
                break
            if path.is_dir() or path.name.startswith(".") or path.suffix == ".json":
                continue
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue

            meta = self._read_sidecar(path)
            attachment = EmailAttachment(
                filename=path.name,
                content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                size_bytes=path.stat().st_size,
                local_path=path,
            )
            messages.append(
                EmailMessage(
                    message_id=meta.get("message_id")
                    or hashlib.sha256(path.name.encode()).hexdigest()[:24],
                    subject=meta.get("subject", f"Invoice: {path.stem}"),
                    sender=meta.get("sender", "ap-inbox@example.com"),
                    received_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
                    body_text=meta.get("body", ""),
                    mailbox=str(self.root),
                    attachments=[attachment],
                )
            )

        return messages

    async def mark_processed(self, message_id: str) -> None:
        for path in self.root.iterdir():
            if path.is_dir():
                continue
            digest = hashlib.sha256(path.name.encode()).hexdigest()[:24]
            meta = self._read_sidecar(path)
            if message_id in (digest, meta.get("message_id")):
                self.processed_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(self.processed_dir / path.name))
                return

    @staticmethod
    def _read_sidecar(path: Path) -> dict[str, str]:
        import json

        sidecar = path.with_suffix(path.suffix + ".meta.json")
        if not sidecar.exists():
            sidecar = path.with_suffix(".meta.json")
        if sidecar.exists():
            try:
                return json.loads(sidecar.read_text())
            except json.JSONDecodeError:
                logger.warning("Ignoring malformed sidecar {}", sidecar)
        return {}
