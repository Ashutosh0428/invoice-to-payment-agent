from __future__ import annotations

from invoice_agent.core.config import MailboxConfig, get_config
from invoice_agent.core.errors import ConfigurationError
from invoice_agent.ingestion.mailbox.base import MailboxClient


def get_mailbox_client(config: MailboxConfig | None = None) -> MailboxClient:
    cfg = config or get_config().mailbox

    if cfg.provider == "local":
        from invoice_agent.ingestion.mailbox.local import LocalFolderMailbox

        return LocalFolderMailbox(cfg)

    if cfg.provider == "graph":
        from invoice_agent.ingestion.mailbox.graph import GraphMailbox

        return GraphMailbox(cfg)

    if cfg.provider == "gmail":
        from invoice_agent.ingestion.mailbox.gmail import GmailMailbox

        return GmailMailbox(cfg)

    raise ConfigurationError(f"unknown mailbox provider '{cfg.provider}'")
