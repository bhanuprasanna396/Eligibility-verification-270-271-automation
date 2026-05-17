"""
Tests for the 271 parser.

Each test uses a realistic 271 EDI string representing a real scenario
a clinic would encounter. No database or network needed.

Scenarios covered:
  1. Active PPO coverage — subscriber is the patient
  2. Inactive (terminated) coverage
  3. Rejected 271 — member not found (AAA segment)
  4. Active coverage with a dependent (patient is child on parent's plan)
  5. Full financial data — deductible, OOP, copay, coinsurance
  6. Family deductible alongside individual deductible
  7. Coinsurance as decimal (0.20) vs whole number (20)
  8. Missing optional fields (payer sends minimal data)
  9. Coverage dates parsed from DTP segments

How to run:
    pytest tests/test_parser_271.py -v
"""
from datetime import date
from decimal import Decimal

import pytest

from app.edi.parser_271 import Parser271


# ---------------------------------------------------------------------------
# Sample 271 EDI strings
# Each is a realistic representation of what a clearinghouse returns.
# The ~ terminator is at end of every segment; newlines are only for readability.
# ---------------------------------------------------------------------------

# Scenario 1: Active PPO coverage, subscriber is the patient, in-network
ACTIVE_PPO = (
    "ISA*00*          *00*          *ZZ*CLEARINGHS     *ZZ*TESTCLINIC     "
    "*260517*1200*^*00501*000000001*0*P*:~"
    "GS*HB*CLEARINGHS*TESTCLINIC*20260517*1200*1*X*005010X279A1~"
    "ST*271*0001*005010X279A1~"
    "BHT*0022*11*appt-001*20260517*1200~"
    "HL*1**20*1~"
    "NM1*PR*2*BLUE CROSS BLUE SHIELD*****PI*00050~"
    "HL*2*1*21*1~"
    "NM1*1P*2*TEST MEDICAL CLINIC*****XX*1234567890~"
    "HL*3*2*22*0~"
    "NM1*IL*1*DOE*JOHN****MI*ABC123456789~"
    "EB*1**30*PP*PREFERRED PPO*******Y~"
    "DTP*346*D8*20260101~"
    "DTP*347*D8*20261231~"
    "EB*C*IND*30***23*500.00~"
    "EB*C*IND*30***29*347.50~"
    "EB*G*IND*30***23*3000.00~"
    "EB*G*IND*30***29*2500.00~"
    "EB*B**98***26*30.00~"
    "EB*A**98*****20~"
    "SE*19*0001~"
    "GE*1*1~"
    "IEA*1*000000001~"
)

# Scenario 2: Inactive (terminated) coverage
INACTIVE_COVERAGE = (
    "ISA*00*          *00*          *ZZ*CLEARINGHS     *ZZ*TESTCLINIC     "
    "*260517*1200*^*00501*000000002*0*P*:~"
    "GS*HB*CLEARINGHS*TESTCLINIC*20260517*1200*1*X*005010X279A1~"
    "ST*271*0001*005010X279A1~"
    "BHT*0022*11*appt-002*20260517*1200~"
    "HL*1**20*1~"
    "NM1*PR*2*AETNA*****PI*60054~"
    "HL*2*1*21*1~"
    "NM1*1P*2*TEST MEDICAL CLINIC*****XX*1234567890~"
    "HL*3*2*22*0~"
    "NM1*IL*1*SMITH*MARY****MI*XYZ789012~"
    "EB*6**30~"
    "SE*10*0001~"
    "GE*1*1~"
    "IEA*1*000000002~"
)

# Scenario 3: Rejected — member not found (AAA segment)
REJECTED_MEMBER_NOT_FOUND = (
    "ISA*00*          *00*          *ZZ*CLEARINGHS     *ZZ*TESTCLINIC     "
    "*260517*1200*^*00501*000000003*0*P*:~"
    "GS*HB*CLEARINGHS*TESTCLINIC*20260517*1200*1*X*005010X279A1~"
    "ST*271*0001*005010X279A1~"
    "BHT*0022*11*appt-003*20260517*1200~"
    "HL*1**20*1~"
    "NM1*PR*2*UNITED HEALTH*****PI*87726~"
    "HL*2*1*21*1~"
    "NM1*1P*2*TEST MEDICAL CLINIC*****XX*1234567890~"
    "HL*3*2*22*0~"
    "NM1*IL*1*JOHNSON*ROBERT****MI*INVALID999~"
    "AAA*N**75*C~"
    "SE*11*0001~"
    "GE*1*1~"
    "IEA*1*000000003~"
)

