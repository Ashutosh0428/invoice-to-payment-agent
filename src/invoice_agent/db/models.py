from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Column, Index, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from invoice_agent.schemas.common import (
    AuditAction,
    DocumentSource,
    ExceptionStatus,
    ExceptionType,
    JournalStatus,
    MatchOutcome,
    MatchType,
    RunStatus,
    WorkflowKind,
)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(UTC)


def _money(**kwargs: Any) -> Any:
    return Field(default=Decimal("0"), sa_column=Column(Numeric(18, 2), **kwargs))


class WorkflowRun(SQLModel, table=True):
    __tablename__ = "workflow_runs"
    __table_args__ = (Index("ix_workflow_runs_status_created", "status", "created_at"),)

    id: uuid.UUID = Field(default_factory=_uuid, primary_key=True)
    thread_id: str = Field(index=True, description="LangGraph checkpoint thread id")
    kind: WorkflowKind = Field(default=WorkflowKind.ACCOUNTS_PAYABLE, index=True)
    status: RunStatus = Field(default=RunStatus.RECEIVED, index=True)

    source: DocumentSource = Field(default=DocumentSource.API_UPLOAD)
    source_message_id: str | None = Field(default=None, index=True)
    source_mailbox: str | None = None
    source_sender: str | None = None
    source_subject: str | None = None
    source_received_at: datetime | None = None
    attachment_name: str | None = None
    document_path: str | None = None
    document_sha256: str | None = Field(default=None, index=True)

    straight_through: bool = Field(default=False, index=True)
    error_message: str | None = Field(default=None, sa_column=Column(Text))

    started_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now, index=True)
    updated_at: datetime = Field(default_factory=_now)


class Invoice(SQLModel, table=True):
    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_fingerprint", "fingerprint"),
        Index("ix_invoices_vendor_number", "vendor_name", "invoice_number"),
    )

    id: uuid.UUID = Field(default_factory=_uuid, primary_key=True)
    run_id: uuid.UUID = Field(foreign_key="workflow_runs.id", index=True)

    invoice_number: str | None = Field(default=None, index=True)
    invoice_date: date | None = None
    due_date: date | None = None
    purchase_order_number: str | None = Field(default=None, index=True)
    vendor_name: str | None = None
    vendor_id: str | None = Field(default=None, index=True)
    vendor_tax_id: str | None = None
    vendor_iban: str | None = None
    currency: str | None = None

    subtotal: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 2)))
    tax_amount: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 2)))
    total_amount: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 2)))

    payment_terms: str | None = None
    line_items: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB))
    field_confidence: dict[str, float] = Field(default_factory=dict, sa_column=Column(JSONB))
    confidence: float = 0.0
    fingerprint: str = Field(index=True)
    raw_markdown: str | None = Field(default=None, sa_column=Column(Text))

    created_at: datetime = Field(default_factory=_now)


class MatchResult(SQLModel, table=True):
    __tablename__ = "match_results"

    id: uuid.UUID = Field(default_factory=_uuid, primary_key=True)
    run_id: uuid.UUID = Field(foreign_key="workflow_runs.id", index=True)
    invoice_id: uuid.UUID | None = Field(default=None, foreign_key="invoices.id")

    match_type: MatchType = Field(default=MatchType.THREE_WAY)
    outcome: MatchOutcome = Field(default=MatchOutcome.EXCEPTION, index=True)
    po_number: str | None = Field(default=None, index=True)
    gr_numbers: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    matched_amount: Decimal = _money()
    confidence: float = 0.0

    header_variances: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB))
    line_matches: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB))
    exceptions: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    reasons: list[str] = Field(default_factory=list, sa_column=Column(JSONB))

    created_at: datetime = Field(default_factory=_now)


class ExceptionCase(SQLModel, table=True):
    __tablename__ = "exception_cases"
    __table_args__ = (Index("ix_exception_cases_status_created", "status", "created_at"),)

    id: uuid.UUID = Field(default_factory=_uuid, primary_key=True)
    run_id: uuid.UUID = Field(foreign_key="workflow_runs.id", index=True)

    exception_type: ExceptionType = Field(index=True)
    status: ExceptionStatus = Field(default=ExceptionStatus.OPEN, index=True)
    severity: str = Field(default="medium")
    summary: str = Field(sa_column=Column(Text))
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    suggested_action: str | None = Field(default=None, sa_column=Column(Text))

    assigned_to: str | None = None
    resolved_by: str | None = None
    resolution_note: str | None = Field(default=None, sa_column=Column(Text))
    resolved_at: datetime | None = None

    created_at: datetime = Field(default_factory=_now, index=True)


class PaymentJournal(SQLModel, table=True):
    __tablename__ = "payment_journals"
    __table_args__ = (UniqueConstraint("run_id", name="uq_payment_journals_run"),)

    id: uuid.UUID = Field(default_factory=_uuid, primary_key=True)
    run_id: uuid.UUID = Field(foreign_key="workflow_runs.id", index=True)
    invoice_id: uuid.UUID | None = Field(default=None, foreign_key="invoices.id")

    status: JournalStatus = Field(default=JournalStatus.DRAFT, index=True)
    erp_document_number: str | None = Field(default=None, index=True)
    fiscal_year: int | None = None
    posting_date: date | None = None
    reference: str = ""
    company_code: str = "1000"
    currency: str = "EUR"
    total_amount: Decimal = _money()
    lines: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB))
    approved_by: str | None = None
    error_message: str | None = Field(default=None, sa_column=Column(Text))

    created_at: datetime = Field(default_factory=_now)


class Remittance(SQLModel, table=True):
    __tablename__ = "remittances"

    id: uuid.UUID = Field(default_factory=_uuid, primary_key=True)
    run_id: uuid.UUID = Field(foreign_key="workflow_runs.id", index=True)

    remittance_number: str | None = Field(default=None, index=True)
    payment_date: date | None = None
    customer_name: str | None = None
    customer_id: str | None = Field(default=None, index=True)
    currency: str | None = None
    total_paid: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 2)))
    bank_reference: str | None = None
    advices: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB))
    applied: bool = Field(default=False, index=True)
    applications: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB))
    residual_amount: Decimal = _money()
    confidence: float = 0.0

    created_at: datetime = Field(default_factory=_now)


class AuditEvent(SQLModel, table=True):
    """Append-only. Every node writes one; nothing updates or deletes rows here."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_run_seq", "run_id", "sequence"),)

    id: uuid.UUID = Field(default_factory=_uuid, primary_key=True)
    run_id: uuid.UUID = Field(foreign_key="workflow_runs.id", index=True)
    sequence: int = Field(default=0)

    action: AuditAction = Field(index=True)
    actor: str = Field(default="agent")
    node: str | None = None
    summary: str = Field(sa_column=Column(Text))
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))

    source_message_id: str | None = None
    document_sha256: str | None = None
    duration_ms: int | None = None

    created_at: datetime = Field(default_factory=_now, index=True)
