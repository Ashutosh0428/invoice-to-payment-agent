from __future__ import annotations

import time
import uuid
from decimal import Decimal
from typing import Any

from loguru import logger

from invoice_agent.agents.nodes._common import audit, repository, set_run_status
from invoice_agent.agents.state import InvoiceState
from invoice_agent.core.errors import ERPError
from invoice_agent.db.models import ExceptionCase, Remittance
from invoice_agent.erp.client import get_erp_client
from invoice_agent.matching.engine import match_remittance
from invoice_agent.rag.retriever import resolve_vendor
from invoice_agent.schemas.common import (
    AuditAction,
    ExceptionStatus,
    ExceptionType,
    RunStatus,
)


async def fetch_ar_items_node(state: InvoiceState) -> dict[str, Any]:
    started = time.perf_counter()
    remittance = state.get("remittance")
    if remittance is None:
        return {"open_ar_items": []}

    client = get_erp_client()
    customer_id = remittance.customer_id

    if not customer_id and remittance.customer_name:
        try:
            vendors = await client.get_vendors()
            resolution = resolve_vendor(vendors, remittance.customer_name)
            if resolution.vendor is not None:
                customer_id = resolution.vendor.vendor_id
        except ERPError as exc:
            logger.warning("Customer resolution failed: {}", exc.message)

    items = []
    try:
        if customer_id:
            items = await client.get_ar_items(customer_id=customer_id)
        else:
            for advice in remittance.advices:
                if advice.invoice_number:
                    items.extend(await client.get_ar_items(invoice_number=advice.invoice_number))
    except ERPError as exc:
        await audit(
            state,
            AuditAction.RUN_FAILED,
            f"AR open item lookup failed: {exc.message}",
            node="fetch_ar_items",
            payload=exc.details,
            started=started,
        )
        return {"open_ar_items": [], "error": exc.message}

    await audit(
        state,
        AuditAction.PO_FETCHED,
        f"Fetched {len(items)} open receivable(s) for "
        f"{remittance.customer_name or customer_id or 'unknown customer'}",
        node="fetch_ar_items",
        payload={"ar_item_ids": [item.ar_item_id for item in items]},
        started=started,
    )
    return {"open_ar_items": items, "decisions": [f"fetched {len(items)} open receivables"]}


async def apply_cash_node(state: InvoiceState) -> dict[str, Any]:
    """AR mirror of journal posting: match the advice lines to open items and clear them."""
    started = time.perf_counter()
    await set_run_status(state, status=RunStatus.POSTING)

    remittance = state.get("remittance")
    if remittance is None:
        return {"status": RunStatus.FAILED, "error": "no extracted remittance to apply"}

    applications, unresolved, residual = match_remittance(
        remittance, state.get("open_ar_items") or []
    )

    client = get_erp_client()
    applied: list[dict[str, Any]] = []
    for item, amount in applications:
        try:
            result = await client.apply_cash(
                item.ar_item_id,
                amount,
                remittance.bank_reference or remittance.remittance_number or str(state["run_id"]),
            )
            applied.append(result.model_dump(mode="json"))
        except ERPError as exc:
            unresolved.append(f"Cash application failed for {item.ar_item_id}: {exc.message}")

    async with repository() as repo:
        await repo.save_remittance(
            Remittance(
                run_id=uuid.UUID(str(state["run_id"])),
                remittance_number=remittance.remittance_number,
                payment_date=remittance.payment_date,
                customer_name=remittance.customer_name,
                customer_id=remittance.customer_id,
                currency=remittance.currency,
                total_paid=remittance.total_paid,
                bank_reference=remittance.bank_reference,
                advices=[advice.model_dump(mode="json") for advice in remittance.advices],
                applied=bool(applied) and not unresolved,
                applications=applied,
                residual_amount=residual if residual else Decimal("0"),
                confidence=remittance.confidence,
            )
        )

        if unresolved:
            await repo.save_exception(
                ExceptionCase(
                    run_id=uuid.UUID(str(state["run_id"])),
                    exception_type=ExceptionType.UNAPPLIED_REMITTANCE,
                    status=ExceptionStatus.OPEN,
                    severity="medium",
                    summary=(
                        f"Remittance {remittance.remittance_number or ''} left "
                        f"{len(unresolved)} item(s) unresolved, residual {residual}"
                    ),
                    suggested_action=(
                        "Review the payment advice against open receivables. Residual cash is "
                        "posted to the unapplied account until collections identifies the item."
                    ),
                    details={"unresolved": unresolved, "residual": str(residual)},
                )
            )

    await audit(
        state,
        AuditAction.CASH_APPLIED,
        f"Applied cash to {len(applied)} receivable(s), residual {residual}"
        + (f"; {len(unresolved)} unresolved" if unresolved else ""),
        node="apply_cash",
        payload={"applications": applied, "unresolved": unresolved, "residual": str(residual)},
        started=started,
    )

    straight_through = bool(applied) and not unresolved
    await set_run_status(
        state,
        status=RunStatus.COMPLETED if applied else RunStatus.AWAITING_APPROVAL,
        straight_through=straight_through,
    )

    return {
        "cash_applications": applied,
        "residual_amount": str(residual),
        "straight_through": straight_through,
        "status": RunStatus.COMPLETED if applied else RunStatus.AWAITING_APPROVAL,
        "decisions": [f"applied {len(applied)} receivable(s), residual {residual}"],
    }
