"""Generate the sample invoice and remittance PDFs used by the demo and the evaluation harness.

Each document is built to exercise one branch of the workflow, and the expected outcome is
written alongside it as ground truth so extraction accuracy and match rate are measured against
a fixed answer key rather than a re-run of the model.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "samples" / "inbox"
GROUND_TRUTH = ROOT / "evaluation" / "datasets" / "ground_truth.json"

VAT_RATE = Decimal("0.19")


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "vendor": ParagraphStyle(
            "vendor",
            parent=base["Heading1"],
            fontSize=17,
            spaceAfter=2,
            textColor=colors.HexColor("#1a1a1a"),
        ),
        "tagline": ParagraphStyle(
            "tagline", parent=base["Normal"], fontSize=8.5, textColor=colors.HexColor("#666666")
        ),
        "title": ParagraphStyle(
            "title", parent=base["Heading2"], fontSize=13, spaceBefore=10, spaceAfter=6
        ),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=9, leading=13),
        "small": ParagraphStyle(
            "small",
            parent=base["Normal"],
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor("#555555"),
        ),
    }


def build_invoice_pdf(path: Path, spec: dict[str, Any]) -> None:
    styles = _styles()
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Invoice {spec['invoice_number']}",
        author=spec["vendor_name"],
    )

    story: list[Any] = [
        Paragraph(spec["vendor_name"], styles["vendor"]),
        Paragraph(spec["vendor_address"], styles["tagline"]),
        Paragraph(
            f"VAT ID {spec['vendor_tax_id']} &nbsp;|&nbsp; IBAN {spec['vendor_iban']}",
            styles["tagline"],
        ),
        Spacer(1, 8 * mm),
        Paragraph("INVOICE", styles["title"]),
    ]

    meta_rows = [
        ["Invoice number", spec["invoice_number"], "Invoice date", spec["invoice_date"]],
        ["Purchase order", spec.get("po_number") or "-", "Due date", spec["due_date"]],
        [
            "Bill to",
            Paragraph(spec["customer"], styles["body"]),
            "Payment terms",
            spec["payment_terms"],
        ],
    ]
    meta = Table(meta_rows, colWidths=[28 * mm, 58 * mm, 30 * mm, 42 * mm])
    meta.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#dddddd")),
            ]
        )
    )
    story += [meta, Spacer(1, 7 * mm)]

    header = ["Pos", "Material", "Description", "Qty", "Unit", "Unit price", "Amount"]
    rows: list[list[str]] = [header]
    net = Decimal("0")
    for line in spec["lines"]:
        amount = (Decimal(str(line["quantity"])) * Decimal(str(line["unit_price"]))).quantize(
            Decimal("0.01")
        )
        net += amount
        rows.append(
            [
                str(line["position"]),
                line.get("material", "-"),
                line["description"],
                f"{Decimal(str(line['quantity'])):,.0f}",
                line["unit"],
                _money(Decimal(str(line["unit_price"]))),
                _money(amount),
            ]
        )

    table = Table(rows, colWidths=[11 * mm, 24 * mm, 62 * mm, 16 * mm, 13 * mm, 24 * mm, 26 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story += [table, Spacer(1, 5 * mm)]

    tax = (net * VAT_RATE).quantize(Decimal("0.01"))
    gross = net + tax
    totals = Table(
        [
            ["Net amount", f"{spec['currency']} {_money(net)}"],
            [f"VAT {int(VAT_RATE * 100)}%", f"{spec['currency']} {_money(tax)}"],
            ["Total payable", f"{spec['currency']} {_money(gross)}"],
        ],
        colWidths=[42 * mm, 40 * mm],
        hAlign="RIGHT",
    )
    totals.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.75, colors.black),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story += [
        totals,
        Spacer(1, 8 * mm),
        Paragraph(
            f"Please remit {spec['currency']} {_money(gross)} to {spec['vendor_iban']} quoting "
            f"invoice {spec['invoice_number']}. {spec['payment_terms']} from invoice date.",
            styles["body"],
        ),
        Spacer(1, 4 * mm),
        Paragraph(spec.get("footer", ""), styles["small"]),
    ]
    doc.build(story)

    spec["_computed"] = {"net": str(net), "tax": str(tax), "gross": str(gross)}


def build_remittance_pdf(path: Path, spec: dict[str, Any]) -> None:
    styles = _styles()
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Remittance {spec['remittance_number']}",
        author=spec["customer_name"],
    )

    story: list[Any] = [
        Paragraph(spec["customer_name"], styles["vendor"]),
        Paragraph(spec["customer_address"], styles["tagline"]),
        Spacer(1, 8 * mm),
        Paragraph("REMITTANCE ADVICE", styles["title"]),
    ]

    meta = Table(
        [
            ["Remittance number", spec["remittance_number"], "Payment date", spec["payment_date"]],
            ["Bank reference", spec["bank_reference"], "Currency", spec["currency"]],
        ],
        colWidths=[34 * mm, 48 * mm, 30 * mm, 52 * mm],
    )
    meta.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story += [meta, Spacer(1, 7 * mm)]

    rows = [["Your invoice", "Invoice date", "Gross", "Deduction", "Amount paid"]]
    total = Decimal("0")
    for advice in spec["advices"]:
        paid = Decimal(str(advice["amount_paid"]))
        total += paid
        rows.append(
            [
                advice["invoice_number"],
                advice["invoice_date"],
                _money(Decimal(str(advice["gross"]))),
                _money(Decimal(str(advice.get("deduction", 0)))),
                _money(paid),
            ]
        )
    rows.append(["", "", "", "Total transferred", _money(total)])

    table = Table(rows, colWidths=[34 * mm, 28 * mm, 32 * mm, 32 * mm, 32 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -2), 0.25, colors.HexColor("#cccccc")),
                ("LINEABOVE", (0, -1), (-1, -1), 0.75, colors.black),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story += [
        table,
        Spacer(1, 8 * mm),
        Paragraph(
            f"The above invoices have been settled by bank transfer on {spec['payment_date']} "
            f"under reference {spec['bank_reference']}.",
            styles["body"],
        ),
    ]
    doc.build(story)
    spec["_computed"] = {"total_paid": str(total)}


def _sidecar(path: Path, sender: str, subject: str, message_id: str) -> None:
    path.with_suffix(path.suffix + ".meta.json").write_text(
        json.dumps(
            {
                "message_id": message_id,
                "sender": sender,
                "subject": subject,
                "body": f"Please find attached {subject}.",
            },
            indent=2,
        )
    )


INVOICE_SPECS: list[dict[str, Any]] = [
    {
        "id": "clean_three_way",
        "filename": "INV-2026-0871_contoso.pdf",
        "invoice_number": "INV-2026-0871",
        "invoice_date": "2026-07-28",
        "due_date": "2026-08-27",
        "po_number": "PO-4500001",
        "vendor_name": "Contoso Supplies GmbH",
        "vendor_address": "Industriestrasse 14, 80339 Munich, Germany",
        "vendor_tax_id": "DE811907980",
        "vendor_iban": "DE89370400440532013000",
        "customer": "Northgate Manufacturing GmbH<br/>Company code 1000",
        "payment_terms": "NET30",
        "currency": "EUR",
        "lines": [
            {
                "position": 10,
                "material": "MAT-A100",
                "description": "Steel bracket, galvanised, 120mm",
                "quantity": 500,
                "unit": "EA",
                "unit_price": "14.50",
            },
            {
                "position": 20,
                "material": "MAT-A210",
                "description": "Hex bolt M12x60 stainless",
                "quantity": 1200,
                "unit": "EA",
                "unit_price": "2.90",
            },
        ],
        "footer": "Goods delivered 2026-07-18 against delivery note DN-77120.",
        "expected": {
            "outcome": "matched",
            "match_type": "three_way",
            "exceptions": [],
            "straight_through": True,
        },
    },
    {
        "id": "price_variance",
        "filename": "INV-2026-0872_fabrikam.pdf",
        "invoice_number": "INV-2026-0872",
        "invoice_date": "2026-07-30",
        "due_date": "2026-09-13",
        "po_number": "PO-4500002",
        "vendor_name": "Fabrikam Industrial AG",
        "vendor_address": "Werkstrasse 8, 70565 Stuttgart, Germany",
        "vendor_tax_id": "DE114203847",
        "vendor_iban": "DE02120300000000202051",
        "customer": "Northgate Manufacturing GmbH<br/>Company code 1000",
        "payment_terms": "NET45",
        "currency": "EUR",
        "lines": [
            {
                "position": 10,
                "material": "MAT-B400",
                "description": "Hydraulic pump assembly HP-400",
                "quantity": 12,
                "unit": "EA",
                "unit_price": "735.00",
            },
        ],
        "footer": "Price adjusted per supplier notice SN-2026-11 effective 2026-07-01.",
        "expected": {
            "outcome": "exception",
            "match_type": "three_way",
            "exceptions": ["price_variance", "total_variance"],
            "straight_through": False,
        },
    },
    {
        "id": "goods_receipt_shortfall",
        "filename": "INV-2026-0873_northwind.pdf",
        "invoice_number": "INV-2026-0873",
        "invoice_date": "2026-08-01",
        "due_date": "2026-08-31",
        "po_number": "PO-4500003",
        "vendor_name": "Northwind Logistics BV",
        "vendor_address": "Havenweg 220, 3089 JJ Rotterdam, Netherlands",
        "vendor_tax_id": "NL004495445B01",
        "vendor_iban": "NL91ABNA0417164300",
        "customer": "Northgate Manufacturing GmbH<br/>Company code 1000",
        "payment_terms": "NET30",
        "currency": "EUR",
        "lines": [
            {
                "position": 10,
                "material": "SRV-FRT",
                "description": "Road freight Rotterdam-Munich, per shipment",
                "quantity": 30,
                "unit": "SHP",
                "unit_price": "200.00",
            },
        ],
        "footer": "Covers shipments 2026-07-01 through 2026-07-31.",
        "expected": {
            "outcome": "exception",
            "match_type": "three_way",
            "exceptions": ["missing_goods_receipt"],
            "straight_through": False,
        },
    },
    {
        "id": "clean_two_way",
        "filename": "INV-2026-0874_adventureworks.pdf",
        "invoice_number": "INV-2026-0874",
        "invoice_date": "2026-08-04",
        "due_date": "2026-08-18",
        "po_number": "PO-4500004",
        "vendor_name": "Adventure Works Ltd",
        "vendor_address": "42 Kingsway, London WC2B 6EX, United Kingdom",
        "vendor_tax_id": "GB123456789",
        "vendor_iban": "GB29NWBK60161331926819",
        "customer": "Northgate Manufacturing GmbH<br/>Company code 1000",
        "payment_terms": "NET14",
        "currency": "EUR",
        "lines": [
            {
                "position": 10,
                "material": "SRV-CONS",
                "description": "SAP S/4HANA advisory, senior consultant day rate",
                "quantity": 4,
                "unit": "DAY",
                "unit_price": "900.00",
            },
        ],
        "footer": "Service period 2026-07-21 to 2026-07-24. No goods receipt required.",
        "expected": {
            "outcome": "matched",
            "match_type": "two_way",
            "exceptions": [],
            "straight_through": True,
        },
    },
    {
        "id": "over_auto_post_ceiling",
        "filename": "INV-2026-0875_tailspin.pdf",
        "invoice_number": "INV-2026-0875",
        "invoice_date": "2026-08-06",
        "due_date": "2026-10-05",
        "po_number": "PO-4500005",
        "vendor_name": "Tailspin Components SA",
        "vendor_address": "12 Rue de l'Industrie, 69100 Villeurbanne, France",
        "vendor_tax_id": "FR40303265045",
        "vendor_iban": "FR1420041010050500013M02606",
        "customer": "Northgate Manufacturing GmbH<br/>Company code 1000",
        "payment_terms": "NET60",
        "currency": "EUR",
        "lines": [
            {
                "position": 10,
                "material": "MAT-C900",
                "description": "CNC machined housing, aluminium 6061",
                "quantity": 340,
                "unit": "EA",
                "unit_price": "118.50",
            },
        ],
        "footer": "Delivered against GR-5000005 on 2026-08-03.",
        "expected": {
            "outcome": "exception",
            "match_type": "three_way",
            "exceptions": ["over_auto_post_ceiling"],
            "straight_through": False,
        },
    },
    {
        "id": "missing_po_unknown_vendor",
        "filename": "INV-2026-0876_litware.pdf",
        "invoice_number": "INV-2026-0876",
        "invoice_date": "2026-08-08",
        "due_date": "2026-09-07",
        "po_number": None,
        "vendor_name": "Litware Marketing Ltd",
        "vendor_address": "8 Harbour Court, Dublin D02 XY45, Ireland",
        "vendor_tax_id": "IE9825613N",
        "vendor_iban": "IE29AIBK93115212345678",
        "customer": "Northgate Manufacturing GmbH<br/>Company code 1000",
        "payment_terms": "NET30",
        "currency": "EUR",
        "lines": [
            {
                "position": 1,
                "material": "-",
                "description": "Trade fair stand design and production, Hannover Messe",
                "quantity": 1,
                "unit": "LOT",
                "unit_price": "4500.00",
            },
        ],
        "footer": "No purchase order was raised for this engagement.",
        "expected": {
            "outcome": "exception",
            "match_type": "non_po",
            "exceptions": ["missing_po", "unknown_vendor"],
            "straight_through": False,
        },
    },
    {
        "id": "duplicate_submission",
        "filename": "INV-2026-0871_contoso_resubmission.pdf",
        "invoice_number": "INV-2026-0871",
        "invoice_date": "2026-08-11",
        "due_date": "2026-08-27",
        "po_number": "PO-4500001",
        "vendor_name": "Contoso Supplies GmbH",
        "vendor_address": "Industriestrasse 14, 80339 Munich, Germany",
        "vendor_tax_id": "DE811907980",
        "vendor_iban": "DE89370400440532013000",
        "customer": "Northgate Manufacturing GmbH<br/>Company code 1000",
        "payment_terms": "NET30",
        "currency": "EUR",
        "lines": [
            {
                "position": 10,
                "material": "MAT-A100",
                "description": "Steel bracket, galvanised, 120mm",
                "quantity": 500,
                "unit": "EA",
                "unit_price": "14.50",
            },
            {
                "position": 20,
                "material": "MAT-A210",
                "description": "Hex bolt M12x60 stainless",
                "quantity": 1200,
                "unit": "EA",
                "unit_price": "2.90",
            },
        ],
        "footer": "Reminder - this invoice remains unpaid. Reprinted 2026-08-11.",
        "expected": {
            "outcome": "exception",
            "match_type": "three_way",
            "exceptions": ["duplicate_invoice"],
            "straight_through": False,
            "requires_prior": "clean_three_way",
        },
    },
]

REMITTANCE_SPECS: list[dict[str, Any]] = [
    {
        "id": "remittance_exact",
        "filename": "REM-2026-0042_globex.pdf",
        "remittance_number": "REM-2026-0042",
        "payment_date": "2026-08-29",
        "bank_reference": "SEPA-CT-88213401",
        "currency": "EUR",
        "customer_name": "Globex Manufacturing SE",
        "customer_address": "Am Hafen 3, 20457 Hamburg, Germany",
        "advices": [
            {
                "invoice_number": "SI-2026-0431",
                "invoice_date": "2026-07-31",
                "gross": "18400.00",
                "deduction": "0.00",
                "amount_paid": "18400.00",
            },
            {
                "invoice_number": "SI-2026-0448",
                "invoice_date": "2026-08-07",
                "gross": "7250.00",
                "deduction": "0.00",
                "amount_paid": "7250.00",
            },
        ],
        "expected": {"applied_items": 2, "residual": "0.00", "straight_through": True},
    },
]


def main() -> None:
    INBOX.mkdir(parents=True, exist_ok=True)
    GROUND_TRUTH.parent.mkdir(parents=True, exist_ok=True)

    truth: dict[str, Any] = {"invoices": [], "remittances": []}

    for spec in INVOICE_SPECS:
        path = INBOX / spec["filename"]
        build_invoice_pdf(path, spec)
        _sidecar(
            path,
            f"ap@{spec['vendor_name'].split()[0].lower()}.example.com",
            f"Invoice {spec['invoice_number']}",
            f"msg-{spec['id']}",
        )
        computed = spec["_computed"]
        truth["invoices"].append(
            {
                "id": spec["id"],
                "file": spec["filename"],
                "fields": {
                    "invoice_number": spec["invoice_number"],
                    "invoice_date": spec["invoice_date"],
                    "due_date": spec["due_date"],
                    "purchase_order_number": spec["po_number"],
                    "vendor_name": spec["vendor_name"],
                    "vendor_tax_id": spec["vendor_tax_id"],
                    "vendor_iban": spec["vendor_iban"],
                    "currency": spec["currency"],
                    "subtotal": computed["net"],
                    "tax_amount": computed["tax"],
                    "total_amount": computed["gross"],
                    "payment_terms": spec["payment_terms"],
                    "line_count": len(spec["lines"]),
                },
                "line_items": [
                    {
                        "po_line_number": line["position"],
                        "material_code": line["material"],
                        "description": line["description"],
                        "quantity": str(line["quantity"]),
                        "unit_price": line["unit_price"],
                    }
                    for line in spec["lines"]
                ],
                "expected": spec["expected"],
            }
        )
        print(f"wrote {path.relative_to(ROOT)}  gross={computed['gross']}")

    for spec in REMITTANCE_SPECS:
        path = INBOX / spec["filename"]
        build_remittance_pdf(path, spec)
        _sidecar(
            path,
            "ar@globex.example.com",
            f"Remittance advice {spec['remittance_number']}",
            f"msg-{spec['id']}",
        )
        truth["remittances"].append(
            {
                "id": spec["id"],
                "file": spec["filename"],
                "fields": {
                    "remittance_number": spec["remittance_number"],
                    "payment_date": spec["payment_date"],
                    "customer_name": spec["customer_name"],
                    "currency": spec["currency"],
                    "bank_reference": spec["bank_reference"],
                    "total_paid": spec["_computed"]["total_paid"],
                    "advice_count": len(spec["advices"]),
                },
                "advices": spec["advices"],
                "expected": spec["expected"],
            }
        )
        print(f"wrote {path.relative_to(ROOT)}  total={spec['_computed']['total_paid']}")

    GROUND_TRUTH.write_text(json.dumps(truth, indent=2) + "\n")
    print(f"wrote {GROUND_TRUTH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
