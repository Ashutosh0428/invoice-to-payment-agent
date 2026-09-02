from __future__ import annotations

from enum import StrEnum


class WorkflowKind(StrEnum):
    ACCOUNTS_PAYABLE = "accounts_payable"
    ACCOUNTS_RECEIVABLE = "accounts_receivable"


class DocumentSource(StrEnum):
    EMAIL_ATTACHMENT = "email_attachment"
    EMAIL_BODY = "email_body"
    API_UPLOAD = "api_upload"
    MANUAL = "manual"


class RunStatus(StrEnum):
    RECEIVED = "received"
    PARSING = "parsing"
    EXTRACTING = "extracting"
    MATCHING = "matching"
    AWAITING_APPROVAL = "awaiting_approval"
    POSTING = "posting"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class MatchType(StrEnum):
    TWO_WAY = "two_way"
    THREE_WAY = "three_way"
    NON_PO = "non_po"


class MatchOutcome(StrEnum):
    MATCHED = "matched"
    MATCHED_WITHIN_TOLERANCE = "matched_within_tolerance"
    EXCEPTION = "exception"


class ExceptionType(StrEnum):
    PRICE_VARIANCE = "price_variance"
    QUANTITY_VARIANCE = "quantity_variance"
    TOTAL_VARIANCE = "total_variance"
    TAX_VARIANCE = "tax_variance"
    MISSING_PO = "missing_po"
    MISSING_GOODS_RECEIPT = "missing_goods_receipt"
    DUPLICATE_INVOICE = "duplicate_invoice"
    UNKNOWN_VENDOR = "unknown_vendor"
    LOW_CONFIDENCE_EXTRACTION = "low_confidence_extraction"
    LINE_NOT_ON_PO = "line_not_on_po"
    OVER_AUTO_POST_CEILING = "over_auto_post_ceiling"
    CURRENCY_MISMATCH = "currency_mismatch"
    UNAPPLIED_REMITTANCE = "unapplied_remittance"
    ERP_POSTING_FAILED = "erp_posting_failed"


class ExceptionStatus(StrEnum):
    OPEN = "open"
    APPROVED = "approved"
    REJECTED = "rejected"


class JournalStatus(StrEnum):
    DRAFT = "draft"
    POSTED = "posted"
    FAILED = "failed"


class AuditAction(StrEnum):
    EMAIL_RECEIVED = "email_received"
    DOCUMENT_PARSED = "document_parsed"
    FIELDS_EXTRACTED = "fields_extracted"
    DUPLICATE_CHECKED = "duplicate_checked"
    VENDOR_RESOLVED = "vendor_resolved"
    PO_FETCHED = "po_fetched"
    GOODS_RECEIPT_FETCHED = "goods_receipt_fetched"
    MATCH_EVALUATED = "match_evaluated"
    EXCEPTION_RAISED = "exception_raised"
    HUMAN_DECISION = "human_decision"
    JOURNAL_POSTED = "journal_posted"
    CASH_APPLIED = "cash_applied"
    RUN_FAILED = "run_failed"
