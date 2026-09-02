"""SAP-flavoured mock ERP.

Stands in for the S/4HANA OData surface the real integration would call: purchase orders,
goods receipts, vendor master, AP journal posting, and AR open items with cash application.
State is in-process and reseeded on startup so evaluation runs stay reproducible.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from mock_erp.data import AR_ITEMS, GOODS_RECEIPTS, PURCHASE_ORDERS, VENDORS

API_KEY = os.getenv("MOCK_ERP_API_KEY", "mock-erp-key")
COMPANY_CODE = os.getenv("MOCK_ERP_COMPANY_CODE", "1000")

_state: dict[str, Any] = {}
_doc_counter = itertools.count(1)
_app_counter = itertools.count(1)


def _reseed() -> None:
    _state["vendors"] = deepcopy(VENDORS)
    _state["purchase_orders"] = deepcopy(PURCHASE_ORDERS)
    _state["goods_receipts"] = deepcopy(GOODS_RECEIPTS)
    _state["ar_items"] = deepcopy(AR_ITEMS)
    _state["journals"] = []
    _state["applications"] = []


def require_api_key(x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None) -> None:
    if x_api_key != API_KEY:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing X-API-Key")


class JournalLineIn(BaseModel):
    account: str
    description: str = ""
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    cost_center: str | None = None
    tax_code: str | None = None


class JournalEntryIn(BaseModel):
    reference: str
    company_code: str = COMPANY_CODE
    currency: str = "EUR"
    posting_date: date | None = None
    vendor_id: str | None = None
    customer_id: str | None = None
    po_number: str | None = None
    memo: str = ""
    lines: list[JournalLineIn] = Field(default_factory=list)


class CashApplicationIn(BaseModel):
    amount: Decimal
    payment_reference: str = ""
    payment_date: date | None = None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    _reseed()
    yield


app = FastAPI(
    title="Mock ERP (SAP S/4HANA style)",
    description="Purchase orders, goods receipts, vendor master, AP journals and AR open items.",
    version="1.0.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)


@app.get("/health", tags=["system"])
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "mock-erp",
        "timestamp": datetime.now(UTC).isoformat(),
        "purchase_orders": len(_state.get("purchase_orders", [])),
        "journals_posted": len(_state.get("journals", [])),
    }


@app.post("/admin/reseed", tags=["system"], dependencies=[Depends(require_api_key)])
def reseed() -> dict[str, str]:
    _reseed()
    return {"status": "reseeded"}


@app.get("/erp/v1/vendors", tags=["master data"], dependencies=[Depends(require_api_key)])
def list_vendors() -> list[dict[str, Any]]:
    return _state["vendors"]


@app.get(
    "/erp/v1/vendors/{vendor_id}", tags=["master data"], dependencies=[Depends(require_api_key)]
)
def get_vendor(vendor_id: str) -> dict[str, Any]:
    vendor = next((v for v in _state["vendors"] if v["vendor_id"] == vendor_id), None)
    if vendor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"vendor {vendor_id} not found")
    return vendor


@app.get("/erp/v1/purchase-orders", tags=["procurement"], dependencies=[Depends(require_api_key)])
def list_purchase_orders(
    vendor_id: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[dict[str, Any]]:
    pos = _state["purchase_orders"]
    if vendor_id:
        pos = [p for p in pos if p["vendor_id"] == vendor_id]
    if status_filter:
        pos = [p for p in pos if p["status"] == status_filter]
    return pos


@app.get(
    "/erp/v1/purchase-orders/{po_number}",
    tags=["procurement"],
    dependencies=[Depends(require_api_key)],
)
def get_purchase_order(po_number: str) -> dict[str, Any]:
    po = next((p for p in _state["purchase_orders"] if p["po_number"] == po_number), None)
    if po is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"purchase order {po_number} not found")
    return po


@app.get("/erp/v1/goods-receipts", tags=["procurement"], dependencies=[Depends(require_api_key)])
def list_goods_receipts(po_number: str | None = Query(default=None)) -> list[dict[str, Any]]:
    grs = _state["goods_receipts"]
    if po_number:
        grs = [g for g in grs if g["po_number"] == po_number]
    return grs


@app.post(
    "/erp/v1/journal-entries",
    tags=["finance"],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
)
def post_journal_entry(entry: JournalEntryIn) -> dict[str, Any]:
    debits = sum((line.debit for line in entry.lines), Decimal("0"))
    credits = sum((line.credit for line in entry.lines), Decimal("0"))
    if not entry.lines:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "journal has no lines")
    if debits != credits:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"journal not balanced: debits {debits} != credits {credits}",
        )

    existing = next((j for j in _state["journals"] if j["reference"] == entry.reference), None)
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"reference {entry.reference} already posted as {existing['document_number']}",
        )

    posting_date = entry.posting_date or date.today()
    document = {
        "document_number": f"51{next(_doc_counter):08d}",
        "fiscal_year": posting_date.year,
        "posting_date": posting_date,
        "reference": entry.reference,
        "company_code": entry.company_code,
        "total_amount": debits,
        "currency": entry.currency,
        "status": "posted",
        "vendor_id": entry.vendor_id,
        "po_number": entry.po_number,
        "memo": entry.memo,
        "lines": [line.model_dump() for line in entry.lines],
        "created_at": datetime.now(UTC),
    }
    _state["journals"].append(document)

    if entry.po_number:
        po = next((p for p in _state["purchase_orders"] if p["po_number"] == entry.po_number), None)
        if po is not None:
            for po_line in po["lines"]:
                po_line["quantity_invoiced"] = po_line["quantity_ordered"]
            po["status"] = "invoiced"

    return document


@app.get("/erp/v1/journal-entries", tags=["finance"], dependencies=[Depends(require_api_key)])
def list_journal_entries(reference: str | None = Query(default=None)) -> list[dict[str, Any]]:
    journals = _state["journals"]
    if reference:
        journals = [j for j in journals if j["reference"] == reference]
    return journals


@app.get("/erp/v1/ar-items", tags=["receivables"], dependencies=[Depends(require_api_key)])
def list_ar_items(
    customer_id: str | None = Query(default=None),
    invoice_number: str | None = Query(default=None),
    status_filter: str | None = Query(default="open", alias="status"),
) -> list[dict[str, Any]]:
    items = _state["ar_items"]
    if customer_id:
        items = [i for i in items if i["customer_id"] == customer_id]
    if invoice_number:
        items = [i for i in items if i["invoice_number"] == invoice_number]
    if status_filter:
        items = [i for i in items if i["status"] == status_filter]
    return items


@app.post(
    "/erp/v1/ar-items/{ar_item_id}/apply-cash",
    tags=["receivables"],
    dependencies=[Depends(require_api_key)],
)
def apply_cash(ar_item_id: str, payload: CashApplicationIn) -> dict[str, Any]:
    item = next((i for i in _state["ar_items"] if i["ar_item_id"] == ar_item_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"AR item {ar_item_id} not found")
    if item["status"] != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, f"AR item {ar_item_id} is not open")

    applied = min(payload.amount, item["open_amount"])
    item["open_amount"] = item["open_amount"] - applied
    if item["open_amount"] <= Decimal("0"):
        item["status"] = "cleared"

    application = {
        "application_id": f"CA-{next(_app_counter):06d}",
        "ar_item_id": ar_item_id,
        "applied_amount": applied,
        "residual_amount": item["open_amount"],
        "payment_reference": payload.payment_reference,
        "payment_date": payload.payment_date or date.today(),
        "status": "applied",
    }
    _state["applications"].append(application)
    return application
