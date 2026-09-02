from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from invoice_agent.api.deps import ServiceDep, TokenGuard
from invoice_agent.ingestion.parser import SUPPORTED_SUFFIXES
from invoice_agent.schemas.api import IngestResponse, MailboxPollRequest, MailboxPollResponse
from invoice_agent.schemas.common import DocumentSource, WorkflowKind

router = APIRouter(tags=["ingestion"], dependencies=[TokenGuard])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


async def _spool_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "upload.pdf").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"unsupported file type '{suffix}'; supported: {sorted(SUPPORTED_SUFFIXES)}",
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix="invoice-upload-"))
    target = tmp_dir / Path(upload.filename or f"upload{suffix}").name
    size = 0
    with target.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
                )
            handle.write(chunk)
    return target


@router.post(
    "/ingest-invoice",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a vendor invoice and run the accounts-payable workflow",
)
async def ingest_invoice(
    service: ServiceDep,
    file: Annotated[UploadFile, File(description="Invoice PDF, image, HTML or DOCX")],
    sender: Annotated[str | None, Form()] = None,
    subject: Annotated[str | None, Form()] = None,
    message_id: Annotated[str | None, Form()] = None,
) -> IngestResponse:
    """Runs parse, extract, duplicate check, vendor resolution, PO/GR fetch and matching.

    A clean match posts the payment journal in the same call and returns straight_through=true.
    An exception returns awaiting_approval with the case detail; the run stays checkpointed
    until a decision arrives on /exceptions/{id}/decision.
    """
    path = await _spool_upload(file)
    summary = await service.start_run(
        path,
        kind=WorkflowKind.ACCOUNTS_PAYABLE,
        source=DocumentSource.API_UPLOAD,
        message_id=message_id,
        sender=sender,
        subject=subject,
    )
    return IngestResponse.model_validate(summary)


@router.post(
    "/ingest-remittance",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a customer remittance and run the accounts-receivable workflow",
)
async def ingest_remittance(
    service: ServiceDep,
    file: Annotated[UploadFile, File(description="Remittance advice document")],
    sender: Annotated[str | None, Form()] = None,
    subject: Annotated[str | None, Form()] = None,
    message_id: Annotated[str | None, Form()] = None,
) -> IngestResponse:
    path = await _spool_upload(file)
    summary = await service.start_run(
        path,
        kind=WorkflowKind.ACCOUNTS_RECEIVABLE,
        source=DocumentSource.API_UPLOAD,
        message_id=message_id,
        sender=sender,
        subject=subject,
    )
    return IngestResponse.model_validate(summary)


@router.post(
    "/mailbox/poll",
    response_model=MailboxPollResponse,
    summary="Poll the configured mailbox and process every attachment found",
)
async def poll_mailbox(
    service: ServiceDep, request: MailboxPollRequest | None = None
) -> MailboxPollResponse:
    payload = request or MailboxPollRequest()
    runs = await service.poll_mailbox(WorkflowKind(payload.kind), payload.limit)
    return MailboxPollResponse(
        polled=len(runs), runs=[IngestResponse.model_validate(run) for run in runs]
    )


@router.get(
    "/runs/{run_id}",
    response_model=IngestResponse,
    summary="Current state of a workflow run",
)
async def get_run(run_id: str, service: ServiceDep) -> IngestResponse:
    import uuid

    from invoice_agent.core.errors import NotFoundError

    try:
        summary = await service.get_run_summary(uuid.UUID(run_id))
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "run_id must be a UUID") from exc
    return IngestResponse.model_validate(summary)
