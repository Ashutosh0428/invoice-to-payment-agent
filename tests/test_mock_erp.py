from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from mock_erp.main import app

HEADERS = {"X-API-Key": "mock-erp-key"}


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        test_client.post("/admin/reseed", headers=HEADERS)
        yield test_client


def test_health_needs_no_key(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_business_endpoints_require_the_api_key(client):
    assert client.get("/erp/v1/purchase-orders/PO-4500001").status_code == 401
    assert client.get("/erp/v1/purchase-orders/PO-4500001", headers=HEADERS).status_code == 200


def test_purchase_order_totals_reconcile(client):
    po = client.get("/erp/v1/purchase-orders/PO-4500001", headers=HEADERS).json()

    lines_total = sum(float(line["line_total"]) for line in po["lines"])
    assert lines_total == pytest.approx(float(po["net_amount"]))
    assert float(po["net_amount"]) + float(po["tax_amount"]) == pytest.approx(
        float(po["gross_amount"])
    )


def test_unknown_purchase_order_returns_404(client):
    assert client.get("/erp/v1/purchase-orders/PO-9999999", headers=HEADERS).status_code == 404


def test_unbalanced_journal_is_rejected(client):
    response = client.post(
        "/erp/v1/journal-entries",
        headers=HEADERS,
        json={
            "reference": "TEST/UNBALANCED",
            "lines": [{"account": "191100", "debit": "100.00", "credit": "0"}],
        },
    )

    assert response.status_code == 422
    assert "not balanced" in response.json()["detail"]


def test_balanced_journal_posts_and_returns_a_document_number(client):
    response = client.post(
        "/erp/v1/journal-entries",
        headers=HEADERS,
        json={
            "reference": "CONTOSO/INV-2026-0871",
            "po_number": "PO-4500001",
            "lines": [
                {"account": "191100", "debit": "10730.00", "credit": "0"},
                {"account": "154000", "debit": "2038.70", "credit": "0"},
                {"account": "160000", "debit": "0", "credit": "12768.70"},
            ],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["document_number"].startswith("51")
    assert float(body["total_amount"]) == pytest.approx(12768.70)


def test_reposting_the_same_reference_is_rejected(client):
    payload = {
        "reference": "CONTOSO/DUPLICATE-GUARD",
        "lines": [
            {"account": "191100", "debit": "100.00", "credit": "0"},
            {"account": "160000", "debit": "0", "credit": "100.00"},
        ],
    }

    assert client.post("/erp/v1/journal-entries", headers=HEADERS, json=payload).status_code == 201
    second = client.post("/erp/v1/journal-entries", headers=HEADERS, json=payload)

    assert second.status_code == 409


def test_posting_consumes_the_purchase_order(client):
    client.post(
        "/erp/v1/journal-entries",
        headers=HEADERS,
        json={
            "reference": "FABRIKAM/INV-2026-0872",
            "po_number": "PO-4500002",
            "lines": [
                {"account": "191100", "debit": "9996.00", "credit": "0"},
                {"account": "160000", "debit": "0", "credit": "9996.00"},
            ],
        },
    )

    po = client.get("/erp/v1/purchase-orders/PO-4500002", headers=HEADERS).json()
    assert po["status"] == "invoiced"


def test_goods_receipts_are_scoped_to_the_purchase_order(client):
    receipts = client.get(
        "/erp/v1/goods-receipts", headers=HEADERS, params={"po_number": "PO-4500001"}
    ).json()

    assert len(receipts) == 1
    assert receipts[0]["gr_number"] == "GR-5000001"


def test_cash_application_clears_the_receivable(client):
    response = client.post(
        "/erp/v1/ar-items/AR-90001/apply-cash",
        headers=HEADERS,
        json={"amount": "18400.00", "payment_reference": "SEPA-CT-88213401"},
    )

    assert response.status_code == 200
    assert float(response.json()["residual_amount"]) == 0.0

    remaining = client.get(
        "/erp/v1/ar-items", headers=HEADERS, params={"customer_id": "C-20001"}
    ).json()
    assert all(item["ar_item_id"] != "AR-90001" for item in remaining)


def test_partial_cash_application_leaves_a_residual(client):
    response = client.post(
        "/erp/v1/ar-items/AR-90002/apply-cash",
        headers=HEADERS,
        json={"amount": "5000.00"},
    )

    assert float(response.json()["residual_amount"]) == pytest.approx(2250.00)
