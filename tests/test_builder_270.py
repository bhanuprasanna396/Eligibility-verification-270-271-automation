"""
Tests for the 270 builder.

These tests require NO database and NO network.
They verify that the EDI string produced is structurally correct
and contains all expected values in the right positions.

How to run:
    cd eligibility-verification
    pytest tests/test_builder_270.py -v
"""
from datetime import date

import pytest

from app.edi.builder_270 import Builder270
from app.edi.control_numbers import InMemoryControlNumberGenerator
from app.edi.models import (
    DependentInfo,
    EligibilityInquiry,
    PayerInfo,
    ProviderInfo,
    SubscriberInfo,
)
from app.edi import segments as SEG


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def provider():
    return ProviderInfo(
        npi="1234567890",
        edi_id="TESTCLINIC",
        org_name="Test Medical Clinic",
    )


@pytest.fixture
def payer():
    return PayerInfo(
        edi_payer_id="00050",
        payer_name="Blue Cross Blue Shield",
    )


@pytest.fixture
def subscriber():
    return SubscriberInfo(
        last_name="DOE",
        first_name="JOHN",
        date_of_birth=date(1985, 1, 15),
        gender="M",
        member_id="ABC123456789",
    )


@pytest.fixture
def dependent():
    return DependentInfo(
        last_name="DOE",
        first_name="JANE",
        date_of_birth=date(2010, 6, 20),
        gender="F",
    )


@pytest.fixture
def gen():
    return InMemoryControlNumberGenerator(start=1)


def make_builder(gen: InMemoryControlNumberGenerator) -> Builder270:
    return Builder270(
        sender_id="TESTCLINIC",
        receiver_id="CLEARINGHS",
        isa_control_number=gen.next(),
        usage="T",
    )


def parse_segments(edi: str) -> list[list[str]]:
    """Split EDI string into a list of segments, each split into elements."""
    raw = edi.rstrip(SEG.SEGMENT_TERM)
    return [seg.split(SEG.ELEMENT_SEP) for seg in raw.split(SEG.SEGMENT_TERM)]


# ---------------------------------------------------------------------------
# Test: subscriber is the patient (most common case)
# ---------------------------------------------------------------------------

