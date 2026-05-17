"""
HIPAA audit log: records every read/write/delete event touching PHI.

Call log_phi_access() from any API endpoint or worker that accesses
patient records, eligibility results, or insurance data.
"""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    audit_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # "read", "write", "delete", "export"
    event_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    # "patient", "appointment", "eligibility_result", "patient_insurance", …
    resource_type: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(sa.String(36))
    # Human or system actor (username, service name, IP-derived identifier)
    actor: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(sa.String(45))
    detail: Mapped[dict | None] = mapped_column(sa.JSON)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


def log_phi_access(
    db,
    *,
    event_type: str,
    resource_type: str,
    resource_id: str | None = None,
    actor: str,
    ip_address: str | None = None,
    detail: dict | None = None,
) -> AuditLog:
    """Persist one audit event and return the persisted row."""
    entry = AuditLog(
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        actor=actor,
        ip_address=ip_address,
        detail=detail,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
