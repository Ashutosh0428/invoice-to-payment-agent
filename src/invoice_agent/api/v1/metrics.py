from __future__ import annotations

from fastapi import APIRouter

from invoice_agent.api.deps import RepositoryDep, TokenGuard
from invoice_agent.schemas.api import MetricsResponse

router = APIRouter(tags=["metrics"], dependencies=[TokenGuard])


@router.get(
    "/metrics/straight-through",
    response_model=MetricsResponse,
    summary="Straight-through processing rate across all runs",
)
async def straight_through(repo: RepositoryDep) -> MetricsResponse:
    """STP rate is the share of runs that reached a posted journal with no human touch.

    It is derived from run rows rather than counted separately, so it cannot drift away from
    what the audit trail says actually happened.
    """
    stats = await repo.straight_through_stats()
    total = stats["total_runs"] or 1
    return MetricsResponse(
        total_runs=stats["total_runs"],
        completed=stats["completed"],
        straight_through=stats["straight_through"],
        exceptions=stats["exceptions"],
        straight_through_rate=round(stats["straight_through"] / total, 4),
        completion_rate=round(stats["completed"] / total, 4),
    )
