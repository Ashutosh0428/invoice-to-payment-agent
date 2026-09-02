from __future__ import annotations

import time
import uuid
from decimal import Decimal
from typing import Any

from langgraph.types import interrupt
from loguru import logger

from invoice_agent.agents.nodes._common import audit, repository, set_run_status
from invoice_agent.agents.prompts import EXCEPTION_GUIDANCE_SYSTEM, EXCEPTION_GUIDANCE_USER
from invoice_agent.agents.state import InvoiceState
from invoice_agent.core.config import get_config
from invoice_agent.db.models import ExceptionCase, MatchResult
from invoice_agent.llm.provider import get_chat_model
from invoice_agent.matching.engine import match_invoice
from invoice_agent.rag.retriever import retrieve_policy
from invoice_agent.schemas.common import (
    AuditAction,
    ExceptionStatus,
    ExceptionType,
    MatchOutcome,
    RunStatus,
)
from invoice_agent.schemas.matching import MatchReport

_SEVERITY = {
    ExceptionType.DUPLICATE_INVOICE: "high",
    ExceptionType.UNKNOWN_VENDOR: "high",
    ExceptionType.MISSING_PO: "high",
    ExceptionType.OVER_AUTO_POST_CEILING: "high",
    ExceptionType.CURRENCY_MISMATCH: "high",
    ExceptionType.ERP_POSTING_FAILED: "high",
    ExceptionType.MISSING_GOODS_RECEIPT: "medium",
    ExceptionType.PRICE_VARIANCE: "medium",
    ExceptionType.QUANTITY_VARIANCE: "medium",
    ExceptionType.TOTAL_VARIANCE: "medium",
    ExceptionType.LINE_NOT_ON_PO: "medium",
    ExceptionType.TAX_VARIANCE: "low",
    ExceptionType.LOW_CONFIDENCE_EXTRACTION: "low",
}


async def match_node(state: InvoiceState) -> dict[str, Any]:
    started = time.perf_counter()
    await set_run_status(state, status=RunStatus.MATCHING)

    invoice = state.get("invoice")
    if invoice is None:
        return {"status": RunStatus.FAILED, "error": "no extracted invoice to match"}

    report = match_invoice(invoice, state.get("purchase_order"), state.get("goods_receipts") or [])

    if state.get("duplicate_of_run_id"):
        report.exceptions = list(
            dict.fromkeys([ExceptionType.DUPLICATE_INVOICE, *report.exceptions])
        )
        report.outcome = MatchOutcome.EXCEPTION
        report.reasons.insert(0, f"Invoice already processed in run {state['duplicate_of_run_id']}")

    if state.get("vendor") is None and invoice.vendor_name:
        report.exceptions = list(dict.fromkeys([*report.exceptions, ExceptionType.UNKNOWN_VENDOR]))
        report.outcome = MatchOutcome.EXCEPTION
        report.reasons.append(
            f"Vendor '{invoice.vendor_name}' could not be resolved against vendor master"
        )
    elif state.get("vendor") is not None:
        report.vendor_id = state["vendor"].vendor_id  # type: ignore[union-attr]

    async with repository() as repo:
        await repo.save_match(
            MatchResult(
                run_id=uuid.UUID(str(state["run_id"])),
                match_type=report.match_type,
                outcome=report.outcome,
                po_number=report.po_number,
                gr_numbers=report.gr_numbers,
                matched_amount=report.matched_amount,
                confidence=report.confidence,
                header_variances=[v.model_dump(mode="json") for v in report.header_variances],
                line_matches=[m.model_dump(mode="json") for m in report.line_matches],
                exceptions=[e.value for e in report.exceptions],
                reasons=report.reasons,
            )
        )

    await audit(
        state,
        AuditAction.MATCH_EVALUATED,
        report.summary(),
        node="match",
        payload={
            "match_type": report.match_type.value,
            "outcome": report.outcome.value,
            "exceptions": [e.value for e in report.exceptions],
            "reasons": report.reasons,
            "header_variances": [v.model_dump(mode="json") for v in report.header_variances],
        },
        started=started,
    )
    logger.info("Match outcome {} for run {}", report.outcome.value, state["run_id"])

    return {"match_report": report, "decisions": [report.summary()]}


def route_after_match(state: InvoiceState) -> str:
    report = state.get("match_report")
    if state.get("error") or state.get("status") == RunStatus.FAILED:
        return "failed"
    if report is not None and report.is_postable:
        return "post_journal"
    return "raise_exception"


