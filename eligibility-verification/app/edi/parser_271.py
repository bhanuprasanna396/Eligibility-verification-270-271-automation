"""
Parser271 — reads a raw X12 005010X279A1 271 EDI string and extracts
structured coverage data from it.

The 271 is the insurance company's answer to our 270 inquiry.
It contains EB (Eligibility/Benefit) segments that carry every piece
of coverage information: active status, deductible, copay, OOP max, etc.

Parsing strategy:
  1. Tokenize — split by segment terminator (~) then by element separator (*)
  2. Detect rejections — AAA segments mean the payer could not answer
  3. Find the benefit loop — EB segments live inside the subscriber (HL*22)
     or dependent (HL*23) loop, depending on who the patient is
  4. Group EB+DTP — each EB segment owns the DTP segments that follow it
  5. Interpret — map EB01 codes to structured fields

Why this is non-trivial:
  Payers send the same data in wildly different formats.
  BCBS may send one EB*C with EB06=23 for the annual deductible total,
  while Aetna sends TWO EB*C segments — one with 23 (total) and one with
  29 (remaining). This parser handles both patterns.
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from app.edi.parsed_models import ParsedEligibility, RawEBSegment
from app.edi import segments as SEG


# ---------------------------------------------------------------------------
# EB01 — Eligibility or Benefit Information Codes
# ---------------------------------------------------------------------------
EB01_ACTIVE_COVERAGE = "1"
EB01_ACTIVE_FULL_RISK_CAPITATION = "2"
EB01_ACTIVE_SERVICES_CAPITATED = "3"
EB01_ACTIVE_SERVICES_CAPITATED_TO_PCP = "4"
EB01_INACTIVE = "6"
EB01_INACTIVE_PENDING_INVESTIGATION = "7"
EB01_CO_INSURANCE = "A"
EB01_CO_PAYMENT = "B"
EB01_DEDUCTIBLE = "C"
EB01_BENEFIT_DESCRIPTION = "D"
EB01_EXCLUSIONS = "E"
EB01_LIMITATIONS = "F"
EB01_OUT_OF_POCKET_STOP_LOSS = "G"
EB01_NOT_COVERED = "I"
EB01_OTHER = "W"

# EB06 — Time Period Qualifier
EB06_CALENDAR_YEAR = "23"
EB06_YEAR_TO_DATE = "24"
EB06_PER_VISIT = "26"
EB06_REMAINING = "29"
EB06_EXCEEDED = "30"

# EB02 — Coverage Level Codes
EB02_INDIVIDUAL = "IND"
EB02_FAMILY = "FAM"
EB02_EMPLOYEE_ONLY = "EMP"

# EB04 — Insurance Type Codes → human-readable plan type
INSURANCE_TYPE_MAP = {
    "AP": "Auto Insurance",
    "C1": "Commercial",
    "CO": "COBRA",
    "HM": "HMO",
    "MA": "Medicare Part A",
    "MB": "Medicare Part B",
    "MC": "Medicaid",
    "MI": "Medigap Part A",
    "MJ": "Medigap Part B",
    "PP": "PPO",
    "TV": "Title V",
}

# DTP01 qualifiers for benefit date interpretation
DTP_BENEFIT_START = "346"
DTP_BENEFIT_END = "347"
DTP_PLAN_DATE = "291"
DTP_ELIGIBILITY_DATE = "307"


class Parser271:
    """
    Parses one 271 EDI string into a ParsedEligibility object.

    Usage:
        parser = Parser271()
        result = parser.parse(raw_271_edi_string)
        if result.coverage_active:
            print(result.deductible_individual)
    """

    def parse(self, raw_edi: str) -> ParsedEligibility:
        """
        Entry point. Takes the raw 271 string, returns structured coverage data.
        """
        segs = self._tokenize(raw_edi)
        result = ParsedEligibility()

        # TA1 = interchange-level rejection (rare, means the clearinghouse
        # could not even route the file)
        ta1 = next((s for s in segs if s[0] == "TA1"), None)
        if ta1:
            result.rejection_reasons.append(
                f"Interchange rejection (TA1): {self._val(ta1, 2)}"
            )
            return result

        # Benefit segments live in the dependent loop (23) when patient is a
        # dependent, otherwise in the subscriber loop (22).
        # Try dependent first — if it exists, that is the patient.
        benefit_segs = self._extract_benefit_loop(segs, hl_type="23")
        if not benefit_segs:
            benefit_segs = self._extract_benefit_loop(segs, hl_type="22")

        if not benefit_segs:
            result.rejection_reasons.append("No subscriber or dependent loop found in 271")
            return result

        # Check for AAA rejection segments within the benefit loop
        for seg in benefit_segs:
            if seg[0] == "AAA":
                code = self._val(seg, 3)
                reason = SEG.REJECTION_REASONS.get(code, f"Rejection code {code}")
                result.rejection_reasons.append(reason)

        if result.rejection_reasons:
            return result

        # Group each EB segment with the DTP segments that follow it
        eb_blocks = self._group_eb_with_dtps(benefit_segs)

        for block in eb_blocks:
            self._interpret_eb(block, result)

        # Store raw EB data for the database JSONB column
        result.raw_eb_segments = [
            {
                "eb01": b.get(1), "eb02": b.get(2), "eb03": b.get(3),
                "eb04": b.get(4), "eb05": b.get(5), "eb06": b.get(6),
                "eb07": b.get(7), "eb08": b.get(8), "eb09": b.get(9),
                "eb10": b.get(10), "eb11": b.get(11), "eb12": b.get(12),
                "dtps": b.associated_dtps,
            }
            for b in eb_blocks
        ]

        return result

    # ------------------------------------------------------------------
    # Tokenizer
    # ------------------------------------------------------------------

    def _tokenize(self, raw_edi: str) -> list[list[str]]:
        """
        Splits raw EDI into a list of segments.
        Each segment is a list of element values (strings).
        Empty trailing elements are preserved so position-based access works.
        """
        # Detect actual delimiters from ISA segment
        # ISA is always 106 characters with fixed-position delimiters
        raw = raw_edi.strip()
        if not raw.startswith("ISA"):
            raise ValueError("EDI string does not start with ISA segment")

        element_sep = raw[3]      # character at position 3 is the element separator
        segment_term = raw[105]   # character at position 105 is the segment terminator

        result = []
        for raw_seg in raw.split(segment_term):
            seg = raw_seg.strip()
            if seg:
                result.append(seg.split(element_sep))
        return result

    # ------------------------------------------------------------------
    # Loop extraction
    # ------------------------------------------------------------------

    def _extract_benefit_loop(
        self, segs: list[list[str]], hl_type: str
    ) -> list[list[str]]:
        """
        Finds the HL loop for the given type (22=subscriber, 23=dependent)
        and returns all segments in that loop up to the next HL or SE.
        The HL segment itself is excluded — we only want the benefit segments.
        """
        in_loop = False
        loop_segs = []

        for seg in segs:
            if seg[0] == "HL":
                hl03 = self._val(seg, 3)
                if hl03 == hl_type:
                    in_loop = True
                    continue
                elif in_loop:
                    # A new HL started — this loop is over
                    break

            if in_loop:
                if seg[0] == "SE":
                    break
                loop_segs.append(seg)

        return loop_segs

    # ------------------------------------------------------------------
    # EB grouping
    # ------------------------------------------------------------------

    def _group_eb_with_dtps(
        self, segs: list[list[str]]
    ) -> list[RawEBSegment]:
        """
        Groups each EB segment with the DTP segments that immediately follow it.
        This is how the spec works: DTP segments always belong to the EB above them.
        """
        blocks: list[RawEBSegment] = []
        current_block: RawEBSegment | None = None

        for seg in segs:
            if seg[0] == "EB":
                if current_block is not None:
                    blocks.append(current_block)
                current_block = RawEBSegment(elements=seg)
            elif seg[0] == "DTP" and current_block is not None:
                current_block.associated_dtps.append(seg)
            # MSG, REF, and other segments between EB segments are ignored
            # They don't affect the financial data we care about

        if current_block is not None:
            blocks.append(current_block)

        return blocks

    # ------------------------------------------------------------------
    # EB interpretation
    # ------------------------------------------------------------------

    def _interpret_eb(self, block: RawEBSegment, result: ParsedEligibility) -> None:
        """
        Reads one EB segment (plus its DTPs) and sets the corresponding
        fields on the ParsedEligibility result.
        """
        eb01 = block.get(1)

        if eb01 in (EB01_ACTIVE_COVERAGE,
                    EB01_ACTIVE_FULL_RISK_CAPITATION,
                    EB01_ACTIVE_SERVICES_CAPITATED,
                    EB01_ACTIVE_SERVICES_CAPITATED_TO_PCP):
            self._handle_active_coverage(block, result)

        elif eb01 in (EB01_INACTIVE, EB01_INACTIVE_PENDING_INVESTIGATION):
            result.coverage_active = False

        elif eb01 == EB01_DEDUCTIBLE:
            self._handle_deductible(block, result)

        elif eb01 == EB01_OUT_OF_POCKET_STOP_LOSS:
            self._handle_oop(block, result)

        elif eb01 == EB01_CO_PAYMENT:
            self._handle_copay(block, result)

        elif eb01 == EB01_CO_INSURANCE:
            self._handle_coinsurance(block, result)

        elif eb01 == EB01_LIMITATIONS:
            self._handle_limitations(block, result)

    def _handle_active_coverage(
        self, block: RawEBSegment, result: ParsedEligibility
    ) -> None:
        result.coverage_active = True

        # Plan type from EB04 (insurance type code)
        eb04 = block.get(4)
        if eb04 and result.plan_type is None:
            result.plan_type = INSURANCE_TYPE_MAP.get(eb04, eb04)

        # Plan name from EB05 (plan coverage description)
        eb05 = block.get(5)
        if eb05 and result.plan_name is None:
            result.plan_name = eb05

        # In-network from EB12
        eb12 = block.get(12)
        if eb12 == "Y":
            result.in_network = True
        elif eb12 == "N" and result.in_network is None:
            result.in_network = False

        # Coverage dates from associated DTP segments
        for dtp in block.associated_dtps:
            qualifier = self._val(dtp, 1)
            if qualifier in (DTP_BENEFIT_START, DTP_PLAN_DATE, DTP_ELIGIBILITY_DATE):
                result.coverage_effective_date = self._parse_date(self._val(dtp, 3))
            elif qualifier == DTP_BENEFIT_END:
                result.coverage_termination_date = self._parse_date(self._val(dtp, 3))

    def _handle_deductible(
        self, block: RawEBSegment, result: ParsedEligibility
    ) -> None:
        amount = self._parse_amount(block.get(7))
        if amount is None:
            return

        eb02 = block.get(2)
        eb06 = block.get(6)
        is_family = eb02 == EB02_FAMILY
        is_remaining = eb06 == EB06_REMAINING

        if is_family:
            if is_remaining:
                result.deductible_family_remaining = amount
            else:
                # Calendar year (23), year-to-date (24), or blank all mean "total"
                result.deductible_family = amount
        else:
            # Individual, Employee, or blank — treat as individual
            if is_remaining:
                result.deductible_individual_remaining = amount
            else:
                result.deductible_individual = amount

    def _handle_oop(
        self, block: RawEBSegment, result: ParsedEligibility
    ) -> None:
        amount = self._parse_amount(block.get(7))
        if amount is None:
            return

        eb02 = block.get(2)
        eb06 = block.get(6)
        is_family = eb02 == EB02_FAMILY
        is_remaining = eb06 == EB06_REMAINING

        if is_family:
            if is_remaining:
                result.oop_max_family_remaining = amount
            else:
                result.oop_max_family = amount
        else:
            if is_remaining:
                result.oop_max_individual_remaining = amount
            else:
                result.oop_max_individual = amount

    def _handle_copay(
        self, block: RawEBSegment, result: ParsedEligibility
    ) -> None:
        amount = self._parse_amount(block.get(7))
        if amount is not None and result.copay_amount is None:
            result.copay_amount = amount

    def _handle_coinsurance(
        self, block: RawEBSegment, result: ParsedEligibility
    ) -> None:
        percent_raw = block.get(8)
        if not percent_raw or result.coinsurance_percent is not None:
            return

        percent = self._parse_amount(percent_raw)
        if percent is None:
            return

        # Some payers send 0.20 (decimal), others send 20 (whole number)
        # Normalize to whole percent (20 = 20%)
        if percent < 1:
            percent = percent * 100

        result.coinsurance_percent = percent

    def _handle_limitations(
        self, block: RawEBSegment, result: ParsedEligibility
    ) -> None:
        # EB*F (limitations) is how some payers signal referral required.
        # The EB11 field is "Yes/No Condition" — Y means auth/referral is needed.
        eb11 = block.get(11)
        if eb11 == "Y":
            result.referral_required = True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _val(seg: list[str], position: int) -> str:
        """Safely get a segment element by 1-based position."""
        try:
            return seg[position].strip()
        except IndexError:
            return ""

    @staticmethod
    def _parse_amount(raw: str) -> Decimal | None:
        """Converts '500.00' or '500' to Decimal. Returns None if empty or invalid."""
        if not raw:
            return None
        try:
            return Decimal(raw)
        except InvalidOperation:
            return None

    @staticmethod
    def _parse_date(raw: str) -> date | None:
        """
        Converts D8-format date string (CCYYMMDD) to date object.
        Also handles RD8 date range — takes the start date.
        """
        if not raw:
            return None
        # RD8 format: CCYYMMDD-CCYYMMDD — take the start date
        date_str = raw.split("-")[0].strip()
        if len(date_str) != 8 or not date_str.isdigit():
            return None
        try:
            return date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
        except ValueError:
            return None
