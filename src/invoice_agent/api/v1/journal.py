from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from invoice_agent.api.deps import RepositoryDep, ServiceDep, TokenGuard
from invoice_agent.core.errors import ERPError, NotFoundError, ValidationError
from invoice_agent.erp.client import get_erp_client
from invoice_agent.schemas.api import PostJournalRequest, PostJournalResponse
from invoice_agent.schemas.common import RunStatus

router = APIRouter(tags=["finance"], dependencies=[TokenGuard])


@router.post(
    "/post-payment-journal",
    response_model=PostJournalResponse,
    summary="Post the payment journal for a matched invoice",
)
async def post_payment_journal(
    request: PostJournalRequest, service: ServiceDep, repo: RepositoryDep
) -> PostJournalResponse:
    """Two ways in.

    With a run_id, this releases a run that is parked awaiting approval: the checkpointed graph
    resumes at the approval node and posts. With an explicit journal payload it posts directly
    to the ERP, which exists for correction entries a controller books by hand.
    """
    if request.journal is not None:
        if not request.journal.is_balanced():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "journal is not balanced: total debits must equal total credits",
            )
        try:
            result = await get_erp_client().post_journal_entry(request.journal)
        except ERPError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, exc.message) from exc
        return PostJournalResponse(
            status="posted",
            erp_document_number=result.document_number,
            fiscal_year=result.fiscal_year,
            total_amount=result.total_amount,
            currency=result.currency,
            reference=result.reference,
            detail="posted directly from the supplied journal payload",
        )

    if request.run_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "supply either a run_id or an explicit journal payload"
        )

    run = await repo.get_run(request.run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {request.run_id} not found")

    if run.status == RunStatus.COMPLETED:
        journal = await repo.get_journal_for_run(request.run_id)
        if journal is not None:
            return PostJournalResponse(
                run_id=str(request.run_id),
                status=journal.status.value,
                erp_document_number=journal.erp_document_number,
                fiscal_year=journal.fiscal_year,
                total_amount=journal.total_amount,
                currency=journal.currency,
                reference=journal.reference,
                straight_through=run.straight_through,
                detail="run already posted; returning the existing document",
            )

    try:
        summary = await service.resume_run(
            request.run_id, "approve", request.approved_by, request.note
        )
    except ValidationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.message) from exc
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc

    journal_summary = summary.get("journal") or {}
    return PostJournalResponse(
        run_id=str(request.run_id),
        status=journal_summary.get("status", summary.get("status", "unknown")),
        erp_document_number=journal_summary.get("erp_document_number"),
        total_amount=journal_summary.get("total_amount"),
        currency=journal_summary.get("currency"),
        reference=journal_summary.get("reference"),
        straight_through=bool(summary.get("straight_through")),
        detail=summary.get("error"),
    )


@router.get(
    "/journals/{run_id}",
    response_model=PostJournalResponse,
    summary="Retrieve the payment journal posted for a run",
)
async def get_journal(run_id: uuid.UUID, repo: RepositoryDep) -> PostJournalResponse:
    journal = await repo.get_journal_for_run(run_id)
    if journal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no journal posted for run {run_id}")
    return PostJournalResponse(
        run_id=str(run_id),
        status=journal.status.value,
        erp_document_number=journal.erp_document_number,
        fiscal_year=journal.fiscal_year,
        total_amount=journal.total_amount,
        currency=journal.currency,
        reference=journal.reference,
        detail=journal.error_message,
    )
