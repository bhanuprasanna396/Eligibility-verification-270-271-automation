"""
API tests for the eligibility verification REST endpoints.

Uses FastAPI's TestClient backed by an in-memory SQLite database (see
conftest.py for shared engine / override setup).

The Celery task (.delay) is patched throughout so tests don't need a broker.

How to run:
    pytest tests/test_api.py -v
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models import (
    Appointment,
    CoverageGap,
    EligibilityCheck,
    EligibilityResult,
    Patient,
    PatientInsurance,
    Payer,
    Provider,
)

# client, db, and reset_db fixtures come from conftest.py


# ---------------------------------------------------------------------------
# Seed helpers — accept the db fixture session
# ---------------------------------------------------------------------------

def _seed(db, days_ahead: int = 2, eligibility_status: str = "pending"):
    """Seeds one complete appointment with all required relationships."""
    payer = Payer(edi_payer_id="00050", payer_name="Blue Cross")
    provider = Provider(
        npi="1234567890",
        first_name="Jane",
        last_name="Smith",
        provider_type="individual",
    )
    patient = Patient(
        first_name="John",
        last_name="Doe",
        date_of_birth=date(1985, 1, 15),
        gender="M",
    )
    db.add_all([payer, provider, patient])
    db.flush()

    insurance = PatientInsurance(
        patient_id=patient.patient_id,
        payer_id=payer.payer_id,
        member_id="MEM001",
        relationship_to_subscriber="self",
        coverage_type="primary",
    )
    db.add(insurance)
    db.flush()

    appt = Appointment(
        patient_id=patient.patient_id,
        provider_id=provider.provider_id,
        insurance_id=insurance.insurance_id,
        appointment_datetime=datetime.now(timezone.utc) + timedelta(days=days_ahead),
        status="scheduled",
        eligibility_status=eligibility_status,
        service_type_code="30",
    )
    db.add(appt)
    db.commit()
    db.refresh(appt)
    return appt, insurance


def _seed_check(db, appt, insurance, status: str = "completed"):
    check = EligibilityCheck(
        appointment_id=appt.appointment_id,
        insurance_id=insurance.insurance_id,
        triggered_by="scheduler",
        status=status,
        attempt_number=1,
    )
    db.add(check)
    db.commit()
    db.refresh(check)
    return check


def _seed_result(db, check):
    result = EligibilityResult(
        check_id=check.check_id,
        coverage_active=True,
        plan_name="Standard PPO",
        plan_type="PPO",
        in_network=True,
        deductible_individual=Decimal("500.00"),
        deductible_individual_remaining=Decimal("347.50"),
        oop_max_individual=Decimal("3000.00"),
        oop_max_individual_remaining=Decimal("2500.00"),
        copay_amount=Decimal("30.00"),
        coinsurance_percent=Decimal("20"),
        referral_required=False,
        prior_auth_required=False,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result


def _seed_gap(db, check, gap_type="HIGH_DEDUCTIBLE", severity="warning"):
    gap = CoverageGap(
        check_id=check.check_id,
        gap_type=gap_type,
        severity=severity,
        description="Patient has $347.50 remaining deductible.",
        is_resolved=False,
    )
    db.add(gap)
    db.commit()
    db.refresh(gap)
    return gap


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealth:

    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /appointments
# ---------------------------------------------------------------------------

class TestListAppointments:

    def test_returns_200(self, client):
        assert client.get("/appointments").status_code == 200

    def test_returns_list(self, client):
        assert isinstance(client.get("/appointments").json(), list)

    def test_upcoming_appointment_in_list(self, client, db):
        _seed(db)
        assert len(client.get("/appointments").json()) == 1

    def test_past_appointment_not_in_list(self, client, db):
        _seed(db, days_ahead=-1)
        assert len(client.get("/appointments").json()) == 0

    def test_filter_by_eligibility_status(self, client, db):
        _seed(db, eligibility_status="gap_found")
        assert len(client.get("/appointments?eligibility_status=gap_found").json()) == 1
        assert len(client.get("/appointments?eligibility_status=verified").json()) == 0

    def test_appointment_has_patient_and_provider(self, client, db):
        _seed(db)
        data = client.get("/appointments").json()[0]
        assert data["patient"]["last_name"] == "Doe"
        assert data["provider"]["last_name"] == "Smith"

    def test_eligibility_status_field_present(self, client, db):
        _seed(db, eligibility_status="pending")
        assert client.get("/appointments").json()[0]["eligibility_status"] == "pending"

    def test_days_ahead_parameter(self, client, db):
        _seed(db, days_ahead=10)
        assert len(client.get("/appointments?days_ahead=7").json()) == 0
        assert len(client.get("/appointments?days_ahead=14").json()) == 1


# ---------------------------------------------------------------------------
# GET /appointments/{id}
# ---------------------------------------------------------------------------

class TestGetAppointment:

    def test_404_for_unknown_id(self, client):
        assert client.get(f"/appointments/{uuid.uuid4()}").status_code == 404

    def test_returns_appointment_detail(self, client, db):
        appt, _ = _seed(db)
        response = client.get(f"/appointments/{appt.appointment_id}")
        assert response.status_code == 200
        assert response.json()["appointment_id"] == str(appt.appointment_id)

    def test_latest_result_none_when_no_check(self, client, db):
        appt, _ = _seed(db)
        data = client.get(f"/appointments/{appt.appointment_id}").json()
        assert data["latest_result"] is None

    def test_latest_result_populated_after_check(self, client, db):
        appt, insurance = _seed(db)
        check = _seed_check(db, appt, insurance)
        _seed_result(db, check)
        data = client.get(f"/appointments/{appt.appointment_id}").json()
        assert data["latest_result"]["plan_type"] == "PPO"

    def test_gaps_populated(self, client, db):
        appt, insurance = _seed(db)
        check = _seed_check(db, appt, insurance)
        _seed_result(db, check)
        _seed_gap(db, check)
        data = client.get(f"/appointments/{appt.appointment_id}").json()
        assert len(data["gaps"]) == 1
        assert data["gaps"][0]["gap_type"] == "HIGH_DEDUCTIBLE"

    def test_gaps_empty_when_no_gaps(self, client, db):
        appt, insurance = _seed(db)
        check = _seed_check(db, appt, insurance)
        _seed_result(db, check)
        data = client.get(f"/appointments/{appt.appointment_id}").json()
        assert data["gaps"] == []

    def test_service_type_code_present(self, client, db):
        appt, _ = _seed(db)
        data = client.get(f"/appointments/{appt.appointment_id}").json()
        assert data["service_type_code"] == "30"


# ---------------------------------------------------------------------------
# POST /appointments/{id}/check
# ---------------------------------------------------------------------------

class TestTriggerCheck:

    @patch("app.api.appointments.run_eligibility_check")
    def test_returns_202(self, mock_task, client, db):
        appt, _ = _seed(db)
        assert client.post(f"/appointments/{appt.appointment_id}/check", json={}).status_code == 202

    @patch("app.api.appointments.run_eligibility_check")
    def test_returns_check_id(self, mock_task, client, db):
        appt, _ = _seed(db)
        data = client.post(f"/appointments/{appt.appointment_id}/check", json={}).json()
        assert "check_id" in data
        assert data["status"] == "queued"

    @patch("app.api.appointments.run_eligibility_check")
    def test_delay_called(self, mock_task, client, db):
        appt, _ = _seed(db)
        client.post(f"/appointments/{appt.appointment_id}/check", json={})
        mock_task.delay.assert_called_once()

    @patch("app.api.appointments.run_eligibility_check")
    def test_404_for_unknown_appointment(self, mock_task, client):
        assert client.post(f"/appointments/{uuid.uuid4()}/check", json={}).status_code == 404

    @patch("app.api.appointments.run_eligibility_check")
    def test_409_when_check_already_in_progress(self, mock_task, client, db):
        appt, insurance = _seed(db)
        _seed_check(db, appt, insurance, status="in_progress")
        assert client.post(f"/appointments/{appt.appointment_id}/check", json={}).status_code == 409

    @patch("app.api.appointments.run_eligibility_check")
    def test_422_when_no_insurance(self, mock_task, client, db):
        appt, _ = _seed(db)
        appt_obj = db.get(Appointment, appt.appointment_id)
        appt_obj.insurance_id = None
        db.commit()
        assert client.post(f"/appointments/{appt.appointment_id}/check", json={}).status_code == 422


# ---------------------------------------------------------------------------
# GET /checks/{check_id}
# ---------------------------------------------------------------------------

class TestGetCheck:

    def test_404_for_unknown_check(self, client):
        assert client.get(f"/checks/{uuid.uuid4()}").status_code == 404

    def test_returns_check_status(self, client, db):
        appt, insurance = _seed(db)
        check = _seed_check(db, appt, insurance, status="completed")
        assert client.get(f"/checks/{check.check_id}").json()["status"] == "completed"

    def test_check_fields_present(self, client, db):
        appt, insurance = _seed(db)
        check = _seed_check(db, appt, insurance)
        data = client.get(f"/checks/{check.check_id}").json()
        assert "check_id" in data
        assert "attempt_number" in data
        assert "triggered_by" in data


# ---------------------------------------------------------------------------
# GET /gaps
# ---------------------------------------------------------------------------

class TestListGaps:

    def test_returns_empty_list_by_default(self, client):
        assert client.get("/gaps").json() == []

    def test_returns_unresolved_gaps(self, client, db):
        appt, insurance = _seed(db)
        check = _seed_check(db, appt, insurance)
        _seed_gap(db, check)
        assert len(client.get("/gaps").json()) == 1

    def test_resolved_gaps_excluded_by_default(self, client, db):
        appt, insurance = _seed(db)
        check = _seed_check(db, appt, insurance)
        gap = _seed_gap(db, check)
        gap_obj = db.get(CoverageGap, gap.gap_id)
        gap_obj.is_resolved = True
        db.commit()
        assert len(client.get("/gaps").json()) == 0

    def test_resolved_gaps_included_when_asked(self, client, db):
        appt, insurance = _seed(db)
        check = _seed_check(db, appt, insurance)
        gap = _seed_gap(db, check)
        gap_obj = db.get(CoverageGap, gap.gap_id)
        gap_obj.is_resolved = True
        db.commit()
        assert len(client.get("/gaps?resolved=true").json()) == 1

    def test_filter_by_severity(self, client, db):
        appt, insurance = _seed(db)
        check = _seed_check(db, appt, insurance)
        _seed_gap(db, check, severity="warning")
        assert len(client.get("/gaps?severity=warning").json()) == 1
        assert len(client.get("/gaps?severity=critical").json()) == 0

    def test_gap_fields_present(self, client, db):
        appt, insurance = _seed(db)
        check = _seed_check(db, appt, insurance)
        _seed_gap(db, check)
        data = client.get("/gaps").json()[0]
        for field in ("gap_id", "gap_type", "severity", "description", "is_resolved"):
            assert field in data


# ---------------------------------------------------------------------------
# PATCH /gaps/{id}/resolve
# ---------------------------------------------------------------------------

class TestResolveGap:

    def test_resolve_marks_gap_resolved(self, client, db):
        appt, insurance = _seed(db)
        check = _seed_check(db, appt, insurance)
        gap = _seed_gap(db, check)
        body = {"resolved_by": "Jane Front Desk", "resolution_note": "Called insurance, OK"}
        data = client.patch(f"/gaps/{gap.gap_id}/resolve", json=body).json()
        assert data["is_resolved"] is True
        assert data["resolved_by"] == "Jane Front Desk"

    def test_resolve_without_note(self, client, db):
        appt, insurance = _seed(db)
        check = _seed_check(db, appt, insurance)
        gap = _seed_gap(db, check)
        data = client.patch(f"/gaps/{gap.gap_id}/resolve", json={"resolved_by": "Staff"}).json()
        assert data["resolution_note"] is None

    def test_resolve_404_for_unknown_gap(self, client):
        assert client.patch(f"/gaps/{uuid.uuid4()}/resolve", json={"resolved_by": "Staff"}).status_code == 404

    def test_gap_persisted_as_resolved(self, client, db):
        appt, insurance = _seed(db)
        check = _seed_check(db, appt, insurance)
        gap = _seed_gap(db, check)
        client.patch(f"/gaps/{gap.gap_id}/resolve", json={"resolved_by": "Admin"})
        # Expire local cache and re-read
        db.expire_all()
        gap_after = db.get(CoverageGap, gap.gap_id)
        assert gap_after.is_resolved is True


# ---------------------------------------------------------------------------
# GET /dashboard
# ---------------------------------------------------------------------------

class TestDashboard:

    def test_returns_200(self, client):
        assert client.get("/dashboard").status_code == 200

    def test_returns_all_count_fields(self, client):
        data = client.get("/dashboard").json()
        for field in ("appointments_today", "pending_checks", "gaps_unresolved", "checks_failed"):
            assert field in data

    def test_counts_start_at_zero(self, client):
        data = client.get("/dashboard").json()
        assert data["pending_checks"] == 0
        assert data["gaps_unresolved"] == 0
        assert data["checks_failed"] == 0

    def test_pending_checks_count(self, client, db):
        appt, insurance = _seed(db)
        _seed_check(db, appt, insurance, status="queued")
        assert client.get("/dashboard").json()["pending_checks"] == 1

    def test_gaps_unresolved_count(self, client, db):
        appt, insurance = _seed(db)
        check = _seed_check(db, appt, insurance)
        _seed_gap(db, check)
        assert client.get("/dashboard").json()["gaps_unresolved"] == 1

    def test_failed_checks_count(self, client, db):
        appt, insurance = _seed(db)
        _seed_check(db, appt, insurance, status="failed")
        assert client.get("/dashboard").json()["checks_failed"] == 1
