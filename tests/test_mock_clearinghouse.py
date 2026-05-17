"""
Tests for the mock clearinghouse — and the first true end-to-end tests.

Each test runs the complete pipeline:
    Builder270 → MockClearinghouseClient → Parser271 → ParsedEligibility

This is the most important test file so far because it proves the three
components work together as a system, not just individually.

How to run:
    pytest tests/test_mock_clearinghouse.py -v
"""
from datetime import date
from decimal import Decimal

import pytest

from app.clearinghouse.mock_client import (
    MockClearinghouseClient,
    SCENARIO_ACTIVE_PPO,
    SCENARIO_ACTIVE_HMO,
    SCENARIO_ACTIVE_HIGH_DEDUCTIBLE,
    SCENARIO_ACTIVE_OUT_OF_NETWORK,
    SCENARIO_INACTIVE,
    SCENARIO_REJECTED_MEMBER_NOT_FOUND,
    SCENARIO_SERVICE_UNAVAILABLE,
)
from app.edi.builder_270 import Builder270
from app.edi.control_numbers import InMemoryControlNumberGenerator
from app.edi.models import EligibilityInquiry, PayerInfo, ProviderInfo, SubscriberInfo
from app.edi.parser_271 import Parser271


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def make_270(member_id: str = "TEST001", service_type: list[str] | None = None) -> str:
    """Builds a valid 270 for a test patient. One line, reused everywhere."""
    gen = InMemoryControlNumberGenerator()
    builder = Builder270(
        sender_id="TESTCLINIC",
        receiver_id="CLEARINGHS",
        isa_control_number=gen.next(),
        usage="T",
    )
    inquiry = EligibilityInquiry(
        provider=ProviderInfo(
            npi="1234567890",
            edi_id="TESTCLINIC",
            org_name="Test Medical Clinic",
        ),
        payer=PayerInfo(
            edi_payer_id="00050",
            payer_name="Blue Cross Blue Shield",
        ),
        subscriber=SubscriberInfo(
            last_name="DOE",
            first_name="JOHN",
            date_of_birth=date(1985, 1, 15),
            gender="M",
            member_id=member_id,
        ),
        reference_id="appt-test-001",
        service_type_codes=service_type or ["30"],
    )
    return builder.build(inquiry)


@pytest.fixture
def client():
    return MockClearinghouseClient()


@pytest.fixture
def parser():
    return Parser271()


# ---------------------------------------------------------------------------
# ClearinghouseResponse tests
# ---------------------------------------------------------------------------

class TestResponseStructure:

    def test_success_is_true_for_active_scenario(self, client):
        response = client.submit_270(make_270())
        assert response.success is True

    def test_edi_271_is_not_none_on_success(self, client):
        response = client.submit_270(make_270())
        assert response.edi_271 is not None

    def test_transaction_id_is_populated(self, client):
        response = client.submit_270(make_270())
        assert response.transaction_id.startswith("MOCK-")

    def test_transaction_ids_are_unique(self, client):
        r1 = client.submit_270(make_270("MEM001"))
        r2 = client.submit_270(make_270("MEM002"))
        assert r1.transaction_id != r2.transaction_id

    def test_service_unavailable_returns_failure(self, client):
        client.register_member("DOWN999", SCENARIO_SERVICE_UNAVAILABLE)
        response = client.submit_270(make_270("DOWN999"))
        assert response.success is False
        assert response.edi_271 is None
        assert response.error_message is not None

    def test_271_starts_with_isa(self, client):
        response = client.submit_270(make_270())
        assert response.edi_271.startswith("ISA")

    def test_271_contains_st_271(self, client):
        response = client.submit_270(make_270())
        assert "ST*271" in response.edi_271

    def test_reference_id_echoed_in_271(self, client):
        """
        BHT03 in the 271 must match BHT03 in the 270.
        This is how the clinic correlates the response to the request.
        """
        response = client.submit_270(make_270())
        assert "appt-test-001" in response.edi_271


# ---------------------------------------------------------------------------
# End-to-end: Builder270 → Mock → Parser271
# ---------------------------------------------------------------------------

class TestEndToEndActivePPO:
    """The complete pipeline for the most common scenario."""

    def setup_method(self):
        self.client = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_PPO)
        self.parser = Parser271()

    def _get_result(self, member_id="TEST001"):
        edi_270 = make_270(member_id)
        response = self.client.submit_270(edi_270)
        assert response.success, f"Mock failed: {response.error_message}"
        return self.parser.parse(response.edi_271)

    def test_coverage_active(self):
        result = self._get_result()
        assert result.coverage_active is True

    def test_no_rejections(self):
        result = self._get_result()
        assert result.rejection_reasons == []

    def test_plan_type_ppo(self):
        result = self._get_result()
        assert result.plan_type == "PPO"

    def test_in_network(self):
        result = self._get_result()
        assert result.in_network is True

    def test_deductible_individual(self):
        result = self._get_result()
        assert result.deductible_individual == Decimal("500.00")

    def test_deductible_individual_remaining(self):
        result = self._get_result()
        assert result.deductible_individual_remaining == Decimal("347.50")

    def test_oop_max_individual(self):
        result = self._get_result()
        assert result.oop_max_individual == Decimal("3000.00")

    def test_oop_max_remaining(self):
        result = self._get_result()
        assert result.oop_max_individual_remaining == Decimal("2500.00")

    def test_copay_amount(self):
        result = self._get_result()
        assert result.copay_amount == Decimal("30.00")

    def test_coinsurance_percent(self):
        result = self._get_result()
        assert result.coinsurance_percent == Decimal("20")

    def test_coverage_dates_present(self):
        result = self._get_result()
        assert result.coverage_effective_date is not None
        assert result.coverage_termination_date is not None


