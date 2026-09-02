from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from invoice_agent.core.config import MailboxConfig, get_config
from invoice_agent.core.errors import MailboxError
from invoice_agent.ingestion.mailbox.base import EmailAttachment, EmailMessage

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
AUTHORITY = "https://login.microsoftonline.com"
SCOPE = "https://graph.microsoft.com/.default"


class GraphMailbox:
    """Microsoft 365 shared mailbox via Graph, client-credentials flow.

    Requires the application permission Mail.ReadWrite with admin consent; delegated
    permissions cannot poll a shared mailbox unattended.
    """

    def __init__(self, config: MailboxConfig | None = None, download_dir: Path | None = None):
        self.config = config or get_config().mailbox
        self.download_dir = download_dir or get_config().document_store / "graph"
        self._token: str | None = None

    def _acquire_token(self) -> str:
        if self._token:
            return self._token
        try:
            import msal
        except ImportError as exc:
            raise MailboxError("msal is required for the Graph mailbox provider") from exc

        if not (self.config.graph_tenant_id and self.config.graph_client_id):
            raise MailboxError("Graph mailbox requires tenant id and client id")

        app = msal.ConfidentialClientApplication(
            client_id=self.config.graph_client_id,
            authority=f"{AUTHORITY}/{self.config.graph_tenant_id}",
            client_credential=self.config.graph_client_secret.get_secret_value(),
        )
        result = app.acquire_token_for_client(scopes=[SCOPE])
        if "access_token" not in result:
            raise MailboxError(
                "Graph token acquisition failed",
                details={"error": result.get("error_description", "unknown")},
            )
        self._token = str(result["access_token"])
        return self._token

    @property
    def _mailbox_path(self) -> str:
        user = self.config.graph_user_principal_name
        if not user:
            raise MailboxError("Graph mailbox requires a user principal name")
        return f"/users/{user}"

    async def fetch_messages(self, limit: int = 25) -> list[EmailMessage]:
        token = self._acquire_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = (
            f"{GRAPH_ROOT}{self._mailbox_path}/mailFolders/{self.config.ap_folder}/messages"
            f"?$filter=hasAttachments eq true and isRead eq false&$top={limit}"
            "&$select=id,subject,from,receivedDateTime,bodyPreview"
        )

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=headers)
            if not response.is_success:
                raise MailboxError(
                    f"Graph list messages failed with {response.status_code}",
                    details={"body": response.text[:500]},
                )
            payload = response.json().get("value", [])
            messages = []
            for item in payload:
                attachments = await self._download_attachments(client, headers, item["id"])
                if not attachments:
                    continue
                messages.append(
                    EmailMessage(
                        message_id=item["id"],
                        subject=item.get("subject", ""),
                        sender=item.get("from", {})
                        .get("emailAddress", {})
                        .get("address", "unknown"),
                        received_at=datetime.fromisoformat(
                            item["receivedDateTime"].replace("Z", "+00:00")
                        ),
                        body_text=item.get("bodyPreview", ""),
                        mailbox=self.config.graph_user_principal_name,
                        attachments=attachments,
                    )
                )
        return messages

    async def _download_attachments(
        self, client: httpx.AsyncClient, headers: dict[str, str], message_id: str
    ) -> list[EmailAttachment]:
        url = f"{GRAPH_ROOT}{self._mailbox_path}/messages/{message_id}/attachments"
        response = await client.get(url, headers=headers)
        if not response.is_success:
            logger.warning(
                "Could not list attachments for {}: {}", message_id, response.status_code
            )
            return []

        target_dir = self.download_dir / message_id
        target_dir.mkdir(parents=True, exist_ok=True)
        results: list[EmailAttachment] = []

        for item in response.json().get("value", []):
            if item.get("@odata.type") != "#microsoft.graph.fileAttachment":
                continue
            content = base64.b64decode(item["contentBytes"])
            path = target_dir / item["name"]
            path.write_bytes(content)
            results.append(
                EmailAttachment(
                    filename=item["name"],
                    content_type=item.get("contentType", "application/octet-stream"),
                    size_bytes=len(content),
                    local_path=path,
                )
            )
        return results

    async def mark_processed(self, message_id: str) -> None:
        token = self._acquire_token()
        url = f"{GRAPH_ROOT}{self._mailbox_path}/messages/{message_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"isRead": True},
            )
            if not response.is_success:
                logger.warning("Could not flag {} as read: {}", message_id, response.status_code)

    def describe(self) -> dict[str, Any]:
        return {"provider": "graph", "mailbox": self.config.graph_user_principal_name}