class TestSubscriberIsPatient:

    def test_produces_non_empty_string(self, provider, payer, subscriber, gen):
        inquiry = EligibilityInquiry(
            provider=provider,
            payer=payer,
            subscriber=subscriber,
            reference_id="appt-001",
        )
        result = make_builder(gen).build(inquiry)
        assert len(result) > 0

    def test_starts_with_isa_ends_with_iea(self, provider, payer, subscriber, gen):
        inquiry = EligibilityInquiry(provider=provider, payer=payer,
                                     subscriber=subscriber, reference_id="appt-001")
        segs = parse_segments(make_builder(gen).build(inquiry))
        assert segs[0][0] == "ISA"
        assert segs[-1][0] == "IEA"

    def test_isa_control_number_is_nine_digits(self, provider, payer, subscriber, gen):
        inquiry = EligibilityInquiry(provider=provider, payer=payer,
                                     subscriber=subscriber, reference_id="appt-001")
        segs = parse_segments(make_builder(gen).build(inquiry))
        isa = segs[0]
        assert len(isa[13]) == 9           # ISA13 is the control number
        assert isa[13].isdigit()

    def test_isa13_matches_iea02(self, provider, payer, subscriber, gen):
        """IEA02 must be identical to ISA13 — clearinghouse validates this."""
        inquiry = EligibilityInquiry(provider=provider, payer=payer,
                                     subscriber=subscriber, reference_id="appt-001")
        segs = parse_segments(make_builder(gen).build(inquiry))
        isa_control = segs[0][13]
        iea_control = segs[-1][2]
        assert isa_control == iea_control

    def test_gs06_matches_ge02(self, provider, payer, subscriber, gen):
        """GE02 must match GS06."""
        inquiry = EligibilityInquiry(provider=provider, payer=payer,
                                     subscriber=subscriber, reference_id="appt-001")
        segs = parse_segments(make_builder(gen).build(inquiry))
        gs = next(s for s in segs if s[0] == "GS")
        ge = next(s for s in segs if s[0] == "GE")
        assert gs[6] == ge[2]

    def test_st02_matches_se02(self, provider, payer, subscriber, gen):
        """SE02 must match ST02."""
        inquiry = EligibilityInquiry(provider=provider, payer=payer,
                                     subscriber=subscriber, reference_id="appt-001")
        segs = parse_segments(make_builder(gen).build(inquiry))
        st = next(s for s in segs if s[0] == "ST")
        se = next(s for s in segs if s[0] == "SE")
        assert st[2] == se[2]

    def test_se_segment_count_is_correct(self, provider, payer, subscriber, gen):
        """
        SE01 = number of segments from ST to SE inclusive.
        For a subscriber-only 270: ST BHT HL NM1 HL NM1 HL NM1 DMG EQ SE = 11
        """
        inquiry = EligibilityInquiry(provider=provider, payer=payer,
                                     subscriber=subscriber, reference_id="appt-001")
        segs = parse_segments(make_builder(gen).build(inquiry))
        st_idx = next(i for i, s in enumerate(segs) if s[0] == "ST")
        se_idx = next(i for i, s in enumerate(segs) if s[0] == "SE")
        expected_count = se_idx - st_idx + 1
        se = segs[se_idx]
        assert int(se[1]) == expected_count

    def test_transaction_type_is_270(self, provider, payer, subscriber, gen):
        inquiry = EligibilityInquiry(provider=provider, payer=payer,
                                     subscriber=subscriber, reference_id="appt-001")
        segs = parse_segments(make_builder(gen).build(inquiry))
        st = next(s for s in segs if s[0] == "ST")
        assert st[1] == "270"

    def test_payer_edi_id_in_nm1(self, provider, payer, subscriber, gen):
        inquiry = EligibilityInquiry(provider=provider, payer=payer,
                                     subscriber=subscriber, reference_id="appt-001")
        segs = parse_segments(make_builder(gen).build(inquiry))
        payer_nm1 = next(s for s in segs if s[0] == "NM1" and s[1] == "PR")
        assert payer_nm1[9] == payer.edi_payer_id

    def test_provider_npi_in_nm1(self, provider, payer, subscriber, gen):
        inquiry = EligibilityInquiry(provider=provider, payer=payer,
                                     subscriber=subscriber, reference_id="appt-001")
        segs = parse_segments(make_builder(gen).build(inquiry))
        provider_nm1 = next(s for s in segs if s[0] == "NM1" and s[1] == "1P")
        assert provider_nm1[9] == provider.npi

    def test_subscriber_member_id_in_nm1(self, provider, payer, subscriber, gen):
        inquiry = EligibilityInquiry(provider=provider, payer=payer,
                                     subscriber=subscriber, reference_id="appt-001")
        segs = parse_segments(make_builder(gen).build(inquiry))
        sub_nm1 = next(s for s in segs if s[0] == "NM1" and s[1] == "IL")
        assert sub_nm1[9] == subscriber.member_id

    def test_subscriber_dob_in_dmg(self, provider, payer, subscriber, gen):
        inquiry = EligibilityInquiry(provider=provider, payer=payer,
                                     subscriber=subscriber, reference_id="appt-001")
        segs = parse_segments(make_builder(gen).build(inquiry))
        dmg = next(s for s in segs if s[0] == "DMG")
        assert dmg[2] == "19850115"

    def test_default_service_type_code_is_30(self, provider, payer, subscriber, gen):
        inquiry = EligibilityInquiry(provider=provider, payer=payer,
                                     subscriber=subscriber, reference_id="appt-001")
        segs = parse_segments(make_builder(gen).build(inquiry))
        eq = next(s for s in segs if s[0] == "EQ")
        assert eq[1] == "30"

    def test_multiple_service_type_codes_produce_multiple_eq(self, provider, payer, subscriber, gen):
        inquiry = EligibilityInquiry(
            provider=provider, payer=payer, subscriber=subscriber,
            reference_id="appt-001", service_type_codes=["30", "98"],
        )
        segs = parse_segments(make_builder(gen).build(inquiry))
        eq_segs = [s for s in segs if s[0] == "EQ"]
        assert len(eq_segs) == 2
        assert eq_segs[0][1] == "30"
        assert eq_segs[1][1] == "98"

    def test_hl_hierarchy_no_dependent(self, provider, payer, subscriber, gen):
        """Subscriber HL04 must be 0 when there is no dependent."""
        inquiry = EligibilityInquiry(provider=provider, payer=payer,
                                     subscriber=subscriber, reference_id="appt-001")
        segs = parse_segments(make_builder(gen).build(inquiry))
        hl_segs = [s for s in segs if s[0] == "HL"]
        # HL 1 = payer (has child), HL 2 = provider (has child), HL 3 = subscriber (no child)
        assert hl_segs[0][4] == "1"   # payer has child
        assert hl_segs[1][4] == "1"   # provider has child
        assert hl_segs[2][4] == "0"   # subscriber is leaf


# ---------------------------------------------------------------------------
# Test: patient is a dependent (child on parent's plan)
# ---------------------------------------------------------------------------

