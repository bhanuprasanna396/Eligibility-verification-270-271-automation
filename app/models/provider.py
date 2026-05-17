"""
Provider = the clinic or individual doctor sending the eligibility request.
NPI (National Provider Identifier) is the 10-digit ID every provider must have.
It is used in every 270 to identify who is asking.
"""
import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Provider(Base):
    __tablename__ = "providers"

    __table_args__ = (
        CheckConstraint(
            "provider_type IN ('individual', 'organization')",
            name="ck_provider_type",
        ),
    )

    provider_id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    # NPI is always exactly 10 digits — enforced at DB level
    npi: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    tax_id: Mapped[str | None] = mapped_column(String(20))
    # For organizations (clinics, hospitals)
    organization_name: Mapped[str | None] = mapped_column(String(255))
    # For individual providers (doctors)
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    provider_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Taxonomy code identifies specialty (e.g. 207Q00000X = Family Medicine)
    taxonomy_code: Mapped[str | None] = mapped_column(String(20))
    address_line1: Mapped[str | None] = mapped_column(String(255))
    address_line2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(2))
    zip_code: Mapped[str | None] = mapped_column(String(10))
    phone: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    appointments: Mapped[list["Appointment"]] = relationship(
        back_populates="provider"
    )

    def __repr__(self) -> str:
        name = self.organization_name or f"{self.first_name} {self.last_name}"
        return f"<Provider {name} NPI={self.npi}>"
