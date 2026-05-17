"""Initial schema — all tables for eligibility verification system

Revision ID: 001
Revises:
Create Date: 2026-05-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgcrypto for gen_random_uuid() if not already enabled
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # ------------------------------------------------------------------ payers
    op.create_table(
        "payers",
        sa.Column("payer_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("payer_name", sa.String(255), nullable=False),
        sa.Column("edi_payer_id", sa.String(50), nullable=False),
        sa.Column("clearinghouse_payer_id", sa.String(100)),
        sa.Column("phone", sa.String(20)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("edi_payer_id", name="uq_payers_edi_payer_id"),
    )

    # ---------------------------------------------------------------- providers
    op.create_table(
        "providers",
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("npi", sa.String(10), nullable=False),
        sa.Column("tax_id", sa.String(20)),
        sa.Column("organization_name", sa.String(255)),
        sa.Column("first_name", sa.String(100)),
        sa.Column("last_name", sa.String(100)),
        sa.Column("provider_type", sa.String(20), nullable=False),
        sa.Column("taxonomy_code", sa.String(20)),
        sa.Column("address_line1", sa.String(255)),
        sa.Column("address_line2", sa.String(255)),
        sa.Column("city", sa.String(100)),
        sa.Column("state", sa.String(2)),
        sa.Column("zip_code", sa.String(10)),
        sa.Column("phone", sa.String(20)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("npi", name="uq_providers_npi"),
        sa.CheckConstraint("provider_type IN ('individual', 'organization')",
                           name="ck_provider_type"),
    )

    # ----------------------------------------------------------------- patients
    op.create_table(
        "patients",
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("date_of_birth", sa.Date, nullable=False),
        sa.Column("gender", sa.String(1)),
        sa.Column("phone", sa.String(20)),
        sa.Column("email", sa.String(255)),
        sa.Column("address_line1", sa.String(255)),
        sa.Column("address_line2", sa.String(255)),
        sa.Column("city", sa.String(100)),
        sa.Column("state", sa.String(2)),
        sa.Column("zip_code", sa.String(10)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("gender IN ('M', 'F', 'U')", name="ck_patient_gender"),
    )

    # -------------------------------------------------------- patient_insurance
    op.create_table(
        "patient_insurance",
        sa.Column("insurance_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("patients.patient_id"), nullable=False),
        sa.Column("payer_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("payers.payer_id"), nullable=False),
        sa.Column("member_id", sa.String(100), nullable=False),
        sa.Column("group_number", sa.String(100)),
        sa.Column("group_name", sa.String(255)),
        sa.Column("plan_name", sa.String(255)),
        sa.Column("subscriber_first_name", sa.String(100)),
        sa.Column("subscriber_last_name", sa.String(100)),
        sa.Column("subscriber_date_of_birth", sa.Date),
        sa.Column("subscriber_member_id", sa.String(100)),
        sa.Column("relationship_to_subscriber", sa.String(20), nullable=False,
                  server_default="self"),
        sa.Column("coverage_type", sa.String(10), nullable=False),
        sa.Column("effective_date", sa.Date),
        sa.Column("termination_date", sa.Date),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "relationship_to_subscriber IN ('self', 'spouse', 'child', 'other')",
            name="ck_subscriber_relationship",
        ),
        sa.CheckConstraint(
            "coverage_type IN ('primary', 'secondary', 'tertiary')",
            name="ck_coverage_type",
        ),
    )
    op.create_index("ix_patient_insurance_patient_id", "patient_insurance", ["patient_id"])

    # --------------------------------------------------------------- appointments
    op.create_table(
        "appointments",
        sa.Column("appointment_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("patients.patient_id"), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("providers.provider_id"), nullable=False),
        sa.Column("insurance_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("patient_insurance.insurance_id")),
        sa.Column("appointment_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("appointment_type", sa.String(100)),
        sa.Column("service_type_code", sa.String(10), nullable=False, server_default="30"),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("eligibility_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("notes", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('scheduled', 'confirmed', 'cancelled', 'completed', 'no_show')",
            name="ck_appointment_status",
        ),
        sa.CheckConstraint(
            "eligibility_status IN ('pending', 'verified', 'gap_found', 'error', 'not_required')",
            name="ck_eligibility_status",
        ),
    )
    op.create_index("ix_appointments_patient_id", "appointments", ["patient_id"])
    op.create_index("ix_appointments_insurance_id", "appointments", ["insurance_id"])
    op.create_index("ix_appointments_datetime", "appointments", ["appointment_datetime"])
    # Partial index — only indexes scheduled appointments, which is what the watcher queries
    op.create_index(
        "ix_appointments_upcoming_pending",
        "appointments",
        ["appointment_datetime", "eligibility_status"],
        postgresql_where=sa.text("status = 'scheduled'"),
    )

    # -------------------------------------------------------- eligibility_checks
    op.create_table(
        "eligibility_checks",
        sa.Column("check_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("appointment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("appointments.appointment_id"), nullable=False),
        sa.Column("insurance_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("patient_insurance.insurance_id"), nullable=False),
        sa.Column("triggered_by", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("attempt_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("responded_at", sa.DateTime(timezone=True)),
        sa.Column("clearinghouse_transaction_id", sa.String(255)),
        sa.Column("error_code", sa.String(50)),
        sa.Column("error_message", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "triggered_by IN ('scheduler', 'manual', 'appointment_created', 'appointment_updated')",
            name="ck_check_triggered_by",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'in_progress', 'completed', 'failed')",
            name="ck_check_status",
        ),
    )
    op.create_index("ix_eligibility_checks_appointment_id", "eligibility_checks", ["appointment_id"])
    op.create_index("ix_eligibility_checks_queued", "eligibility_checks", ["status", "created_at"])

    # ------------------------------------------------------- eligibility_results
    op.create_table(
        "eligibility_results",
        sa.Column("result_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("check_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("eligibility_checks.check_id"), nullable=False),
        sa.Column("coverage_active", sa.Boolean, nullable=False),
        sa.Column("coverage_effective_date", sa.Date),
        sa.Column("coverage_termination_date", sa.Date),
        sa.Column("plan_name", sa.String(255)),
        sa.Column("plan_type", sa.String(50)),
        sa.Column("in_network", sa.Boolean),
        sa.Column("deductible_individual", sa.Numeric(10, 2)),
        sa.Column("deductible_individual_remaining", sa.Numeric(10, 2)),
        sa.Column("deductible_family", sa.Numeric(10, 2)),
        sa.Column("deductible_family_remaining", sa.Numeric(10, 2)),
        sa.Column("oop_max_individual", sa.Numeric(10, 2)),
        sa.Column("oop_max_individual_remaining", sa.Numeric(10, 2)),
        sa.Column("oop_max_family", sa.Numeric(10, 2)),
        sa.Column("oop_max_family_remaining", sa.Numeric(10, 2)),
        sa.Column("copay_amount", sa.Numeric(10, 2)),
        sa.Column("coinsurance_percent", sa.Numeric(5, 2)),
        sa.Column("referral_required", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("prior_auth_required", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("raw_parsed_data", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("check_id", name="uq_eligibility_results_check_id"),
    )

    # ----------------------------------------------------------- coverage_gaps
    op.create_table(
        "coverage_gaps",
        sa.Column("gap_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("check_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("eligibility_checks.check_id"), nullable=False),
        sa.Column("gap_type", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("is_resolved", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("resolved_by", sa.String(255)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_note", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "severity IN ('critical', 'high', 'warning', 'info')",
            name="ck_gap_severity",
        ),
    )
    op.create_index("ix_coverage_gaps_check_id", "coverage_gaps", ["check_id"])
    op.create_index("ix_coverage_gaps_unresolved", "coverage_gaps", ["is_resolved", "severity"])

    # ---------------------------------------------------- edi_transaction_log
    op.create_table(
        "edi_transaction_log",
        sa.Column("log_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("check_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("eligibility_checks.check_id"), nullable=False),
        sa.Column("transaction_type", sa.String(10), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("isa_control_number", sa.String(20), nullable=False),
        sa.Column("raw_edi", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "transaction_type IN ('270', '271', '999', 'TA1')",
            name="ck_edi_transaction_type",
        ),
        sa.CheckConstraint(
            "direction IN ('outbound', 'inbound')",
            name="ck_edi_direction",
        ),
    )
    op.create_index("ix_edi_transaction_log_check_id", "edi_transaction_log", ["check_id"])

    # --------------------------------------------------- edi_control_numbers
    op.create_table(
        "edi_control_numbers",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("control_number", sa.String(20), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("control_number", name="uq_edi_control_numbers"),
    )


def downgrade() -> None:
    op.drop_table("edi_control_numbers")
    op.drop_table("edi_transaction_log")
    op.drop_table("coverage_gaps")
    op.drop_table("eligibility_results")
    op.drop_table("eligibility_checks")
    op.drop_table("appointments")
    op.drop_table("patient_insurance")
    op.drop_table("patients")
    op.drop_table("providers")
    op.drop_table("payers")
