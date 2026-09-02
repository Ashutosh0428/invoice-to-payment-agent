from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz, process

from invoice_agent.core.config import get_config
from invoice_agent.rag.index import get_knowledge_base
from invoice_agent.schemas.common import ExceptionType
from invoice_agent.schemas.erp import Vendor


@dataclass(slots=True)
class VendorResolution:
    vendor: Vendor | None
    score: float
    method: str
    candidates: list[str]

    @property
    def resolved(self) -> bool:
        return self.vendor is not None


def resolve_vendor(
    vendors: list[Vendor],
    name: str | None,
    tax_id: str | None = None,
    iban: str | None = None,
) -> VendorResolution:
    """Deterministic identifiers first, then fuzzy name, then semantic search.

    Tax id and IBAN are exact keys that survive OCR damage to the name line, so they outrank
    any similarity score; falling straight to embeddings would let a close-sounding name beat
    an exact tax identifier.
    """
    cfg = get_config().matching

    if tax_id:
        needle = tax_id.replace(" ", "").upper()
        exact = next(
            (v for v in vendors if (v.tax_id or "").replace(" ", "").upper() == needle), None
        )
        if exact is not None:
            return VendorResolution(exact, 100.0, "tax_id", [])

    if iban:
        needle = iban.replace(" ", "").upper()
        exact = next(
            (v for v in vendors if (v.iban or "").replace(" ", "").upper() == needle), None
        )
        if exact is not None:
            return VendorResolution(exact, 100.0, "iban", [])

    if not name:
        return VendorResolution(None, 0.0, "none", [])

    lookup: dict[str, Vendor] = {}
    for vendor in vendors:
        lookup[vendor.name.lower()] = vendor
        for alias in vendor.aliases:
            lookup[alias.lower()] = vendor

    best = process.extractOne(name.lower(), lookup.keys(), scorer=fuzz.token_sort_ratio)
    if best is not None and best[1] >= cfg.vendor_match_threshold:
        return VendorResolution(lookup[best[0]], float(best[1]), "fuzzy_name", [best[0]])

    hits = get_knowledge_base().search_vendors(name, top_k=3)
    by_id = {vendor.vendor_id: vendor for vendor in vendors}
    for hit in hits:
        vendor_id = str(hit["metadata"].get("vendor_id", ""))
        if vendor_id in by_id and hit["score"] >= 0.75:
            return VendorResolution(
                by_id[vendor_id],
                float(hit["score"]) * 100,
                "semantic",
                [h["metadata"].get("name", "") for h in hits],
            )

    candidates = [best[0]] if best else [h["metadata"].get("name", "") for h in hits]
    return VendorResolution(None, float(best[1]) if best else 0.0, "unresolved", candidates)


def retrieve_policy(exception_types: list[ExceptionType], top_k: int = 3) -> list[str]:
    """Pull the AP policy passages that govern the raised exceptions, so the guidance shown to
    the approver comes from written policy rather than model improvisation."""
    if not exception_types:
        return []
    query = " ".join(e.value.replace("_", " ") for e in exception_types)
    hits = get_knowledge_base().search_policy(query, top_k=top_k)
    return [hit["text"] for hit in hits]
