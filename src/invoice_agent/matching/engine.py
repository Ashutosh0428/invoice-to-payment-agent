"""Deterministic 2-way / 3-way matching.

The LLM extracts fields; this module decides. Every verdict is Decimal arithmetic against
configured tolerances, so a match is reproducible and explainable to an auditor without
re-running a model.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from rapidfuzz import fuzz

from invoice_agent.core.config import MatchingConfig, get_config
from invoice_agent.schemas.common import ExceptionType, MatchOutcome, MatchType
from invoice_agent.schemas.erp import ARItem, GoodsReceipt, PurchaseOrder, PurchaseOrderLine
from invoice_agent.schemas.invoice import ExtractedInvoice, ExtractedRemittance, InvoiceLineItem
from invoice_agent.schemas.matching import LineMatch, MatchReport, Variance

_CENTS = Decimal("0.01")
_DESCRIPTION_MATCH_THRESHOLD = 75


def _q(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _build_variance(
    field: str,
    invoice_value: Decimal,
    reference_value: Decimal,
    tolerance_abs: Decimal,
    tolerance_pct: Decimal,
    exception_type: ExceptionType,
) -> Variance:
    delta = invoice_value - reference_value
    abs_delta = abs(delta)
    if reference_value != 0:
        pct_delta = _q(abs_delta / abs(reference_value) * Decimal("100"))
    else:
        pct_delta = Decimal("100.00") if abs_delta > 0 else Decimal("0.00")

    within = abs_delta <= tolerance_abs or pct_delta <= tolerance_pct
    return Variance(
        field=field,
        invoice_value=_q(invoice_value),
        reference_value=_q(reference_value),
        absolute_delta=_q(delta),
        percent_delta=pct_delta,
        tolerance_abs=tolerance_abs,
        tolerance_pct=tolerance_pct,
        within_tolerance=within,
        exception_type=None if within else exception_type,
    )


def _price_variance(
    invoice_line: InvoiceLineItem,
    po_line: PurchaseOrderLine,
    cfg: MatchingConfig,
) -> Variance:
    """Percentage tolerance on the unit price, absolute tolerance on the extended line value.

    Applying the absolute floor per unit would make it 34% of a 14.50 part and wave through
    hundreds of euros across a large quantity; the cash at risk is price delta times quantity,
    so that is what the absolute limit governs.
    """
    delta = invoice_line.unit_price - po_line.unit_price
    abs_delta = abs(delta)
    reference = po_line.unit_price
    if reference != 0:
        pct_delta = _q(abs_delta / abs(reference) * Decimal("100"))
    else:
        pct_delta = Decimal("100.00") if abs_delta > 0 else Decimal("0.00")

    quantity = invoice_line.quantity or po_line.quantity_ordered
    value_impact = _q(abs_delta * quantity)
    within = pct_delta <= cfg.price_tolerance_pct or value_impact <= cfg.price_tolerance_abs

    return Variance(
        field="unit_price",
        invoice_value=_q(invoice_line.unit_price),
        reference_value=_q(reference),
        absolute_delta=_q(delta),
        percent_delta=pct_delta,
        tolerance_abs=cfg.price_tolerance_abs,
        tolerance_pct=cfg.price_tolerance_pct,
        within_tolerance=within,
        exception_type=None if within else ExceptionType.PRICE_VARIANCE,
        value_impact=value_impact,
    )


def _received_quantities(goods_receipts: list[GoodsReceipt]) -> dict[int, Decimal]:
    totals: dict[int, Decimal] = {}
    for gr in goods_receipts:
        for line in gr.lines:
            totals[line.po_line_number] = totals.get(line.po_line_number, Decimal("0")) + (
                line.quantity_received
            )
    return totals


def _pair_line(
    invoice_line: InvoiceLineItem,
    po_lines: list[PurchaseOrderLine],
    consumed: set[int],
) -> PurchaseOrderLine | None:
    """PO line number, then material code, then fuzzy description. Real invoices frequently
    carry none of the first two, and hard-joining on them manufactures false exceptions."""
    if invoice_line.po_line_number is not None:
        exact = next(
            (
                line
                for line in po_lines
                if line.line_number == invoice_line.po_line_number
                and line.line_number not in consumed
            ),
            None,
        )
        if exact is not None:
            return exact

    if invoice_line.material_code:
        code = invoice_line.material_code.strip().upper()
        by_code = next(
            (
                line
                for line in po_lines
                if (line.material_code or "").strip().upper() == code
                and line.line_number not in consumed
            ),
            None,
        )
        if by_code is not None:
            return by_code

    if invoice_line.description:
        scored = [
            (fuzz.token_set_ratio(invoice_line.description.lower(), line.description.lower()), line)
            for line in po_lines
            if line.line_number not in consumed
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if scored and scored[0][0] >= _DESCRIPTION_MATCH_THRESHOLD:
            return scored[0][1]

    return None


def _match_lines(
    invoice: ExtractedInvoice,
    po: PurchaseOrder,
    received: dict[int, Decimal],
    three_way: bool,
    cfg: MatchingConfig,
) -> tuple[list[LineMatch], list[ExceptionType], list[str]]:
    line_matches: list[LineMatch] = []
    exceptions: list[ExceptionType] = []
    reasons: list[str] = []
    consumed: set[int] = set()

    for index, invoice_line in enumerate(invoice.line_items, start=1):
        po_line = _pair_line(invoice_line, po.lines, consumed)
        if po_line is None:
            line_matches.append(
                LineMatch(
                    invoice_line_number=invoice_line.line_number or index,
                    description=invoice_line.description,
                    matched=False,
                    quantity_invoiced=invoice_line.quantity,
                    exceptions=[ExceptionType.LINE_NOT_ON_PO],
                )
            )
            exceptions.append(ExceptionType.LINE_NOT_ON_PO)
            reasons.append(
                f"Invoice line {invoice_line.line_number or index} "
                f"('{invoice_line.description[:60]}') has no counterpart on {po.po_number}"
            )
            continue

        consumed.add(po_line.line_number)
        quantity_received = received.get(po_line.line_number, po_line.quantity_received)

        price_variance = _price_variance(invoice_line, po_line, cfg)
        quantity_variance = _build_variance(
            "quantity",
            invoice_line.quantity,
            po_line.quantity_ordered,
            cfg.quantity_tolerance_abs,
            cfg.quantity_tolerance_pct,
            ExceptionType.QUANTITY_VARIANCE,
        )

        variances = [price_variance, quantity_variance]
        line_exceptions: list[ExceptionType] = []

        if not price_variance.within_tolerance:
            line_exceptions.append(ExceptionType.PRICE_VARIANCE)
            reasons.append(
                f"PO line {po_line.line_number}: unit price {price_variance.invoice_value} vs "
                f"{price_variance.reference_value} ({price_variance.percent_delta}% over "
                f"{cfg.price_tolerance_pct}% tolerance), line impact "
                f"{price_variance.value_impact}"
            )
        if not quantity_variance.within_tolerance:
            line_exceptions.append(ExceptionType.QUANTITY_VARIANCE)
            reasons.append(
                f"PO line {po_line.line_number}: invoiced {quantity_variance.invoice_value} vs "
                f"ordered {quantity_variance.reference_value}"
            )

        if three_way:
            receipt_variance = _build_variance(
                "quantity_received",
                invoice_line.quantity,
                quantity_received,
                cfg.quantity_tolerance_abs,
                cfg.quantity_tolerance_pct,
                ExceptionType.QUANTITY_VARIANCE,
            )
            variances.append(receipt_variance)
            if invoice_line.quantity > quantity_received:
                line_exceptions.append(ExceptionType.MISSING_GOODS_RECEIPT)
                reasons.append(
                    f"PO line {po_line.line_number}: invoiced {invoice_line.quantity} but only "
                    f"{quantity_received} goods-received"
                )

        line_matches.append(
            LineMatch(
                invoice_line_number=invoice_line.line_number or index,
                po_line_number=po_line.line_number,
                description=invoice_line.description or po_line.description,
                matched=not line_exceptions,
                quantity_invoiced=invoice_line.quantity,
                quantity_ordered=po_line.quantity_ordered,
                quantity_received=quantity_received,
                variances=variances,
                exceptions=line_exceptions,
            )
        )
        exceptions.extend(line_exceptions)

    return line_matches, exceptions, reasons


def match_invoice(
    invoice: ExtractedInvoice,
    po: PurchaseOrder | None,
    goods_receipts: list[GoodsReceipt] | None = None,
    config: MatchingConfig | None = None,
) -> MatchReport:
    cfg = config or get_config().matching
    goods_receipts = goods_receipts or []

    if po is None:
        return MatchReport(
            match_type=MatchType.NON_PO,
            outcome=MatchOutcome.EXCEPTION,
            exceptions=[ExceptionType.MISSING_PO],
            reasons=[
                "No purchase order reference could be resolved; non-PO invoices require "
                "manual coding and approval."
            ],
            matched_amount=invoice.total_amount or Decimal("0"),
            confidence=invoice.confidence,
        )

    three_way = po.goods_receipt_required
    match_type = MatchType.THREE_WAY if three_way else MatchType.TWO_WAY
    exceptions: list[ExceptionType] = []
    reasons: list[str] = []
    header_variances: list[Variance] = []

    if three_way and not goods_receipts:
        exceptions.append(ExceptionType.MISSING_GOODS_RECEIPT)
        reasons.append(f"{po.po_number} requires a goods receipt but none was posted in the ERP")

    invoice_currency = (invoice.currency or po.currency).upper()
    if invoice_currency != po.currency.upper():
        exceptions.append(ExceptionType.CURRENCY_MISMATCH)
        reasons.append(
            f"Invoice currency {invoice_currency} does not match PO currency {po.currency}"
        )

    total_variance = _build_variance(
        "total_amount",
        invoice.total_amount or Decimal("0"),
        po.gross_amount,
        cfg.total_tolerance_abs,
        cfg.total_tolerance_pct,
        ExceptionType.TOTAL_VARIANCE,
    )
    header_variances.append(total_variance)
    if not total_variance.within_tolerance:
        exceptions.append(ExceptionType.TOTAL_VARIANCE)
        reasons.append(
            f"Invoice total {total_variance.invoice_value} vs PO gross "
            f"{total_variance.reference_value} "
            f"(delta {total_variance.absolute_delta}, {total_variance.percent_delta}%)"
        )

    if invoice.tax_amount is not None and po.tax_amount:
        tax_variance = _build_variance(
            "tax_amount",
            invoice.tax_amount,
            po.tax_amount,
            cfg.tax_tolerance_abs,
            cfg.total_tolerance_pct,
            ExceptionType.TAX_VARIANCE,
        )
        header_variances.append(tax_variance)
        if not tax_variance.within_tolerance:
            exceptions.append(ExceptionType.TAX_VARIANCE)
            reasons.append(
                f"Tax {tax_variance.invoice_value} vs PO tax {tax_variance.reference_value}"
            )

    received = _received_quantities(goods_receipts)
    line_matches, line_exceptions, line_reasons = _match_lines(
        invoice, po, received, three_way, cfg
    )
    exceptions.extend(line_exceptions)
    reasons.extend(line_reasons)

    if invoice.confidence < cfg.min_extraction_confidence:
        exceptions.append(ExceptionType.LOW_CONFIDENCE_EXTRACTION)
        reasons.append(
            f"Extraction confidence {invoice.confidence:.2f} below "
            f"{cfg.min_extraction_confidence:.2f} threshold"
        )

    total_amount = invoice.total_amount or Decimal("0")
    if total_amount > cfg.auto_post_ceiling:
        exceptions.append(ExceptionType.OVER_AUTO_POST_CEILING)
        reasons.append(
            f"Invoice total {_q(total_amount)} exceeds the auto-post ceiling "
            f"{cfg.auto_post_ceiling}; policy requires human approval"
        )

    deduped = list(dict.fromkeys(exceptions))
    if deduped:
        outcome = MatchOutcome.EXCEPTION
    elif any(v.absolute_delta != 0 for v in header_variances):
        outcome = MatchOutcome.MATCHED_WITHIN_TOLERANCE
        reasons.append("All variances fall inside configured tolerances")
    else:
        outcome = MatchOutcome.MATCHED
        reasons.append("Exact match against purchase order and goods receipt")

    return MatchReport(
        match_type=match_type,
        outcome=outcome,
        po_number=po.po_number,
        gr_numbers=[gr.gr_number for gr in goods_receipts],
        vendor_id=po.vendor_id,
        header_variances=header_variances,
        line_matches=line_matches,
        exceptions=deduped,
        reasons=reasons,
        matched_amount=_q(total_amount),
        confidence=invoice.confidence,
    )


def match_remittance(
    remittance: ExtractedRemittance,
    open_items: list[ARItem],
    config: MatchingConfig | None = None,
) -> tuple[list[tuple[ARItem, Decimal]], list[str], Decimal]:
    """Pair each remittance advice line to an open receivable.

    Returns the applications to post, unresolved reasons, and the residual that could not be
    applied. Residual above tolerance becomes an UNAPPLIED_REMITTANCE exception upstream.
    """
    cfg = config or get_config().matching
    by_invoice = {item.invoice_number.strip().upper(): item for item in open_items}
    applications: list[tuple[ARItem, Decimal]] = []
    unresolved: list[str] = []
    applied_total = Decimal("0")

    for advice in remittance.advices:
        if not advice.invoice_number:
            unresolved.append("Remittance line without an invoice reference")
            continue
        item = by_invoice.get(advice.invoice_number.strip().upper())
        if item is None:
            unresolved.append(f"No open receivable found for invoice {advice.invoice_number}")
            continue

        amount = advice.amount_paid or item.open_amount
        delta = abs(amount - item.open_amount)
        if delta > cfg.remittance_tolerance_abs:
            unresolved.append(
                f"Invoice {advice.invoice_number}: paid {_q(amount)} against open "
                f"{_q(item.open_amount)} (short/over by {_q(delta)})"
            )
        applications.append((item, _q(amount)))
        applied_total += amount

    declared_total = remittance.total_paid or applied_total
    residual = _q(declared_total - applied_total)
    if abs(residual) > cfg.remittance_tolerance_abs:
        unresolved.append(
            f"Remittance declares {_q(declared_total)} but only {_q(applied_total)} could be "
            f"applied (residual {residual})"
        )

    return applications, unresolved, residual
