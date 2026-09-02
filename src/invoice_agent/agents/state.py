from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from invoice_agent.schemas.common import RunStatus, WorkflowKind
from invoice_agent.schemas.erp import (
    ARItem,
    GoodsReceipt,
    JournalPostingResult,
    PurchaseOrder,
    Vendor,
)
from invoice_agent.schemas.invoice import ExtractedInvoice, ExtractedRemittance
from invoice_agent.schemas.matching import MatchReport


class InvoiceState(TypedDict, total=False):
    """LangGraph channel state.

    Checkpointed to Postgres after every node, which is what lets a run pause on an exception
    for days and resume on approval without holding a process open.
    """

    run_id: str
    thread_id: str
    kind: WorkflowKind
    status: RunStatus

    source_message_id: str
    source_sender: str
    source_subject: str
    mailbox: str
    document_path: str
    document_sha256: str
    attachment_name: str

    document_markdown: str
    parser: str
    page_count: int

    invoice: ExtractedInvoice | None
    remittance: ExtractedRemittance | None
    confidence_signals: dict[str, float]

    duplicate_of_run_id: str | None
    vendor: Vendor | None
    vendor_match_method: str
    purchase_order: PurchaseOrder | None
    goods_receipts: list[GoodsReceipt]
    open_ar_items: list[ARItem]

    match_report: MatchReport | None
    policy_extracts: list[str]
    exception_case_id: str | None
    exception_guidance: str

    approval_decision: str
    approved_by: str
    approval_note: str

    journal_result: JournalPostingResult | None
    cash_applications: list[dict[str, Any]]
    residual_amount: str

    straight_through: bool
    error: str | None
    decisions: Annotated[list[str], operator.add]
