from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from invoice_agent.core.config import AppConfig, get_config
from invoice_agent.db.repository import WorkflowRepository
from invoice_agent.db.session import get_session
from invoice_agent.services.workflow import WorkflowService, get_workflow_service


async def require_token(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Bearer token or X-API-Key. Disabled by default for local runs; enable with
    INVOICE_AGENT_AUTH_ENABLED=true before exposing this service anywhere shared."""
    cfg = get_config()
    if not cfg.auth_enabled:
        return

    expected = cfg.api_token.get_secret_value()
    supplied = x_api_key or ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:]

    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing or invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WorkflowRepository:
    return WorkflowRepository(session)


def get_service() -> WorkflowService:
    return get_workflow_service()


def get_settings() -> AppConfig:
    return get_config()


TokenGuard = Depends(require_token)
RepositoryDep = Annotated[WorkflowRepository, Depends(get_repository)]
ServiceDep = Annotated[WorkflowService, Depends(get_service)]
ConfigDep = Annotated[AppConfig, Depends(get_settings)]
