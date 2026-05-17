"""HIPAA: encrypted PHI columns + audit_logs table

PHI columns in patients and patient_insurance are changed from VARCHAR/Date
to TEXT so they can hold Fernet ciphertext (Fernet output is always TEXT).

For a fresh database this migration just changes column types with no data
to move.  For a database with existing plaintext rows, encrypt the rows
FIRST using scripts/encrypt_existing_data.py before running this migration.

Revision ID: 002
Revises: 001
Create Date: 2026-05-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── patients: PHI columns → TEXT (Fernet ciphertext) ──────────────────
    for col in ("first_name", "last_name", "phone", "email",
                "address_line1", "address_line2", "city", "zip_code"):
        op.alter_column("patients", col, type_=sa.Text())

    # date_of_birth: Date → TEXT (encrypted ISO string)
    op.alter_column(
        "patients", "date_of_birth",
        type_=sa.Text(),
        postgresql_using="date_of_birth::TEXT",
    )

    # ── patient_insurance: PHI columns → TEXT ─────────────────────────────
    op.alter_column("patient_insurance", "member_id", type_=sa.Text())
    for col in ("subscriber_first_name", "subscriber_last_name", "subscriber_member_id"):
        op.alter_column("patient_insurance", col, type_=sa.Text())

    for col in ("subscriber_date_of_birth", "effective_date", "termination_date"):
        op.alter_column(
            "patient_insurance", col,
            type_=sa.Text(),
            postgresql_using=f"{col}::TEXT",
        )

    # ── audit_logs ─────────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(36)),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("detail", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_logs_resource", "audit_logs", ["resource_type", "resource_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_logs")

    # Restore patient_insurance columns
    for col in ("subscriber_first_name", "subscriber_last_name",
                "subscriber_member_id"):
        op.alter_column("patient_insurance", col, type_=sa.String(100))
    op.alter_column("patient_insurance", "member_id", type_=sa.String(100))
    for col in ("subscriber_date_of_birth", "effective_date", "termination_date"):
        op.alter_column(
            "patient_insurance", col,
            type_=sa.Date(),
            postgresql_using=f"{col}::DATE",
        )

    # Restore patient columns
    for col, length in [
        ("first_name", 100), ("last_name", 100), ("phone", 20),
        ("email", 255), ("address_line1", 255), ("address_line2", 255),
        ("city", 100), ("zip_code", 10),
    ]:
        op.alter_column("patients", col, type_=sa.String(length))
    op.alter_column(
        "patients", "date_of_birth",
        type_=sa.Date(),
        postgresql_using="date_of_birth::DATE",
    )
