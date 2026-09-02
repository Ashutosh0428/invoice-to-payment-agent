from __future__ import annotations

from typing import Any


class InvoiceAgentError(Exception):
    """Base for every error this service raises deliberately."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(InvoiceAgentError):
    status_code = 500
    code = "configuration_error"


class DocumentParseError(InvoiceAgentError):
    status_code = 422
    code = "document_parse_error"


class ExtractionError(InvoiceAgentError):
    status_code = 422
    code = "extraction_error"


class ERPError(InvoiceAgentError):
    status_code = 502
    code = "erp_error"


class PurchaseOrderNotFoundError(ERPError):
    status_code = 404
    code = "purchase_order_not_found"


class DuplicateInvoiceError(InvoiceAgentError):
    status_code = 409
    code = "duplicate_invoice"


class MailboxError(InvoiceAgentError):
    status_code = 502
    code = "mailbox_error"


class NotFoundError(InvoiceAgentError):
    status_code = 404
    code = "not_found"


class ValidationError(InvoiceAgentError):
    status_code = 400
    code = "validation_error"
