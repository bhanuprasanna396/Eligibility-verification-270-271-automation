"""
Eligibility worker — the core background task.

One run of `run_eligibility_check` does exactly this:

  1. Load the EligibilityCheck record from the database
  2. Build a 270 EDI string from the patient/insurance/provider data
  3. Generate a unique control number and log the raw 270 (HIPAA)
  4. Submit the 270 to the clearinghouse
  5. Log the raw 271 response (HIPAA)
  6. Parse the 271 into structured coverage data
  7. Save the parsed result to eligibility_results
  8. Run the gap analyzer — save any CoverageGap records found
  9. Update eligibility_status on the appointment ('verified' or 'gap_found')

Retry behaviour:
  Network-level failures (clearinghouse down) → retry up to 3 times,
  with exponential backoff (1 min, 2 min, 4 min).

  Business rejections (AAA — member not found) → no retry. Mark failed.
  These need a human to correct the data, not another automated attempt.
"""
import uuid
from datetime import datetime, timezone

from celery import Task
from sqlalchemy.orm import Session

from app.analyzers.gap_analyzer import GapAnalyzer
from app.clearinghouse.base import ClearinghouseClientBase
from app.clearinghouse.mock_client import MockClearinghouseClient
from app.config import settings
from app.database import create_db_session
from app.edi.builder_270 import Builder270
from app.edi.control_numbers import next_control_number
from app.edi.models import DependentInfo, EligibilityInquiry, PayerInfo, ProviderInfo, SubscriberInfo
from app.edi.parser_271 import Parser271
from app.models import (
    Appointment,
    CoverageGap,
    EdiTransactionLog,
    EligibilityCheck,
    EligibilityResult,
)
from app.workers.celery_app import celery_app


# ---------------------------------------------------------------------------
# Clearinghouse factory
# ---------------------------------------------------------------------------

def get_clearinghouse_client() -> ClearinghouseClientBase:
    """
    Returns the clearinghouse client to use for this environment.
    Production: swap MockClearinghouseClient for RealClearinghouseClient
    when credentials are available. Nothing else in this file changes.
    """
    return MockClearinghouseClient()


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="app.workers.eligibility_worker.run_eligibility_check",
    max_retries=3,
    acks_late=True,
)
def run_eligibility_check(self: Task, check_id: str) -> dict:
    """
    Celery entry point. Thin wrapper — all logic is in _process_check
    so it can be tested without Celery infrastructure.
    """
    db = create_db_session()
    clearinghouse = get_clearinghouse_client()
    try:
        return _process_check(self, check_id, db, clearinghouse)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Core pipeline (separated for testability)
# ---------------------------------------------------------------------------

def _process_check(
    task: Task,
    check_id: str,
    db: Session,
    clearinghouse: ClearinghouseClientBase,
) -> dict:
    """
    Runs the full eligibility pipeline for one check.
    Returns a summary dict on success, raises on unrecoverable failure.
    """
    check = db.get(EligibilityCheck, uuid.UUID(check_id))
    if not check:
        return {"status": "not_found", "check_id": check_id}

    # Mark as in-progress so concurrent workers skip this check
    check.status = "in_progress"
    check.attempt_number = (check.attempt_number or 0) + 1
    check.submitted_at = datetime.now(timezone.utc)
    db.commit()

    try:
        # --- Build 270 ---
        inquiry = build_inquiry_from_check(check)
        control_number = next_control_number(db)
        usage = "P" if settings.app_env == "production" else "T"
        builder = Builder270(
            sender_id=settings.clinic_edi_id,
            receiver_id=settings.clearinghouse_edi_id,
            isa_control_number=control_number,
            usage=usage,
        )
        edi_270 = builder.build(inquiry)

        # --- Log raw 270 (HIPAA audit) ---
        db.add(EdiTransactionLog(
            check_id=check.check_id,
            transaction_type="270",
            direction="outbound",
            isa_control_number=control_number,
            raw_edi=edi_270,
        ))
        db.commit()

        # --- Submit to clearinghouse ---
        response = clearinghouse.submit_270(edi_270)
        check.responded_at = datetime.now(timezone.utc)

        if not response.success:
            # Network-level failure (clearinghouse down/timeout) → retry
            raise ConnectionError(
                f"Clearinghouse unavailable: {response.error_message}"
            )

        check.clearinghouse_transaction_id = response.transaction_id

        # --- Log raw 271 (HIPAA audit) ---
        db.add(EdiTransactionLog(
            check_id=check.check_id,
            transaction_type="271",
            direction="inbound",
            isa_control_number=control_number,
            raw_edi=response.edi_271,
        ))

        # --- Parse 271 ---
        parsed = Parser271().parse(response.edi_271)

        # --- Save eligibility result ---
        save_eligibility_result(check.check_id, parsed, db)

        # --- Run gap analyzer ---
        appt_date = (
            check.appointment.appointment_datetime.date()
            if check.appointment.appointment_datetime
            else None
        )
        gaps = GapAnalyzer().analyze(parsed, appointment_date=appt_date)
        for gap in gaps:
            db.add(CoverageGap(
                check_id=check.check_id,
                gap_type=gap.gap_type,
                severity=gap.severity,
                description=gap.description,
            ))

        # --- Update statuses ---
        check.status = "completed"
        check.appointment.eligibility_status = "gap_found" if gaps else "verified"
        db.commit()

        return {
            "status": "completed",
            "check_id": check_id,
            "coverage_active": parsed.coverage_active,
            "gaps_found": len(gaps),
        }

    except ConnectionError as exc:
        # Transient network error — safe to retry
        db.rollback()
        _mark_check_failed(check_id, str(exc), update_appointment=False, db=db)
        raise task.retry(
            exc=exc,
            countdown=60 * (2 ** task.request.retries),  # 60s, 120s, 240s
        )

    except Exception as exc:
        # Non-retryable error (bad data, payer rejection, bug)
        db.rollback()
        _mark_check_failed(check_id, str(exc), update_appointment=True, db=db)
        raise


