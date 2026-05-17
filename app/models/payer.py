"""
Payer = insurance company (Blue Cross, Aetna, United Health, etc.)
Each payer has an EDI payer ID — this is the ID used inside 270/271 transactions.
It is different from our internal UUID.
"""
import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Payer(Base):
    __tablename__ = "payers"

    payer_id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    payer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The X12 standard payer ID used in every EDI transaction with this payer
    edi_payer_id: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    # How the clearinghouse refers to this payer in their API
    clearinghouse_payer_id: Mapped[str | None] = mapped_column(String(100))
    # Provider services phone number for manual fallback
    phone: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    patient_insurances: Mapped[list["PatientInsurance"]] = relationship(
        back_populates="payer"
    )

    def __repr__(self) -> str:
        return f"<Payer {self.payer_name} ({self.edi_payer_id})>"
