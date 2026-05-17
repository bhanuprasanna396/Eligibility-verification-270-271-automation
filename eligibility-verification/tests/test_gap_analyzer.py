"""
Tests for GapAnalyzer — the rule engine that turns parsed coverage data
into actionable staff alerts.

Each test class covers one rule. Tests verify:
  - the right gap_type is produced
  - the right severity is set
  - the rule fires (or does not fire) under correct conditions
  - early-exit rules prevent downstream rules from running

How to run:
    pytest tests/test_gap_analyzer.py -v
"""
from datetime import date
from decimal import Decimal

import pytest

from app.analyzers.gap_analyzer import Gap, GapAnalyzer
from app.edi.parsed_models import ParsedEligibility


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def active_result(**kwargs) -> ParsedEligibility:
    """
    Returns a ParsedEligibility that represents clean, active coverage.
    Override any field via keyword args to test specific conditions.
    """
    defaults = dict(
        coverage_active=True,
        rejection_reasons=[],
        plan_name="Standard PPO",
        plan_type="PPO",
        in_network=True,
        coverage_effective_date=date(2024, 1, 1),
        coverage_termination_date=date(2024, 12, 31),
        deductible_individual=Decimal("500.00"),
        deductible_individual_remaining=Decimal("0.00"),
        deductible_family=None,
        deductible_family_remaining=None,
        oop_max_individual=Decimal("3000.00"),
        oop_max_individual_remaining=Decimal("0.00"),
        oop_max_family=None,
        oop_max_family_remaining=None,
        copay_amount=Decimal("30.00"),
        coinsurance_percent=Decimal("20"),
        referral_required=False,
        prior_auth_required=False,
        raw_eb_segments=[],
    )
    defaults.update(kwargs)
    return ParsedEligibility(**defaults)


APPT_DATE_WITHIN = date(2024, 6, 15)   # inside the coverage window
APPT_DATE_BEFORE  = date(2023, 12, 31) # before effective date
APPT_DATE_AFTER   = date(2025, 1, 1)   # after termination date


# ---------------------------------------------------------------------------
# No gaps — clean coverage
# ---------------------------------------------------------------------------

class TestNoGaps:

    def test_clean_active_coverage_produces_no_gaps(self):
        result = active_result(deductible_individual_remaining=Decimal("0.00"))
        gaps = GapAnalyzer().analyze(result, appointment_date=APPT_DATE_WITHIN)
        assert gaps == []

    def test_returns_list(self):
        result = active_result()
        gaps = GapAnalyzer().analyze(result)
        assert isinstance(gaps, list)

    def test_no_appointment_date_skips_date_check(self):
        """Passing no appointment_date must not raise and must produce no date gap."""
        result = active_result()
        gaps = GapAnalyzer().analyze(result, appointment_date=None)
        gap_types = [g.gap_type for g in gaps]
        assert "COVERAGE_DATE_MISMATCH" not in gap_types


# ---------------------------------------------------------------------------
# Rule 1: Payer rejection
# ---------------------------------------------------------------------------

class TestPayerRejection:

    def test_rejection_produces_payer_rejection_gap(self):
        result = active_result(
            coverage_active=False,
            rejection_reasons=["Member not found"],
        )
        gaps = GapAnalyzer().analyze(result)
        assert any(g.gap_type == "PAYER_REJECTION" for g in gaps)

    def test_rejection_severity_is_critical(self):
        result = active_result(
            coverage_active=False,
            rejection_reasons=["Member not found"],
        )
        gaps = GapAnalyzer().analyze(result)
        gap = next(g for g in gaps if g.gap_type == "PAYER_REJECTION")
        assert gap.severity == "critical"

    def test_rejection_reason_in_description(self):
        result = active_result(
            coverage_active=False,
            rejection_reasons=["Member not found"],
        )
        gaps = GapAnalyzer().analyze(result)
        gap = next(g for g in gaps if g.gap_type == "PAYER_REJECTION")
        assert "Member not found" in gap.description

    def test_rejection_stops_analysis_early(self):
        """No other gaps should be produced when a rejection occurs."""
        result = active_result(
            coverage_active=False,
            rejection_reasons=["Member not found"],
            in_network=False,
            prior_auth_required=True,
            referral_required=True,
        )
        gaps = GapAnalyzer().analyze(result)
        assert len(gaps) == 1
        assert gaps[0].gap_type == "PAYER_REJECTION"

    def test_multiple_rejection_reasons_all_in_description(self):
        result = active_result(
            coverage_active=False,
            rejection_reasons=["Member not found", "Payer not recognized"],
        )
        gaps = GapAnalyzer().analyze(result)
        gap = next(g for g in gaps if g.gap_type == "PAYER_REJECTION")
        assert "Member not found" in gap.description
        assert "Payer not recognized" in gap.description


