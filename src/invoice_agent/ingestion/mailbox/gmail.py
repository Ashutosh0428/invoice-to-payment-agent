from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from invoice_agent.core.config import MailboxConfig, get_config
from invoice_agent.core.errors import MailboxError
from invoice_agent.ingestion.mailbox.base import EmailAttachment, EmailMessage

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class GmailMailbox:
    """Gmail API via an OAuth installed-app flow.

    The token file is written on first authorisation and reused; the container must therefore
    mount `credentials/` as a volume or the agent re-prompts on every restart.
    """

    def __init__(self, config: MailboxConfig | None = None, download_dir: Path | None = None):
        self.config = config or get_config().mailbox
        self.download_dir = download_dir or get_config().document_store / "gmail"
        self._service: Any = None

    def _build_service(self) -> Any:
        if self._service is not None:
            return self._service
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise MailboxError(
                "google-api-python-client is required for the Gmail provider"
            ) from exc

        creds = None
        token_path = Path(self.config.gmail_token_file)
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        if creds is None or not creds.valid:
            if creds is not None and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                credentials_path = Path(self.config.gmail_credentials_file)
                if not credentials_path.exists():
                    raise MailboxError(f"Gmail credentials file missing at {credentials_path}")
                flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
                creds = flow.run_local_server(port=0)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json())

        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    async def fetch_messages(self, limit: int = 25) -> list[EmailMessage]:
        service = self._build_service()
        listing = (
            service.users()
            .messages()
            .list(userId="me", q=self.config.gmail_query, maxResults=limit)
            .execute()
        )

        messages: list[EmailMessage] = []
        for stub in listing.get("messages", []):
            detail = (
                service.users().messages().get(userId="me", id=stub["id"], format="full").execute()
            )
            headers = {
                h["name"].lower(): h["value"] for h in detail.get("payload", {}).get("headers", [])
            }
            attachments = self._download_attachments(service, detail)
            if not attachments:
                continue
            messages.append(
                EmailMessage(
                    message_id=stub["id"],
                    subject=headers.get("subject", ""),
                    sender=headers.get("from", "unknown"),
                    received_at=datetime.fromtimestamp(
                        int(detail.get("internalDate", "0")) / 1000, tz=UTC
                    ),
                    body_text=detail.get("snippet", ""),
                    mailbox="me",
                    attachments=attachments,
                )
            )
        return messages

    def _download_attachments(self, service: Any, detail: dict[str, Any]) -> list[EmailAttachment]:
        target_dir = self.download_dir / detail["id"]
        results: list[EmailAttachment] = []

        def walk(parts: list[dict[str, Any]]) -> None:
            for part in parts:
                if part.get("parts"):
                    walk(part["parts"])
                body = part.get("body", {})
                if not part.get("filename") or "attachmentId" not in body:
                    continue
                blob = (
                    service.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=detail["id"], id=body["attachmentId"])
                    .execute()
                )
                content = base64.urlsafe_b64decode(blob["data"])
                target_dir.mkdir(parents=True, exist_ok=True)
                path = target_dir / part["filename"]
                path.write_bytes(content)
                results.append(
                    EmailAttachment(
                        filename=part["filename"],
                        content_type=part.get("mimeType", "application/octet-stream"),
                        size_bytes=len(content),
                        local_path=path,
                    )
                )

        walk(detail.get("payload", {}).get("parts", []))
        return results

    async def mark_processed(self, message_id: str) -> None:
        service = self._build_service()
        try:
            service.users().messages().modify(
                userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
            ).execute()
        except Exception as exc:
            logger.warning("Could not flag Gmail message {} as read: {}", message_id, exc)
