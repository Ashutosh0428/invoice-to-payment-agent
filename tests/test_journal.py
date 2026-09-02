from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from invoice_agent.agents.nodes.post import (
    ACCOUNTS_PAYABLE_ACCOUNT,
    GRIR_CLEARING_ACCOUNT,
    INPUT_VAT_ACCOUNT,
    NON_PO_EXPENSE_ACCOUNT,
    build_journal,
)


def _state(invoice, purchase_order=None, vendor=None):
    return {
        "run_id": "11111111-1111-1111-1111-111111111111",
        "invoice": invoice,
        "purchase_order": purchase_order,
        "vendor": vendor,
        "source_message_id": "msg-1",
        "document_sha256": "abc123def456",
    }


def test_journal_balances_and_uses_grir_for_po_invoices(clean_invoice, purchase_order):
    journal = build_journal(_state(clean_invoice, purchase_order))

    assert journal.is_balanced()
    accounts = {line.account for line in journal.lines}
    assert accounts == {GRIR_CLEARING_ACCOUNT, INPUT_VAT_ACCOUNT, ACCOUNTS_PAYABLE_ACCOUNT}

    debits = sum(line.debit for line in journal.lines)
    credits = sum(line.credit for line in journal.lines)
    assert debits == credits == Decimal("12768.70")


def test_non_po_invoice_books_to_the_expense_account(clean_invoice):
    journal = build_journal(_state(clean_invoice, None))

    debit_accounts = {line.account for line in journal.lines if line.debit > 0}
    assert NON_PO_EXPENSE_ACCOUNT in debit_accounts
    assert GRIR_CLEARING_ACCOUNT not in debit_accounts


def test_missing_net_is_derived_from_gross_and_tax(clean_invoice, purchase_order):
    invoice = deepcopy(clean_invoice)
    invoice.subtotal = None

    journal = build_journal(_state(invoice, purchase_order))

    assert journal.is_balanced()
    grir = next(line for line in journal.lines if line.account == GRIR_CLEARING_ACCOUNT)
    assert grir.debit == Decimal("10730.00")


def test_invoice_without_tax_still_balances(clean_invoice, purchase_order):
    invoice = deepcopy(clean_invoice)
    invoice.tax_amount = None
    invoice.subtotal = None

    journal = build_journal(_state(invoice, purchase_order))

    assert journal.is_balanced()
    assert all(line.account != INPUT_VAT_ACCOUNT for line in journal.lines)


def test_reference_carries_vendor_and_invoice_number(clean_invoice, purchase_order):
    journal = build_journal(_state(clean_invoice, purchase_order))

    assert "INV-2026-0871" in journal.reference
    assert journal.po_number == "PO-4500001"
    assert "msg-1" in journal.memo
