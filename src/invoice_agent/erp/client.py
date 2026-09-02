from __future__ import annotations

from typing import Any, Protocol

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from invoice_agent.core.config import ERPConfig, get_config
from invoice_agent.core.errors import ERPError, PurchaseOrderNotFoundError
from invoice_agent.schemas.erp import (
    ARItem,
    CashApplicationResult,
    GoodsReceipt,
    JournalPostingRequest,
    JournalPostingResult,
    PurchaseOrder,
    Vendor,
)

_RETRYABLE = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)


class ERPClient(Protocol):
    """The surface the agent depends on. Swapping the mock for real S/4HANA means
    implementing this, not editing the workflow."""

    async def get_vendors(self) -> list[Vendor]: ...
    async def get_purchase_order(self, po_number: str) -> PurchaseOrder: ...
    async def find_purchase_orders(self, vendor_id: str) -> list[PurchaseOrder]: ...
    async def get_goods_receipts(self, po_number: str) -> list[GoodsReceipt]: ...
    async def post_journal_entry(self, request: JournalPostingRequest) -> JournalPostingResult: ...
    async def get_ar_items(
        self, customer_id: str | None = None, invoice_number: str | None = None
    ) -> list[ARItem]: ...
    async def apply_cash(
        self, ar_item_id: str, amount: Any, reference: str
    ) -> CashApplicationResult: ...
    async def aclose(self) -> None: ...


class HttpERPClient:
    def __init__(self, config: ERPConfig | None = None, client: httpx.AsyncClient | None = None):
        self.config = config or get_config().erp
        self._client = client or httpx.AsyncClient(
            base_url=self.config.base_url,
            timeout=self.config.timeout,
            headers={"X-API-Key": self.config.api_key.get_secret_value()},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        retry=retry_if_exception_type(_RETRYABLE),
        reraise=True,
    )
    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, url, **kwargs)
        except _RETRYABLE:
            raise
        except httpx.HTTPError as exc:
            raise ERPError(f"ERP transport failure on {method} {url}: {exc}") from exc
        return response

    @staticmethod
    def _raise_for_status(response: httpx.Response, context: str) -> None:
        if response.is_success:
            return
        detail = response.text[:500]
        if response.status_code == 404:
            raise PurchaseOrderNotFoundError(f"{context}: not found", details={"body": detail})
        raise ERPError(
            f"{context}: ERP returned {response.status_code}",
            details={"status_code": response.status_code, "body": detail},
        )

    async def get_vendors(self) -> list[Vendor]:
        response = await self._request("GET", "/erp/v1/vendors")
        self._raise_for_status(response, "get_vendors")
        return [Vendor.model_validate(item) for item in response.json()]

    async def get_purchase_order(self, po_number: str) -> PurchaseOrder:
        response = await self._request("GET", f"/erp/v1/purchase-orders/{po_number}")
        if response.status_code == 404:
            raise PurchaseOrderNotFoundError(
                f"purchase order {po_number} not found in ERP",
                details={"po_number": po_number},
            )
        self._raise_for_status(response, f"get_purchase_order {po_number}")
        return PurchaseOrder.model_validate(response.json())

    async def find_purchase_orders(self, vendor_id: str) -> list[PurchaseOrder]:
        response = await self._request(
            "GET", "/erp/v1/purchase-orders", params={"vendor_id": vendor_id, "status": "open"}
        )
        self._raise_for_status(response, f"find_purchase_orders {vendor_id}")
        return [PurchaseOrder.model_validate(item) for item in response.json()]

    async def get_goods_receipts(self, po_number: str) -> list[GoodsReceipt]:
        response = await self._request(
            "GET", "/erp/v1/goods-receipts", params={"po_number": po_number}
        )
        self._raise_for_status(response, f"get_goods_receipts {po_number}")
        return [GoodsReceipt.model_validate(item) for item in response.json()]

    async def post_journal_entry(self, request: JournalPostingRequest) -> JournalPostingResult:
        payload = request.model_dump(mode="json", exclude_none=True)
        response = await self._request("POST", "/erp/v1/journal-entries", json=payload)
        if response.status_code == 409:
            raise ERPError(
                f"journal reference {request.reference} already posted",
                details={"reference": request.reference, "body": response.text[:500]},
            )
        self._raise_for_status(response, "post_journal_entry")
        logger.info("Posted journal {} to ERP", request.reference)
        return JournalPostingResult.model_validate(response.json())

    async def get_ar_items(
        self, customer_id: str | None = None, invoice_number: str | None = None
    ) -> list[ARItem]:
        params = {
            k: v
            for k, v in {"customer_id": customer_id, "invoice_number": invoice_number}.items()
            if v
        }
        response = await self._request("GET", "/erp/v1/ar-items", params=params)
        self._raise_for_status(response, "get_ar_items")
        return [ARItem.model_validate(item) for item in response.json()]

    async def apply_cash(
        self, ar_item_id: str, amount: Any, reference: str
    ) -> CashApplicationResult:
        response = await self._request(
            "POST",
            f"/erp/v1/ar-items/{ar_item_id}/apply-cash",
            json={"amount": str(amount), "payment_reference": reference},
        )
        self._raise_for_status(response, f"apply_cash {ar_item_id}")
        return CashApplicationResult.model_validate(response.json())


_client: HttpERPClient | None = None


def get_erp_client() -> HttpERPClient:
    global _client
    if _client is None:
        _client = HttpERPClient()
    return _client


async def close_erp_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