async def _draft_guidance(report: MatchReport, state: InvoiceState, policy: list[str]) -> str:
    invoice = state.get("invoice")
    variance_lines = [v.describe() for v in report.header_variances if not v.within_tolerance]
    variance_lines += [
        f"line {m.po_line_number}: {v.describe()}"
        for m in report.line_matches
        for v in m.variances
        if not v.within_tolerance
    ]
    variance_lines += report.reasons

    prompt = EXCEPTION_GUIDANCE_USER.format(
        invoice_number=invoice.invoice_number if invoice else "unknown",
        vendor_name=invoice.vendor_name if invoice else "unknown",
        total_amount=invoice.total_amount if invoice else "0",
        currency=invoice.currency if invoice else "",
        po_number=report.po_number or "none",
        exceptions=", ".join(e.value for e in report.exceptions),
        variances="\n".join(f"- {line}" for line in variance_lines) or "- none recorded",
        policy="\n\n".join(policy) or "No policy extract retrieved.",
    )

    try:
        response = await get_chat_model().ainvoke(
            [("system", EXCEPTION_GUIDANCE_SYSTEM), ("human", prompt)]
        )
        return str(response.content).strip()
    except Exception as exc:
        logger.warning("Exception guidance generation failed: {}", exc)
        return "; ".join(report.reasons)


async def exception_node(state: InvoiceState) -> dict[str, Any]:
    """Persist the exception, then pause the graph.

    interrupt() checkpoints the run to Postgres and stops. The case can sit in the approval
    queue indefinitely; resuming replays this node with the approver's decision as the return
    value, so nothing above this point re-runs.
    """
    started = time.perf_counter()
    report = state.get("match_report")
    if report is None:
        return {"status": RunStatus.FAILED, "error": "no match report to escalate"}

    if not state.get("exception_case_id"):
        policy = retrieve_policy(report.exceptions)
        guidance = await _draft_guidance(report, state, policy)
        primary = report.exceptions[0] if report.exceptions else ExceptionType.TOTAL_VARIANCE
        invoice = state.get("invoice")

        async with repository() as repo:
            case = await repo.save_exception(
                ExceptionCase(
                    run_id=uuid.UUID(str(state["run_id"])),
                    exception_type=primary,
                    status=ExceptionStatus.OPEN,
                    severity=_SEVERITY.get(primary, "medium"),
                    summary=report.summary(),
                    suggested_action=guidance,
                    details={
                        "all_exceptions": [e.value for e in report.exceptions],
                        "reasons": report.reasons,
                        "po_number": report.po_number,
                        "invoice_number": invoice.invoice_number if invoice else None,
                        "vendor_name": invoice.vendor_name if invoice else None,
                        "total_amount": str(invoice.total_amount) if invoice else None,
                        "currency": invoice.currency if invoice else None,
                        "policy_extracts": policy,
                        "duplicate_of_run_id": state.get("duplicate_of_run_id"),
                    },
                )
            )

        await set_run_status(state, status=RunStatus.AWAITING_APPROVAL)
        await audit(
            state,
            AuditAction.EXCEPTION_RAISED,
            f"Raised {primary.value} exception for human approval: {report.summary()}",
            node="raise_exception",
            payload={
                "exception_case_id": str(case.id),
                "exception_types": [e.value for e in report.exceptions],
                "severity": case.severity,
                "policy_extracts": policy,
            },
            started=started,
        )
        state = {
            **state,
            "exception_case_id": str(case.id),
            "exception_guidance": guidance,
            "policy_extracts": policy,
        }

    decision = interrupt(
        {
            "exception_case_id": state.get("exception_case_id"),
            "run_id": state.get("run_id"),
            "exceptions": [e.value for e in report.exceptions],
            "summary": report.summary(),
            "suggested_action": state.get("exception_guidance", ""),
            "reasons": report.reasons,
        }
    )

    verdict = str(decision.get("decision", "reject")).lower()
    approved_by = str(decision.get("approved_by", "unknown"))
    note = decision.get("note")

    if state.get("exception_case_id"):
        async with repository() as repo:
            await repo.resolve_exception(
                uuid.UUID(str(state["exception_case_id"])),
                ExceptionStatus.APPROVED if verdict == "approve" else ExceptionStatus.REJECTED,
                approved_by,
                note,
            )

    await audit(
        state,
        AuditAction.HUMAN_DECISION,
        f"{approved_by} {verdict}d the exception" + (f": {note}" if note else ""),
        node="human_approval",
        actor=approved_by,
        payload={
            "decision": verdict,
            "note": note,
            "exception_case_id": state.get("exception_case_id"),
        },
    )

    return {
        "approval_decision": verdict,
        "approved_by": approved_by,
        "approval_note": note or "",
        "exception_case_id": state.get("exception_case_id"),
        "exception_guidance": state.get("exception_guidance", ""),
        "policy_extracts": state.get("policy_extracts", []),
        "decisions": [f"human {verdict}: {note or 'no note'}"],
    }


def route_after_approval(state: InvoiceState) -> str:
    if state.get("approval_decision") == "approve":
        return "post_journal"
    return "rejected"


def over_ceiling(total: Decimal | None) -> bool:
    ceiling = get_config().matching.auto_post_ceiling
    return total is not None and total > ceiling
