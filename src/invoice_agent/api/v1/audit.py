from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from invoice_agent.api.deps import RepositoryDep, TokenGuard
from invoice_agent.schemas.api import AuditEventOut, AuditLogResponse
from invoice_agent.schemas.common import AuditAction

router = APIRouter(tags=["audit"], dependencies=[TokenGuard])


@router.get(
    "/audit-log",
    response_model=AuditLogResponse,
    summary="Append-only decision trail",
)
async def audit_log(
    repo: RepositoryDep,
    run_id: Annotated[uuid.UUID | None, Query(description="Restrict to one workflow run")] = None,
    action: Annotated[AuditAction | None, Query(description="Restrict to one action type")] = None,
    since: Annotated[
        datetime | None, Query(description="Only events at or after this time")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditLogResponse:
    """Every automated decision, in order, linked back to the source email and document hash.

    Filtered by run_id the events come back in workflow sequence, which is the order an auditor
    reads them; unfiltered they come back newest first.
    """
    events = await repo.list_audit_events(
        run_id=run_id, action=action, since=since, limit=limit, offset=offset
    )
    total = await repo.count_audit_events(run_id=run_id)

    return AuditLogResponse(
        total=total,
        returned=len(events),
        limit=limit,
        offset=offset,
        events=[
            AuditEventOut(
                id=str(event.id),
                run_id=str(event.run_id),
                sequence=event.sequence,
                action=event.action.value,
                actor=event.actor,
                node=event.node,
                summary=event.summary,
                payload=event.payload,
                source_message_id=event.source_message_id,
                document_sha256=event.document_sha256,
                duration_ms=event.duration_ms,
                created_at=event.created_at,
            )
            for event in events
        ],
    )