class TestEndToEndInactive:

    def test_coverage_inactive(self):
        client = MockClearinghouseClient(default_scenario=SCENARIO_INACTIVE)
        response = client.submit_270(make_270())
        result = Parser271().parse(response.edi_271)
        assert result.coverage_active is False
        assert result.rejection_reasons == []

    def test_no_financial_data_when_inactive(self):
        client = MockClearinghouseClient(default_scenario=SCENARIO_INACTIVE)
        response = client.submit_270(make_270())
        result = Parser271().parse(response.edi_271)
        assert result.deductible_individual is None
        assert result.copay_amount is None


class TestEndToEndRejected:

    def test_rejection_returns_success_response(self):
        """
        A rejected 271 (AAA segment) is still a successful network call.
        The rejection is in the 271 content, not the HTTP layer.
        """
        client = MockClearinghouseClient(default_scenario=SCENARIO_REJECTED_MEMBER_NOT_FOUND)
        response = client.submit_270(make_270())
        assert response.success is True

    def test_rejection_reason_populated(self):
        client = MockClearinghouseClient(default_scenario=SCENARIO_REJECTED_MEMBER_NOT_FOUND)
        response = client.submit_270(make_270())
        result = Parser271().parse(response.edi_271)
        assert len(result.rejection_reasons) == 1

    def test_coverage_inactive_on_rejection(self):
        client = MockClearinghouseClient(default_scenario=SCENARIO_REJECTED_MEMBER_NOT_FOUND)
        response = client.submit_270(make_270())
        result = Parser271().parse(response.edi_271)
        assert result.coverage_active is False


class TestEndToEndHighDeductible:

    def test_high_deductible_amount(self):
        client = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_HIGH_DEDUCTIBLE)
        response = client.submit_270(make_270())
        result = Parser271().parse(response.edi_271)
        assert result.deductible_individual == Decimal("5000.00")

    def test_nothing_met_yet(self):
        client = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_HIGH_DEDUCTIBLE)
        response = client.submit_270(make_270())
        result = Parser271().parse(response.edi_271)
        # remaining == total means nothing has been met
        assert result.deductible_individual_remaining == result.deductible_individual

    def test_no_copay_for_hdhp(self):
        """HDHP plans typically have no copay — must meet deductible first."""
        client = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_HIGH_DEDUCTIBLE)
        response = client.submit_270(make_270())
        result = Parser271().parse(response.edi_271)
        assert result.copay_amount is None


class TestEndToEndOutOfNetwork:

    def test_coverage_active_but_out_of_network(self):
        client = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_OUT_OF_NETWORK)
        response = client.submit_270(make_270())
        result = Parser271().parse(response.edi_271)
        assert result.coverage_active is True
        assert result.in_network is False

    def test_higher_coinsurance_out_of_network(self):
        client = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_OUT_OF_NETWORK)
        response = client.submit_270(make_270())
        result = Parser271().parse(response.edi_271)
        assert result.coinsurance_percent == Decimal("40")


# ---------------------------------------------------------------------------
# Member override tests
# ---------------------------------------------------------------------------

class TestMemberOverrides:

    def test_default_scenario_used_for_unknown_member(self):
        client = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_PPO)
        response = client.submit_270(make_270("UNKNOWN"))
        result = Parser271().parse(response.edi_271)
        assert result.coverage_active is True
        assert result.plan_type == "PPO"

    def test_override_specific_member_to_inactive(self):
        client = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_PPO)
        client.register_member("TERM999", SCENARIO_INACTIVE)

        # Other members still get active PPO
        r_active = Parser271().parse(
            client.submit_270(make_270("ACTIVE001")).edi_271
        )
        assert r_active.coverage_active is True

        # Overridden member gets inactive
        r_inactive = Parser271().parse(
            client.submit_270(make_270("TERM999")).edi_271
        )
        assert r_inactive.coverage_active is False

    def test_multiple_overrides_independently(self):
        client = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_PPO)
        client.register_member("HMO001", SCENARIO_ACTIVE_HMO)
        client.register_member("DOWN001", SCENARIO_SERVICE_UNAVAILABLE)
        client.register_member("TERM001", SCENARIO_INACTIVE)

        hmo = Parser271().parse(client.submit_270(make_270("HMO001")).edi_271)
        assert hmo.plan_type == "HMO"

        down = client.submit_270(make_270("DOWN001"))
        assert down.success is False

        term = Parser271().parse(client.submit_270(make_270("TERM001")).edi_271)
        assert term.coverage_active is False

    def test_change_default_scenario(self):
        client = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_PPO)
        client.set_default_scenario(SCENARIO_INACTIVE)
        response = client.submit_270(make_270("ANY"))
        result = Parser271().parse(response.edi_271)
        assert result.coverage_active is False

    def test_hmo_scenario(self):
        client = MockClearinghouseClient(default_scenario=SCENARIO_ACTIVE_HMO)
        response = client.submit_270(make_270())
        result = Parser271().parse(response.edi_271)
        assert result.plan_type == "HMO"
        assert result.copay_amount == Decimal("25.00")
        assert result.deductible_individual == Decimal("1500.00")
