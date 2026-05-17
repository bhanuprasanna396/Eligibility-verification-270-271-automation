"""
Coverage gap endpoints.

GET   /gaps              — list unresolved gaps (dashboard view)
PATCH /gaps/{id}/resolve — mark a gap resolved with staff notes
GET   /dashboard         — counts summary for the front desk widget
"""
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CoverageGap, EligibilityCheck
from app.schemas.eligibility import (
    DashboardSummary,
    GapSchema,
    ResolveGapRequest,
    ResolveGapResponse,
)
from app.models import Appointment, Patient

router = APIRouter(tags=["gaps"])

DbDep = Annotated[Session, Depends(get_db)]


# ---------------------------------------------------------------------------
# List unresolved gaps
# ---------------------------------------------------------------------------

@router.get("/gaps", response_model=list[GapSchema])
def list_gaps(
    db: DbDep,
    severity: str | None = Query(None, description="Filter by severity: critical, high, warning, info"),
    resolved: bool = Query(False, description="Include resolved gaps"),
):
    """
    Returns coverage gaps for the dashboard, enriched with patient name and
    appointment date so front-desk staff know which patient each gap belongs to.
    """
    severity_order = {"critical": 0, "high": 1, "warning": 2, "info": 3}

    stmt = select(CoverageGap)
    if not resolved:
        stmt = stmt.where(CoverageGap.is_resolved == False)  # noqa: E712
    if severity:
        stmt = stmt.where(CoverageGap.severity == severity)
    stmt = stmt.order_by(CoverageGap.created_at.desc())

    gaps = db.scalars(stmt).all()
    gaps_sorted = sorted(gaps, key=lambda g: (severity_order.get(g.severity, 99), g.created_at))

    result = []
    for gap in gaps_sorted:
        check = db.get(EligibilityCheck, gap.check_id)
        appt = db.get(Appointment, check.appointment_id) if check else None
        patient = db.get(Patient, appt.patient_id) if appt else None

        result.append({
            "gap_id": gap.gap_id,
            "gap_type": gap.gap_type,
            "severity": gap.severity,
            "description": gap.description,
            "is_resolved": gap.is_resolved,
            "resolved_by": gap.resolved_by,
            "resolved_at": gap.resolved_at,
            "resolution_note": gap.resolution_note,
            "created_at": gap.created_at,
            "patient_name": f"{patient.first_name} {patient.last_name}" if patient else None,
            "appointment_datetime": appt.appointment_datetime if appt else None,
        })
    return result


# ---------------------------------------------------------------------------
# Resolve a gap
# ---------------------------------------------------------------------------

@router.patch("/gaps/{gap_id}/resolve", response_model=ResolveGapResponse)
def resolve_gap(gap_id: uuid.UUID, body: ResolveGapRequest, db: DbDep):
    """
    Marks a gap as resolved. Staff notes who resolved it and optionally why.
    Idempotent: resolving an already-resolved gap updates the notes.
    """
    gap = db.get(CoverageGap, gap_id)
    if not gap:
        raise HTTPException(status_code=404, detail="Gap not found")

    gap.is_resolved = True
    gap.resolved_by = body.resolved_by
    gap.resolved_at = datetime.now(timezone.utc)
    gap.resolution_note = body.resolution_note
    db.commit()
    db.refresh(gap)

    return gap


# ---------------------------------------------------------------------------
# Dashboard summary
# ---------------------------------------------------------------------------

@router.get("/dashboard", response_model=DashboardSummary)
def get_dashboard(db: DbDep):
    """
    Aggregate counts for the front desk summary widget.
    All counts are for today's appointments or currently active issues.
    """
    from datetime import date

    today_start = datetime(date.today().year, date.today().month, date.today().day,
                           tzinfo=timezone.utc)
    today_end = today_start.replace(hour=23, minute=59, second=59)

    appointments_today = db.scalar(
        select(func.count(Appointment.appointment_id)).where(
            Appointment.appointment_datetime >= today_start,
            Appointment.appointment_datetime <= today_end,
            Appointment.status == "scheduled",
        )
    ) or 0

    pending_checks = db.scalar(
        select(func.count(EligibilityCheck.check_id)).where(
            EligibilityCheck.status.in_(["queued", "in_progress"])
        )
    ) or 0

    gaps_unresolved = db.scalar(
        select(func.count(CoverageGap.gap_id)).where(
            CoverageGap.is_resolved == False  # noqa: E712
        )
    ) or 0

    checks_failed = db.scalar(
        select(func.count(EligibilityCheck.check_id)).where(
            EligibilityCheck.status == "failed"
        )
    ) or 0

    return DashboardSummary(
        appointments_today=appointments_today,
        pending_checks=pending_checks,
        gaps_unresolved=gaps_unresolved,
        checks_failed=checks_failed,
    )
