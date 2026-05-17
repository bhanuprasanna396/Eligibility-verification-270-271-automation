"""
Appointment watcher — nightly Celery beat task.

Scans for appointments that need eligibility verification and queues
a run_eligibility_check task for each one that isn't already covered.

What gets queued:
  - status = 'scheduled'  (not cancelled / completed)
  - eligibility_status = 'pending'  (not already verified or in-flight)
  - appointment_datetime within the next N days (configurable)
  - has an insurance plan attached  (self-pay → skip)
  - no in-progress or queued EligibilityCheck already exists for it

This runs at 23:00 UTC every night via Celery beat (celery_app.py).
It can also be triggered manually via the API for ad-hoc re-checks.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import create_db_session
from app.models import Appointment, EligibilityCheck
from app.workers.celery_app import celery_app
from app.workers.eligibility_worker import run_eligibility_check


@celery_app.task(name="app.workers.appointment_watcher.queue_upcoming_appointments")
def queue_upcoming_appointments() -> dict:
    """
    Celery beat entry point. Finds eligible appointments and queues checks.
    Returns a summary dict for monitoring.
    """
    db = create_db_session()
    try:
        return _queue_checks(db)
    finally:
        db.close()


def _queue_checks(db: Session) -> dict:
    """
    Core logic — separated so it can be tested without Celery infrastructure.
    """
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=settings.eligibility_check_days_before)

    # Find appointments that need a check queued.
    # Exclude those that already have an active (queued or in_progress) check —
    # re-queuing would waste a clearinghouse transaction and confuse status tracking.
    already_active = (
        select(EligibilityCheck.appointment_id)
        .where(EligibilityCheck.status.in_(["queued", "in_progress"]))
    )

    stmt = (
        select(Appointment)
        .where(
            Appointment.status == "scheduled",
            Appointment.eligibility_status == "pending",
            Appointment.appointment_datetime >= now,
            Appointment.appointment_datetime <= horizon,
            Appointment.insurance_id.is_not(None),
            Appointment.appointment_id.not_in(already_active),
        )
        .order_by(Appointment.appointment_datetime)
    )

    appointments = db.scalars(stmt).all()

    queued_ids = []
    for appointment in appointments:
        check = EligibilityCheck(
            appointment_id=appointment.appointment_id,
            insurance_id=appointment.insurance_id,
            triggered_by="scheduler",
            status="queued",
            attempt_number=0,
        )
        db.add(check)
        db.flush()  # get check_id before commit

        run_eligibility_check.delay(str(check.check_id))
        queued_ids.append(str(check.check_id))

    db.commit()

    return {
        "queued": len(queued_ids),
        "check_ids": queued_ids,
        "window_days": settings.eligibility_check_days_before,
        "ran_at": now.isoformat(),
    }
