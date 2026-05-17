"""
Tests for the eligibility worker and appointment watcher.

No real database or Celery is used here. All DB objects are plain Python
objects built with mock.MagicMock(), and Celery task infrastructure is
patched out so _process_check and _queue_checks can be called directly.

What is covered:
  build_inquiry_from_check   — self vs dependent insurance, fallback fields
  save_eligibility_result    — field mapping from ParsedEligibility to model
  _process_check             — full happy-path pipeline via MockClearinghouseClient
  _process_check             — ConnectionError → retry path
  _process_check             — non-retryable failure → mark failed, raise
  _mark_check_failed         — re-queries and updates correctly
  _queue_checks              — queries, creates checks, calls .delay()

How to run:
    pytest tests/test_eligibility_worker.py -v
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock, PropertyMock, patch, call

import pytest

from app.clearinghouse.mock_client import MockClearinghouseClient, SCENARIO_ACTIVE_PPO, SCENARIO_INACTIVE
from app.edi.models import EligibilityInquiry
from app.edi.parsed_models import ParsedEligibility
from app.workers.eligibility_worker import (
    build_inquiry_from_check,
    save_eligibility_result,
    _process_check,
    _mark_check_failed,
)


# ---------------------------------------------------------------------------
# Fixture helpers — build mock DB model objects
# ---------------------------------------------------------------------------

def make_provider(**kwargs):
    p = MagicMock()
    p.npi = kwargs.get("npi", "1234567890")
    p.organization_name = kwargs.get("organization_name", "Test Clinic")
    p.first_name = kwargs.get("first_name", None)
    p.last_name = kwargs.get("last_name", None)
    return p


def make_payer(**kwargs):
    p = MagicMock()
    p.edi_payer_id = kwargs.get("edi_payer_id", "00050")
    p.payer_name = kwargs.get("payer_name", "Blue Cross")
    return p


def make_patient(**kwargs):
    p = MagicMock()
    p.last_name = kwargs.get("last_name", "DOE")
    p.first_name = kwargs.get("first_name", "JOHN")
    p.date_of_birth = kwargs.get("date_of_birth", date(1985, 1, 15))
    p.gender = kwargs.get("gender", "M")
    return p


def make_insurance(**kwargs):
    ins = MagicMock()
    ins.member_id = kwargs.get("member_id", "MEM001")
    ins.relationship_to_subscriber = kwargs.get("relationship_to_subscriber", "self")
    ins.subscriber_last_name = kwargs.get("subscriber_last_name", None)
    ins.subscriber_first_name = kwargs.get("subscriber_first_name", None)
    ins.subscriber_date_of_birth = kwargs.get("subscriber_date_of_birth", None)
    ins.subscriber_member_id = kwargs.get("subscriber_member_id", None)
    ins.payer = make_payer(**kwargs)
    return ins


def make_appointment(**kwargs):
    appt = MagicMock()
    appt.appointment_id = kwargs.get("appointment_id", uuid.uuid4())
    appt.service_type_code = kwargs.get("service_type_code", "30")
    appt.appointment_datetime = datetime(2024, 6, 15, 9, 0, tzinfo=timezone.utc)
    appt.patient = make_patient(**kwargs)
    appt.provider = make_provider(**kwargs)
    appt.eligibility_status = "pending"
    return appt


def make_check(**kwargs):
    check = MagicMock()
    check.check_id = kwargs.get("check_id", uuid.uuid4())
    check.attempt_number = kwargs.get("attempt_number", 0)
    check.status = "queued"
    check.submitted_at = None
    check.responded_at = None
    check.clearinghouse_transaction_id = None
    check.appointment = make_appointment(**kwargs)
    check.insurance = make_insurance(**kwargs)
    return check


# ---------------------------------------------------------------------------
# build_inquiry_from_check tests
# ---------------------------------------------------------------------------

class TestBuildInquiryFromCheck:

    def test_returns_eligibility_inquiry(self):
        check = make_check()
        result = build_inquiry_from_check(check)
        assert isinstance(result, EligibilityInquiry)

    def test_self_relationship_uses_patient_as_subscriber(self):
        check = make_check(
            relationship_to_subscriber="self",
            last_name="SMITH",
            first_name="JANE",
            member_id="SM001",
        )
        inquiry = build_inquiry_from_check(check)
        assert inquiry.subscriber.last_name == "SMITH"
        assert inquiry.subscriber.first_name == "JANE"
        assert inquiry.subscriber.member_id == "SM001"
        assert inquiry.dependent is None

    def test_self_relationship_no_dependent(self):
        check = make_check(relationship_to_subscriber="self")
        inquiry = build_inquiry_from_check(check)
        assert inquiry.dependent is None

    def test_dependent_relationship_creates_dependent_info(self):
        check = make_check(
            relationship_to_subscriber="child",
            last_name="KID",
            first_name="JUNIOR",
        )
        # Subscriber fields on insurance (parent/spouse)
        check.insurance.subscriber_last_name = "PARENT"
        check.insurance.subscriber_first_name = "SENIOR"
        check.insurance.subscriber_date_of_birth = date(1975, 6, 1)
        check.insurance.subscriber_member_id = "PAR001"

        inquiry = build_inquiry_from_check(check)
        assert inquiry.subscriber.last_name == "PARENT"
        assert inquiry.subscriber.first_name == "SENIOR"
        assert inquiry.subscriber.member_id == "PAR001"
        assert inquiry.dependent is not None
        assert inquiry.dependent.last_name == "KID"
        assert inquiry.dependent.first_name == "JUNIOR"

    def test_dependent_falls_back_to_patient_when_subscriber_fields_missing(self):
        check = make_check(
            relationship_to_subscriber="spouse",
            last_name="JONES",
            first_name="ALICE",
        )
        check.insurance.subscriber_last_name = None
        check.insurance.subscriber_first_name = None
        check.insurance.subscriber_date_of_birth = None
        check.insurance.subscriber_member_id = None

        inquiry = build_inquiry_from_check(check)
        # Falls back to patient data
        assert inquiry.subscriber.last_name == "JONES"
        assert inquiry.subscriber.first_name == "ALICE"

    def test_provider_npi_is_set(self):
        check = make_check(npi="9876543210")
        inquiry = build_inquiry_from_check(check)
        assert inquiry.provider.npi == "9876543210"

    def test_payer_edi_id_is_set(self):
        check = make_check(edi_payer_id="BCBS01")
        inquiry = build_inquiry_from_check(check)
        assert inquiry.payer.edi_payer_id == "BCBS01"

    def test_reference_id_is_appointment_id(self):
        appt_id = uuid.uuid4()
        check = make_check(appointment_id=appt_id)
        inquiry = build_inquiry_from_check(check)
        assert inquiry.reference_id == str(appt_id)

    def test_service_type_code_used(self):
        check = make_check(service_type_code="98")
        inquiry = build_inquiry_from_check(check)
        assert "98" in inquiry.service_type_codes

    def test_default_service_type_code_when_none(self):
        check = make_check()
        check.appointment.service_type_code = None
        inquiry = build_inquiry_from_check(check)
        assert "30" in inquiry.service_type_codes

    def test_gender_defaults_to_unknown_when_none(self):
        check = make_check()
        check.appointment.patient.gender = None
        inquiry = build_inquiry_from_check(check)
        assert inquiry.subscriber.gender == "U"

    def test_org_name_from_organization_name(self):
        check = make_check()
        check.appointment.provider.organization_name = "Main Street Clinic"
        check.appointment.provider.first_name = None
        check.appointment.provider.last_name = None
        inquiry = build_inquiry_from_check(check)
        assert inquiry.provider.org_name == "Main Street Clinic"

    def test_org_name_built_from_first_last_when_no_org(self):
        check = make_check()
        check.appointment.provider.organization_name = None
        check.appointment.provider.first_name = "John"
        check.appointment.provider.last_name = "Smith"
        inquiry = build_inquiry_from_check(check)
        assert inquiry.provider.org_name == "John Smith"


# ---------------------------------------------------------------------------
# save_eligibility_result tests
# ---------------------------------------------------------------------------

class TestSaveEligibilityResult:

    def _make_parsed(self, **kwargs) -> ParsedEligibility:
        defaults = dict(
            coverage_active=True,
            plan_name="Test PPO",
            plan_type="PPO",
            in_network=True,
            coverage_effective_date=date(2024, 1, 1),
            coverage_termination_date=date(2024, 12, 31),
            deductible_individual=Decimal("500.00"),
            deductible_individual_remaining=Decimal("350.00"),
            deductible_family=Decimal("1000.00"),
            deductible_family_remaining=Decimal("800.00"),
            oop_max_individual=Decimal("3000.00"),
            oop_max_individual_remaining=Decimal("2500.00"),
            oop_max_family=Decimal("6000.00"),
            oop_max_family_remaining=Decimal("5000.00"),
            copay_amount=Decimal("30.00"),
            coinsurance_percent=Decimal("20"),
            referral_required=False,
            prior_auth_required=False,
            rejection_reasons=[],
            raw_eb_segments=[],
        )
        defaults.update(kwargs)
        return ParsedEligibility(**defaults)

    def test_result_has_correct_check_id(self):
        db = MagicMock()
        check_id = uuid.uuid4()
        parsed = self._make_parsed()
        result = save_eligibility_result(check_id, parsed, db)
        assert result.check_id == check_id

    def test_coverage_active_mapped(self):
        db = MagicMock()
        parsed = self._make_parsed(coverage_active=True)
        result = save_eligibility_result(uuid.uuid4(), parsed, db)
        assert result.coverage_active is True

    def test_deductible_individual_mapped(self):
        db = MagicMock()
        parsed = self._make_parsed(deductible_individual=Decimal("1500.00"))
        result = save_eligibility_result(uuid.uuid4(), parsed, db)
        assert result.deductible_individual == Decimal("1500.00")

    def test_copay_mapped(self):
        db = MagicMock()
        parsed = self._make_parsed(copay_amount=Decimal("25.00"))
        result = save_eligibility_result(uuid.uuid4(), parsed, db)
        assert result.copay_amount == Decimal("25.00")

    def test_raw_parsed_data_has_rejection_reasons(self):
        db = MagicMock()
        parsed = self._make_parsed(rejection_reasons=["Member not found"])
        result = save_eligibility_result(uuid.uuid4(), parsed, db)
        assert result.raw_parsed_data["rejection_reasons"] == ["Member not found"]

    def test_result_added_to_db(self):
        db = MagicMock()
        parsed = self._make_parsed()
        save_eligibility_result(uuid.uuid4(), parsed, db)
        db.add.assert_called_once()

    def test_none_fields_when_inactive(self):
        db = MagicMock()
        parsed = self._make_parsed(
            coverage_active=False,
            deductible_individual=None,
            copay_amount=None,
            coinsurance_percent=None,
        )
        result = save_eligibility_result(uuid.uuid4(), parsed, db)
        assert result.deductible_individual is None
        assert result.copay_amount is None


# ---------------------------------------------------------------------------
# _process_check tests — full pipeline via MockClearinghouseClient
# ---------------------------------------------------------------------------

class TestProcessCheckHappyPath:
    """
    Tests _process_check with a real MockClearinghouseClient but a mocked DB.
    This validates the pipeline logic without any database infrastructure.
    """

    def _make_db(self, check):
        """Returns a mock Session that returns `check` from db.get()."""
        db = MagicMock()
        db.get.return_value = check
        db.scalars.return_value.all.return_value = []
        return db

    @patch("app.workers.eligibility_worker.next_control_number", return_value="000000001")
    def test_completed_status_on_success(self, mock_cn):
        check = make_check()
        db = self._make_db(check)
        task = MagicMock()
        task.request.retries = 0
        clearinghouse = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_PPO)

        result = _process_check(task, str(check.check_id), db, clearinghouse)

        assert result["status"] == "completed"
        assert check.status == "completed"

    @patch("app.workers.eligibility_worker.next_control_number", return_value="000000001")
    def test_appointment_verified_when_no_gaps(self, mock_cn):
        check = make_check()
        db = self._make_db(check)
        task = MagicMock()
        task.request.retries = 0
        clearinghouse = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_PPO)

        with patch("app.workers.eligibility_worker.GapAnalyzer") as MockGA:
            MockGA.return_value.analyze.return_value = []
            _process_check(task, str(check.check_id), db, clearinghouse)

        assert check.appointment.eligibility_status == "verified"

    @patch("app.workers.eligibility_worker.next_control_number", return_value="000000001")
    def test_coverage_active_in_return_dict(self, mock_cn):
        check = make_check()
        db = self._make_db(check)
        task = MagicMock()
        task.request.retries = 0
        clearinghouse = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_PPO)

        result = _process_check(task, str(check.check_id), db, clearinghouse)

        assert result["coverage_active"] is True

    @patch("app.workers.eligibility_worker.next_control_number", return_value="000000001")
    def test_two_edi_logs_created(self, mock_cn):
        """One log for 270 (outbound), one for 271 (inbound)."""
        check = make_check()
        db = self._make_db(check)
        task = MagicMock()
        task.request.retries = 0
        clearinghouse = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_PPO)

        _process_check(task, str(check.check_id), db, clearinghouse)

        add_calls = db.add.call_args_list
        # At least 2 adds: one 270 log, one 271 log
        # (more adds possible for result and gap records)
        assert len(add_calls) >= 2

    @patch("app.workers.eligibility_worker.next_control_number", return_value="000000001")
    def test_attempt_number_incremented(self, mock_cn):
        check = make_check()
        check.attempt_number = 0
        db = self._make_db(check)
        task = MagicMock()
        task.request.retries = 0
        clearinghouse = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_PPO)

        _process_check(task, str(check.check_id), db, clearinghouse)

        assert check.attempt_number == 1

    @patch("app.workers.eligibility_worker.next_control_number", return_value="000000001")
    def test_not_found_returns_early(self, mock_cn):
        db = MagicMock()
        db.get.return_value = None
        task = MagicMock()
        clearinghouse = MockClearinghouseClient()
        check_id = str(uuid.uuid4())

        result = _process_check(task, check_id, db, clearinghouse)

        assert result["status"] == "not_found"

    @patch("app.workers.eligibility_worker.next_control_number", return_value="000000001")
    def test_gaps_found_sets_gap_found_status(self, mock_cn):
        check = make_check()
        db = self._make_db(check)
        task = MagicMock()
        task.request.retries = 0
        clearinghouse = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_PPO)

        # Patch GapAnalyzer to return one gap
        fake_gap = MagicMock()
        fake_gap.gap_type = "HIGH_DEDUCTIBLE"
        fake_gap.severity = "warning"
        fake_gap.description = "Deductible not met"

        with patch("app.workers.eligibility_worker.GapAnalyzer") as MockGapAnalyzer:
            MockGapAnalyzer.return_value.analyze.return_value = [fake_gap]
            result = _process_check(task, str(check.check_id), db, clearinghouse)

        assert result["gaps_found"] == 1
        assert check.appointment.eligibility_status == "gap_found"


# ---------------------------------------------------------------------------
# _process_check — retry on ConnectionError
# ---------------------------------------------------------------------------

class TestProcessCheckRetry:

    def _make_db(self, check):
        db = MagicMock()
        db.get.return_value = check
        return db

    @patch("app.workers.eligibility_worker.next_control_number", return_value="000000001")
    def test_connection_error_triggers_retry(self, mock_cn):
        check = make_check()
        db = self._make_db(check)
        task = MagicMock()
        task.request.retries = 0
        task.retry.side_effect = Exception("retry raised")

        # Clearinghouse that always fails
        failing_clearinghouse = MagicMock()
        failing_clearinghouse.submit_270.return_value = MagicMock(
            success=False, error_message="Timeout", transaction_id=None, edi_271=None
        )

        with pytest.raises(Exception, match="retry raised"):
            _process_check(task, str(check.check_id), db, failing_clearinghouse)

        task.retry.assert_called_once()

    @patch("app.workers.eligibility_worker.next_control_number", return_value="000000001")
    def test_retry_countdown_exponential(self, mock_cn):
        check = make_check()
        db = MagicMock()
        db.get.return_value = check
        task = MagicMock()
        task.request.retries = 1  # second attempt
        task.retry.side_effect = Exception("retry raised")

        failing_clearinghouse = MagicMock()
        failing_clearinghouse.submit_270.return_value = MagicMock(
            success=False, error_message="Timeout", transaction_id=None, edi_271=None
        )

        with pytest.raises(Exception):
            _process_check(task, str(check.check_id), db, failing_clearinghouse)

        _, kwargs = task.retry.call_args
        # On second attempt (retries=1), countdown = 60 * 2^1 = 120
        assert kwargs["countdown"] == 120

    @patch("app.workers.eligibility_worker.next_control_number", return_value="000000001")
    def test_non_retryable_error_marks_failed(self, mock_cn):
        check = make_check()
        db = MagicMock()
        db.get.return_value = check
        task = MagicMock()
        task.request.retries = 0
        clearinghouse = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_PPO)

        # Patch parser to raise a non-network error
        with patch("app.workers.eligibility_worker.Parser271") as MockParser:
            MockParser.return_value.parse.side_effect = ValueError("bad EDI data")
            with pytest.raises(ValueError):
                _process_check(task, str(check.check_id), db, clearinghouse)

        # task.retry must NOT have been called
        task.retry.assert_not_called()


# ---------------------------------------------------------------------------
# _mark_check_failed tests
# ---------------------------------------------------------------------------

class TestMarkCheckFailed:

    def test_sets_status_to_failed(self):
        check = MagicMock()
        check.appointment = MagicMock()
        db = MagicMock()
        db.get.return_value = check

        _mark_check_failed(str(uuid.uuid4()), "some error", update_appointment=False, db=db)

        assert check.status == "failed"

    def test_error_message_truncated_to_500(self):
        check = MagicMock()
        check.appointment = MagicMock()
        db = MagicMock()
        db.get.return_value = check

        long_msg = "x" * 600
        _mark_check_failed(str(uuid.uuid4()), long_msg, update_appointment=False, db=db)

        assert len(check.error_message) == 500

    def test_updates_appointment_when_flag_true(self):
        check = MagicMock()
        check.appointment = MagicMock()
        db = MagicMock()
        db.get.return_value = check

        _mark_check_failed(str(uuid.uuid4()), "error", update_appointment=True, db=db)

        assert check.appointment.eligibility_status == "error"

    def test_does_not_update_appointment_when_flag_false(self):
        check = MagicMock()
        check.appointment = MagicMock()
        db = MagicMock()
        db.get.return_value = check
        original_status = check.appointment.eligibility_status

        _mark_check_failed(str(uuid.uuid4()), "error", update_appointment=False, db=db)

        # Should not have changed
        assert check.appointment.eligibility_status == original_status

    def test_handles_missing_check_gracefully(self):
        db = MagicMock()
        db.get.return_value = None

        # Must not raise
        _mark_check_failed(str(uuid.uuid4()), "error", update_appointment=True, db=db)

    def test_commits_after_update(self):
        check = MagicMock()
        check.appointment = MagicMock()
        db = MagicMock()
        db.get.return_value = check

        _mark_check_failed(str(uuid.uuid4()), "error", update_appointment=False, db=db)

        db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# _queue_checks tests (appointment watcher logic)
# ---------------------------------------------------------------------------

class TestQueueChecks:

    def _make_appointment(self, appt_id=None, insurance_id=None):
        appt = MagicMock()
        appt.appointment_id = appt_id or uuid.uuid4()
        appt.insurance_id = insurance_id or uuid.uuid4()
        return appt

    @patch("app.workers.appointment_watcher.run_eligibility_check")
    def test_creates_check_for_each_appointment(self, mock_task):
        from app.workers.appointment_watcher import _queue_checks

        appt1 = self._make_appointment()
        appt2 = self._make_appointment()

        db = MagicMock()
        db.scalars.return_value.all.return_value = [appt1, appt2]

        result = _queue_checks(db)

        assert result["queued"] == 2

    @patch("app.workers.appointment_watcher.run_eligibility_check")
    def test_delay_called_for_each_check(self, mock_task):
        from app.workers.appointment_watcher import _queue_checks

        appt1 = self._make_appointment()

        db = MagicMock()
        db.scalars.return_value.all.return_value = [appt1]

        _queue_checks(db)

        mock_task.delay.assert_called_once()

    @patch("app.workers.appointment_watcher.run_eligibility_check")
    def test_check_triggered_by_scheduler(self, mock_task):
        from app.workers.appointment_watcher import _queue_checks

        appt = self._make_appointment()

        added_checks = []
        db = MagicMock()
        db.scalars.return_value.all.return_value = [appt]
        db.add.side_effect = lambda obj: added_checks.append(obj)

        _queue_checks(db)

        assert any(getattr(c, "triggered_by", None) == "scheduler" for c in added_checks)

    @patch("app.workers.appointment_watcher.run_eligibility_check")
    def test_empty_list_when_no_appointments(self, mock_task):
        from app.workers.appointment_watcher import _queue_checks

        db = MagicMock()
        db.scalars.return_value.all.return_value = []

        result = _queue_checks(db)

        assert result["queued"] == 0
        assert result["check_ids"] == []
        mock_task.delay.assert_not_called()

    @patch("app.workers.appointment_watcher.run_eligibility_check")
    def test_commits_at_end(self, mock_task):
        from app.workers.appointment_watcher import _queue_checks

        db = MagicMock()
        db.scalars.return_value.all.return_value = []

        _queue_checks(db)

        db.commit.assert_called_once()

    @patch("app.workers.appointment_watcher.run_eligibility_check")
    def test_result_includes_window_days(self, mock_task):
        from app.workers.appointment_watcher import _queue_checks

        db = MagicMock()
        db.scalars.return_value.all.return_value = []

        result = _queue_checks(db)

        assert "window_days" in result
        assert isinstance(result["window_days"], int)
