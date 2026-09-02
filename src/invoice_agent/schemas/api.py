from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from invoice_agent.schemas.erp import JournalPostingRequest
from invoice_agent.schemas.invoice import ExtractedInvoice
from invoice_agent.schemas.matching import MatchReport


class ComponentHealth(BaseModel):
    name: str
    status: Literal["ok", "degraded", "down"]
    detail: str = ""
    latency_ms: float | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    service: str = "invoice-to-payment-agent"
    version: str
    environment: str
    timestamp: datetime
    components: list[ComponentHealth] = Field(default_factory=list)


class IngestResponse(BaseModel):
    run_id: str
    status: str
    kind: str
    straight_through: bool
    attachment_name: str | None = None
    document_sha256: str | None = None
    match: dict[str, Any] | None = None
    journal: dict[str, Any] | None = None
    exceptions: list[dict[str, Any]] = Field(default_factory=list)
    awaiting_approval: dict[str, Any] | None = None
    decisions: list[str] = Field(default_factory=list)
    error: str | None = None


class MailboxPollRequest(BaseModel):
    kind: Literal["accounts_payable", "accounts_receivable"] = "accounts_payable"
    limit: int | None = Field(default=None, ge=1, le=100)


class MailboxPollResponse(BaseModel):
    polled: int
    runs: list[IngestResponse] = Field(default_factory=list)


class MatchRequest(BaseModel):
    """Either supply a run_id to re-match a processed document, or an inline invoice payload
    to match without ingesting anything."""

    run_id: uuid.UUID | None = None
    invoice: ExtractedInvoice | None = None
    po_number: str | None = None


class MatchResponse(BaseModel):
    run_id: str | None = None
    report: MatchReport
    policy_extracts: list[str] = Field(default_factory=list)


class PostJournalRequest(BaseModel):
    run_id: uuid.UUID | None = None
    approved_by: str = "api"
    note: str | None = None
    journal: JournalPostingRequest | None = None


class PostJournalResponse(BaseModel):
    run_id: str | None = None
    status: str
    erp_document_number: str | None = None
    fiscal_year: int | None = None
    total_amount: Decimal | None = None
    currency: str | None = None
    reference: str | None = None
    straight_through: bool = False
    detail: str | None = None


class AuditEventOut(BaseModel):
    id: str
    run_id: str
    sequence: int
    action: str
    actor: str
    node: str | None
    summary: str
    payload: dict[str, Any]
    source_message_id: str | None
    document_sha256: str | None
    duration_ms: int | None
    created_at: datetime


class AuditLogResponse(BaseModel):
    total: int
    returned: int
    limit: int
    offset: int
    events: list[AuditEventOut] = Field(default_factory=list)


class ExceptionOut(BaseModel):
    id: str
    run_id: str
    exception_type: str
    status: str
    severity: str
    summary: str
    suggested_action: str | None
    details: dict[str, Any]
    resolved_by: str | None
    resolution_note: str | None
    created_at: datetime


class ExceptionDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    approved_by: str
    note: str | None = None


class MetricsResponse(BaseModel):
    total_runs: int
    completed: int
    straight_through: int
    exceptions: int
    straight_through_rate: float
    completion_rate: float
