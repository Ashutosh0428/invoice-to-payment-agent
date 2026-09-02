from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from invoice_agent.matching.engine import match_invoice
from invoice_agent.schemas.common import ExceptionType, MatchOutcome, MatchType


def test_clean_three_way_match_posts(
    clean_invoice, purchase_order, goods_receipts, matching_config
):
    report = match_invoice(clean_invoice, purchase_order, goods_receipts, matching_config)

    assert report.outcome is MatchOutcome.MATCHED
    assert report.match_type is MatchType.THREE_WAY
    assert report.exceptions == []
    assert report.is_postable
    assert report.gr_numbers == ["GR-5000001"]
    assert all(line.matched for line in report.line_matches)


def test_two_way_match_when_no_goods_receipt_required(
    clean_invoice, purchase_order, matching_config
):
    po = deepcopy(purchase_order)
    po.goods_receipt_required = False

    report = match_invoice(clean_invoice, po, [], matching_config)

    assert report.match_type is MatchType.TWO_WAY
    assert report.outcome is MatchOutcome.MATCHED
    assert report.exceptions == []


def test_price_above_tolerance_raises_price_variance(
    clean_invoice, purchase_order, goods_receipts, matching_config
):
    invoice = deepcopy(clean_invoice)
    invoice.line_items[0].unit_price = Decimal("15.50")
    invoice.line_items[0].line_total = Decimal("7750.00")
    invoice.subtotal = Decimal("11230.00")
    invoice.tax_amount = Decimal("2133.70")
    invoice.total_amount = Decimal("13363.70")

    report = match_invoice(invoice, purchase_order, goods_receipts, matching_config)

    assert report.outcome is MatchOutcome.EXCEPTION
    assert ExceptionType.PRICE_VARIANCE in report.exceptions
    assert not report.is_postable


def test_price_inside_tolerance_still_posts(
    clean_invoice, purchase_order, goods_receipts, matching_config
):
    invoice = deepcopy(clean_invoice)
    invoice.line_items[0].unit_price = Decimal("14.60")
    invoice.line_items[0].line_total = Decimal("7300.00")
    invoice.subtotal = Decimal("10780.00")
    invoice.tax_amount = Decimal("2048.20")
    invoice.total_amount = Decimal("12828.20")

    report = match_invoice(invoice, purchase_order, goods_receipts, matching_config)

    assert report.outcome is MatchOutcome.MATCHED_WITHIN_TOLERANCE
    assert report.is_postable
    assert ExceptionType.PRICE_VARIANCE not in report.exceptions


def test_absolute_tolerance_is_measured_on_line_value_not_unit_price(
    clean_invoice, purchase_order, goods_receipts, matching_config
):
    """A percentage breach with negligible cash impact stays inside the absolute floor."""
    invoice = deepcopy(clean_invoice)
    invoice.line_items[1].quantity = Decimal("1")
    invoice.line_items[1].unit_price = Decimal("6.90")

    report = match_invoice(invoice, purchase_order, goods_receipts, matching_config)

    line = next(m for m in report.line_matches if m.po_line_number == 20)
    price_variance = next(v for v in line.variances if v.field == "unit_price")
    assert price_variance.percent_delta > matching_config.price_tolerance_pct
    assert price_variance.value_impact == Decimal("4.00")
    assert price_variance.within_tolerance
    assert ExceptionType.PRICE_VARIANCE not in report.exceptions


def test_small_per_unit_drift_over_large_quantity_is_caught(
    clean_invoice, purchase_order, goods_receipts, matching_config
):
    """0.10 per unit is under the 5.00 floor but 1200 units makes it 120.00 of leakage."""
    invoice = deepcopy(clean_invoice)
    invoice.line_items[1].unit_price = Decimal("3.00")

    report = match_invoice(invoice, purchase_order, goods_receipts, matching_config)

    line = next(m for m in report.line_matches if m.po_line_number == 20)
    price_variance = next(v for v in line.variances if v.field == "unit_price")
    assert price_variance.value_impact == Decimal("120.00")
    assert not price_variance.within_tolerance
    assert ExceptionType.PRICE_VARIANCE in report.exceptions


