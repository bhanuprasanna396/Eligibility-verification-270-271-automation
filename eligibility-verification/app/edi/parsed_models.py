"""
Output data structures produced by Parser271.

ParsedEligibility is what every downstream component works with:
  - Gap analyzer reads it to find problems
  - EligibilityResult model maps directly from it to the database
  - API serializes it to JSON for the dashboard

RawEBSegment is an internal structure used only during parsing.
It holds one EB segment plus any DTP segments that follow it,
before interpretation happens.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class RawEBSegment:
    """
    One EB segment and the DTP segments that immediately follow it.
    DTP segments in a 271 always belong to the EB segment above them.
    """
    elements: list[str]
    associated_dtps: list[list[str]] = field(default_factory=list)

    def get(self, position: int) -> str:
        """
        Safely get an element by 1-based position (EB01, EB02, etc.)
        Returns empty string if the segment is shorter than expected.
        Payers frequently omit trailing empty elements, so this never raises.
        """
        try:
            return self.elements[position].strip()
        except IndexError:
            return ""


@dataclass
class ParsedEligibility:
    """
    All coverage information extracted from one 271 response.

    Fields map directly to EligibilityResult columns in the database.
    When a field is None it means the payer did not send that information
    (different from False or 0 — those are explicit values).

    rejection_reasons is populated when the 271 contains AAA segments
    (the payer is saying "I cannot answer this inquiry, here is why").
    A non-empty rejection_reasons list always means coverage_active=False
    and all financial fields will be None.
    """
    # Core coverage status
    coverage_active: bool = False
    rejection_reasons: list[str] = field(default_factory=list)

    # Plan identification
    plan_name: str | None = None
    plan_type: str | None = None      # HMO, PPO, EPO, etc.
    in_network: bool | None = None

    # Coverage dates (from DTP*346 and DTP*347 after EB*1)
    coverage_effective_date: date | None = None
    coverage_termination_date: date | None = None

    # Deductible — individual
    deductible_individual: Decimal | None = None            # annual total
    deductible_individual_remaining: Decimal | None = None  # what is left this year

    # Deductible — family
    deductible_family: Decimal | None = None
    deductible_family_remaining: Decimal | None = None

    # Out-of-pocket maximum — individual
    oop_max_individual: Decimal | None = None
    oop_max_individual_remaining: Decimal | None = None

    # Out-of-pocket maximum — family
    oop_max_family: Decimal | None = None
    oop_max_family_remaining: Decimal | None = None

    # Cost sharing
    copay_amount: Decimal | None = None       # flat dollar copay per visit
    coinsurance_percent: Decimal | None = None  # percentage patient owes after deductible

    # Service requirements
    referral_required: bool = False
    prior_auth_required: bool = False

    # All EB segments in raw form — anything the explicit fields above missed
    # is still queryable here (e.g. dental coverage limits, vision coverage)
    raw_eb_segments: list[dict] = field(default_factory=list)
