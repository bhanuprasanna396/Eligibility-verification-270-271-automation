"""
Two tables for EDI transaction tracking:

  EdiTransactionLog  — every raw 270 sent and 271 received (HIPAA requires 7-year retention)
  EdiControlNumber   — tracks used ISA control numbers to prevent duplicate submissions
"""
import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, ForeignKey, BigInteger, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EdiTransactionLog(Base):
    """
    Full audit trail of every EDI transaction.
    Raw EDI stored as-is — in production, encrypt this column
    since it contains PHI (patient name, DOB, member ID).
    """
    __tablename__ = "edi_transaction_log"

    __table_args__ = (
        CheckConstraint(
            "transaction_type IN ('270', '271', '999', 'TA1')",
            name="ck_edi_transaction_type",
        ),
        CheckConstraint(
            "direction IN ('outbound', 'inbound')",
            name="ck_edi_direction",
        ),
    )

    log_id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    check_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eligibility_checks.check_id"), nullable=False, index=True
    )
    # 270 = we sent, 271 = we received, 999 = functional ack, TA1 = interchange ack
    transaction_type: Mapped[str] = mapped_column(String(10), nullable=False)
    # outbound = we sent it, inbound = we received it
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    # ISA13 control number from the EDI envelope
    isa_control_number: Mapped[str] = mapped_column(String(20), nullable=False)
    # The complete raw EDI string — encrypt at application layer in production
    raw_edi: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    check: Mapped["EligibilityCheck"] = relationship(back_populates="edi_logs")

    def __repr__(self) -> str:
        return f"<EdiTransactionLog {self.transaction_type} {self.direction}>"


class EdiControlNumber(Base):
    """
    Tracks every ISA control number ever used.
    Clearinghouses reject duplicate control numbers — this table prevents that.
    Uses a BigInteger sequence so numbers never repeat across restarts.
    """
    __tablename__ = "edi_control_numbers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # The formatted control number string (e.g. "000000001")
    control_number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<EdiControlNumber {self.control_number}>"