# Scenario 4: Active HMO coverage, patient is a dependent (child on parent's plan)
ACTIVE_WITH_DEPENDENT = (
    "ISA*00*          *00*          *ZZ*CLEARINGHS     *ZZ*TESTCLINIC     "
    "*260517*1200*^*00501*000000004*0*P*:~"
    "GS*HB*CLEARINGHS*TESTCLINIC*20260517*1200*1*X*005010X279A1~"
    "ST*271*0001*005010X279A1~"
    "BHT*0022*11*appt-004*20260517*1200~"
    "HL*1**20*1~"
    "NM1*PR*2*CIGNA*****PI*62308~"
    "HL*2*1*21*1~"
    "NM1*1P*2*TEST MEDICAL CLINIC*****XX*1234567890~"
    "HL*3*2*22*1~"
    "NM1*IL*1*BROWN*JAMES****MI*DEF456789~"
    "EB*1**30*HM*CIGNA HMO~"
    "HL*4*3*23*0~"
    "NM1*QC*1*BROWN*LISA~"
    "EB*1**30*HM*CIGNA HMO*******Y~"
    "EB*C*IND*30***23*1500.00~"
    "EB*C*IND*30***29*1500.00~"
    "EB*G*IND*30***23*5000.00~"
    "EB*G*IND*30***29*5000.00~"
    "EB*B**98***26*25.00~"
    "SE*20*0001~"
    "GE*1*1~"
    "IEA*1*000000004~"
)

# Scenario 5: Family deductible alongside individual deductible
FAMILY_AND_INDIVIDUAL_DEDUCTIBLE = (
    "ISA*00*          *00*          *ZZ*CLEARINGHS     *ZZ*TESTCLINIC     "
    "*260517*1200*^*00501*000000005*0*P*:~"
    "GS*HB*CLEARINGHS*TESTCLINIC*20260517*1200*1*X*005010X279A1~"
    "ST*271*0001*005010X279A1~"
    "BHT*0022*11*appt-005*20260517*1200~"
    "HL*1**20*1~"
    "NM1*PR*2*ANTHEM*****PI*00001~"
    "HL*2*1*21*1~"
    "NM1*1P*2*TEST MEDICAL CLINIC*****XX*1234567890~"
    "HL*3*2*22*0~"
    "NM1*IL*1*DAVIS*SARAH****MI*GHI321654~"
    "EB*1**30*PP*ANTHEM PPO~"
    "EB*C*IND*30***23*800.00~"
    "EB*C*IND*30***29*600.00~"
    "EB*C*FAM*30***23*2400.00~"
    "EB*C*FAM*30***29*1800.00~"
    "EB*G*IND*30***23*4000.00~"
    "EB*G*FAM*30***23*12000.00~"
    "SE*13*0001~"
    "GE*1*1~"
    "IEA*1*000000005~"
)

# Scenario 6: Coinsurance sent as decimal (0.20 instead of 20)
COINSURANCE_AS_DECIMAL = (
    "ISA*00*          *00*          *ZZ*CLEARINGHS     *ZZ*TESTCLINIC     "
    "*260517*1200*^*00501*000000006*0*P*:~"
    "GS*HB*CLEARINGHS*TESTCLINIC*20260517*1200*1*X*005010X279A1~"
    "ST*271*0001*005010X279A1~"
    "BHT*0022*11*appt-006*20260517*1200~"
    "HL*1**20*1~"
    "NM1*PR*2*HUMANA*****PI*61101~"
    "HL*2*1*21*1~"
    "NM1*1P*2*TEST MEDICAL CLINIC*****XX*1234567890~"
    "HL*3*2*22*0~"
    "NM1*IL*1*WILSON*TOM****MI*JKL987654~"
    "EB*1**30*PP*HUMANA PPO~"
    "EB*A**98*****0.20~"
    "SE*10*0001~"
    "GE*1*1~"
    "IEA*1*000000006~"
)

