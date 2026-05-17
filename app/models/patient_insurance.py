"""
Links a patient to an insurance plan.
A patient can have primary + secondary coverage — both rows live here.
If the patient is a dependent (e.g. child on parent's plan), the subscriber
fields hold the policyholder's info, not the patient's.

PHI fields (member_id, subscriber name/DOB/member_id) are encrypted at rest.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import String, Boolean, DateTime, ForeignKey, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.security.encryption import EncryptedDate, EncryptedString


class PatientInsurance(Base):
    __tablename__ = "patient_insurance"

    __table_args__ = (
        CheckConstraint(
            "relationship_to_subscriber IN ('self', 'spouse', 'child', 'other')",
            name="ck_subscriber_relationship",
        ),
        CheckConstraint(
            "coverage_type IN ('primary', 'secondary', 'tertiary')",
            name="ck_coverage_type",
        ),
    )

    insurance_id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.patient_id"), nullable=False
    )
    payer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payers.payer_id"), nullable=False
    )
    # The ID on the patient's insurance card — encrypted PHI
    member_id: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    group_number: Mapped[str | None] = mapped_column(String(100))
    group_name: Mapped[str | None] = mapped_column(String(255))
    plan_name: Mapped[str | None] = mapped_column(String(255))

    # Subscriber = the person who owns the policy (may differ from patient for dependents)
    subscriber_first_name: Mapped[str | None] = mapped_column(EncryptedString())
    subscriber_last_name: Mapped[str | None] = mapped_column(EncryptedString())
    subscriber_date_of_birth: Mapped[date | None] = mapped_column(EncryptedDate())
    # Subscriber's own member ID (needed when patient is a dependent)
    subscriber_member_id: Mapped[str | None] = mapped_column(EncryptedString())
    # How is this patient related to the subscriber?
    relationship_to_subscriber: Mapped[str] = mapped_column(
        String(20), nullable=False, default="self"
    )

    # Primary = billed first, secondary = billed after primary pays
    coverage_type: Mapped[str] = mapped_column(String(10), nullable=False)
    effective_date: Mapped[date | None] = mapped_column(EncryptedDate())
    termination_date: Mapped[date | None] = mapped_column(EncryptedDate())
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    patient: Mapped["Patient"] = relationship(back_populates="insurances")
    payer: Mapped["Payer"] = relationship(back_populates="patient_insurances")
    appointments: Mapped[list["Appointment"]] = relationship(
        back_populates="insurance"
    )
    eligibility_checks: Mapped[list["EligibilityCheck"]] = relationship(
        back_populates="insurance"
    )

    def __repr__(self) -> str:
        return f"<PatientInsurance member={self.member_id} type={self.coverage_type}>"