class TestPatientIsDependent:

    def test_dependent_nm1_qc_present(self, provider, payer, subscriber, dependent, gen):
        inquiry = EligibilityInquiry(
            provider=provider, payer=payer, subscriber=subscriber,
            reference_id="appt-002", dependent=dependent,
        )
        segs = parse_segments(make_builder(gen).build(inquiry))
        dep_nm1 = next((s for s in segs if s[0] == "NM1" and s[1] == "QC"), None)
        assert dep_nm1 is not None

    def test_dependent_name_in_nm1(self, provider, payer, subscriber, dependent, gen):
        inquiry = EligibilityInquiry(
            provider=provider, payer=payer, subscriber=subscriber,
            reference_id="appt-002", dependent=dependent,
        )
        segs = parse_segments(make_builder(gen).build(inquiry))
        dep_nm1 = next(s for s in segs if s[0] == "NM1" and s[1] == "QC")
        assert dep_nm1[3] == "DOE"
        assert dep_nm1[4] == "JANE"

    def test_subscriber_hl04_is_1_when_dependent_present(self, provider, payer, subscriber, dependent, gen):
        """Subscriber HL04 must be 1 when a dependent follows."""
        inquiry = EligibilityInquiry(
            provider=provider, payer=payer, subscriber=subscriber,
            reference_id="appt-002", dependent=dependent,
        )
        segs = parse_segments(make_builder(gen).build(inquiry))
        hl_segs = [s for s in segs if s[0] == "HL"]
        subscriber_hl = hl_segs[2]  # 3rd HL = subscriber
        assert subscriber_hl[4] == "1"

    def test_dependent_hl04_is_0(self, provider, payer, subscriber, dependent, gen):
        """Dependent is always a leaf — HL04 must be 0."""
        inquiry = EligibilityInquiry(
            provider=provider, payer=payer, subscriber=subscriber,
            reference_id="appt-002", dependent=dependent,
        )
        segs = parse_segments(make_builder(gen).build(inquiry))
        hl_segs = [s for s in segs if s[0] == "HL"]
        dependent_hl = hl_segs[3]  # 4th HL = dependent
        assert dependent_hl[4] == "0"

    def test_two_dmg_segments_when_dependent(self, provider, payer, subscriber, dependent, gen):
        """One DMG for subscriber, one for dependent."""
        inquiry = EligibilityInquiry(
            provider=provider, payer=payer, subscriber=subscriber,
            reference_id="appt-002", dependent=dependent,
        )
        segs = parse_segments(make_builder(gen).build(inquiry))
        dmg_segs = [s for s in segs if s[0] == "DMG"]
        assert len(dmg_segs) == 2
        assert dmg_segs[0][2] == "19850115"  # subscriber DOB
        assert dmg_segs[1][2] == "20100620"  # dependent DOB

    def test_se_count_correct_with_dependent(self, provider, payer, subscriber, dependent, gen):
        """
        SE01 count with dependent:
        ST BHT HL NM1 HL NM1 HL NM1 DMG HL NM1 DMG EQ SE = 14
        """
        inquiry = EligibilityInquiry(
            provider=provider, payer=payer, subscriber=subscriber,
            reference_id="appt-002", dependent=dependent,
        )
        segs = parse_segments(make_builder(gen).build(inquiry))
        st_idx = next(i for i, s in enumerate(segs) if s[0] == "ST")
        se_idx = next(i for i, s in enumerate(segs) if s[0] == "SE")
        expected_count = se_idx - st_idx + 1
        se = segs[se_idx]
        assert int(se[1]) == expected_count


# ---------------------------------------------------------------------------
# Test: control numbers
# ---------------------------------------------------------------------------

class TestControlNumbers:

    def test_sequential_control_numbers(self, provider, payer, subscriber):
        gen = InMemoryControlNumberGenerator(start=1)
        assert gen.next() == "000000001"
        assert gen.next() == "000000002"
        assert gen.next() == "000000003"

    def test_different_builds_have_different_control_numbers(self, provider, payer, subscriber):
        gen = InMemoryControlNumberGenerator(start=5)
        b1 = Builder270("CLINIC", "CH", gen.next(), "T")
        b2 = Builder270("CLINIC", "CH", gen.next(), "T")

        inquiry = EligibilityInquiry(provider=provider, payer=payer,
                                     subscriber=subscriber, reference_id="appt-001")
        s1 = parse_segments(b1.build(inquiry))
        s2 = parse_segments(b2.build(inquiry))

        isa1_control = s1[0][13]
        isa2_control = s2[0][13]
        assert isa1_control != isa2_control
