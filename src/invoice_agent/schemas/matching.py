from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from invoice_agent.schemas.common import ExceptionType, MatchOutcome, MatchType


class Variance(BaseModel):
    field: str
    invoice_value: Decimal
    reference_value: Decimal
    absolute_delta: Decimal
    percent_delta: Decimal
    tolerance_abs: Decimal
    tolerance_pct: Decimal
    within_tolerance: bool
    exception_type: ExceptionType | None = None
    value_impact: Decimal | None = None

    def describe(self) -> str:
        base = (
            f"{self.field}: invoice {self.invoice_value} vs reference {self.reference_value} "
            f"(delta {self.absolute_delta}, {self.percent_delta}%)"
        )
        if self.value_impact is not None:
            base += f", line impact {self.value_impact}"
        return base


class LineMatch(BaseModel):
    invoice_line_number: int | None = None
    po_line_number: int | None = None
    description: str = ""
    matched: bool = False
    quantity_invoiced: Decimal = Decimal("0")
    quantity_ordered: Decimal = Decimal("0")
    quantity_received: Decimal = Decimal("0")
    variances: list[Variance] = Field(default_factory=list)
    exceptions: list[ExceptionType] = Field(default_factory=list)


class MatchReport(BaseModel):
    match_type: MatchType
    outcome: MatchOutcome
    po_number: str | None = None
    gr_numbers: list[str] = Field(default_factory=list)
    vendor_id: str | None = None
    header_variances: list[Variance] = Field(default_factory=list)
    line_matches: list[LineMatch] = Field(default_factory=list)
    exceptions: list[ExceptionType] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    matched_amount: Decimal = Decimal("0")
    confidence: float = 0.0

    @property
    def is_postable(self) -> bool:
        return self.outcome in (MatchOutcome.MATCHED, MatchOutcome.MATCHED_WITHIN_TOLERANCE)

    def summary(self) -> str:
        if self.is_postable:
            return f"{self.match_type.value} match passed against PO {self.po_number}"
        return (
            f"{self.match_type.value} match failed: {', '.join(e.value for e in self.exceptions)}"
        )
