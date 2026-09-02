from __future__ import annotations

import time
import uuid
from typing import Any

from loguru import logger

from invoice_agent.agents.nodes._common import audit, repository
from invoice_agent.agents.state import InvoiceState
from invoice_agent.core.errors import ERPError, PurchaseOrderNotFoundError
from invoice_agent.db.models import Invoice
from invoice_agent.erp.client import get_erp_client
from invoice_agent.rag.retriever import resolve_vendor
from invoice_agent.schemas.common import AuditAction


async def persist_invoice_node(state: InvoiceState) -> dict[str, Any]:
    """Write the extracted header before matching so a crash mid-match still leaves the
    duplicate key and the audit trail behind."""
    invoice = state.get("invoice")
    if invoice is None:
        return {}

    async with repository() as repo:
        await repo.save_invoice(
            Invoice(
                run_id=uuid.UUID(str(state["run_id"])),
                invoice_number=invoice.invoice_number,
                invoice_date=invoice.invoice_date,
                due_date=invoice.due_date,
                purchase_order_number=invoice.purchase_order_number,
                vendor_name=invoice.vendor_name,
                vendor_tax_id=invoice.vendor_tax_id,
                vendor_iban=invoice.vendor_iban,
                currency=invoice.currency,
                subtotal=invoice.subtotal,
                tax_amount=invoice.tax_amount,
                total_amount=invoice.total_amount,
                payment_terms=invoice.payment_terms,
                line_items=[item.model_dump(mode="json") for item in invoice.line_items],
                field_confidence=invoice.field_confidence,
                confidence=invoice.confidence,
                fingerprint=invoice.fingerprint(),
                raw_markdown=state.get("document_markdown", "")[:20000],
            )
        )
    return {}


async def duplicate_check_node(state: InvoiceState) -> dict[str, Any]:
    started = time.perf_counter()
    invoice = state.get("invoice")
    if invoice is None:
        return {}

    fingerprint = invoice.fingerprint()
    async with repository() as repo:
        existing = await repo.find_duplicate(fingerprint, uuid.UUID(str(state["run_id"])))

    if existing is None:
        await audit(
            state,
            AuditAction.DUPLICATE_CHECKED,
            "No prior invoice matches vendor, number and amount",
            node="duplicate_check",
            payload={"fingerprint": fingerprint},
            started=started,
        )
        return {"duplicate_of_run_id": None, "decisions": ["duplicate check passed"]}

    await audit(
        state,
        AuditAction.DUPLICATE_CHECKED,
        f"Duplicate of invoice {existing.invoice_number} first seen on "
        f"{existing.created_at.date()} in run {existing.run_id}",
        node="duplicate_check",
        payload={
            "fingerprint": fingerprint,
            "original_run_id": str(existing.run_id),
            "original_invoice_id": str(existing.id),
        },
        started=started,
    )
    return {
        "duplicate_of_run_id": str(existing.run_id),
        "decisions": [f"duplicate of run {existing.run_id}"],
    }


async def resolve_vendor_node(state: InvoiceState) -> dict[str, Any]:
    started = time.perf_counter()
    invoice = state.get("invoice")
    if invoice is None:
        return {}

    try:
        vendors = await get_erp_client().get_vendors()
    except ERPError as exc:
        logger.warning("Vendor master unavailable: {}", exc.message)
        await audit(
            state,
            AuditAction.VENDOR_RESOLVED,
            f"Vendor master unavailable: {exc.message}",
            node="resolve_vendor",
            payload=exc.details,
            started=started,
        )
        return {"vendor": None, "vendor_match_method": "erp_unavailable"}

    resolution = resolve_vendor(
        vendors, invoice.vendor_name, invoice.vendor_tax_id, invoice.vendor_iban
    )

    if resolution.resolved and resolution.vendor is not None:
        summary = (
            f"Resolved '{invoice.vendor_name}' to {resolution.vendor.vendor_id} "
            f"({resolution.vendor.name}) by {resolution.method} at score {resolution.score:.0f}"
        )
    else:
        summary = (
            f"Could not resolve vendor '{invoice.vendor_name}'; "
            f"best candidates: {', '.join(resolution.candidates) or 'none'}"
        )

    await audit(
        state,
        AuditAction.VENDOR_RESOLVED,
        summary,
        node="resolve_vendor",
        payload={
            "method": resolution.method,
            "score": resolution.score,
            "candidates": resolution.candidates,
            "vendor_id": resolution.vendor.vendor_id if resolution.vendor else None,
        },
        started=started,
    )
    return {
        "vendor": resolution.vendor,
        "vendor_match_method": resolution.method,
        "decisions": [summary],
    }


async def fetch_po_node(state: InvoiceState) -> dict[str, Any]:
    started = time.perf_counter()
    invoice = state.get("invoice")
    if invoice is None or not invoice.purchase_order_number:
        await audit(
            state,
            AuditAction.PO_FETCHED,
            "Invoice carries no purchase order reference",
            node="fetch_po",
            started=started,
        )
        return {
            "purchase_order": None,
            "goods_receipts": [],
            "decisions": ["no purchase order reference on invoice"],
        }

    client = get_erp_client()
    po_number = invoice.purchase_order_number

    try:
        purchase_order = await client.get_purchase_order(po_number)
    except PurchaseOrderNotFoundError:
        await audit(
            state,
            AuditAction.PO_FETCHED,
            f"Purchase order {po_number} not found in ERP",
            node="fetch_po",
            payload={"po_number": po_number},
            started=started,
        )
        return {
            "purchase_order": None,
            "goods_receipts": [],
            "decisions": [f"purchase order {po_number} not found"],
        }
    except ERPError as exc:
        await audit(
            state,
            AuditAction.RUN_FAILED,
            f"ERP lookup failed for {po_number}: {exc.message}",
            node="fetch_po",
            payload=exc.details,
            started=started,
        )
        return {
            "purchase_order": None,
            "goods_receipts": [],
            "error": exc.message,
            "decisions": [f"ERP unavailable: {exc.message}"],
        }

    await audit(
        state,
        AuditAction.PO_FETCHED,
        f"Fetched {po_number} for vendor {purchase_order.vendor_name}: "
        f"{len(purchase_order.lines)} lines, gross {purchase_order.gross_amount} "
        f"{purchase_order.currency}",
        node="fetch_po",
        payload={"purchase_order": purchase_order.model_dump(mode="json")},
        started=started,
    )

    goods_receipts = []
    if purchase_order.goods_receipt_required:
        gr_started = time.perf_counter()
        try:
            goods_receipts = await client.get_goods_receipts(po_number)
        except ERPError as exc:
            logger.warning("Goods receipt lookup failed for {}: {}", po_number, exc.message)
        await audit(
            state,
            AuditAction.GOODS_RECEIPT_FETCHED,
            f"Found {len(goods_receipts)} goods receipt(s) for {po_number}",
            node="fetch_goods_receipts",
            payload={"gr_numbers": [gr.gr_number for gr in goods_receipts]},
            started=gr_started,
        )

    return {
        "purchase_order": purchase_order,
        "goods_receipts": goods_receipts,
        "decisions": [f"fetched {po_number} with {len(goods_receipts)} goods receipt(s)"],
    }