# Scenario 7: Minimal 271 — payer sends only active/inactive with no financial data
MINIMAL_ACTIVE = (
    "ISA*00*          *00*          *ZZ*CLEARINGHS     *ZZ*TESTCLINIC     "
    "*260517*1200*^*00501*000000007*0*P*:~"
    "GS*HB*CLEARINGHS*TESTCLINIC*20260517*1200*1*X*005010X279A1~"
    "ST*271*0001*005010X279A1~"
    "BHT*0022*11*appt-007*20260517*1200~"
    "HL*1**20*1~"
    "NM1*PR*2*MEDICARE*****PI*MEDICARE~"
    "HL*2*1*21*1~"
    "NM1*1P*2*TEST MEDICAL CLINIC*****XX*1234567890~"
    "HL*3*2*22*0~"
    "NM1*IL*1*TAYLOR*ALICE****MI*1EG4TE5MK73~"
    "EB*1**30~"
    "SE*10*0001~"
    "GE*1*1~"
    "IEA*1*000000007~"
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.fixture
def parser():
    return Parser271()


class TestActivePPOCoverage:

    def test_coverage_is_active(self, parser):
        result = parser.parse(ACTIVE_PPO)
        assert result.coverage_active is True

    def test_no_rejection_reasons(self, parser):
        result = parser.parse(ACTIVE_PPO)
        assert result.rejection_reasons == []

    def test_plan_type_is_ppo(self, parser):
        result = parser.parse(ACTIVE_PPO)
        assert result.plan_type == "PPO"

    def test_plan_name(self, parser):
        result = parser.parse(ACTIVE_PPO)
        assert result.plan_name == "PREFERRED PPO"

    def test_in_network_is_true(self, parser):
        result = parser.parse(ACTIVE_PPO)
        assert result.in_network is True

    def test_coverage_effective_date(self, parser):
        result = parser.parse(ACTIVE_PPO)
        assert result.coverage_effective_date == date(2026, 1, 1)

    def test_coverage_termination_date(self, parser):
        result = parser.parse(ACTIVE_PPO)
        assert result.coverage_termination_date == date(2026, 12, 31)

    def test_individual_deductible_total(self, parser):
        result = parser.parse(ACTIVE_PPO)
        assert result.deductible_individual == Decimal("500.00")

    def test_individual_deductible_remaining(self, parser):
        result = parser.parse(ACTIVE_PPO)
        assert result.deductible_individual_remaining == Decimal("347.50")

    def test_oop_max_individual_total(self, parser):
        result = parser.parse(ACTIVE_PPO)
        assert result.oop_max_individual == Decimal("3000.00")

    def test_oop_max_individual_remaining(self, parser):
        result = parser.parse(ACTIVE_PPO)
        assert result.oop_max_individual_remaining == Decimal("2500.00")

    def test_copay_amount(self, parser):
        result = parser.parse(ACTIVE_PPO)
        assert result.copay_amount == Decimal("30.00")

    def test_coinsurance_percent(self, parser):
        result = parser.parse(ACTIVE_PPO)
        assert result.coinsurance_percent == Decimal("20")

    def test_raw_eb_segments_populated(self, parser):
        result = parser.parse(ACTIVE_PPO)
        assert len(result.raw_eb_segments) > 0


class TestInactiveCoverage:

    def test_coverage_is_inactive(self, parser):
        result = parser.parse(INACTIVE_COVERAGE)
        assert result.coverage_active is False

    def test_no_rejection_reasons(self, parser):
        # Inactive is not a rejection — it is a valid answer from the payer
        result = parser.parse(INACTIVE_COVERAGE)
        assert result.rejection_reasons == []

    def test_no_financial_data(self, parser):
        result = parser.parse(INACTIVE_COVERAGE)
        assert result.deductible_individual is None
        assert result.copay_amount is None
        assert result.oop_max_individual is None


class TestRejectedMemberNotFound:

    def test_coverage_is_inactive(self, parser):
        result = parser.parse(REJECTED_MEMBER_NOT_FOUND)
        assert result.coverage_active is False

    def test_rejection_reason_populated(self, parser):
        result = parser.parse(REJECTED_MEMBER_NOT_FOUND)
        assert len(result.rejection_reasons) == 1

    def test_rejection_reason_contains_member_not_found(self, parser):
        result = parser.parse(REJECTED_MEMBER_NOT_FOUND)
        assert "not found" in result.rejection_reasons[0].lower()

    def test_no_financial_data_on_rejection(self, parser):
        result = parser.parse(REJECTED_MEMBER_NOT_FOUND)
        assert result.deductible_individual is None
        assert result.copay_amount is None


class TestDependentCoverage:

    def test_coverage_active_from_dependent_loop(self, parser):
        """Parser must find EB segments in the dependent (23) loop, not subscriber (22)."""
        result = parser.parse(ACTIVE_WITH_DEPENDENT)
        assert result.coverage_active is True

    def test_in_network_from_dependent_loop(self, parser):
        result = parser.parse(ACTIVE_WITH_DEPENDENT)
        assert result.in_network is True

    def test_deductible_from_dependent_loop(self, parser):
        result = parser.parse(ACTIVE_WITH_DEPENDENT)
        assert result.deductible_individual == Decimal("1500.00")

    def test_deductible_remaining_zero_met(self, parser):
        # Remaining == total means nothing has been met yet
        result = parser.parse(ACTIVE_WITH_DEPENDENT)
        assert result.deductible_individual_remaining == Decimal("1500.00")

    def test_copay_from_dependent_loop(self, parser):
        result = parser.parse(ACTIVE_WITH_DEPENDENT)
        assert result.copay_amount == Decimal("25.00")

    def test_plan_type_hmo(self, parser):
        result = parser.parse(ACTIVE_WITH_DEPENDENT)
        assert result.plan_type == "HMO"


class TestFamilyAndIndividualDeductible:

    def test_individual_deductible(self, parser):
        result = parser.parse(FAMILY_AND_INDIVIDUAL_DEDUCTIBLE)
        assert result.deductible_individual == Decimal("800.00")

    def test_individual_deductible_remaining(self, parser):
        result = parser.parse(FAMILY_AND_INDIVIDUAL_DEDUCTIBLE)
        assert result.deductible_individual_remaining == Decimal("600.00")

    def test_family_deductible(self, parser):
        result = parser.parse(FAMILY_AND_INDIVIDUAL_DEDUCTIBLE)
        assert result.deductible_family == Decimal("2400.00")

    def test_family_deductible_remaining(self, parser):
        result = parser.parse(FAMILY_AND_INDIVIDUAL_DEDUCTIBLE)
        assert result.deductible_family_remaining == Decimal("1800.00")

    def test_family_oop_max(self, parser):
        result = parser.parse(FAMILY_AND_INDIVIDUAL_DEDUCTIBLE)
        assert result.oop_max_family == Decimal("12000.00")


class TestCoinsuranceNormalization:

    def test_decimal_coinsurance_normalized_to_percent(self, parser):
        """
        Payer sent 0.20 — parser must normalize to 20 (percent).
        Both representations mean "patient pays 20%".
        """
        result = parser.parse(COINSURANCE_AS_DECIMAL)
        assert result.coinsurance_percent == Decimal("20")


class TestMinimalCoverage:

    def test_active_with_no_financial_fields(self, parser):
        result = parser.parse(MINIMAL_ACTIVE)
        assert result.coverage_active is True

    def test_no_plan_name_when_not_sent(self, parser):
        result = parser.parse(MINIMAL_ACTIVE)
        assert result.plan_name is None

    def test_no_deductible_when_not_sent(self, parser):
        result = parser.parse(MINIMAL_ACTIVE)
        assert result.deductible_individual is None

    def test_no_rejection_on_minimal(self, parser):
        result = parser.parse(MINIMAL_ACTIVE)
        assert result.rejection_reasons == []
