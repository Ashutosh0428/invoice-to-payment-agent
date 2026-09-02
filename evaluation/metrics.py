"""Scoring rules for extraction accuracy, match rate and straight-through processing.

Field comparison is type-aware on purpose: identifiers must match exactly after normalisation,
money is compared at two decimal places, and dates are compared as dates. Scoring every field
as a string would fail a correct 1.234,56 -> 1234.56 conversion and reward a model that copies
the printed text without understanding it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

MONEY_FIELDS = {"subtotal", "tax_amount", "total_amount"}
DATE_FIELDS = {"invoice_date", "due_date", "payment_date"}
IDENTIFIER_FIELDS = {
    "invoice_number",
    "purchase_order_number",
    "vendor_tax_id",
    "vendor_iban",
    "currency",
    "remittance_number",
    "bank_reference",
}
MONEY_TOLERANCE = Decimal("0.01")


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _normalise_identifier(value: Any) -> str:
    return str(value or "").replace(" ", "").replace("-", "").upper()


def compare_field(name: str, expected: Any, actual: Any) -> bool:
    if expected is None:
        return actual is None
    if name in MONEY_FIELDS:
        left, right = _as_decimal(expected), _as_decimal(actual)
        return left is not None and right is not None and abs(left - right) <= MONEY_TOLERANCE
    if name in DATE_FIELDS:
        return _as_date(expected) == _as_date(actual)
    if name in IDENTIFIER_FIELDS:
        return _normalise_identifier(expected) == _normalise_identifier(actual)
    if name == "line_count":
        return int(expected) == int(actual or 0)
    return str(expected or "").strip().lower() == str(actual or "").strip().lower()


@dataclass(slots=True)
class DocumentScore:
    document_id: str
    file: str
    fields_total: int = 0
    fields_correct: int = 0
    field_results: dict[str, bool] = field(default_factory=dict)
    lines_expected: int = 0
    lines_correct: int = 0
    confidence: float = 0.0
    parse_seconds: float = 0.0
    extract_seconds: float = 0.0
    error: str | None = None

    @property
    def field_accuracy(self) -> float:
        return self.fields_correct / self.fields_total if self.fields_total else 0.0

    @property
    def line_accuracy(self) -> float:
        return self.lines_correct / self.lines_expected if self.lines_expected else 0.0


def score_fields(expected: dict[str, Any], actual: dict[str, Any]) -> DocumentScore:
    score = DocumentScore(document_id="", file="")
    for name, want in expected.items():
        got = actual.get(name)
        correct = compare_field(name, want, got)
        score.field_results[name] = correct
        score.fields_total += 1
        score.fields_correct += int(correct)
    return score


def score_lines(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> tuple[int, int]:
    """A line counts as correct only when quantity and unit price both match.

    Matching on description alone would score a hallucinated price as a success, which is the
    error mode that actually costs money in accounts payable.
    """
    correct = 0
    for want in expected:
        wanted_qty = _as_decimal(want.get("quantity"))
        wanted_price = _as_decimal(want.get("unit_price"))
        for got in actual:
            got_qty = _as_decimal(got.get("quantity"))
            got_price = _as_decimal(got.get("unit_price"))
            if (
                wanted_qty is not None
                and got_qty is not None
                and abs(wanted_qty - got_qty) <= MONEY_TOLERANCE
                and wanted_price is not None
                and got_price is not None
                and abs(wanted_price - got_price) <= MONEY_TOLERANCE
            ):
                correct += 1
                break
    return len(expected), correct


@dataclass(slots=True)
class RunOutcome:
    document_id: str
    expected_outcome: str
    actual_outcome: str
    expected_exceptions: list[str]
    actual_exceptions: list[str]
    expected_stp: bool
    actual_stp: bool

    @property
    def outcome_correct(self) -> bool:
        return self.expected_outcome == self.actual_outcome

    @property
    def exceptions_correct(self) -> bool:
        return set(self.expected_exceptions).issubset(set(self.actual_exceptions))

    @property
    def stp_correct(self) -> bool:
        return self.expected_stp == self.actual_stp


def aggregate(scores: list[DocumentScore], outcomes: list[RunOutcome]) -> dict[str, Any]:
    scored = [s for s in scores if s.error is None]
    fields_total = sum(s.fields_total for s in scored)
    fields_correct = sum(s.fields_correct for s in scored)
    lines_expected = sum(s.lines_expected for s in scored)
    lines_correct = sum(s.lines_correct for s in scored)

    return {
        "documents_total": len(scores),
        "documents_processed": len(scored),
        "documents_failed": len(scores) - len(scored),
        "extraction": {
            "field_accuracy": round(fields_correct / fields_total, 4) if fields_total else 0.0,
            "fields_correct": fields_correct,
            "fields_total": fields_total,
            "line_item_accuracy": (
                round(lines_correct / lines_expected, 4) if lines_expected else 0.0
            ),
            "lines_correct": lines_correct,
            "lines_expected": lines_expected,
            "mean_confidence": (
                round(sum(s.confidence for s in scored) / len(scored), 4) if scored else 0.0
            ),
            "mean_parse_seconds": (
                round(sum(s.parse_seconds for s in scored) / len(scored), 2) if scored else 0.0
            ),
            "mean_extract_seconds": (
                round(sum(s.extract_seconds for s in scored) / len(scored), 2) if scored else 0.0
            ),
        },
        "matching": {
            "match_rate": (
                round(sum(o.outcome_correct for o in outcomes) / len(outcomes), 4)
                if outcomes
                else 0.0
            ),
            "exception_detection_rate": (
                round(sum(o.exceptions_correct for o in outcomes) / len(outcomes), 4)
                if outcomes
                else 0.0
            ),
            "outcomes_correct": sum(o.outcome_correct for o in outcomes),
            "outcomes_total": len(outcomes),
        },
        "straight_through": {
            "stp_rate": (
                round(sum(o.actual_stp for o in outcomes) / len(outcomes), 4) if outcomes else 0.0
            ),
            "expected_stp_rate": (
                round(sum(o.expected_stp for o in outcomes) / len(outcomes), 4) if outcomes else 0.0
            ),
            "stp_decision_accuracy": (
                round(sum(o.stp_correct for o in outcomes) / len(outcomes), 4) if outcomes else 0.0
            ),
            "auto_posted": sum(o.actual_stp for o in outcomes),
        },
        "per_field_accuracy": _per_field(scored),
    }


def _per_field(scores: list[DocumentScore]) -> dict[str, float]:
    totals: dict[str, list[bool]] = {}
    for score in scores:
        for name, correct in score.field_results.items():
            totals.setdefault(name, []).append(correct)
    return {name: round(sum(results) / len(results), 4) for name, results in sorted(totals.items())}
