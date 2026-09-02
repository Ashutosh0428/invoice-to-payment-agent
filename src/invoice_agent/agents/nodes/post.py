from __future__ import annotations

import time
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from loguru import logger

from invoice_agent.agents.nodes._common import audit, repository, set_run_status
from invoice_agent.agents.state import InvoiceState
from invoice_agent.core.config import get_config
from invoice_agent.core.errors import ERPError, ExtractionError
from invoice_agent.db.models import ExceptionCase, PaymentJournal
from invoice_agent.erp.client import get_erp_client
from invoice_agent.schemas.common import (
    AuditAction,
    ExceptionStatus,
    ExceptionType,
    JournalStatus,
    RunStatus,
)
from invoice_agent.schemas.erp import JournalLine, JournalPostingRequest

GRIR_CLEARING_ACCOUNT = "191100"
INPUT_VAT_ACCOUNT = "154000"
ACCOUNTS_PAYABLE_ACCOUNT = "160000"
NON_PO_EXPENSE_ACCOUNT = "470000"

_CENTS = Decimal("0.01")


def _q(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def build_journal(state: InvoiceState) -> JournalPostingRequest:
    """Debit GR/IR clearing and input VAT, credit accounts payable.

    Net and tax are derived from the gross when the invoice does not print them separately,
    which keeps the entry balanced rather than posting a lopsided document the ERP rejects.
    """
    cfg = get_config()
    invoice = state["invoice"]
    if invoice is None:
        raise ExtractionError("cannot build a payment journal without an extracted invoice")

    purchase_order = state.get("purchase_order")
    vendor = state.get("vendor")

    gross = _q(invoice.total_amount or Decimal("0"))
    tax = _q(invoice.tax_amount) if invoice.tax_amount is not None else None
    net = _q(invoice.subtotal) if invoice.subtotal is not None else None

    if net is None:
        net = _q(gross - tax) if tax is not None else gross
    if tax is None:
        tax = _q(gross - net)

    if _q(net + tax) != gross:
        net = _q(gross - tax)

    debit_account = GRIR_CLEARING_ACCOUNT if purchase_order else NON_PO_EXPENSE_ACCOUNT
    reference = f"{invoice.vendor_name or 'VENDOR'}/{invoice.invoice_number or state['run_id']}"

    lines = [
        JournalLine(
            account=debit_account,
            description=f"Invoice {invoice.invoice_number or ''} net goods/services value",
            debit=net,
        )
    ]
    if tax > 0:
        lines.append(
            JournalLine(
                account=INPUT_VAT_ACCOUNT,
                description=f"Input VAT on invoice {invoice.invoice_number or ''}",
                debit=tax,
                tax_code="V1",
            )
        )
    lines.append(
        JournalLine(
            account=ACCOUNTS_PAYABLE_ACCOUNT,
            description=f"Payable to {invoice.vendor_name or 'vendor'}",
            credit=gross,
        )
    )

    return JournalPostingRequest(
        reference=reference[:80],
        company_code=cfg.erp.company_code,
        currency=invoice.currency or cfg.erp.default_currency,
        posting_date=date.today(),
        vendor_id=vendor.vendor_id if vendor else None,
        po_number=purchase_order.po_number if purchase_order else None,
        memo=(
            f"Automated posting from email {state.get('source_message_id', 'n/a')} "
            f"(document {state.get('document_sha256', '')[:12]})"
        ),
        lines=lines,
    )


async def post_journal_node(state: InvoiceState) -> dict[str, Any]:
    started = time.perf_counter()
    await set_run_status(state, status=RunStatus.POSTING)

    if state.get("invoice") is None:
        return {"status": RunStatus.FAILED, "error": "no invoice to post"}

    request = build_journal(state)
    if not request.is_balanced():
        message = "refusing to post an unbalanced journal"
        await audit(
            state,
            AuditAction.RUN_FAILED,
            message,
            node="post_journal",
            payload={"journal": request.model_dump(mode="json")},
            started=started,
        )
        await set_run_status(state, status=RunStatus.FAILED, error_message=message)
        return {"status": RunStatus.FAILED, "error": message}

    straight_through = state.get("exception_case_id") is None

    try:
        result = await get_erp_client().post_journal_entry(request)
    except ERPError as exc:
        async with repository() as repo:
            await repo.save_journal(
                PaymentJournal(
                    run_id=uuid.UUID(str(state["run_id"])),
                    status=JournalStatus.FAILED,
                    reference=request.reference,
                    company_code=request.company_code,
                    currency=request.currency,
                    total_amount=sum((line.debit for line in request.lines), Decimal("0")),
                    lines=[line.model_dump(mode="json") for line in request.lines],
                    approved_by=state.get("approved_by"),
                    error_message=exc.message,
                )
            )
            await repo.save_exception(
                ExceptionCase(
                    run_id=uuid.UUID(str(state["run_id"])),
                    exception_type=ExceptionType.ERP_POSTING_FAILED,
                    status=ExceptionStatus.OPEN,
                    severity="high",
                    summary=f"ERP rejected the payment journal: {exc.message}",
                    suggested_action=(
                        "Verify the journal reference has not already been posted, then retry "
                        "the run. Check ERP connectivity before re-releasing the invoice."
                    ),
                    details={"journal": request.model_dump(mode="json"), **exc.details},
                )
            )
        await audit(
            state,
            AuditAction.RUN_FAILED,
            f"Journal posting failed: {exc.message}",
            node="post_journal",
            payload=exc.details,
            started=started,
        )
        await set_run_status(state, status=RunStatus.FAILED, error_message=exc.message)
        return {
            "status": RunStatus.FAILED,
            "error": exc.message,
            "decisions": [f"ERP posting failed: {exc.message}"],
        }

    async with repository() as repo:
        await repo.save_journal(
            PaymentJournal(
                run_id=uuid.UUID(str(state["run_id"])),
                status=JournalStatus.POSTED,
                erp_document_number=result.document_number,
                fiscal_year=result.fiscal_year,
                posting_date=result.posting_date,
                reference=result.reference,
                company_code=result.company_code,
                currency=result.currency,
                total_amount=result.total_amount,
                lines=[line.model_dump(mode="json") for line in request.lines],
                approved_by=state.get("approved_by"),
            )
        )

    await audit(
        state,
        AuditAction.JOURNAL_POSTED,
        f"Posted journal {result.document_number} for {result.total_amount} {result.currency}"
        + (
            " straight through"
            if straight_through
            else f" after approval by {state.get('approved_by')}"
        ),
        node="post_journal",
        payload={
            "document_number": result.document_number,
            "fiscal_year": result.fiscal_year,
            "total_amount": str(result.total_amount),
            "straight_through": straight_through,
            "journal_lines": [line.model_dump(mode="json") for line in request.lines],
        },
        started=started,
    )
    await set_run_status(state, status=RunStatus.COMPLETED, straight_through=straight_through)
    logger.info("Posted ERP document {} for run {}", result.document_number, state["run_id"])

    return {
        "journal_result": result,
        "status": RunStatus.COMPLETED,
        "straight_through": straight_through,
        "decisions": [f"posted ERP document {result.document_number}"],
    }


async def rejected_node(state: InvoiceState) -> dict[str, Any]:
    await set_run_status(state, status=RunStatus.REJECTED)
    await audit(
        state,
        AuditAction.HUMAN_DECISION,
        f"Invoice rejected by {state.get('approved_by', 'approver')}; no journal posted",
        node="rejected",
        actor=state.get("approved_by", "approver"),
        payload={"note": state.get("approval_note", "")},
    )
    return {"status": RunStatus.REJECTED, "decisions": ["run closed without posting"]}


async def failed_node(state: InvoiceState) -> dict[str, Any]:
    await set_run_status(
        state, status=RunStatus.FAILED, error_message=state.get("error", "unknown failure")
    )
    return {"status": RunStatus.FAILED}
