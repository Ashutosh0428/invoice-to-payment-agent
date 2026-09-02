from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoice_agent.core.config import MatchingConfig
from invoice_agent.schemas.erp import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    Vendor,
)
from invoice_agent.schemas.invoice import ExtractedInvoice, InvoiceLineItem

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = REPO_ROOT / "samples" / "inbox"


@pytest.fixture
def matching_config() -> MatchingConfig:
    return MatchingConfig(
        price_tolerance_pct=Decimal("2.0"),
        price_tolerance_abs=Decimal("5.00"),
        quantity_tolerance_pct=Decimal("0.0"),
        quantity_tolerance_abs=Decimal("0"),
        total_tolerance_pct=Decimal("1.0"),
        total_tolerance_abs=Decimal("10.00"),
        tax_tolerance_abs=Decimal("1.00"),
        auto_post_ceiling=Decimal("25000.00"),
        min_extraction_confidence=0.75,
    )


@pytest.fixture
def purchase_order() -> PurchaseOrder:
    return PurchaseOrder(
        po_number="PO-4500001",
        vendor_id="V-10001",
        vendor_name="Contoso Supplies GmbH",
        currency="EUR",
        order_date=date(2026, 7, 2),
        goods_receipt_required=True,
        net_amount=Decimal("10730.00"),
        tax_amount=Decimal("2038.70"),
        gross_amount=Decimal("12768.70"),
        lines=[
            PurchaseOrderLine(
                line_number=10,
                material_code="MAT-A100",
                description="Steel bracket, galvanised, 120mm",
                quantity_ordered=Decimal("500"),
                quantity_received=Decimal("500"),
                unit="EA",
                unit_price=Decimal("14.50"),
                line_total=Decimal("7250.00"),
            ),
            PurchaseOrderLine(
                line_number=20,
                material_code="MAT-A210",
                description="Hex bolt M12x60 stainless",
                quantity_ordered=Decimal("1200"),
                quantity_received=Decimal("1200"),
                unit="EA",
                unit_price=Decimal("2.90"),
                line_total=Decimal("3480.00"),
            ),
        ],
    )


@pytest.fixture
def goods_receipts() -> list[GoodsReceipt]:
    return [
        GoodsReceipt(
            gr_number="GR-5000001",
            po_number="PO-4500001",
            posting_date=date(2026, 7, 18),
            lines=[
                GoodsReceiptLine(
                    po_line_number=10,
                    material_code="MAT-A100",
                    quantity_received=Decimal("500"),
                    unit="EA",
                ),
                GoodsReceiptLine(
                    po_line_number=20,
                    material_code="MAT-A210",
                    quantity_received=Decimal("1200"),
                    unit="EA",
                ),
            ],
        )
    ]


@pytest.fixture
def clean_invoice() -> ExtractedInvoice:
    return ExtractedInvoice(
        invoice_number="INV-2026-0871",
        invoice_date=date(2026, 7, 28),
        due_date=date(2026, 8, 27),
        purchase_order_number="PO-4500001",
        vendor_name="Contoso Supplies GmbH",
        vendor_tax_id="DE811907980",
        vendor_iban="DE89370400440532013000",
        currency="EUR",
        subtotal=Decimal("10730.00"),
        tax_amount=Decimal("2038.70"),
        total_amount=Decimal("12768.70"),
        payment_terms="NET30",
        confidence=0.95,
        line_items=[
            InvoiceLineItem(
                line_number=1,
                po_line_number=10,
                material_code="MAT-A100",
                description="Steel bracket, galvanised, 120mm",
                quantity=Decimal("500"),
                unit="EA",
                unit_price=Decimal("14.50"),
                line_total=Decimal("7250.00"),
            ),
            InvoiceLineItem(
                line_number=2,
                po_line_number=20,
                material_code="MAT-A210",
                description="Hex bolt M12x60 stainless",
                quantity=Decimal("1200"),
                unit="EA",
                unit_price=Decimal("2.90"),
                line_total=Decimal("3480.00"),
            ),
        ],
    )


@pytest.fixture
def vendors() -> list[Vendor]:
    return [
        Vendor(
            vendor_id="V-10001",
            name="Contoso Supplies GmbH",
            tax_id="DE811907980",
            iban="DE89370400440532013000",
            aliases=["Contoso Supplies", "Contoso GmbH"],
        ),
        Vendor(
            vendor_id="V-10002",
            name="Fabrikam Industrial AG",
            tax_id="DE114203847",
            iban="DE02120300000000202051",
            aliases=["Fabrikam Industrial"],
        ),
    ]


class _StubKnowledgeBase:
    """Unit tests must not reach pgvector. The real fallback path is covered by the
    integration suite, which runs against the compose stack."""

    def search_vendors(self, query: str, top_k: int = 5) -> list[dict]:
        return []

    def search_policy(self, query: str, top_k: int = 3) -> list[dict]:
        return []


@pytest.fixture(autouse=True)
def stub_knowledge_base(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("integration"):
        return
    stub = _StubKnowledgeBase()
    monkeypatch.setattr("invoice_agent.rag.retriever.get_knowledge_base", lambda: stub)
    monkeypatch.setattr("invoice_agent.rag.index.get_knowledge_base", lambda: stub)
