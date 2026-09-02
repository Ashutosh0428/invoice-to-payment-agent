from __future__ import annotations

from decimal import Decimal

import pytest

from invoice_agent.matching.engine import match_remittance
from invoice_agent.schemas.erp import ARItem
from invoice_agent.schemas.invoice import ExtractedRemittance, RemittanceAdvice


@pytest.fixture
def open_items() -> list[ARItem]:
    return [
        ARItem(
            ar_item_id="AR-90001",
            invoice_number="SI-2026-0431",
            customer_id="C-20001",
            customer_name="Globex Manufacturing SE",
            open_amount=Decimal("18400.00"),
            original_amount=Decimal("18400.00"),
        ),
        ARItem(
            ar_item_id="AR-90002",
            invoice_number="SI-2026-0448",
            customer_id="C-20001",
            customer_name="Globex Manufacturing SE",
            open_amount=Decimal("7250.00"),
            original_amount=Decimal("7250.00"),
        ),
    ]


def test_exact_remittance_clears_every_item(open_items, matching_config):
    remittance = ExtractedRemittance(
        remittance_number="REM-2026-0042",
        customer_name="Globex Manufacturing SE",
        total_paid=Decimal("25650.00"),
        advices=[
            RemittanceAdvice(invoice_number="SI-2026-0431", amount_paid=Decimal("18400.00")),
            RemittanceAdvice(invoice_number="SI-2026-0448", amount_paid=Decimal("7250.00")),
        ],
    )

    applications, unresolved, residual = match_remittance(remittance, open_items, matching_config)

    assert len(applications) == 2
    assert unresolved == []
    assert residual == Decimal("0.00")


def test_short_payment_is_reported_but_still_applied(open_items, matching_config):
    remittance = ExtractedRemittance(
        remittance_number="REM-2026-0043",
        total_paid=Decimal("18000.00"),
        advices=[RemittanceAdvice(invoice_number="SI-2026-0431", amount_paid=Decimal("18000.00"))],
    )

    applications, unresolved, residual = match_remittance(remittance, open_items, matching_config)

    assert len(applications) == 1
    assert any("short/over" in reason for reason in unresolved)
    assert residual == Decimal("0.00")


def test_unknown_invoice_reference_is_unresolved(open_items, matching_config):
    remittance = ExtractedRemittance(
        total_paid=Decimal("100.00"),
        advices=[RemittanceAdvice(invoice_number="SI-9999-9999", amount_paid=Decimal("100.00"))],
    )

    applications, unresolved, residual = match_remittance(remittance, open_items, matching_config)

    assert applications == []
    assert any("SI-9999-9999" in reason for reason in unresolved)
    assert residual == Decimal("100.00")


def test_declared_total_above_applied_lines_leaves_residual(open_items, matching_config):
    remittance = ExtractedRemittance(
        total_paid=Decimal("20000.00"),
        advices=[RemittanceAdvice(invoice_number="SI-2026-0431", amount_paid=Decimal("18400.00"))],
    )

    _, unresolved, residual = match_remittance(remittance, open_items, matching_config)

    assert residual == Decimal("1600.00")
    assert any("residual" in reason for reason in unresolved)
