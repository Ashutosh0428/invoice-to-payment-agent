from __future__ import annotations

import time
from typing import Any

from loguru import logger

from invoice_agent.agents.nodes._common import audit, set_run_status
from invoice_agent.agents.prompts import (
    INVOICE_EXTRACTION_SYSTEM,
    INVOICE_EXTRACTION_USER,
    REMITTANCE_EXTRACTION_SYSTEM,
    REMITTANCE_EXTRACTION_USER,
)
from invoice_agent.agents.state import InvoiceState
from invoice_agent.core.errors import DocumentParseError, ExtractionError
from invoice_agent.ingestion.confidence import score_extraction
from invoice_agent.ingestion.parser import parse_document
from invoice_agent.llm.structured import extract_structured
from invoice_agent.schemas.common import AuditAction, RunStatus
from invoice_agent.schemas.invoice import ExtractedInvoice, ExtractedRemittance

MAX_DOCUMENT_CHARS = 24000


async def parse_node(state: InvoiceState) -> dict[str, Any]:
    started = time.perf_counter()
    await set_run_status(state, status=RunStatus.PARSING)

    try:
        parsed = parse_document(state["document_path"])
    except DocumentParseError as exc:
        await audit(
            state,
            AuditAction.RUN_FAILED,
            f"Parsing failed: {exc.message}",
            node="parse",
            payload=exc.details,
            started=started,
        )
        return {
            "status": RunStatus.FAILED,
            "error": exc.message,
            "decisions": [f"parse failed: {exc.message}"],
        }

    await audit(
        state,
        AuditAction.DOCUMENT_PARSED,
        f"Parsed {parsed.path.name} with {parsed.parser} "
        f"({parsed.page_count} pages, {parsed.table_count} tables)",
        node="parse",
        payload={
            "parser": parsed.parser,
            "page_count": parsed.page_count,
            "table_count": parsed.table_count,
            "characters": len(parsed.markdown),
            "sha256": parsed.sha256,
        },
        started=started,
    )
    return {
        "document_markdown": parsed.markdown[:MAX_DOCUMENT_CHARS],
        "document_sha256": parsed.sha256,
        "parser": parsed.parser,
        "page_count": parsed.page_count,
        "decisions": [f"parsed with {parsed.parser}"],
    }


async def extract_invoice_node(state: InvoiceState) -> dict[str, Any]:
    started = time.perf_counter()
    await set_run_status(state, status=RunStatus.EXTRACTING)

    try:
        invoice = await extract_structured(
            INVOICE_EXTRACTION_SYSTEM,
            INVOICE_EXTRACTION_USER.format(document=state["document_markdown"]),
            ExtractedInvoice,
        )
    except ExtractionError as exc:
        await audit(
            state,
            AuditAction.RUN_FAILED,
            f"Extraction failed: {exc.message}",
            node="extract_invoice",
            payload=exc.details,
            started=started,
        )
        return {
            "status": RunStatus.FAILED,
            "error": exc.message,
            "decisions": [f"extraction failed: {exc.message}"],
        }

    score, signals = score_extraction(invoice)
    invoice.confidence = score

    await audit(
        state,
        AuditAction.FIELDS_EXTRACTED,
        f"Extracted invoice {invoice.invoice_number or 'unknown'} from "
        f"{invoice.vendor_name or 'unknown vendor'} for "
        f"{invoice.total_amount or 0} {invoice.currency or ''} (confidence {score:.2f})",
        node="extract_invoice",
        payload={
            "invoice": invoice.model_dump(mode="json"),
            "confidence": score,
            "confidence_signals": signals,
        },
        started=started,
    )
    logger.info("Extracted invoice {} at confidence {:.2f}", invoice.invoice_number, score)

    return {
        "invoice": invoice,
        "confidence_signals": signals,
        "decisions": [f"extracted {len(invoice.line_items)} line items at confidence {score:.2f}"],
    }


async def extract_remittance_node(state: InvoiceState) -> dict[str, Any]:
    started = time.perf_counter()
    await set_run_status(state, status=RunStatus.EXTRACTING)

    try:
        remittance = await extract_structured(
            REMITTANCE_EXTRACTION_SYSTEM,
            REMITTANCE_EXTRACTION_USER.format(document=state["document_markdown"]),
            ExtractedRemittance,
        )
    except ExtractionError as exc:
        await audit(
            state,
            AuditAction.RUN_FAILED,
            f"Remittance extraction failed: {exc.message}",
            node="extract_remittance",
            payload=exc.details,
            started=started,
        )
        return {
            "status": RunStatus.FAILED,
            "error": exc.message,
            "decisions": [f"remittance extraction failed: {exc.message}"],
        }

    await audit(
        state,
        AuditAction.FIELDS_EXTRACTED,
        f"Extracted remittance {remittance.remittance_number or 'unknown'} from "
        f"{remittance.customer_name or 'unknown customer'} covering "
        f"{len(remittance.advices)} invoices",
        node="extract_remittance",
        payload={"remittance": remittance.model_dump(mode="json")},
        started=started,
    )
    return {
        "remittance": remittance,
        "decisions": [f"extracted {len(remittance.advices)} remittance advice lines"],
    }