def test_invoiced_more_than_received_raises_goods_receipt_exception(
    clean_invoice, purchase_order, matching_config
):
    report = match_invoice(clean_invoice, purchase_order, [], matching_config)

    assert ExceptionType.MISSING_GOODS_RECEIPT in report.exceptions
    assert report.outcome is MatchOutcome.EXCEPTION


def test_quantity_over_receipt_flags_shortfall(
    clean_invoice, purchase_order, goods_receipts, matching_config
):
    receipts = deepcopy(goods_receipts)
    receipts[0].lines[0].quantity_received = Decimal("400")

    report = match_invoice(clean_invoice, purchase_order, receipts, matching_config)

    assert ExceptionType.MISSING_GOODS_RECEIPT in report.exceptions
    line = next(m for m in report.line_matches if m.po_line_number == 10)
    assert line.quantity_received == Decimal("400")


def test_missing_po_returns_non_po_match(clean_invoice, matching_config):
    report = match_invoice(clean_invoice, None, [], matching_config)

    assert report.match_type is MatchType.NON_PO
    assert report.exceptions == [ExceptionType.MISSING_PO]
    assert not report.is_postable


def test_total_above_ceiling_blocks_straight_through(
    clean_invoice, purchase_order, goods_receipts, matching_config
):
    invoice = deepcopy(clean_invoice)
    invoice.total_amount = Decimal("30000.00")
    po = deepcopy(purchase_order)
    po.gross_amount = Decimal("30000.00")

    report = match_invoice(invoice, po, goods_receipts, matching_config)

    assert ExceptionType.OVER_AUTO_POST_CEILING in report.exceptions
    assert not report.is_postable


def test_currency_mismatch_is_an_exception(
    clean_invoice, purchase_order, goods_receipts, matching_config
):
    invoice = deepcopy(clean_invoice)
    invoice.currency = "USD"

    report = match_invoice(invoice, purchase_order, goods_receipts, matching_config)

    assert ExceptionType.CURRENCY_MISMATCH in report.exceptions


def test_line_absent_from_po_is_flagged(
    clean_invoice, purchase_order, goods_receipts, matching_config
):
    invoice = deepcopy(clean_invoice)
    invoice.line_items.append(
        type(invoice.line_items[0])(
            line_number=3,
            description="Expedited freight surcharge",
            quantity=Decimal("1"),
            unit_price=Decimal("250.00"),
            line_total=Decimal("250.00"),
        )
    )

    report = match_invoice(invoice, purchase_order, goods_receipts, matching_config)

    assert ExceptionType.LINE_NOT_ON_PO in report.exceptions
    unmatched = [m for m in report.line_matches if not m.matched]
    assert any(m.po_line_number is None for m in unmatched)


def test_low_confidence_extraction_blocks_posting(
    clean_invoice, purchase_order, goods_receipts, matching_config
):
    invoice = deepcopy(clean_invoice)
    invoice.confidence = 0.40

    report = match_invoice(invoice, purchase_order, goods_receipts, matching_config)

    assert ExceptionType.LOW_CONFIDENCE_EXTRACTION in report.exceptions


def test_lines_pair_by_material_code_when_position_missing(
    clean_invoice, purchase_order, goods_receipts, matching_config
):
    invoice = deepcopy(clean_invoice)
    for item in invoice.line_items:
        item.po_line_number = None

    report = match_invoice(invoice, purchase_order, goods_receipts, matching_config)

    assert report.outcome is MatchOutcome.MATCHED
    assert {m.po_line_number for m in report.line_matches} == {10, 20}


def test_lines_pair_by_description_when_no_identifiers(
    clean_invoice, purchase_order, goods_receipts, matching_config
):
    invoice = deepcopy(clean_invoice)
    for item in invoice.line_items:
        item.po_line_number = None
        item.material_code = None

    report = match_invoice(invoice, purchase_order, goods_receipts, matching_config)

    assert {m.po_line_number for m in report.line_matches} == {10, 20}
    assert report.outcome is MatchOutcome.MATCHED
