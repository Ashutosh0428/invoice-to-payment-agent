from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from loguru import logger

from invoice_agent.db.repository import WorkflowRepository
from invoice_agent.db.session import get_session_factory
from invoice_agent.schemas.common import AuditAction


@asynccontextmanager
async def repository() -> AsyncIterator[WorkflowRepository]:
    async with get_session_factory()() as session:
        yield WorkflowRepository(session)


async def audit(
    state: Mapping[str, Any],
    action: AuditAction,
    summary: str,
    *,
    node: str,
    payload: dict[str, Any] | None = None,
    actor: str = "agent",
    started: float | None = None,
) -> None:
    """Audit writes never break a run: a failed trail write is logged, not raised, because
    losing the invoice is worse than losing one trail row."""
    run_id = state.get("run_id")
    if not run_id:
        return
    duration_ms = int((time.perf_counter() - started) * 1000) if started else None
    try:
        async with repository() as repo:
            await repo.append_audit(
                uuid.UUID(str(run_id)),
                action,
                summary,
                actor=actor,
                node=node,
                payload=payload or {},
                duration_ms=duration_ms,
                source_message_id=state.get("source_message_id"),
                document_sha256=state.get("document_sha256"),
            )
    except Exception as exc:
        logger.warning("Audit write failed for run {} at {}: {}", run_id, node, exc)


async def set_run_status(state: Mapping[str, Any], **fields: Any) -> None:
    run_id = state.get("run_id")
    if not run_id:
        return
    try:
        async with repository() as repo:
            await repo.update_run(uuid.UUID(str(run_id)), **fields)
    except Exception as exc:
        logger.warning("Run status update failed for {}: {}", run_id, exc)
