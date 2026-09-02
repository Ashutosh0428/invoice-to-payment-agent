"""Score extraction accuracy, match rate and STP against the ground-truth answer key.

Two modes:

  --mode extraction  parses and extracts locally. Needs Docling and Ollama only.
  --mode e2e         drives the running API end to end. Needs the full compose stack.

Writes evaluation/results/evaluation_report.json and .md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from evaluation.metrics import DocumentScore, RunOutcome, aggregate, score_fields, score_lines

ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH = ROOT / "evaluation" / "datasets" / "ground_truth.json"
RESULTS = ROOT / "evaluation" / "results"
INBOX = ROOT / "samples" / "inbox"


def load_ground_truth() -> dict[str, Any]:
    return json.loads(GROUND_TRUTH.read_text())


def _resolve(filename: str) -> Path:
    candidate = INBOX / filename
    if candidate.exists():
        return candidate
    processed = INBOX / "processed" / filename
    if processed.exists():
        return processed
    raise FileNotFoundError(f"{filename} not found in {INBOX} or its processed folder")


async def evaluate_extraction(
    truth: dict[str, Any],
) -> tuple[list[DocumentScore], list[RunOutcome]]:
    from invoice_agent.agents.prompts import INVOICE_EXTRACTION_SYSTEM, INVOICE_EXTRACTION_USER
    from invoice_agent.ingestion.confidence import score_extraction
    from invoice_agent.ingestion.parser import parse_document
    from invoice_agent.llm.structured import extract_structured
    from invoice_agent.schemas.invoice import ExtractedInvoice

    scores: list[DocumentScore] = []

    for case in truth["invoices"]:
        score = DocumentScore(document_id=case["id"], file=case["file"])
        try:
            started = time.perf_counter()
            parsed = parse_document(_resolve(case["file"]))
            score.parse_seconds = time.perf_counter() - started

            started = time.perf_counter()
            invoice = await extract_structured(
                INVOICE_EXTRACTION_SYSTEM,
                INVOICE_EXTRACTION_USER.format(document=parsed.markdown[:24000]),
                ExtractedInvoice,
            )
            score.extract_seconds = time.perf_counter() - started

            confidence, _ = score_extraction(invoice)
            invoice.confidence = confidence

            actual = invoice.model_dump(mode="json")
            actual["line_count"] = len(invoice.line_items)

            field_score = score_fields(case["fields"], actual)
            score.fields_total = field_score.fields_total
            score.fields_correct = field_score.fields_correct
            score.field_results = field_score.field_results
            score.lines_expected, score.lines_correct = score_lines(
                case["line_items"], actual.get("line_items", [])
            )
            score.confidence = confidence
            print(
                f"  {case['id']:<28} fields {score.fields_correct}/{score.fields_total}  "
                f"lines {score.lines_correct}/{score.lines_expected}  "
                f"conf {confidence:.2f}  {score.extract_seconds:.1f}s"
            )
        except Exception as exc:
            score.error = str(exc)[:300]
            print(f"  {case['id']:<28} FAILED: {score.error}")
        scores.append(score)

    return scores, []


async def evaluate_end_to_end(
    truth: dict[str, Any], base_url: str, token: str | None
) -> tuple[list[DocumentScore], list[RunOutcome]]:
    scores: list[DocumentScore] = []
    outcomes: list[RunOutcome] = []
    headers = {"X-API-Key": token} if token else {}

    async with httpx.AsyncClient(base_url=base_url, timeout=600.0, headers=headers) as client:
        for case in truth["invoices"]:
            score = DocumentScore(document_id=case["id"], file=case["file"])
            path = _resolve(case["file"])
            started = time.perf_counter()
            try:
                with path.open("rb") as handle:
                    response = await client.post(
                        "/api/v1/ingest-invoice",
                        files={"file": (path.name, handle, "application/pdf")},
                        data={"message_id": f"eval-{case['id']}"},
                    )
                response.raise_for_status()
                body = response.json()
                score.extract_seconds = time.perf_counter() - started

                run = await client.get(f"/api/v1/runs/{body['run_id']}")
                run.raise_for_status()
                run_body = run.json()

                match = run_body.get("match") or {}
                expected = case["expected"]
                actual_outcome = match.get("outcome", "unknown")
                if actual_outcome == "matched_within_tolerance":
                    actual_outcome = "matched"

                outcomes.append(
                    RunOutcome(
                        document_id=case["id"],
                        expected_outcome=expected["outcome"],
                        actual_outcome=actual_outcome,
                        expected_exceptions=expected["exceptions"],
                        actual_exceptions=match.get("exceptions", []),
                        expected_stp=expected["straight_through"],
                        actual_stp=bool(run_body.get("straight_through")),
                    )
                )

                audit = await client.get(
                    "/api/v1/audit-log", params={"run_id": body["run_id"], "limit": 200}
                )
                extracted = next(
                    (
                        event["payload"]["invoice"]
                        for event in audit.json()["events"]
                        if event["action"] == "fields_extracted" and "invoice" in event["payload"]
                    ),
                    None,
                )
                if extracted is not None:
                    extracted["line_count"] = len(extracted.get("line_items", []))
                    field_score = score_fields(case["fields"], extracted)
                    score.fields_total = field_score.fields_total
                    score.fields_correct = field_score.fields_correct
                    score.field_results = field_score.field_results
                    score.lines_expected, score.lines_correct = score_lines(
                        case["line_items"], extracted.get("line_items", [])
                    )
                    score.confidence = float(extracted.get("confidence", 0.0))

                print(
                    f"  {case['id']:<28} outcome {actual_outcome:<10} "
                    f"stp={run_body.get('straight_through')}  "
                    f"fields {score.fields_correct}/{score.fields_total}"
                )
            except Exception as exc:
                score.error = str(exc)[:300]
                print(f"  {case['id']:<28} FAILED: {score.error}")
            scores.append(score)

    return scores, outcomes


def write_report(summary: dict[str, Any], scores: list[DocumentScore], mode: str) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    summary["mode"] = mode
    summary["generated_at"] = datetime.now(UTC).isoformat()
    summary["documents"] = [
        {
            "id": s.document_id,
            "file": s.file,
            "field_accuracy": round(s.field_accuracy, 4),
            "line_accuracy": round(s.line_accuracy, 4),
            "confidence": round(s.confidence, 4),
            "parse_seconds": round(s.parse_seconds, 2),
            "extract_seconds": round(s.extract_seconds, 2),
            "failed_fields": [name for name, ok in s.field_results.items() if not ok],
            "error": s.error,
        }
        for s in scores
    ]

    json_path = RESULTS / "evaluation_report.json"
    json_path.write_text(json.dumps(summary, indent=2) + "\n")

    extraction = summary["extraction"]
    matching = summary["matching"]
    stp = summary["straight_through"]

    lines = [
        "# Evaluation Report",
        "",
        f"Mode: `{mode}`  ",
        f"Generated: {summary['generated_at']}  ",
        f"Documents: {summary['documents_processed']} processed, "
        f"{summary['documents_failed']} failed",
        "",
        "## Headline metrics",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Extraction field accuracy | {extraction['field_accuracy']:.1%} "
        f"({extraction['fields_correct']}/{extraction['fields_total']}) |",
        f"| Line item accuracy | {extraction['line_item_accuracy']:.1%} "
        f"({extraction['lines_correct']}/{extraction['lines_expected']}) |",
        f"| Mean extraction confidence | {extraction['mean_confidence']:.2f} |",
        f"| Match rate | {matching['match_rate']:.1%} "
        f"({matching['outcomes_correct']}/{matching['outcomes_total']}) |",
        f"| Exception detection rate | {matching['exception_detection_rate']:.1%} |",
        f"| Straight-through rate | {stp['stp_rate']:.1%} ({stp['auto_posted']} auto-posted) |",
        f"| STP decision accuracy | {stp['stp_decision_accuracy']:.1%} |",
        f"| Mean parse time | {extraction['mean_parse_seconds']:.2f}s |",
        f"| Mean extraction time | {extraction['mean_extract_seconds']:.2f}s |",
        "",
        "## Per-field accuracy",
        "",
        "| Field | Accuracy |",
        "|---|---|",
    ]
    lines += [
        f"| `{name}` | {value:.1%} |" for name, value in summary["per_field_accuracy"].items()
    ]
    lines += [
        "",
        "## Per-document detail",
        "",
        "| Document | Fields | Lines | Confidence | Extract (s) | Failed fields |",
        "|---|---|---|---|---|---|",
    ]
    for doc in summary["documents"]:
        failed = ", ".join(f"`{f}`" for f in doc["failed_fields"]) or "-"
        lines.append(
            f"| {doc['id']} | {doc['field_accuracy']:.0%} | {doc['line_accuracy']:.0%} | "
            f"{doc['confidence']:.2f} | {doc['extract_seconds']:.1f} | {failed} |"
        )

    md_path = RESULTS / "evaluation_report.md"
    md_path.write_text("\n".join(lines) + "\n")
    return json_path


async def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the invoice-to-payment agent")
    parser.add_argument("--mode", choices=["extraction", "e2e"], default="extraction")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--token", default=None)
    args = parser.parse_args()

    truth = load_ground_truth()
    print(f"Evaluating {len(truth['invoices'])} invoices in {args.mode} mode\n")

    if args.mode == "extraction":
        scores, outcomes = await evaluate_extraction(truth)
    else:
        scores, outcomes = await evaluate_end_to_end(truth, args.base_url, args.token)

    summary = aggregate(scores, outcomes)
    path = write_report(summary, scores, args.mode)

    print(f"\nField accuracy      {summary['extraction']['field_accuracy']:.1%}")
    print(f"Line item accuracy  {summary['extraction']['line_item_accuracy']:.1%}")
    if outcomes:
        print(f"Match rate          {summary['matching']['match_rate']:.1%}")
        print(f"STP rate            {summary['straight_through']['stp_rate']:.1%}")
    print(f"\nReport written to {path.parent.relative_to(ROOT)}/")


if __name__ == "__main__":
    asyncio.run(main())
