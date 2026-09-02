from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from invoice_agent.db.models import (
    AuditEvent,
    ExceptionCase,
    Invoice,
    MatchResult,
    PaymentJournal,
    Remittance,
    WorkflowRun,
)
from invoice_agent.schemas.common import AuditAction, ExceptionStatus, RunStatus


class WorkflowRepository:
    """Every persistence path the agent needs, in one place, so nodes never build queries."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_run(self, run: WorkflowRun) -> WorkflowRun:
        self.session.add(run)
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def get_run(self, run_id: uuid.UUID) -> WorkflowRun | None:
        return await self.session.get(WorkflowRun, run_id)

    async def get_run_by_thread(self, thread_id: str) -> WorkflowRun | None:
        result = await self.session.execute(
            select(WorkflowRun).where(WorkflowRun.thread_id == thread_id)
        )
        return result.scalars().first()

    async def update_run(self, run_id: uuid.UUID, **fields: Any) -> WorkflowRun | None:
        run = await self.session.get(WorkflowRun, run_id)
        if run is None:
            return None
        for key, value in fields.items():
            setattr(run, key, value)
        run.updated_at = datetime.now(UTC)
        if fields.get("status") in (RunStatus.COMPLETED, RunStatus.REJECTED, RunStatus.FAILED):
            run.completed_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def save_invoice(self, invoice: Invoice) -> Invoice:
        self.session.add(invoice)
        await self.session.commit()
        await self.session.refresh(invoice)
        return invoice

    async def find_duplicate(
        self, fingerprint: str, exclude_run_id: uuid.UUID | None = None
    ) -> Invoice | None:
        stmt = select(Invoice).where(Invoice.fingerprint == fingerprint)
        if exclude_run_id is not None:
            stmt = stmt.where(Invoice.run_id != exclude_run_id)
        result = await self.session.execute(stmt.order_by(Invoice.created_at).limit(1))
        return result.scalars().first()

    async def save_match(self, match: MatchResult) -> MatchResult:
        self.session.add(match)
        await self.session.commit()
        await self.session.refresh(match)
        return match

    async def get_match_for_run(self, run_id: uuid.UUID) -> MatchResult | None:
        result = await self.session.execute(
            select(MatchResult)
            .where(MatchResult.run_id == run_id)
            .order_by(MatchResult.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def save_exception(self, case: ExceptionCase) -> ExceptionCase:
        self.session.add(case)
        await self.session.commit()
        await self.session.refresh(case)
        return case

    async def list_exceptions(
        self,
        status: ExceptionStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ExceptionCase]:
        stmt = select(ExceptionCase)
        if status is not None:
            stmt = stmt.where(ExceptionCase.status == status)
        stmt = stmt.order_by(ExceptionCase.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_exception(self, case_id: uuid.UUID) -> ExceptionCase | None:
        return await self.session.get(ExceptionCase, case_id)

    async def resolve_exception(
        self,
        case_id: uuid.UUID,
        status: ExceptionStatus,
        resolved_by: str,
        note: str | None,
    ) -> ExceptionCase | None:
        case = await self.session.get(ExceptionCase, case_id)
        if case is None:
            return None
        case.status = status
        case.resolved_by = resolved_by
        case.resolution_note = note
        case.resolved_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(case)
        return case

    async def save_journal(self, journal: PaymentJournal) -> PaymentJournal:
        self.session.add(journal)
        await self.session.commit()
        await self.session.refresh(journal)
        return journal

    async def get_journal_for_run(self, run_id: uuid.UUID) -> PaymentJournal | None:
        result = await self.session.execute(
            select(PaymentJournal).where(PaymentJournal.run_id == run_id)
        )
        return result.scalars().first()

    async def save_remittance(self, remittance: Remittance) -> Remittance:
        self.session.add(remittance)
        await self.session.commit()
        await self.session.refresh(remittance)
        return remittance

    async def append_audit(
        self,
        run_id: uuid.UUID,
        action: AuditAction,
        summary: str,
        *,
        actor: str = "agent",
        node: str | None = None,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        source_message_id: str | None = None,
        document_sha256: str | None = None,
    ) -> AuditEvent:
        next_seq = await self.session.scalar(
            select(func.coalesce(func.max(AuditEvent.sequence), 0) + 1).where(
                AuditEvent.run_id == run_id
            )
        )
        event = AuditEvent(
            run_id=run_id,
            sequence=int(next_seq or 1),
            action=action,
            actor=actor,
            node=node,
            summary=summary,
            payload=payload or {},
            duration_ms=duration_ms,
            source_message_id=source_message_id,
            document_sha256=document_sha256,
        )
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(event)
        return event

    async def list_audit_events(
        self,
        run_id: uuid.UUID | None = None,
        action: AuditAction | None = None,
        since: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        stmt = select(AuditEvent)
        if run_id is not None:
            stmt = stmt.where(AuditEvent.run_id == run_id)
        if action is not None:
            stmt = stmt.where(AuditEvent.action == action)
        if since is not None:
            stmt = stmt.where(AuditEvent.created_at >= since)
        order = (
            (AuditEvent.run_id, AuditEvent.sequence)
            if run_id is not None
            else (AuditEvent.created_at.desc(),)
        )
        stmt = stmt.order_by(*order).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_audit_events(self, run_id: uuid.UUID | None = None) -> int:
        stmt = select(func.count()).select_from(AuditEvent)
        if run_id is not None:
            stmt = stmt.where(AuditEvent.run_id == run_id)
        return int(await self.session.scalar(stmt) or 0)

    async def straight_through_stats(self) -> dict[str, int]:
        total = int(await self.session.scalar(select(func.count()).select_from(WorkflowRun)) or 0)
        posted = int(
            await self.session.scalar(
                select(func.count())
                .select_from(WorkflowRun)
                .where(WorkflowRun.status == RunStatus.COMPLETED)
            )
            or 0
        )
        stp = int(
            await self.session.scalar(
                select(func.count())
                .select_from(WorkflowRun)
                .where(WorkflowRun.straight_through.is_(True))
            )
            or 0
        )
        exceptions = int(
            await self.session.scalar(select(func.count()).select_from(ExceptionCase)) or 0
        )
        return {
            "total_runs": total,
            "completed": posted,
            "straight_through": stp,
            "exceptions": exceptions,
        }
