from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_MONEY = Field(default=Decimal("0"), decimal_places=2)


class InvoiceLineItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    line_number: int | None = None
    po_line_number: int | None = None
    description: str = ""
    material_code: str | None = None
    quantity: Decimal = Decimal("0")
    unit: str | None = None
    unit_price: Decimal = Decimal("0")
    line_total: Decimal = Decimal("0")
    tax_rate: Decimal | None = None

    @field_validator("quantity", "unit_price", "line_total", mode="before")
    @classmethod
    def _coerce_money(cls, v: object) -> Decimal:
        if v is None or v == "":
            return Decimal("0")
        if isinstance(v, Decimal):
            return v
        text = str(v).replace(",", "").replace("€", "").replace("$", "").strip()
        try:
            return Decimal(text)
        except Exception:
            return Decimal("0")

    def recomputed_total(self) -> Decimal:
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))


class ExtractedInvoice(BaseModel):
    """What the LLM is asked to return for an AP invoice. Every field is nullable on purpose:
    a missing field must surface as an exception, not as a silent zero."""

    model_config = ConfigDict(populate_by_name=True)

    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    purchase_order_number: str | None = None
    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    vendor_iban: str | None = None
    vendor_id: str | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    payment_terms: str | None = None
    line_items: list[InvoiceLineItem] = Field(default_factory=list)

    confidence: float = 0.0
    field_confidence: dict[str, float] = Field(default_factory=dict)
    extraction_notes: str | None = None

    @field_validator("subtotal", "tax_amount", "total_amount", mode="before")
    @classmethod
    def _coerce_optional_money(cls, v: object) -> Decimal | None:
        if v is None or v == "":
            return None
        if isinstance(v, Decimal):
            return v
        text = str(v).replace(",", "").replace("€", "").replace("$", "").strip()
        try:
            return Decimal(text)
        except Exception:
            return None

    @field_validator("field_confidence", mode="before")
    @classmethod
    def _coerce_field_confidence(cls, v: object) -> dict[str, float]:
        """Keep the scores that are scores and drop the rest.

        Smaller models routinely return null for a field they did not populate, or nest a
        per-line map under line_items. field_confidence is an annotation - score_extraction
        derives the confidence that actually gates posting from arithmetic the model cannot
        fake - so a malformed annotation must not discard an otherwise good extraction.
        """
        if not isinstance(v, dict):
            return {}
        scores: dict[str, float] = {}
        for name, score in v.items():
            if isinstance(score, bool) or not isinstance(score, int | float | str):
                continue
            try:
                scores[str(name)] = max(0.0, min(1.0, float(score)))
            except (TypeError, ValueError):
                continue
        return scores

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, v: object) -> str | None:
        if not v:
            return None
        text = str(v).strip().upper()
        symbol_map = {"€": "EUR", "$": "USD", "£": "GBP", "₹": "INR"}
        return symbol_map.get(text, text[:3])

    @field_validator("purchase_order_number", "invoice_number", mode="before")
    @classmethod
    def _strip_identifier(cls, v: object) -> str | None:
        if v is None:
            return None
        text = str(v).strip()
        return text or None

    def fingerprint(self) -> str:
        """Vendor + invoice number + total. The industry-standard duplicate key; deliberately
        excludes dates because vendors re-issue the same invoice with a new print date."""
        parts = [
            (self.vendor_name or "").strip().lower(),
            (self.invoice_number or "").strip().lower(),
            str(self.total_amount or Decimal("0")),
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    def computed_subtotal(self) -> Decimal:
        return sum((li.line_total for li in self.line_items), Decimal("0")).quantize(
            Decimal("0.01")
        )


class RemittanceAdvice(BaseModel):
    invoice_number: str | None = None
    amount_paid: Decimal | None = None
    deduction: Decimal | None = None
    reference: str | None = None


class ExtractedRemittance(BaseModel):
    """AR mirror of ExtractedInvoice: what arrived from a customer paying us."""

    model_config = ConfigDict(populate_by_name=True)

    remittance_number: str | None = None
    payment_date: date | None = None
    customer_name: str | None = None
    customer_id: str | None = None
    currency: str | None = None
    total_paid: Decimal | None = None
    bank_reference: str | None = None
    advices: list[RemittanceAdvice] = Field(default_factory=list)

    confidence: float = 0.0
    extraction_notes: str | None = None

    @field_validator("total_paid", mode="before")
    @classmethod
    def _coerce_money(cls, v: object) -> Decimal | None:
        if v is None or v == "":
            return None
        if isinstance(v, Decimal):
            return v
        try:
            return Decimal(str(v).replace(",", "").strip())
        except Exception:
            return None