# ---------------------------------------------------------------------------
# Rule 2: Inactive coverage
# ---------------------------------------------------------------------------

class TestInactiveCoverage:

    def test_inactive_produces_inactive_coverage_gap(self):
        result = active_result(coverage_active=False)
        gaps = GapAnalyzer().analyze(result)
        assert any(g.gap_type == "INACTIVE_COVERAGE" for g in gaps)

    def test_inactive_severity_is_critical(self):
        result = active_result(coverage_active=False)
        gaps = GapAnalyzer().analyze(result)
        gap = next(g for g in gaps if g.gap_type == "INACTIVE_COVERAGE")
        assert gap.severity == "critical"

    def test_inactive_stops_analysis_early(self):
        """Financial rules must not fire when coverage is inactive."""
        result = active_result(
            coverage_active=False,
            in_network=False,
            prior_auth_required=True,
            deductible_individual_remaining=Decimal("2000.00"),
        )
        gaps = GapAnalyzer().analyze(result)
        gap_types = [g.gap_type for g in gaps]
        assert "INACTIVE_COVERAGE" in gap_types
        assert "OUT_OF_NETWORK" not in gap_types
        assert "HIGH_DEDUCTIBLE" not in gap_types

    def test_active_coverage_does_not_produce_inactive_gap(self):
        result = active_result(coverage_active=True)
        gaps = GapAnalyzer().analyze(result)
        assert not any(g.gap_type == "INACTIVE_COVERAGE" for g in gaps)


# ---------------------------------------------------------------------------
# Rule 3: Coverage date mismatch
# ---------------------------------------------------------------------------

class TestCoverageDateMismatch:

    def test_appointment_before_effective_date_produces_gap(self):
        result = active_result(coverage_effective_date=date(2024, 1, 1))
        gaps = GapAnalyzer().analyze(result, appointment_date=APPT_DATE_BEFORE)
        assert any(g.gap_type == "COVERAGE_DATE_MISMATCH" for g in gaps)

    def test_appointment_after_termination_date_produces_gap(self):
        result = active_result(coverage_termination_date=date(2024, 12, 31))
        gaps = GapAnalyzer().analyze(result, appointment_date=APPT_DATE_AFTER)
        assert any(g.gap_type == "COVERAGE_DATE_MISMATCH" for g in gaps)

    def test_date_mismatch_severity_is_critical(self):
        result = active_result()
        gaps = GapAnalyzer().analyze(result, appointment_date=APPT_DATE_BEFORE)
        gap = next(g for g in gaps if g.gap_type == "COVERAGE_DATE_MISMATCH")
        assert gap.severity == "critical"

    def test_appointment_within_window_no_date_gap(self):
        result = active_result(
            coverage_effective_date=date(2024, 1, 1),
            coverage_termination_date=date(2024, 12, 31),
        )
        gaps = GapAnalyzer().analyze(result, appointment_date=APPT_DATE_WITHIN)
        assert not any(g.gap_type == "COVERAGE_DATE_MISMATCH" for g in gaps)

    def test_appointment_on_effective_date_is_ok(self):
        result = active_result(coverage_effective_date=date(2024, 6, 15))
        gaps = GapAnalyzer().analyze(result, appointment_date=date(2024, 6, 15))
        assert not any(g.gap_type == "COVERAGE_DATE_MISMATCH" for g in gaps)

    def test_appointment_on_termination_date_is_ok(self):
        result = active_result(coverage_termination_date=date(2024, 6, 15))
        gaps = GapAnalyzer().analyze(result, appointment_date=date(2024, 6, 15))
        assert not any(g.gap_type == "COVERAGE_DATE_MISMATCH" for g in gaps)

    def test_no_effective_date_skips_start_check(self):
        result = active_result(coverage_effective_date=None)
        # appointment_date before a missing effective_date → should NOT flag
        gaps = GapAnalyzer().analyze(result, appointment_date=date(2020, 1, 1))
        assert not any(g.gap_type == "COVERAGE_DATE_MISMATCH" for g in gaps)

    def test_no_termination_date_skips_end_check(self):
        result = active_result(coverage_termination_date=None)
        gaps = GapAnalyzer().analyze(result, appointment_date=date(2099, 1, 1))
        assert not any(g.gap_type == "COVERAGE_DATE_MISMATCH" for g in gaps)

    def test_dates_in_description(self):
        result = active_result(coverage_effective_date=date(2024, 3, 1))
        gaps = GapAnalyzer().analyze(result, appointment_date=date(2024, 1, 15))
        gap = next(g for g in gaps if g.gap_type == "COVERAGE_DATE_MISMATCH")
        assert "2024-01-15" in gap.description
        assert "2024-03-01" in gap.description


