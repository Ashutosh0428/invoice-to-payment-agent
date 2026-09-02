from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from invoice_agent.api.deps import RepositoryDep, TokenGuard
from invoice_agent.core.errors import ERPError, PurchaseOrderNotFoundError
from invoice_agent.erp.client import get_erp_client
from invoice_agent.matching.engine import match_invoice
from invoice_agent.rag.retriever import retrieve_policy
from invoice_agent.schemas.api import MatchRequest, MatchResponse
from invoice_agent.schemas.invoice import ExtractedInvoice, InvoiceLineItem

router = APIRouter(tags=["matching"], dependencies=[TokenGuard])


@router.post(
    "/match-po",
    response_model=MatchResponse,
    summary="Match an invoice against its purchase order and goods receipts",
)
async def match_po(request: MatchRequest, repo: RepositoryDep) -> MatchResponse:
    """Runs the 2-way / 3-way match on demand.

    Supply an inline invoice to test tolerance behaviour without ingesting a document, or a
    run_id to re-evaluate a stored extraction after tolerances or ERP data changed. This is
    pure arithmetic against configured tolerances; no model is called.
    """
    invoice = request.invoice
    run_id: str | None = None

    if invoice is None:
        if request.run_id is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "supply either an invoice payload or a run_id"
            )
        from sqlalchemy import select

        from invoice_agent.db.models import Invoice as InvoiceRow

        result = await repo.session.execute(
            select(InvoiceRow).where(InvoiceRow.run_id == request.run_id)  # type: ignore[arg-type]
        )
        stored = result.scalars().first()
        if stored is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"no extracted invoice stored for run {request.run_id}"
            )
        run_id = str(request.run_id)
        invoice = ExtractedInvoice(
            invoice_number=stored.invoice_number,
            invoice_date=stored.invoice_date,
            due_date=stored.due_date,
            purchase_order_number=stored.purchase_order_number,
            vendor_name=stored.vendor_name,
            vendor_tax_id=stored.vendor_tax_id,
            vendor_iban=stored.vendor_iban,
            currency=stored.currency,
            subtotal=stored.subtotal,
            tax_amount=stored.tax_amount,
            total_amount=stored.total_amount,
            payment_terms=stored.payment_terms,
            line_items=[InvoiceLineItem.model_validate(item) for item in stored.line_items],
            confidence=stored.confidence,
            field_confidence=stored.field_confidence,
        )

    po_number = request.po_number or invoice.purchase_order_number
    client = get_erp_client()
    purchase_order = None
    goods_receipts = []

    if po_number:
        try:
            purchase_order = await client.get_purchase_order(po_number)
            if purchase_order.goods_receipt_required:
                goods_receipts = await client.get_goods_receipts(po_number)
        except PurchaseOrderNotFoundError:
            purchase_order = None
        except ERPError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, exc.message) from exc

    report = match_invoice(invoice, purchase_order, goods_receipts)
    return MatchResponse(
        run_id=run_id,
        report=report,
        policy_extracts=retrieve_policy(report.exceptions),
    )
