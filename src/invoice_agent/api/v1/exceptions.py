from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from invoice_agent.api.deps import RepositoryDep, ServiceDep, TokenGuard
from invoice_agent.core.errors import NotFoundError, ValidationError
from invoice_agent.schemas.api import ExceptionDecisionRequest, ExceptionOut, IngestResponse
from invoice_agent.schemas.common import ExceptionStatus

router = APIRouter(tags=["exceptions"], dependencies=[TokenGuard])


def _to_out(case) -> ExceptionOut:  # type: ignore[no-untyped-def]
    return ExceptionOut(
        id=str(case.id),
        run_id=str(case.run_id),
        exception_type=case.exception_type.value,
        status=case.status.value,
        severity=case.severity,
        summary=case.summary,
        suggested_action=case.suggested_action,
        details=case.details,
        resolved_by=case.resolved_by,
        resolution_note=case.resolution_note,
        created_at=case.created_at,
    )


@router.get(
    "/exceptions",
    response_model=list[ExceptionOut],
    summary="Human approval queue",
)
async def list_exceptions(
    repo: RepositoryDep,
    exception_status: Annotated[
        ExceptionStatus | None, Query(alias="status", description="Filter by resolution status")
    ] = ExceptionStatus.OPEN,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ExceptionOut]:
    cases = await repo.list_exceptions(status=exception_status, limit=limit, offset=offset)
    return [_to_out(case) for case in cases]


@router.get(
    "/exceptions/{case_id}",
    response_model=ExceptionOut,
    summary="One exception case with its variance detail and policy guidance",
)
async def get_exception(case_id: uuid.UUID, repo: RepositoryDep) -> ExceptionOut:
    case = await repo.get_exception(case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"exception {case_id} not found")
    return _to_out(case)


@router.post(
    "/exceptions/{case_id}/decision",
    response_model=IngestResponse,
    summary="Approve or reject an exception and resume the workflow",
)
async def decide_exception(
    case_id: uuid.UUID,
    request: ExceptionDecisionRequest,
    repo: RepositoryDep,
    service: ServiceDep,
) -> IngestResponse:
    """Resumes the checkpointed run from the approval node.

    Approving posts the payment journal; rejecting closes the run without posting. Either way
    the decision, the approver, and the note are written to the audit trail.
    """
    case = await repo.get_exception(case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"exception {case_id} not found")
    if case.status != ExceptionStatus.OPEN:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"exception {case_id} was already {case.status.value} by {case.resolved_by}",
        )

    try:
        summary = await service.resume_run(
            case.run_id, request.decision, request.approved_by, request.note
        )
    except ValidationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.message) from exc
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc

    return IngestResponse.model_validate(summary)