# ---------------------------------------------------------------------------
# Rule 4: Out of network
# ---------------------------------------------------------------------------

class TestOutOfNetwork:

    def test_out_of_network_produces_gap(self):
        result = active_result(in_network=False)
        gaps = GapAnalyzer().analyze(result)
        assert any(g.gap_type == "OUT_OF_NETWORK" for g in gaps)

    def test_out_of_network_severity_is_high(self):
        result = active_result(in_network=False)
        gaps = GapAnalyzer().analyze(result)
        gap = next(g for g in gaps if g.gap_type == "OUT_OF_NETWORK")
        assert gap.severity == "high"

    def test_in_network_does_not_produce_gap(self):
        result = active_result(in_network=True)
        gaps = GapAnalyzer().analyze(result)
        assert not any(g.gap_type == "OUT_OF_NETWORK" for g in gaps)

    def test_unknown_network_status_does_not_produce_gap(self):
        """in_network=None means payer didn't send the info — don't assume OON."""
        result = active_result(in_network=None)
        gaps = GapAnalyzer().analyze(result)
        assert not any(g.gap_type == "OUT_OF_NETWORK" for g in gaps)


# ---------------------------------------------------------------------------
# Rule 5: Prior authorization required
# ---------------------------------------------------------------------------

class TestPriorAuthRequired:

    def test_prior_auth_produces_gap(self):
        result = active_result(prior_auth_required=True)
        gaps = GapAnalyzer().analyze(result)
        assert any(g.gap_type == "PRIOR_AUTH_REQUIRED" for g in gaps)

    def test_prior_auth_severity_is_high(self):
        result = active_result(prior_auth_required=True)
        gaps = GapAnalyzer().analyze(result)
        gap = next(g for g in gaps if g.gap_type == "PRIOR_AUTH_REQUIRED")
        assert gap.severity == "high"

    def test_no_prior_auth_no_gap(self):
        result = active_result(prior_auth_required=False)
        gaps = GapAnalyzer().analyze(result)
        assert not any(g.gap_type == "PRIOR_AUTH_REQUIRED" for g in gaps)


# ---------------------------------------------------------------------------
# Rule 6: Referral required
# ---------------------------------------------------------------------------

class TestReferralRequired:

    def test_referral_required_produces_gap(self):
        result = active_result(referral_required=True)
        gaps = GapAnalyzer().analyze(result)
        assert any(g.gap_type == "REFERRAL_REQUIRED" for g in gaps)

    def test_referral_severity_is_warning(self):
        result = active_result(referral_required=True)
        gaps = GapAnalyzer().analyze(result)
        gap = next(g for g in gaps if g.gap_type == "REFERRAL_REQUIRED")
        assert gap.severity == "warning"

    def test_no_referral_no_gap(self):
        result = active_result(referral_required=False)
        gaps = GapAnalyzer().analyze(result)
        assert not any(g.gap_type == "REFERRAL_REQUIRED" for g in gaps)


# ---------------------------------------------------------------------------
# Rule 7: High deductible remaining
# ---------------------------------------------------------------------------

