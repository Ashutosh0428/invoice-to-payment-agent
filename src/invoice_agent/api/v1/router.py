from fastapi import APIRouter

from invoice_agent.api.v1 import audit, exceptions, health, ingest, journal, match, metrics

router = APIRouter()
router.include_router(health.router)
router.include_router(ingest.router)
router.include_router(match.router)
router.include_router(journal.router)
router.include_router(audit.router)
router.include_router(exceptions.router)
router.include_router(metrics.router)
