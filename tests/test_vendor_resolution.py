from __future__ import annotations

from invoice_agent.rag.retriever import resolve_vendor


def test_exact_tax_id_beats_a_damaged_name(vendors):
    resolution = resolve_vendor(vendors, "C0nt0so Supp1ies Gmb#", tax_id="DE811907980")

    assert resolution.resolved
    assert resolution.vendor.vendor_id == "V-10001"
    assert resolution.method == "tax_id"


def test_iban_resolves_when_name_and_tax_id_are_missing(vendors):
    resolution = resolve_vendor(vendors, None, iban="DE02120300000000202051")

    assert resolution.vendor.vendor_id == "V-10002"
    assert resolution.method == "iban"


def test_alias_resolves_by_fuzzy_name(vendors):
    resolution = resolve_vendor(vendors, "Contoso Supplies")

    assert resolution.resolved
    assert resolution.vendor.vendor_id == "V-10001"
    assert resolution.method == "fuzzy_name"


def test_unknown_vendor_stays_unresolved(vendors):
    resolution = resolve_vendor(vendors, "Litware Marketing Ltd")

    assert not resolution.resolved
    assert resolution.method == "unresolved"


def test_no_identifiers_at_all_returns_none(vendors):
    resolution = resolve_vendor(vendors, None)

    assert not resolution.resolved
    assert resolution.method == "none"
