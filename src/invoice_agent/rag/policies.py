"""AP/AR policy corpus indexed into pgvector and retrieved to ground exception guidance.

Kept in code rather than a document store so the container starts with a usable knowledge base
and the evaluation harness has a fixed ground truth to score retrieval against.
"""

from __future__ import annotations

AP_POLICIES: list[dict[str, str]] = [
    {
        "title": "Three-way match requirement",
        "text": (
            "Invoices referencing a purchase order that requires goods receipt must pass a "
            "three-way match: invoice quantity and unit price must agree with the purchase "
            "order, and the invoiced quantity must not exceed the quantity goods-received. "
            "If no goods receipt exists, the invoice is parked as a GR/IR exception and routed "
            "to the receiving cost centre owner, not to Accounts Payable."
        ),
    },
    {
        "title": "Two-way match requirement",
        "text": (
            "Service purchase orders flagged as not requiring goods receipt are matched two-way: "
            "invoice against purchase order only. Price and quantity tolerances still apply. "
            "Service entry confirmation is captured by the requisitioner rather than a GR document."
        ),
    },
    {
        "title": "Price and quantity tolerances",
        "text": (
            "Unit price may deviate from the purchase order by up to 2 percent, or by any "
            "amount whose total line impact - the price difference multiplied by the invoiced "
            "quantity - stays within 5.00 EUR. The absolute limit is applied to the extended "
            "line value rather than the unit price, so a small per-unit difference across a "
            "large quantity is still caught. Quantity must match the ordered quantity exactly; "
            "there "
            "is no quantity tolerance for goods. Invoice gross total may deviate from the "
            "purchase order gross by up to 1 percent or 10.00 EUR. Tax may deviate by 1.00 EUR "
            "to absorb rounding. Any breach creates a variance exception for human review."
        ),
    },
    {
        "title": "Auto-post ceiling",
        "text": (
            "Invoices with a gross total above 25,000 EUR are never posted straight through, "
            "even on a perfect three-way match. They are routed to the AP manager for release. "
            "This is a segregation-of-duties control, not a data-quality control."
        ),
    },
    {
        "title": "Duplicate invoice handling",
        "text": (
            "A duplicate is identified by the combination of vendor, invoice number, and gross "
            "amount. Invoice date is deliberately excluded because vendors re-issue the same "
            "invoice with a new print date. A suspected duplicate is blocked from posting and "
            "routed to AP with a link to the original run. Never post a second journal for the "
            "same vendor invoice number."
        ),
    },
    {
        "title": "Missing purchase order",
        "text": (
            "An invoice with no resolvable purchase order reference is a non-PO invoice. It "
            "cannot be matched and must be routed for manual GL coding and cost centre approval. "
            "Do not guess a purchase order from vendor history; an incorrectly guessed PO "
            "consumes budget from the wrong cost centre."
        ),
    },
    {
        "title": "Unknown vendor",
        "text": (
            "If the vendor on the invoice cannot be resolved against vendor master by name, tax "
            "identifier, or bank details, the invoice is routed to vendor master data for "
            "onboarding or correction. Payment to an unresolved vendor is a fraud risk and is "
            "blocked. Bank details that differ from vendor master are escalated as a possible "
            "payment-redirection attempt regardless of match outcome."
        ),
    },
    {
        "title": "Low extraction confidence",
        "text": (
            "When document extraction confidence falls below 0.75, the invoice is routed for "
            "human verification of the extracted fields before any match decision is trusted. "
            "Low confidence typically indicates a scanned document with poor OCR quality or a "
            "layout the extractor has not seen."
        ),
    },
    {
        "title": "Payment journal structure",
        "text": (
            "A matched AP invoice posts as a balanced journal: debit the GR/IR clearing account "
            "for the net goods value, debit input VAT for the tax amount, and credit accounts "
            "payable for the gross amount. The purchase order number and the source invoice "
            "number are carried as the document reference so the posting reconciles to the "
            "originating email."
        ),
    },
    {
        "title": "AR cash application",
        "text": (
            "Incoming customer remittances are matched to open receivables by invoice number "
            "first, then by exact amount, then by customer and payment reference. A remittance "
            "that leaves a residual above 2.00 EUR after applying all advice lines is posted as "
            "a partial application and the residual is routed to collections as an unapplied "
            "cash exception."
        ),
    },
    {
        "title": "Audit trail requirement",
        "text": (
            "Every automated decision must be traceable to the source email message identifier, "
            "the SHA-256 of the source document, the extracted field values, and the tolerance "
            "configuration in force at the time of the decision. Audit events are append-only "
            "and are never updated or deleted."
        ),
    },
]
