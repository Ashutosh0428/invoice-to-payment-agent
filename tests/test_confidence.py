from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from invoice_agent.ingestion.confidence import score_extraction
from invoice_agent.schemas.invoice import ExtractedInvoice


def test_arithmetically_consistent_invoice_scores_high(clean_invoice):
    score, signals = score_extraction(clean_invoice)

    assert score > 0.90
    assert signals["totals_reconcile"] == 1.0
    assert signals["lines_sum_to_subtotal"] == 1.0
    assert signals["line_arithmetic"] == 1.0


def test_totals_that_do_not_reconcile_drop_the_score(clean_invoice):
    broken = deepcopy(clean_invoice)
    broken.total_amount = Decimal("99999.00")

    score, signals = score_extraction(broken)

    assert signals["totals_reconcile"] == 0.0
    assert score < 0.90


def test_line_arithmetic_failure_is_detected(clean_invoice):
    broken = deepcopy(clean_invoice)
    broken.line_items[0].line_total = Decimal("1.00")

    _, signals = score_extraction(broken)

    assert signals["line_arithmetic"] == 0.5
    assert signals["lines_sum_to_subtotal"] == 0.0


def test_empty_extraction_scores_low():
    score, signals = score_extraction(ExtractedInvoice())

    assert score < 0.40
    assert signals["required_fields"] == 0.0
    assert signals["has_line_items"] == 0.0


def test_model_self_report_cannot_rescue_bad_arithmetic(clean_invoice):
    """A model claiming 1.0 confidence on numbers that do not add up must not clear the gate."""
    optimistic = deepcopy(clean_invoice)
    optimistic.confidence = 1.0
    optimistic.total_amount = Decimal("500.00")
    optimistic.subtotal = Decimal("400.00")
    optimistic.tax_amount = Decimal("50.00")

    score, _ = score_extraction(optimistic)

    assert score < 0.75


def test_malformed_field_confidence_does_not_discard_the_extraction():
    """A small model returns null for an absent field and nests a map under line_items.

    That annotation is not what gates posting, so it is cleaned rather than fatal.
    """
    invoice = ExtractedInvoice.model_validate(
        {
            "invoice_number": "INV-2026-0999",
            "vendor_name": "Contoso Industrial Supplies GmbH",
            "currency": "EUR",
            "total_amount": "1190.00",
            "field_confidence": {
                "invoice_number": 0.97,
                "subtotal": None,
                "vendor_name": "0.88",
                "total_amount": 1.4,
                "line_items": {"0": {"line_number": 1.0, "tax_rate": None}},
            },
        }
    )

    assert invoice.invoice_number == "INV-2026-0999"
    assert invoice.total_amount == Decimal("1190.00")
    assert invoice.field_confidence == {
        "invoice_number": 0.97,
        "vendor_name": 0.88,
        "total_amount": 1.0,
    }


def test_field_confidence_that_is_not_a_mapping_is_dropped():
    invoice = ExtractedInvoice.model_validate(
        {"invoice_number": "INV-2026-1000", "field_confidence": ["0.9", "0.8"]}
    )

    assert invoice.field_confidence == {}
