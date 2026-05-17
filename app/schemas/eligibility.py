"""
Pydantic schemas — the shapes of data flowing in and out of the API.

Separating schemas from SQLAlchemy models keeps the API contract stable
even when DB schema changes. It also prevents accidentally leaking internal
fields (e.g. raw EDI strings) to API consumers.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------

class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Patient (embedded in appointment responses)
# ---------------------------------------------------------------------------

class PatientSummary(_Base):
    patient_id: uuid.UUID
    first_name: str
    last_name: str
    date_of_birth: date


# ---------------------------------------------------------------------------
# Provider (embedded in appointment responses)
# ---------------------------------------------------------------------------

class ProviderSummary(_Base):
    provider_id: uuid.UUID
    first_name: str | None
    last_name: str | None
    organization_name: str | None
    npi: str
    taxonomy_code: str | None = None  # used as specialty display label


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------

class AppointmentSummary(_Base):
    """Lightweight row for list views."""
    appointment_id: uuid.UUID
    appointment_datetime: datetime
    appointment_type: str | None
    status: str
    eligibility_status: str
    patient: PatientSummary
    provider: ProviderSummary
    # Set when a check is currently queued or in_progress — lets the UI auto-resume polling
    active_check_id: uuid.UUID | None = None
    active_check_status: str | None = None


class AppointmentDetail(AppointmentSummary):
    """Full detail including latest eligibility result and any gaps."""
    service_type_code: str
    notes: str | None
    latest_check: "CheckSummary | None" = None
    latest_result: "EligibilityResultSchema | None" = None
    gaps: list["GapSchema"] = []


# ---------------------------------------------------------------------------
# Eligibility checks
# ---------------------------------------------------------------------------

class CheckSummary(_Base):
    check_id: uuid.UUID
    status: str
    attempt_number: int
    triggered_by: str
    submitted_at: datetime | None
    responded_at: datetime | None
    error_message: str | None
    created_at: datetime


class TriggerCheckRequest(BaseModel):
    """Body for POST /appointments/{id}/check — manual re-trigger."""
    triggered_by: str = "manual"


class TriggerCheckResponse(BaseModel):
    check_id: uuid.UUID
    status: str
    message: str


# ---------------------------------------------------------------------------
# Eligibility results
# ---------------------------------------------------------------------------

class EligibilityResultSchema(_Base):
    result_id: uuid.UUID
    coverage_active: bool
    coverage_effective_date: date | None
    coverage_termination_date: date | None
    plan_name: str | None
    plan_type: str | None
    in_network: bool | None
    deductible_individual: Decimal | None
    deductible_individual_remaining: Decimal | None
    deductible_family: Decimal | None
    deductible_family_remaining: Decimal | None
    oop_max_individual: Decimal | None
    oop_max_individual_remaining: Decimal | None
    oop_max_family: Decimal | None
    oop_max_family_remaining: Decimal | None
    copay_amount: Decimal | None
    coinsurance_percent: Decimal | None
    referral_required: bool
    prior_auth_required: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# Coverage gaps
# ---------------------------------------------------------------------------

class GapSchema(_Base):
    gap_id: uuid.UUID
    gap_type: str
    severity: str
    description: str
    is_resolved: bool
    resolved_by: str | None
    resolved_at: datetime | None
    resolution_note: str | None
    created_at: datetime
    # Enriched fields — populated by list_gaps endpoint, not stored on CoverageGap directly
    patient_name: str | None = None
    appointment_datetime: datetime | None = None


class ResolveGapRequest(BaseModel):
    resolved_by: str
    resolution_note: str | None = None


class ResolveGapResponse(_Base):
    gap_id: uuid.UUID
    is_resolved: bool
    resolved_by: str | None
    resolved_at: datetime | None
    resolution_note: str | None


# ---------------------------------------------------------------------------
# Dashboard summary
# ---------------------------------------------------------------------------

class DashboardSummary(BaseModel):
    """Counts for the front desk overview widget."""
    appointments_today: int
    pending_checks: int
    gaps_unresolved: int
    checks_failed: int


# Rebuild forward references
AppointmentDetail.model_rebuild()
