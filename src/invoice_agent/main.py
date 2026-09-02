from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from invoice_agent import __version__
from invoice_agent.api.v1.router import router as v1_router
from invoice_agent.core.config import get_config
from invoice_agent.core.errors import InvoiceAgentError
from invoice_agent.core.logging import configure_logging
from invoice_agent.core.observability import setup_tracing

DESCRIPTION = """
Agentic invoice-to-payment automation.

Reads vendor invoices from a shared mailbox, extracts them with Docling, resolves the vendor
against ERP master data, performs a 2-way or 3-way match against the purchase order and goods
receipt, and posts the payment journal. Anything outside tolerance is escalated to a human
approver and the run is checkpointed until a decision arrives. The same workflow runs in
mirror for accounts-receivable remittances.

Every decision is written to an append-only audit trail linked to the source email message id
and the SHA-256 of the source document.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    setup_tracing()
    config = get_config()
    config.document_store.mkdir(parents=True, exist_ok=True)
    logger.info(
        "invoice-to-payment-agent {} starting in {} environment", __version__, config.environment
    )
    yield

    from invoice_agent.agents.graph import close_checkpointer
    from invoice_agent.db.session import dispose_engine
    from invoice_agent.erp.client import close_erp_client

    await close_checkpointer()
    await close_erp_client()
    await dispose_engine()
    logger.info("invoice-to-payment-agent stopped")


def create_app() -> FastAPI:
    config = get_config()
    app = FastAPI(
        title="Invoice-to-Payment Agent",
        description=DESCRIPTION,
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        contact={"name": "Ashutosh Sharma", "email": "kapil.shrm1.kks@gmail.com"},
        license_info={"name": "MIT"},
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(InvoiceAgentError)
    async def domain_error_handler(_: Request, exc: InvoiceAgentError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    app.include_router(v1_router, prefix=config.api_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": "invoice-to-payment-agent",
            "version": __version__,
            "docs": "/docs",
            "health": f"{config.api_prefix}/health",
        }

    return app


app = create_app()
