from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class Vendor(BaseModel):
    vendor_id: str
    name: str
    tax_id: str | None = None
    iban: str | None = None
    payment_terms: str | None = None
    currency: str = "EUR"
    aliases: list[str] = Field(default_factory=list)
    blocked: bool = False


class PurchaseOrderLine(BaseModel):
    line_number: int
    material_code: str | None = None
    description: str = ""
    quantity_ordered: Decimal = Decimal("0")
    quantity_received: Decimal = Decimal("0")
    quantity_invoiced: Decimal = Decimal("0")
    unit: str = "EA"
    unit_price: Decimal = Decimal("0")
    line_total: Decimal = Decimal("0")

    @property
    def quantity_open(self) -> Decimal:
        return self.quantity_ordered - self.quantity_invoiced


class PurchaseOrder(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    po_number: str
    vendor_id: str
    vendor_name: str
    company_code: str = "1000"
    currency: str = "EUR"
    order_date: date | None = None
    status: str = "open"
    goods_receipt_required: bool = True
    net_amount: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    gross_amount: Decimal = Decimal("0")
    lines: list[PurchaseOrderLine] = Field(default_factory=list)

    def line(self, number: int) -> PurchaseOrderLine | None:
        return next((line for line in self.lines if line.line_number == number), None)


class GoodsReceiptLine(BaseModel):
    po_line_number: int
    material_code: str | None = None
    quantity_received: Decimal = Decimal("0")
    unit: str = "EA"
    receipt_date: date | None = None


class GoodsReceipt(BaseModel):
    gr_number: str
    po_number: str
    posting_date: date | None = None
    lines: list[GoodsReceiptLine] = Field(default_factory=list)


class ARItem(BaseModel):
    """An open receivable: an invoice we issued and are waiting to be paid for."""

    ar_item_id: str
    invoice_number: str
    customer_id: str
    customer_name: str
    currency: str = "EUR"
    open_amount: Decimal = Decimal("0")
    original_amount: Decimal = Decimal("0")
    due_date: date | None = None
    status: str = "open"


class JournalLine(BaseModel):
    account: str
    description: str = ""
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    cost_center: str | None = None
    tax_code: str | None = None


class JournalPostingRequest(BaseModel):
    reference: str
    company_code: str = "1000"
    currency: str = "EUR"
    posting_date: date | None = None
    vendor_id: str | None = None
    customer_id: str | None = None
    po_number: str | None = None
    memo: str = ""
    lines: list[JournalLine] = Field(default_factory=list)

    def is_balanced(self) -> bool:
        debits = sum((line.debit for line in self.lines), Decimal("0"))
        credits = sum((line.credit for line in self.lines), Decimal("0"))
        return debits == credits and debits > 0


class JournalPostingResult(BaseModel):
    document_number: str
    fiscal_year: int
    posting_date: date
    reference: str
    company_code: str
    total_amount: Decimal
    currency: str
    status: str = "posted"


class CashApplicationResult(BaseModel):
    application_id: str
    ar_item_id: str
    applied_amount: Decimal
    residual_amount: Decimal
    status: str = "applied"
