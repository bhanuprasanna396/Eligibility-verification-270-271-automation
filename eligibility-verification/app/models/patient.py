"""
Patient demographic information.
PHI (Protected Health Information) — every field here is sensitive under HIPAA.
Name, DOB, phone, email, and address columns are encrypted at rest using
EncryptedString / EncryptedDate (Fernet AES-128-CBC).

gender is NOT encrypted because it is constrained by a DB-level CHECK that
would reject any ciphertext value.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import String, Boolean, DateTime, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.security.encryption import EncryptedDate, EncryptedString


class Patient(Base):
    __tablename__ = "patients"

    __table_args__ = (
        CheckConstraint(
            "gender IN ('M', 'F', 'U')",
            name="ck_patient_gender",
        ),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    first_name: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    last_name: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(EncryptedDate(), nullable=False)
    # M = Male, F = Female, U = Unknown/Not specified — not encrypted (CHECK constraint)
    gender: Mapped[str | None] = mapped_column(String(1))
    phone: Mapped[str | None] = mapped_column(EncryptedString())
    email: Mapped[str | None] = mapped_column(EncryptedString())
    address_line1: Mapped[str | None] = mapped_column(EncryptedString())
    address_line2: Mapped[str | None] = mapped_column(EncryptedString())
    city: Mapped[str | None] = mapped_column(EncryptedString())
    state: Mapped[str | None] = mapped_column(String(2))
    zip_code: Mapped[str | None] = mapped_column(EncryptedString())
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    insurances: Mapped[list["PatientInsurance"]] = relationship(
        back_populates="patient"
    )
    appointments: Mapped[list["Appointment"]] = relationship(
        back_populates="patient"
    )

    def __repr__(self) -> str:
        return f"<Patient {self.last_name}, {self.first_name} DOB={self.date_of_birth}>"