class TestHighDeductible:

    def test_deductible_above_high_threshold_produces_high_gap(self):
        # ≥ $1 000 → high severity
        result = active_result(deductible_individual_remaining=Decimal("1500.00"))
        gaps = GapAnalyzer().analyze(result)
        gap = next(g for g in gaps if g.gap_type == "HIGH_DEDUCTIBLE")
        assert gap.severity == "high"

    def test_deductible_exactly_high_threshold_produces_high_gap(self):
        result = active_result(deductible_individual_remaining=Decimal("1000.00"))
        gaps = GapAnalyzer().analyze(result)
        gap = next(g for g in gaps if g.gap_type == "HIGH_DEDUCTIBLE")
        assert gap.severity == "high"

    def test_deductible_above_warning_threshold_produces_warning_gap(self):
        # ≥ $500 but < $1 000 → warning
        result = active_result(deductible_individual_remaining=Decimal("750.00"))
        gaps = GapAnalyzer().analyze(result)
        gap = next(g for g in gaps if g.gap_type == "HIGH_DEDUCTIBLE")
        assert gap.severity == "warning"

    def test_deductible_exactly_warning_threshold_produces_warning_gap(self):
        result = active_result(deductible_individual_remaining=Decimal("500.00"))
        gaps = GapAnalyzer().analyze(result)
        gap = next(g for g in gaps if g.gap_type == "HIGH_DEDUCTIBLE")
        assert gap.severity == "warning"

    def test_deductible_below_threshold_no_gap(self):
        result = active_result(deductible_individual_remaining=Decimal("499.99"))
        gaps = GapAnalyzer().analyze(result)
        assert not any(g.gap_type == "HIGH_DEDUCTIBLE" for g in gaps)

    def test_zero_deductible_remaining_no_gap(self):
        result = active_result(deductible_individual_remaining=Decimal("0.00"))
        gaps = GapAnalyzer().analyze(result)
        assert not any(g.gap_type == "HIGH_DEDUCTIBLE" for g in gaps)

    def test_none_deductible_remaining_no_gap(self):
        """Payer didn't send remaining — we can't infer it, so no gap."""
        result = active_result(deductible_individual_remaining=None)
        gaps = GapAnalyzer().analyze(result)
        assert not any(g.gap_type == "HIGH_DEDUCTIBLE" for g in gaps)

    def test_deductible_amount_in_description(self):
        result = active_result(deductible_individual_remaining=Decimal("1200.00"))
        gaps = GapAnalyzer().analyze(result)
        gap = next(g for g in gaps if g.gap_type == "HIGH_DEDUCTIBLE")
        assert "1200.00" in gap.description


# ---------------------------------------------------------------------------
# Multiple gaps at once
# ---------------------------------------------------------------------------

class TestMultipleGaps:

    def test_out_of_network_and_prior_auth_both_fire(self):
        result = active_result(in_network=False, prior_auth_required=True)
        gaps = GapAnalyzer().analyze(result)
        gap_types = [g.gap_type for g in gaps]
        assert "OUT_OF_NETWORK" in gap_types
        assert "PRIOR_AUTH_REQUIRED" in gap_types

    def test_referral_and_high_deductible_both_fire(self):
        result = active_result(
            referral_required=True,
            deductible_individual_remaining=Decimal("800.00"),
        )
        gaps = GapAnalyzer().analyze(result)
        gap_types = [g.gap_type for g in gaps]
        assert "REFERRAL_REQUIRED" in gap_types
        assert "HIGH_DEDUCTIBLE" in gap_types

    def test_all_active_rules_fire_simultaneously(self):
        result = active_result(
            in_network=False,
            prior_auth_required=True,
            referral_required=True,
            deductible_individual_remaining=Decimal("1500.00"),
            coverage_termination_date=date(2023, 12, 31),  # already expired
        )
        gaps = GapAnalyzer().analyze(result, appointment_date=date(2024, 6, 1))
        gap_types = [g.gap_type for g in gaps]
        assert "COVERAGE_DATE_MISMATCH" in gap_types
        assert "OUT_OF_NETWORK" in gap_types
        assert "PRIOR_AUTH_REQUIRED" in gap_types
        assert "REFERRAL_REQUIRED" in gap_types
        assert "HIGH_DEDUCTIBLE" in gap_types

    def test_gap_dataclass_fields_present(self):
        result = active_result(prior_auth_required=True)
        gaps = GapAnalyzer().analyze(result)
        gap = gaps[0]
        assert hasattr(gap, "gap_type")
        assert hasattr(gap, "severity")
        assert hasattr(gap, "description")
        assert isinstance(gap.description, str)
        assert len(gap.description) > 0
