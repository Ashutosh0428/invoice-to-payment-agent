from __future__ import annotations

import pytest

from invoice_agent.main import create_app

MANDATED_ENDPOINTS = {
    ("/api/v1/ingest-invoice", "post"),
    ("/api/v1/match-po", "post"),
    ("/api/v1/post-payment-journal", "post"),
    ("/api/v1/audit-log", "get"),
    ("/api/v1/health", "get"),
}


@pytest.fixture(scope="module")
def openapi() -> dict:
    return create_app().openapi()


@pytest.mark.parametrize(("path", "method"), sorted(MANDATED_ENDPOINTS))
def test_mandated_endpoint_is_published(openapi, path, method):
    assert path in openapi["paths"], f"{path} missing from the OpenAPI document"
    assert method in openapi["paths"][path], f"{method.upper()} {path} missing"


def test_human_approval_queue_is_exposed(openapi):
    assert "/api/v1/exceptions" in openapi["paths"]
    assert "post" in openapi["paths"]["/api/v1/exceptions/{case_id}/decision"]


def test_accounts_receivable_mirror_is_exposed(openapi):
    assert "post" in openapi["paths"]["/api/v1/ingest-remittance"]


def test_every_published_operation_is_documented(openapi):
    undocumented = [
        f"{method.upper()} {path}"
        for path, operations in openapi["paths"].items()
        for method, operation in operations.items()
        if not operation.get("summary")
    ]

    assert undocumented == []


def test_ingest_invoice_accepts_a_file_upload(openapi):
    body = openapi["paths"]["/api/v1/ingest-invoice"]["post"]["requestBody"]

    assert "multipart/form-data" in body["content"]
