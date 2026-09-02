# Evaluation Report

Mode: `extraction`  
Generated: 2026-09-02T16:45:50.720370+00:00  
Documents: 7 processed, 0 failed

## Headline metrics

| Metric | Value |
|---|---|
| Extraction field accuracy | 94.5% (86/91) |
| Line item accuracy | 100.0% (9/9) |
| Mean extraction confidence | 0.84 |
| Match rate | 0.0% (0/0) |
| Exception detection rate | 0.0% |
| Straight-through rate | 0.0% (0 auto-posted) |
| STP decision accuracy | 0.0% |
| Mean parse time | 81.01s |
| Mean extraction time | 156.60s |

## Per-field accuracy

| Field | Accuracy |
|---|---|
| `currency` | 100.0% |
| `due_date` | 100.0% |
| `invoice_date` | 100.0% |
| `invoice_number` | 100.0% |
| `line_count` | 100.0% |
| `payment_terms` | 100.0% |
| `purchase_order_number` | 100.0% |
| `subtotal` | 28.6% |
| `tax_amount` | 100.0% |
| `total_amount` | 100.0% |
| `vendor_iban` | 100.0% |
| `vendor_name` | 100.0% |
| `vendor_tax_id` | 100.0% |

## Per-document detail

| Document | Fields | Lines | Confidence | Extract (s) | Failed fields |
|---|---|---|---|---|---|
| clean_three_way | 92% | 100% | 0.82 | 228.0 | `subtotal` |
| price_variance | 92% | 100% | 0.81 | 140.3 | `subtotal` |
| goods_receipt_shortfall | 92% | 100% | 0.82 | 117.4 | `subtotal` |
| clean_two_way | 92% | 100% | 0.81 | 134.7 | `subtotal` |
| over_auto_post_ceiling | 92% | 100% | 0.68 | 186.4 | `subtotal` |
| missing_po_unknown_vendor | 100% | 100% | 0.94 | 134.1 | - |
| duplicate_submission | 100% | 100% | 0.98 | 155.3 | - |
