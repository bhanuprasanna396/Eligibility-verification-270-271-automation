"""
Three tables that capture the full lifecycle of one eligibility check:

  EligibilityCheck  — the attempt itself (was it sent? did it succeed?)
  EligibilityResult — the parsed coverage data extracted from the 271
  CoverageGap       — each specific problem the gap analyzer found
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    String, Boolean, Date, DateTime, ForeignKey,
    CheckConstraint, Numeric, Text, Integer, Index, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.types import JSONBCompatible


class EligibilityCheck(Base):
    """
    One row per verification attempt.
    Multiple attempts may exist for one appointment (retries, re-verifications).
    """
    __tablename__ = "eligibility_checks"

    __table_args__ = (
        CheckConstraint(
            "triggered_by IN ('scheduler', 'manual', 'appointment_created', 'appointment_updated')",
            name="ck_check_triggered_by",
        ),
        CheckConstraint(
            "status IN ('queued', 'in_progress', 'completed', 'failed')",
            name="ck_check_status",
        ),
        # Index for the worker to pick up queued jobs
        Index("ix_eligibility_checks_queued", "status", "created_at"),
    )

    check_id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    appointment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("appointments.appointment_id"), nullable=False, index=True
    )
    insurance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient_insurance.insurance_id"), nullable=False
    )
    triggered_by: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    # Tracks how many times we've tried (max 3 before dead letter)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Timestamps for SLA monitoring
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # ID returned by the clearinghouse — used to correlate 270 → 271
    clearinghouse_transaction_id: Mapped[str | None] = mapped_column(String(255))
    # AAA segment rejection code from 271, or HTTP error code
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    appointment: Mapped["Appointment"] = relationship(back_populates="eligibility_checks")
    insurance: Mapped["PatientInsurance"] = relationship(back_populates="eligibility_checks")
    result: Mapped["EligibilityResult | None"] = relationship(
        back_populates="check", uselist=False
    )
    gaps: Mapped[list["CoverageGap"]] = relationship(
        back_populates="check", order_by="CoverageGap.severity"
    )
    edi_logs: Mapped[list["EdiTransactionLog"]] = relationship(
        back_populates="check"
    )

    def __repr__(self) -> str:
        return f"<EligibilityCheck {self.check_id} status={self.status}>"


class EligibilityResult(Base):
    """
    The parsed coverage data from the 271.
    One result per check — unique constraint enforces this.
    Explicit columns cover the most common fields.
    raw_parsed_data (JSONB) catches everything else.
    """
    __tablename__ = "eligibility_results"

    result_id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    # unique=True enforces one result per check
    check_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eligibility_checks.check_id"), nullable=False, unique=True
    )

    coverage_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    coverage_effective_date: Mapped[date | None] = mapped_column(Date)
    coverage_termination_date: Mapped[date | None] = mapped_column(Date)
    plan_name: Mapped[str | None] = mapped_column(String(255))
    # HMO, PPO, EPO, POS, HDHP
    plan_type: Mapped[str | None] = mapped_column(String(50))
    in_network: Mapped[bool | None] = mapped_column(Boolean)

    # Deductible — individual and family, with remaining amounts
    deductible_individual: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    deductible_individual_remaining: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    deductible_family: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    deductible_family_remaining: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    # Out-of-pocket maximum — individual and family, with remaining amounts
    oop_max_individual: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    oop_max_individual_remaining: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    oop_max_family: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    oop_max_family_remaining: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    # Cost sharing
    copay_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    coinsurance_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    # Requirements that affect workflow
    referral_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    prior_auth_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Full parsed 271 data — anything not in explicit columns lives here
    raw_parsed_data: Mapped[dict | None] = mapped_column(JSONBCompatible)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    check: Mapped["EligibilityCheck"] = relationship(back_populates="result")

    def __repr__(self) -> str:
        return f"<EligibilityResult check={self.check_id} active={self.coverage_active}>"


class CoverageGap(Base):
    """
    Each specific problem found by the gap analyzer.
    One check can have multiple gaps (e.g. inactive + referral required).
    Staff resolves gaps one by one with notes.
    """
    __tablename__ = "coverage_gaps"

    __table_args__ = (
        CheckConstraint(
            "severity IN ('critical', 'high', 'warning', 'info')",
            name="ck_gap_severity",
        ),
        # Index for the dashboard: show all unresolved gaps by severity
        Index("ix_coverage_gaps_unresolved", "is_resolved", "severity"),
    )

    gap_id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    check_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eligibility_checks.check_id"), nullable=False, index=True
    )
    # Machine-readable type for filtering and rule logic
    # e.g. INACTIVE_COVERAGE, OUT_OF_NETWORK, REFERRAL_REQUIRED,
    #       HIGH_DEDUCTIBLE, SERVICE_NOT_COVERED, COVERAGE_DATE_MISMATCH
    gap_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    # Human-readable message shown to front desk staff
    description: Mapped[str] = mapped_column(Text, nullable=False)
    is_resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_by: Mapped[str | None] = mapped_column(String(255))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    check: Mapped["EligibilityCheck"] = relationship(back_populates="gaps")

    def __repr__(self) -> str:
        return f"<CoverageGap {self.gap_type} severity={self.severity}>"
