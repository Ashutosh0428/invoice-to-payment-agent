from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.types import Command
from loguru import logger

from invoice_agent.agents.graph import get_ap_graph, get_ar_graph
from invoice_agent.core.config import get_config
from invoice_agent.core.errors import NotFoundError, ValidationError
from invoice_agent.db.models import WorkflowRun
from invoice_agent.db.repository import WorkflowRepository
from invoice_agent.db.session import get_session_factory
from invoice_agent.ingestion.mailbox import get_mailbox_client
from invoice_agent.ingestion.parser import sha256_file
from invoice_agent.schemas.common import DocumentSource, RunStatus, WorkflowKind


def _thread_id(run_id: uuid.UUID) -> str:
    return f"run-{run_id}"


def _config(run_id: uuid.UUID) -> dict[str, Any]:
    return {"configurable": {"thread_id": _thread_id(run_id)}, "recursion_limit": 40}


def _interrupt_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    raw = result.get("__interrupt__")
    if not raw:
        return None
    first = raw[0] if isinstance(raw, list | tuple) else raw
    value = getattr(first, "value", first)
    return value if isinstance(value, dict) else {"detail": str(value)}


def _store_document(source: Path, run_id: uuid.UUID) -> Path:
    target_dir = get_config().document_store / str(run_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    return target


class WorkflowService:
    """Owns the run lifecycle: create the row, drive the graph, expose the result.

    API handlers never touch LangGraph directly, so the same path serves the REST endpoints
    and the mailbox poller.
    """

    async def _repo(self) -> tuple[Any, WorkflowRepository]:
        session = get_session_factory()()
        return session, WorkflowRepository(session)

    async def start_run(
        self,
        document_path: str | Path,
        *,
        kind: WorkflowKind = WorkflowKind.ACCOUNTS_PAYABLE,
        source: DocumentSource = DocumentSource.API_UPLOAD,
        message_id: str | None = None,
        sender: str | None = None,
        subject: str | None = None,
        mailbox: str | None = None,
        received_at: datetime | None = None,
    ) -> dict[str, Any]:
        path = Path(document_path)
        if not path.exists():
            raise ValidationError(f"document not found: {path}")

        run_id = uuid.uuid4()
        stored = _store_document(path, run_id)
        digest = sha256_file(stored)

        session, repo = await self._repo()
        async with session:
            run = await repo.create_run(
                WorkflowRun(
                    id=run_id,
                    thread_id=_thread_id(run_id),
                    kind=kind,
                    status=RunStatus.RECEIVED,
                    source=source,
                    source_message_id=message_id,
                    source_mailbox=mailbox,
                    source_sender=sender,
                    source_subject=subject,
                    source_received_at=received_at or datetime.now(UTC),
                    attachment_name=stored.name,
                    document_path=str(stored),
                    document_sha256=digest,
                )
            )
            from invoice_agent.schemas.common import AuditAction

            await repo.append_audit(
                run.id,
                AuditAction.EMAIL_RECEIVED,
                f"Received {stored.name} from {sender or source.value}"
                + (f" (subject: {subject})" if subject else ""),
                node="ingest",
                payload={
                    "source": source.value,
                    "message_id": message_id,
                    "mailbox": mailbox,
                    "filename": stored.name,
                    "size_bytes": stored.stat().st_size,
                },
                source_message_id=message_id,
                document_sha256=digest,
            )

        initial: dict[str, Any] = {
            "run_id": str(run_id),
            "thread_id": _thread_id(run_id),
            "kind": kind,
            "status": RunStatus.RECEIVED,
            "source_message_id": message_id or "",
            "source_sender": sender or "",
            "source_subject": subject or "",
            "mailbox": mailbox or "",
            "document_path": str(stored),
            "document_sha256": digest,
            "attachment_name": stored.name,
            "decisions": [],
        }

        graph = await (
            get_ar_graph() if kind == WorkflowKind.ACCOUNTS_RECEIVABLE else get_ap_graph()
        )
        result = await graph.ainvoke(initial, _config(run_id))
        return await self._summarise(run_id, result)

    async def resume_run(
        self, run_id: uuid.UUID, decision: str, approved_by: str, note: str | None = None
    ) -> dict[str, Any]:
        if decision not in ("approve", "reject"):
            raise ValidationError("decision must be 'approve' or 'reject'")

        session, repo = await self._repo()
        async with session:
            run = await repo.get_run(run_id)
        if run is None:
            raise NotFoundError(f"run {run_id} not found")
        if run.status not in (RunStatus.AWAITING_APPROVAL, RunStatus.MATCHING):
            raise ValidationError(f"run {run_id} is {run.status.value}, not awaiting approval")

        graph = await (
            get_ar_graph() if run.kind == WorkflowKind.ACCOUNTS_RECEIVABLE else get_ap_graph()
        )
        result = await graph.ainvoke(
            Command(resume={"decision": decision, "approved_by": approved_by, "note": note}),
            _config(run_id),
        )
        return await self._summarise(run_id, result)

    async def poll_mailbox(
        self, kind: WorkflowKind = WorkflowKind.ACCOUNTS_PAYABLE, limit: int | None = None
    ) -> list[dict[str, Any]]:
        cfg = get_config().mailbox
        client = get_mailbox_client()
        messages = await client.fetch_messages(limit or cfg.max_messages_per_poll)
        summaries: list[dict[str, Any]] = []

        for message in messages:
            for attachment in message.attachments:
                try:
                    summary = await self.start_run(
                        attachment.local_path,
                        kind=kind,
                        source=DocumentSource.EMAIL_ATTACHMENT,
                        message_id=message.message_id,
                        sender=message.sender,
                        subject=message.subject,
                        mailbox=message.mailbox,
                        received_at=message.received_at,
                    )
                    summaries.append(summary)
                except Exception as exc:
                    logger.exception(
                        "Run failed for {} in message {}: {}",
                        attachment.filename,
                        message.message_id,
                        exc,
                    )
            await client.mark_processed(message.message_id)

        return summaries

    async def get_run_summary(self, run_id: uuid.UUID) -> dict[str, Any]:
        return await self._summarise(run_id, {})

    async def _summarise(self, run_id: uuid.UUID, result: dict[str, Any]) -> dict[str, Any]:
        session, repo = await self._repo()
        async with session:
            run = await repo.get_run(run_id)
            if run is None:
                raise NotFoundError(f"run {run_id} not found")
            match = await repo.get_match_for_run(run_id)
            journal = await repo.get_journal_for_run(run_id)
            open_cases = [
                case for case in await repo.list_exceptions(limit=200) if case.run_id == run_id
            ]

        pending = _interrupt_payload(result)
        return {
            "run_id": str(run_id),
            "thread_id": run.thread_id,
            "kind": run.kind.value,
            "status": run.status.value,
            "straight_through": run.straight_through,
            "source_message_id": run.source_message_id,
            "attachment_name": run.attachment_name,
            "document_sha256": run.document_sha256,
            "error": run.error_message,
            "match": {
                "match_type": match.match_type.value,
                "outcome": match.outcome.value,
                "po_number": match.po_number,
                "gr_numbers": match.gr_numbers,
                "matched_amount": str(match.matched_amount),
                "exceptions": match.exceptions,
                "reasons": match.reasons,
            }
            if match
            else None,
            "journal": {
                "status": journal.status.value,
                "erp_document_number": journal.erp_document_number,
                "total_amount": str(journal.total_amount),
                "currency": journal.currency,
                "reference": journal.reference,
                "approved_by": journal.approved_by,
            }
            if journal
            else None,
            "exceptions": [
                {
                    "id": str(case.id),
                    "type": case.exception_type.value,
                    "status": case.status.value,
                    "severity": case.severity,
                    "summary": case.summary,
                    "suggested_action": case.suggested_action,
                }
                for case in open_cases
            ],
            "awaiting_approval": pending,
            "decisions": result.get("decisions", []),
        }


_service: WorkflowService | None = None


def get_workflow_service() -> WorkflowService:
    global _service
    if _service is None:
        _service = WorkflowService()
    return _service
