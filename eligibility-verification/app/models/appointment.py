"""
An appointment links a patient, provider, and insurance plan together on a date.
eligibility_status is the summary field that drives the dashboard:
  pending   = check not run yet
  verified  = ran, no gaps found
  gap_found = ran, problems detected — staff must act
  error     = check failed to complete
"""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, CheckConstraint, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Appointment(Base):
    __tablename__ = "appointments"

    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled', 'confirmed', 'cancelled', 'completed', 'no_show')",
            name="ck_appointment_status",
        ),
        CheckConstraint(
            "eligibility_status IN ('pending', 'verified', 'gap_found', 'error', 'not_required')",
            name="ck_eligibility_status",
        ),
        # Index for the nightly watcher query: "give me all upcoming appointments not yet verified"
        Index(
            "ix_appointments_upcoming_pending",
            "appointment_datetime",
            "eligibility_status",
            postgresql_where=text("status = 'scheduled'"),
        ),
    )

    appointment_id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.patient_id"), nullable=False, index=True
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("providers.provider_id"), nullable=False
    )
    # Which insurance plan to bill — nullable because some visits are self-pay
    insurance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("patient_insurance.insurance_id"), index=True
    )
    appointment_datetime: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Human-readable label: 'office_visit', 'specialist', 'lab', 'imaging', etc.
    appointment_type: Mapped[str | None] = mapped_column(String(100))
    # X12 service type code sent in the EQ segment of the 270
    # 30 = Health Benefit Plan Coverage (general), 98 = Professional (physician visit)
    service_type_code: Mapped[str] = mapped_column(
        String(10), nullable=False, default="30"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="scheduled"
    )
    eligibility_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    notes: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    patient: Mapped["Patient"] = relationship(back_populates="appointments")
    provider: Mapped["Provider"] = relationship(back_populates="appointments")
    insurance: Mapped["PatientInsurance | None"] = relationship(
        back_populates="appointments"
    )
    eligibility_checks: Mapped[list["EligibilityCheck"]] = relationship(
        back_populates="appointment", order_by="EligibilityCheck.created_at"
    )

    def __repr__(self) -> str:
        return f"<Appointment {self.appointment_id} at {self.appointment_datetime}>"
