from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter
from sqlalchemy import text

from invoice_agent import __version__
from invoice_agent.api.deps import ConfigDep
from invoice_agent.db.session import get_session_factory
from invoice_agent.schemas.api import ComponentHealth, HealthResponse

router = APIRouter(tags=["system"])


async def _check_database() -> ComponentHealth:
    started = time.perf_counter()
    try:
        async with get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
        return ComponentHealth(
            name="postgres",
            status="ok",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    except Exception as exc:
        return ComponentHealth(name="postgres", status="down", detail=str(exc)[:200])


async def _check_http(
    name: str, url: str, headers: dict[str, str] | None = None
) -> ComponentHealth:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url, headers=headers or {})
        latency = round((time.perf_counter() - started) * 1000, 2)
        if response.is_success:
            return ComponentHealth(name=name, status="ok", latency_ms=latency)
        return ComponentHealth(
            name=name,
            status="degraded",
            detail=f"HTTP {response.status_code}",
            latency_ms=latency,
        )
    except Exception as exc:
        return ComponentHealth(name=name, status="down", detail=str(exc)[:200])


@router.get("/health", response_model=HealthResponse, summary="Liveness and dependency health")
async def health(config: ConfigDep) -> HealthResponse:
    """Reports each dependency separately.

    Postgres down means the service cannot run; Ollama or the ERP being down degrades it but
    the audit trail and approval queue remain readable, so the overall status distinguishes
    the two.
    """
    components = [
        await _check_database(),
        await _check_http("ollama", f"{config.llm.base_url}/api/version"),
        await _check_http(
            "erp",
            f"{config.erp.base_url}/health",
            {"X-API-Key": config.erp.api_key.get_secret_value()},
        ),
    ]
    if config.observability.enabled:
        components.append(
            await _check_http(
                "phoenix", config.observability.phoenix_endpoint.replace("/v1/traces", "/healthz")
            )
        )

    database = next(c for c in components if c.name == "postgres")
    if database.status == "down":
        overall = "down"
    elif any(c.status != "ok" for c in components):
        overall = "degraded"
    else:
        overall = "ok"

    return HealthResponse(
        status=overall,
        version=__version__,
        environment=config.environment,
        timestamp=datetime.now(UTC),
        components=components,
    )