def _mark_check_failed(
    check_id: str,
    error_message: str,
    update_appointment: bool,
    db: Session,
) -> None:
    """
    Re-queries and marks the check as failed after a rollback.
    Re-querying is necessary because rollback detaches previously loaded objects.
    """
    check = db.get(EligibilityCheck, uuid.UUID(check_id))
    if not check:
        return
    check.status = "failed"
    check.error_message = error_message[:500]   # DB column is not unbounded
    if update_appointment and check.appointment:
        check.appointment.eligibility_status = "error"
    db.commit()


# ---------------------------------------------------------------------------
# Build inquiry from DB models
# ---------------------------------------------------------------------------

def build_inquiry_from_check(check: EligibilityCheck) -> EligibilityInquiry:
    """
    Constructs an EligibilityInquiry from the check's related DB models.
    Handles both the common case (patient IS the subscriber)
    and the dependent case (patient is on someone else's plan).
    """
    appointment: Appointment = check.appointment
    insurance = check.insurance
    patient = appointment.patient
    payer = insurance.payer
    provider = appointment.provider

    provider_info = ProviderInfo(
        npi=provider.npi,
        edi_id=settings.clinic_edi_id,
        org_name=(
            provider.organization_name
            or f"{provider.first_name or ''} {provider.last_name or ''}".strip()
        ),
    )

    payer_info = PayerInfo(
        edi_payer_id=payer.edi_payer_id,
        payer_name=payer.payer_name,
    )

    if insurance.relationship_to_subscriber == "self":
        # Patient IS the subscriber — most common case
        subscriber_info = SubscriberInfo(
            last_name=patient.last_name,
            first_name=patient.first_name,
            date_of_birth=patient.date_of_birth,
            gender=patient.gender or "U",
            member_id=insurance.member_id,
        )
        dependent_info = None
    else:
        # Patient is a dependent — subscriber is someone else (parent, spouse)
        subscriber_info = SubscriberInfo(
            last_name=insurance.subscriber_last_name or patient.last_name,
            first_name=insurance.subscriber_first_name or patient.first_name,
            date_of_birth=insurance.subscriber_date_of_birth or patient.date_of_birth,
            gender="U",
            member_id=insurance.subscriber_member_id or insurance.member_id,
        )
        dependent_info = DependentInfo(
            last_name=patient.last_name,
            first_name=patient.first_name,
            date_of_birth=patient.date_of_birth,
            gender=patient.gender or "U",
        )

    return EligibilityInquiry(
        provider=provider_info,
        payer=payer_info,
        subscriber=subscriber_info,
        reference_id=str(appointment.appointment_id),
        service_type_codes=[appointment.service_type_code or "30"],
        dependent=dependent_info,
    )


# ---------------------------------------------------------------------------
# Save parsed result to database
# ---------------------------------------------------------------------------

def save_eligibility_result(
    check_id: uuid.UUID,
    parsed: "ParsedEligibility",
    db: Session,
) -> EligibilityResult:
    """
    Maps ParsedEligibility fields to an EligibilityResult row.
    The JSONB raw_parsed_data column stores everything else.
    """
    result = EligibilityResult(
        check_id=check_id,
        coverage_active=parsed.coverage_active,
        coverage_effective_date=parsed.coverage_effective_date,
        coverage_termination_date=parsed.coverage_termination_date,
        plan_name=parsed.plan_name,
        plan_type=parsed.plan_type,
        in_network=parsed.in_network,
        deductible_individual=parsed.deductible_individual,
        deductible_individual_remaining=parsed.deductible_individual_remaining,
        deductible_family=parsed.deductible_family,
        deductible_family_remaining=parsed.deductible_family_remaining,
        oop_max_individual=parsed.oop_max_individual,
        oop_max_individual_remaining=parsed.oop_max_individual_remaining,
        oop_max_family=parsed.oop_max_family,
        oop_max_family_remaining=parsed.oop_max_family_remaining,
        copay_amount=parsed.copay_amount,
        coinsurance_percent=parsed.coinsurance_percent,
        referral_required=parsed.referral_required,
        prior_auth_required=parsed.prior_auth_required,
        raw_parsed_data={
            "rejection_reasons": parsed.rejection_reasons,
            "eb_segments": parsed.raw_eb_segments,
        },
    )
    db.add(result)
    return result
