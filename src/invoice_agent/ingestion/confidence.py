from __future__ import annotations

from decimal import Decimal

from invoice_agent.schemas.invoice import ExtractedInvoice

_REQUIRED_FIELDS = (
    "invoice_number",
    "invoice_date",
    "vendor_name",
    "total_amount",
    "currency",
)
_ARITHMETIC_TOLERANCE = Decimal("0.05")


def score_extraction(invoice: ExtractedInvoice) -> tuple[float, dict[str, float]]:
    """Blend the model's self-report with checks it cannot fake.

    A model asked to rate its own extraction is optimistic and uncalibrated. Arithmetic
    consistency - do the line totals sum to the subtotal, does subtotal plus tax equal the
    gross - is objective evidence that the numbers came off the page correctly, so it carries
    the larger weight.
    """
    signals: dict[str, float] = {}

    present = sum(1 for name in _REQUIRED_FIELDS if getattr(invoice, name) is not None)
    signals["required_fields"] = present / len(_REQUIRED_FIELDS)

    signals["has_line_items"] = 1.0 if invoice.line_items else 0.0
    signals["has_po_reference"] = 1.0 if invoice.purchase_order_number else 0.0

    if invoice.subtotal is not None and invoice.line_items:
        delta = abs(invoice.computed_subtotal() - invoice.subtotal)
        signals["lines_sum_to_subtotal"] = 1.0 if delta <= _ARITHMETIC_TOLERANCE else 0.0
    else:
        signals["lines_sum_to_subtotal"] = 0.5

    if (
        invoice.subtotal is not None
        and invoice.tax_amount is not None
        and invoice.total_amount is not None
    ):
        delta = abs(invoice.subtotal + invoice.tax_amount - invoice.total_amount)
        signals["totals_reconcile"] = 1.0 if delta <= _ARITHMETIC_TOLERANCE else 0.0
    else:
        signals["totals_reconcile"] = 0.5

    line_price_ok = [
        1.0 if abs(item.recomputed_total() - item.line_total) <= _ARITHMETIC_TOLERANCE else 0.0
        for item in invoice.line_items
        if item.line_total
    ]
    signals["line_arithmetic"] = sum(line_price_ok) / len(line_price_ok) if line_price_ok else 0.5

    signals["model_self_report"] = max(0.0, min(1.0, invoice.confidence))

    weights = {
        "required_fields": 0.25,
        "totals_reconcile": 0.20,
        "lines_sum_to_subtotal": 0.15,
        "line_arithmetic": 0.15,
        "has_line_items": 0.05,
        "has_po_reference": 0.05,
        "model_self_report": 0.15,
    }
    score = sum(signals[name] * weight for name, weight in weights.items())
    return round(score, 4), signals
