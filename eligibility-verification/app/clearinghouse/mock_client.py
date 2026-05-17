"""
MockClearinghouseClient — a fake clearinghouse for development and testing.

Behaviour:
  - Accepts a raw 270 string
  - Parses it to extract patient and payer info
  - Generates a realistic 271 response based on a configurable scenario
  - Returns ClearinghouseResponse with the 271 string

Scenarios:
  ACTIVE_PPO             — in-network PPO, $500 deductible ($347.50 remaining),
                           $3000 OOP, $30 copay, 20% coinsurance
  ACTIVE_HMO             — in-network HMO, $1500 deductible (none met), $5000 OOP, $25 copay
  ACTIVE_HIGH_DEDUCTIBLE — in-network HDHP, $5000 deductible (none met), $7000 OOP, 20% coins.
  ACTIVE_OUT_OF_NETWORK  — plan is active but provider is not in-network
  INACTIVE               — coverage terminated (EB*6)
  REJECTED_MEMBER_NOT_FOUND — AAA rejection, code 75
  SERVICE_UNAVAILABLE    — simulates clearinghouse down (network failure, no 271)

Usage:
    client = MockClearinghouseClient()                          # default: ACTIVE_PPO
    client.register_member("BAD123", client.SCENARIO_INACTIVE)  # override one member
    response = client.submit_270(edi_270_string)
    if response.success:
        result = Parser271().parse(response.edi_271)
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from app.clearinghouse.base import ClearinghouseClientBase, ClearinghouseResponse


# Scenario name constants — use these, never raw strings
SCENARIO_ACTIVE_PPO = "active_ppo"
SCENARIO_ACTIVE_HMO = "active_hmo"
SCENARIO_ACTIVE_HIGH_DEDUCTIBLE = "active_high_deductible"
SCENARIO_ACTIVE_OUT_OF_NETWORK = "active_out_of_network"
SCENARIO_INACTIVE = "inactive"
SCENARIO_REJECTED_MEMBER_NOT_FOUND = "rejected_member_not_found"
SCENARIO_SERVICE_UNAVAILABLE = "service_unavailable"


@dataclass
class _Context270:
    """
    Data extracted from a 270 that is needed to build the matching 271.
    The 271 echoes back most of this data so the clinic can match it.
    """
    clinic_edi_id: str          # ISA06 (sender of 270) → receiver in 271
    clearinghouse_edi_id: str   # ISA08 (receiver of 270) → sender in 271
    control_number: str         # ISA13 — used to correlate 270/271
    reference_id: str           # BHT03 — your appointment ID
    payer_edi_id: str           # NM1*PR NM109
    payer_name: str             # NM1*PR NM103
    provider_npi: str           # NM1*1P NM109
    provider_name: str          # NM1*1P NM103
    member_id: str              # NM1*IL NM109 — key for scenario lookup
    subscriber_last: str        # NM1*IL NM103
    subscriber_first: str       # NM1*IL NM104
    dob: str                    # DMG02 in CCYYMMDD format
    gender: str                 # DMG03


class MockClearinghouseClient(ClearinghouseClientBase):
    """
    Drop-in mock for the real clearinghouse client.
    All tests and local development use this. When you are ready for production,
    swap in the real ClearinghouseClient — no other code changes.
    """

    # Class-level demo member IDs — recognised by every worker instance so
    # seed scripts can create predictable scenarios without calling register_member().
    _DEMO_MEMBERS: dict[str, str] = {
        "MEM-HMO-001":      SCENARIO_ACTIVE_HMO,
        "MEM-HDHP-001":     SCENARIO_ACTIVE_HIGH_DEDUCTIBLE,
        "MEM-OON-001":      SCENARIO_ACTIVE_OUT_OF_NETWORK,
        "MEM-INACTIVE-001": SCENARIO_INACTIVE,
        "MEM-REJECT-001":   SCENARIO_REJECTED_MEMBER_NOT_FOUND,
        "MEM-DOWN-001":     SCENARIO_SERVICE_UNAVAILABLE,
    }

    def __init__(self, default_scenario: str = SCENARIO_ACTIVE_PPO) -> None:
        self._default_scenario = default_scenario
        # member_id → scenario name (instance-level overrides, used in tests)
        self._overrides: dict[str, str] = {}
        self._response_counter = 0

    def register_member(self, member_id: str, scenario: str) -> None:
        """
        Override the scenario for a specific member ID.
        Useful in tests: register "INACTIVE123" → SCENARIO_INACTIVE,
        then submit a 270 with that member ID and get an inactive 271 back.
        """
        self._overrides[member_id] = scenario

    def set_default_scenario(self, scenario: str) -> None:
        """Change the default scenario for all unregistered members."""
        self._default_scenario = scenario

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def submit_270(self, edi_270: str) -> ClearinghouseResponse:
        """
        Accepts a 270, returns a ClearinghouseResponse with the 271 inside.
        Mirrors what a real clearinghouse API returns.
        """
        self._response_counter += 1
        transaction_id = f"MOCK-{self._response_counter:08d}"

        try:
            ctx = self._parse_270(edi_270)
        except Exception as exc:
            return ClearinghouseResponse(
                success=False,
                transaction_id=transaction_id,
                edi_271=None,
                error_message=f"Could not parse 270: {exc}",
            )

        scenario = (
            self._overrides.get(ctx.member_id)
            or self._DEMO_MEMBERS.get(ctx.member_id)
            or self._default_scenario
        )

        if scenario == SCENARIO_SERVICE_UNAVAILABLE:
            return ClearinghouseResponse(
                success=False,
                transaction_id=transaction_id,
                edi_271=None,
                error_message="Clearinghouse service temporarily unavailable",
            )

        edi_271 = self._build_271(ctx, scenario)
        return ClearinghouseResponse(
            success=True,
            transaction_id=transaction_id,
            edi_271=edi_271,
        )

    # ------------------------------------------------------------------
    # 270 parsing
    # ------------------------------------------------------------------

    def _parse_270(self, raw_edi: str) -> _Context270:
        """
        Tokenizes the 270 and extracts all fields needed to build the 271.
        Raises ValueError if required segments are missing.
        """
        segs = self._tokenize(raw_edi)

        def find(seg_id: str, qualifier_pos: int = 0, qualifier: str = "") -> list[str]:
            for s in segs:
                if s[0] != seg_id:
                    continue
                if qualifier and self._val(s, qualifier_pos) != qualifier:
                    continue
                return s
            raise ValueError(f"Segment {seg_id!r} not found in 270")

        isa = find("ISA")
        bht = find("BHT")
        payer_nm1 = find("NM1", 1, "PR")
        provider_nm1 = find("NM1", 1, "1P")
        subscriber_nm1 = find("NM1", 1, "IL")

        # DMG is optional — some 270s omit it for group-level inquiries
        dmg = next((s for s in segs if s[0] == "DMG"), None)

        return _Context270(
            clinic_edi_id=self._val(isa, 6).strip(),
            clearinghouse_edi_id=self._val(isa, 8).strip(),
            control_number=self._val(isa, 13),
            reference_id=self._val(bht, 3),
            payer_edi_id=self._val(payer_nm1, 9),
            payer_name=self._val(payer_nm1, 3),
            provider_npi=self._val(provider_nm1, 9),
            provider_name=self._val(provider_nm1, 3),
            member_id=self._val(subscriber_nm1, 9),
            subscriber_last=self._val(subscriber_nm1, 3),
            subscriber_first=self._val(subscriber_nm1, 4),
            dob=self._val(dmg, 2) if dmg else "",
            gender=self._val(dmg, 3) if dmg else "U",
        )

    # ------------------------------------------------------------------
    # 271 building
    # ------------------------------------------------------------------

    def _build_271(self, ctx: _Context270, scenario: str) -> str:
        """
        Builds a complete, parseable 271 EDI string for the given scenario.
        Segment structure mirrors what real payers return.
        """
        now = datetime.now(timezone.utc)
        sep = "*"
        term = "~"

        # --- Transaction segments (ST through SE, counted in SE01) ---
        t: list[str] = []

        # ST — transaction set header
        t.append(f"ST{sep}271{sep}0001{sep}005010X279A1")

        # BHT — echo back the reference_id from the 270 (appointment ID)
        # This is how the clinic matches the 271 back to the original request
        t.append(
            f"BHT{sep}0022{sep}11{sep}{ctx.reference_id}"
            f"{sep}{now.strftime('%Y%m%d')}{sep}{now.strftime('%H%M')}"
        )

        # Payer loop (HL*1)
        t.append(f"HL{sep}1{sep}{sep}20{sep}1")
        t.append(
            f"NM1{sep}PR{sep}2{sep}{ctx.payer_name}"
            f"{sep}{sep}{sep}{sep}{sep}PI{sep}{ctx.payer_edi_id}"
        )

        # Provider loop (HL*2)
        t.append(f"HL{sep}2{sep}1{sep}21{sep}1")
        t.append(
            f"NM1{sep}1P{sep}2{sep}{ctx.provider_name}"
            f"{sep}{sep}{sep}{sep}{sep}XX{sep}{ctx.provider_npi}"
        )

        # Subscriber loop (HL*3)
        t.append(f"HL{sep}3{sep}2{sep}22{sep}0")
        t.append(
            f"NM1{sep}IL{sep}1{sep}{ctx.subscriber_last}{sep}{ctx.subscriber_first}"
            f"{sep}{sep}{sep}{sep}MI{sep}{ctx.member_id}"
        )

        # EB / AAA segments based on scenario
        t.extend(self._eb_segments_for(scenario))

        # SE — segment count = everything in t so far + 1 (SE itself)
        se_count = len(t) + 1
        t.append(f"SE{sep}{se_count}{sep}0001")

        # --- Envelope (not counted in SE01) ---
        resp_control = str(self._response_counter).zfill(9)

        # In the 271, sender = clearinghouse, receiver = clinic (swapped from 270)
        isa = (
            f"ISA{sep}00{sep}{'':10}{sep}00{sep}{'':10}"
            f"{sep}ZZ{sep}{ctx.clearinghouse_edi_id.ljust(15)}"
            f"{sep}ZZ{sep}{ctx.clinic_edi_id.ljust(15)}"
            f"{sep}{now.strftime('%y%m%d')}{sep}{now.strftime('%H%M')}"
            f"{sep}^{sep}00501{sep}{resp_control}{sep}0{sep}P{sep}:"
        )
        gs = (
            f"GS{sep}HB"
            f"{sep}{ctx.clearinghouse_edi_id.strip()}"
            f"{sep}{ctx.clinic_edi_id.strip()}"
            f"{sep}{now.strftime('%Y%m%d')}{sep}{now.strftime('%H%M')}"
            f"{sep}1{sep}X{sep}005010X279A1"
        )
        ge = f"GE{sep}1{sep}1"
        iea = f"IEA{sep}1{sep}{resp_control}"

        all_segs = [isa, gs] + t + [ge, iea]
        return term.join(all_segs) + term

    def _eb_segments_for(self, scenario: str) -> list[str]:
        """
        Returns the EB (and DTP/AAA) segments for the given scenario.
        These are plain strings without the ~ terminator — the caller joins them.

        EB position reference (1-based):
          EB01 = benefit code  EB02 = coverage level  EB03 = service type
          EB04 = insurance type  EB05 = plan name  EB06 = time period
          EB07 = amount  EB08 = percent  EB09-10 = quantity
          EB11 = auth required  EB12 = in-network (Y/N)
        """
        if scenario == SCENARIO_INACTIVE:
            return ["EB*6**30"]

        if scenario == SCENARIO_REJECTED_MEMBER_NOT_FOUND:
            # AAA: rejection indicator N, reason 75 = member not found, C = contact plan
            return ["AAA*N**75*C"]

        if scenario == SCENARIO_ACTIVE_PPO:
            return [
                # Active, PPO, EB12=Y (in-network) — 7 stars between plan name and Y
                "EB*1**30*PP*PREFERRED PPO*******Y",
                "DTP*346*D8*20260101",   # coverage start
                "DTP*347*D8*20261231",   # coverage end
                "EB*C*IND*30***23*500.00",    # deductible — annual total
                "EB*C*IND*30***29*347.50",    # deductible — remaining
                "EB*G*IND*30***23*3000.00",   # OOP max — annual total
                "EB*G*IND*30***29*2500.00",   # OOP max — remaining
                "EB*B**98***26*30.00",        # copay $30 per visit
                "EB*A**98*****20",            # 20% coinsurance — 5 stars to reach EB08
            ]

        if scenario == SCENARIO_ACTIVE_HMO:
            return [
                "EB*1**30*HM*CIGNA HMO*******Y",
                "DTP*346*D8*20260101",
                "DTP*347*D8*20261231",
                "EB*C*IND*30***23*1500.00",   # deductible — none met yet
                "EB*C*IND*30***29*1500.00",
                "EB*G*IND*30***23*5000.00",
                "EB*G*IND*30***29*5000.00",
                "EB*B**98***26*25.00",        # $25 copay
            ]

        if scenario == SCENARIO_ACTIVE_HIGH_DEDUCTIBLE:
            return [
                "EB*1**30*PP*HIGH DEDUCTIBLE PPO*******Y",
                "DTP*346*D8*20260101",
                "DTP*347*D8*20261231",
                "EB*C*IND*30***23*5000.00",   # $5000 deductible — none met
                "EB*C*IND*30***29*5000.00",
                "EB*G*IND*30***23*7000.00",
                "EB*G*IND*30***29*7000.00",
                "EB*A**98*****20",            # 20% after deductible
            ]

        if scenario == SCENARIO_ACTIVE_OUT_OF_NETWORK:
            # EB12=N means out-of-network for this provider
            return [
                "EB*1**30*PP*STANDARD PPO*******N",
                "DTP*346*D8*20260101",
                "DTP*347*D8*20261231",
                "EB*C*IND*30***23*1000.00",
                "EB*C*IND*30***29*1000.00",
                "EB*G*IND*30***23*5000.00",
                "EB*G*IND*30***29*5000.00",
                "EB*A**98*****40",            # 40% coinsurance out-of-network
            ]

        # Unknown scenario — return minimal active response rather than crashing
        return ["EB*1**30"]

    # ------------------------------------------------------------------
    # Shared helpers (duplicated from Parser271 to keep modules independent)
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize(raw_edi: str) -> list[list[str]]:
        raw = raw_edi.strip()
        if not raw.startswith("ISA"):
            raise ValueError("EDI string does not start with ISA")
        element_sep = raw[3]
        segment_term = raw[105]
        result = []
        for raw_seg in raw.split(segment_term):
            seg = raw_seg.strip()
            if seg:
                result.append(seg.split(element_sep))
        return result

    @staticmethod
    def _val(seg: list[str], position: int) -> str:
        try:
            return seg[position].strip()
        except IndexError:
            return ""
