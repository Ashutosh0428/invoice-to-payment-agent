"""Initial schema: workflow runs, invoices, matches, exceptions, journals, remittances, audit.

Revision ID: 0001
Revises:
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "workflow_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("thread_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_message_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("source_mailbox", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("source_sender", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("source_subject", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("source_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attachment_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("document_path", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("document_sha256", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("straight_through", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workflow_runs_thread_id", "workflow_runs", ["thread_id"])
    op.create_index("ix_workflow_runs_kind", "workflow_runs", ["kind"])
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])
    op.create_index("ix_workflow_runs_source_message_id", "workflow_runs", ["source_message_id"])
    op.create_index("ix_workflow_runs_document_sha256", "workflow_runs", ["document_sha256"])
    op.create_index("ix_workflow_runs_straight_through", "workflow_runs", ["straight_through"])
    op.create_index("ix_workflow_runs_created_at", "workflow_runs", ["created_at"])
    op.create_index("ix_workflow_runs_status_created", "workflow_runs", ["status", "created_at"])

    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_number", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("purchase_order_number", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("vendor_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("vendor_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("vendor_tax_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("vendor_iban", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("currency", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("subtotal", sa.Numeric(18, 2), nullable=True),
        sa.Column("tax_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("total_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("payment_terms", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("line_items", postgresql.JSONB(), nullable=True),
        sa.Column("field_confidence", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fingerprint", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("raw_markdown", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"]),
    )
    op.create_index("ix_invoices_run_id", "invoices", ["run_id"])
    op.create_index("ix_invoices_invoice_number", "invoices", ["invoice_number"])
    op.create_index("ix_invoices_purchase_order_number", "invoices", ["purchase_order_number"])
    op.create_index("ix_invoices_vendor_id", "invoices", ["vendor_id"])
    op.create_index("ix_invoices_fingerprint", "invoices", ["fingerprint"])
    op.create_index("ix_invoices_vendor_number", "invoices", ["vendor_name", "invoice_number"])

    op.create_table(
        "match_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("match_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("outcome", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("po_number", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("gr_numbers", postgresql.JSONB(), nullable=True),
        sa.Column("matched_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("header_variances", postgresql.JSONB(), nullable=True),
        sa.Column("line_matches", postgresql.JSONB(), nullable=True),
        sa.Column("exceptions", postgresql.JSONB(), nullable=True),
        sa.Column("reasons", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"]),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
    )
    op.create_index("ix_match_results_run_id", "match_results", ["run_id"])
    op.create_index("ix_match_results_outcome", "match_results", ["outcome"])
    op.create_index("ix_match_results_po_number", "match_results", ["po_number"])

    op.create_table(
        "exception_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exception_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("severity", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("suggested_action", sa.Text(), nullable=True),
        sa.Column("assigned_to", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("resolved_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"]),
    )
    op.create_index("ix_exception_cases_run_id", "exception_cases", ["run_id"])
    op.create_index("ix_exception_cases_exception_type", "exception_cases", ["exception_type"])
    op.create_index("ix_exception_cases_status", "exception_cases", ["status"])
    op.create_index("ix_exception_cases_created_at", "exception_cases", ["created_at"])
    op.create_index(
        "ix_exception_cases_status_created", "exception_cases", ["status", "created_at"]
    )

    op.create_table(
        "payment_journals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("erp_document_number", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("posting_date", sa.Date(), nullable=True),
        sa.Column("reference", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("company_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("currency", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("total_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("lines", postgresql.JSONB(), nullable=True),
        sa.Column("approved_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"]),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.UniqueConstraint("run_id", name="uq_payment_journals_run"),
    )
    op.create_index("ix_payment_journals_run_id", "payment_journals", ["run_id"])
    op.create_index("ix_payment_journals_status", "payment_journals", ["status"])
    op.create_index(
        "ix_payment_journals_erp_document_number", "payment_journals", ["erp_document_number"]
    )

    op.create_table(
        "remittances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("remittance_number", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("customer_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("customer_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("currency", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("total_paid", sa.Numeric(18, 2), nullable=True),
        sa.Column("bank_reference", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("advices", postgresql.JSONB(), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("applications", postgresql.JSONB(), nullable=True),
        sa.Column("residual_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"]),
    )
    op.create_index("ix_remittances_run_id", "remittances", ["run_id"])
    op.create_index("ix_remittances_remittance_number", "remittances", ["remittance_number"])
    op.create_index("ix_remittances_customer_id", "remittances", ["customer_id"])
    op.create_index("ix_remittances_applied", "remittances", ["applied"])

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("action", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("actor", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("node", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("source_message_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("document_sha256", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"]),
    )
    op.create_index("ix_audit_events_run_id", "audit_events", ["run_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_index("ix_audit_events_run_seq", "audit_events", ["run_id", "sequence"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("remittances")
    op.drop_table("payment_journals")
    op.drop_table("exception_cases")
    op.drop_table("match_results")
    op.drop_table("invoices")
    op.drop_table("workflow_runs")
