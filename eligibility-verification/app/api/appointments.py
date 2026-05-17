"""
Appointment endpoints.

GET  /appointments              — list upcoming appointments with eligibility status
GET  /appointments/{id}         — full detail: result + gaps
POST /appointments/{id}/check   — manually trigger an eligibility re-check
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import (
    Appointment,
    EligibilityCheck,
    EligibilityResult,
    CoverageGap,
)
from app.schemas.eligibility import (
    AppointmentDetail,
    AppointmentSummary,
    CheckSummary,
    TriggerCheckRequest,
    TriggerCheckResponse,
    EligibilityResultSchema,
    GapSchema,
)
from app.workers.eligibility_worker import run_eligibility_check

router = APIRouter(prefix="/appointments", tags=["appointments"])

DbDep = Annotated[Session, Depends(get_db)]


# ---------------------------------------------------------------------------
# List appointments
# ---------------------------------------------------------------------------

@router.get("", response_model=list[AppointmentSummary])
def list_appointments(
    db: DbDep,
    eligibility_status: str | None = Query(None, description="Filter by eligibility_status"),
    days_ahead: int = Query(7, ge=1, le=90, description="How many days forward to look"),
):
    """
    Returns upcoming scheduled appointments ordered by appointment time.
    Useful for the front desk morning check: "what's happening in the next N days?"
    """
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=days_ahead)

    stmt = (
        select(Appointment)
        .options(
            selectinload(Appointment.patient),
            selectinload(Appointment.provider),
            selectinload(Appointment.eligibility_checks),
        )
        .where(
            Appointment.status == "scheduled",
            Appointment.appointment_datetime >= now,
            Appointment.appointment_datetime <= horizon,
        )
        .order_by(Appointment.appointment_datetime)
    )

    if eligibility_status:
        stmt = stmt.where(Appointment.eligibility_status == eligibility_status)

    appts = db.scalars(stmt).all()
    result = []
    for appt in appts:
        active = next(
            (c for c in appt.eligibility_checks if c.status in ("queued", "in_progress")),
            None,
        )
        result.append({
            "appointment_id": appt.appointment_id,
            "appointment_datetime": appt.appointment_datetime,
            "appointment_type": appt.appointment_type,
            "status": appt.status,
            "eligibility_status": appt.eligibility_status,
            "patient": appt.patient,
            "provider": appt.provider,
            "active_check_id": active.check_id if active else None,
            "active_check_status": active.status if active else None,
        })
    return result


# ---------------------------------------------------------------------------
# Get appointment detail
# ---------------------------------------------------------------------------

@router.get("/{appointment_id}", response_model=AppointmentDetail)
def get_appointment(appointment_id: uuid.UUID, db: DbDep):
    """
    Returns full appointment detail including the most recent eligibility
    result and all unresolved coverage gaps.
    """
    appt = db.get(
        Appointment,
        appointment_id,
        options=[
            selectinload(Appointment.patient),
            selectinload(Appointment.provider),
            selectinload(Appointment.eligibility_checks).selectinload(
                EligibilityCheck.result
            ),
            selectinload(Appointment.eligibility_checks).selectinload(
                EligibilityCheck.gaps
            ),
        ],
    )
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")

    # Most recent completed check
    completed_checks = [c for c in appt.eligibility_checks if c.status == "completed"]
    latest_check = appt.eligibility_checks[-1] if appt.eligibility_checks else None
    latest_completed = completed_checks[-1] if completed_checks else None

    return AppointmentDetail(
        appointment_id=appt.appointment_id,
        appointment_datetime=appt.appointment_datetime,
        appointment_type=appt.appointment_type,
        status=appt.status,
        eligibility_status=appt.eligibility_status,
        service_type_code=appt.service_type_code,
        notes=appt.notes,
        patient=appt.patient,
        provider=appt.provider,
        latest_check=CheckSummary.model_validate(latest_check) if latest_check else None,
        latest_result=(
            EligibilityResultSchema.model_validate(latest_completed.result)
            if latest_completed and latest_completed.result
            else None
        ),
        gaps=(
            [GapSchema.model_validate(g) for g in latest_completed.gaps]
            if latest_completed
            else []
        ),
    )


# ---------------------------------------------------------------------------
# Trigger manual eligibility re-check
# ---------------------------------------------------------------------------

@router.post("/{appointment_id}/check", response_model=TriggerCheckResponse, status_code=202)
def trigger_check(
    appointment_id: uuid.UUID,
    body: TriggerCheckRequest,
    db: DbDep,
):
    """
    Queues a new eligibility check for this appointment.

    Returns 409 if there is already a check in progress — callers should
    wait for it to complete rather than flooding the clearinghouse.
    Returns 422 if the appointment has no insurance plan (self-pay).
    """
    appt = db.get(Appointment, appointment_id)
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")

    if not appt.insurance_id:
        raise HTTPException(
            status_code=422,
            detail="Appointment has no insurance plan — eligibility check not applicable",
        )

    # Block if there is already an active check
    active_check = db.scalars(
        select(EligibilityCheck)
        .where(
            EligibilityCheck.appointment_id == appointment_id,
            EligibilityCheck.status.in_(["queued", "in_progress"]),
        )
        .limit(1)
    ).first()

    if active_check:
        raise HTTPException(
            status_code=409,
            detail=f"A check is already {active_check.status} for this appointment",
        )

    check = EligibilityCheck(
        appointment_id=appointment_id,
        insurance_id=appt.insurance_id,
        triggered_by=body.triggered_by,
        status="queued",
        attempt_number=0,
    )
    db.add(check)
    db.commit()
    db.refresh(check)

    run_eligibility_check.delay(str(check.check_id))

    return TriggerCheckResponse(
        check_id=check.check_id,
        status="queued",
        message="Eligibility check queued. Poll GET /checks/{check_id} for status.",
    )
