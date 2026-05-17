"""
Builder270 — constructs a valid X12 005010X279A1 270 EDI string.

How it works:
  1. Caller passes an EligibilityInquiry dataclass (patient + payer + provider)
  2. Builder assembles segments in the required order
  3. Returns the complete EDI string ready to send to the clearinghouse

Segment order (required by the spec):
  ISA → GS → ST → BHT → [payer HL+NM1] → [provider HL+NM1]
  → [subscriber HL+NM1+DMG] → [dependent HL+NM1+DMG if applicable]
  → EQ → SE → GE → IEA
"""
from datetime import datetime, date, timezone

from app.edi.models import EligibilityInquiry
from app.edi import segments as SEG


class Builder270:
    """
    Builds one complete 270 EDI transaction.

    Usage:
        builder = Builder270(
            sender_id="YOURCLINIC",
            receiver_id="CLEARINGHOUSE",
            isa_control_number="000000001",
            usage="P",   # P = production, T = test
        )
        edi_string = builder.build(inquiry)
    """

    def __init__(
        self,
        sender_id: str,
        receiver_id: str,
        isa_control_number: str,
        usage: str = "P",
    ) -> None:
        self._sender_id = sender_id
        self._receiver_id = receiver_id
        self._isa_control_number = isa_control_number.zfill(9)  # always 9 digits
        self._usage = usage
        self._gs_control_number = "1"   # one group per file
        self._st_control_number = "0001"
        self._now = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def build(self, inquiry: EligibilityInquiry) -> str:
        """
        Returns the complete 270 EDI string for one eligibility inquiry.
        The string uses * as element separator and ~ as segment terminator.
        """
        self._segments: list[str] = []
        self._segment_count = 0  # tracks ST..SE count for SE01

        self._add_isa()
        self._add_gs()
        self._add_st()
        self._add_bht(inquiry.reference_id)
        self._add_payer_loop(inquiry.payer)
        self._add_provider_loop(inquiry.provider)
        self._add_subscriber_loop(inquiry.subscriber, has_dependent=inquiry.dependent is not None)
        if inquiry.dependent:
            self._add_dependent_loop(inquiry.dependent)
        self._add_eq(inquiry.service_type_codes)
        self._add_se()
        self._add_ge()
        self._add_iea()

        return SEG.SEGMENT_TERM.join(self._segments) + SEG.SEGMENT_TERM

    # ------------------------------------------------------------------
    # Envelope segments (ISA / GS / GE / IEA)
    # These wrap the transaction — they are NOT counted in the SE count.
    # ------------------------------------------------------------------

    def _add_isa(self) -> None:
        """
        ISA — Interchange Control Header.
        Every field has a fixed width — this is the only segment in X12
        that works this way. The last character (ISA16) sets the component
        separator for the entire file, so the total ISA length is always 106.
        """
        isa = SEG.ELEMENT_SEP.join([
            "ISA",
            SEG.ISA_AUTH_QUALIFIER,       # ISA01: 00 = no auth
            " " * 10,                     # ISA02: 10 spaces (auth info, blank)
            SEG.ISA_SECURITY_QUALIFIER,   # ISA03: 00 = no security
            " " * 10,                     # ISA04: 10 spaces (security info, blank)
            SEG.ISA_ID_QUALIFIER,         # ISA05: ZZ
            self._sender_id.ljust(15),    # ISA06: 15 chars, right-padded
            SEG.ISA_ID_QUALIFIER,         # ISA07: ZZ
            self._receiver_id.ljust(15),  # ISA08: 15 chars, right-padded
            self._now.strftime("%y%m%d"), # ISA09: YYMMDD
            self._now.strftime("%H%M"),   # ISA10: HHMM
            SEG.REPETITION_SEP,           # ISA11: ^ repetition separator
            SEG.ISA_VERSION,              # ISA12: 00501
            self._isa_control_number,     # ISA13: 9-digit control number
            "0",                          # ISA14: ack not requested
            self._usage,                  # ISA15: P=production, T=test
            SEG.COMPONENT_SEP,            # ISA16: component separator
        ])
        self._segments.append(isa)

    def _add_gs(self) -> None:
        """
        GS — Functional Group Header.
        Groups one or more transactions of the same type (HS = eligibility).
        """
        gs = SEG.ELEMENT_SEP.join([
            "GS",
            SEG.GS_FUNCTIONAL_ID,                   # GS01: HS
            self._sender_id.strip(),                 # GS02: sender application ID
            self._receiver_id.strip(),               # GS03: receiver application ID
            self._now.strftime("%Y%m%d"),            # GS04: CCYYMMDD
            self._now.strftime("%H%M"),              # GS05: HHMM
            self._gs_control_number,                 # GS06: group control number
            SEG.GS_RESPONSIBILITY_AGENCY,            # GS07: X
            SEG.GS_VERSION,                          # GS08: 005010X279A1
        ])
        self._segments.append(gs)

    def _add_ge(self) -> None:
        """GE — Functional Group Trailer. Closes GS."""
        ge = SEG.ELEMENT_SEP.join([
            "GE",
            "1",                         # GE01: number of transaction sets in group
            self._gs_control_number,     # GE02: must match GS06
        ])
        self._segments.append(ge)

    def _add_iea(self) -> None:
        """IEA — Interchange Control Trailer. Closes ISA."""
        iea = SEG.ELEMENT_SEP.join([
            "IEA",
            "1",                             # IEA01: number of functional groups
            self._isa_control_number,        # IEA02: must match ISA13
        ])
        self._segments.append(iea)

    # ------------------------------------------------------------------
    # Transaction segments (counted in SE01)
    # ------------------------------------------------------------------

    def _add_transaction_segment(self, *elements: str) -> None:
        """Builds one segment, appends it, and increments the SE count."""
        self._segments.append(SEG.ELEMENT_SEP.join(elements))
        self._segment_count += 1

    def _add_st(self) -> None:
        """
        ST — Transaction Set Header.
        Marks the start of one 270 transaction.
        ST02 is the transaction control number (distinct from ISA13).
        """
        self._add_transaction_segment(
            "ST",
            SEG.TRANSACTION_270,     # ST01: 270
            self._st_control_number, # ST02: 0001
            SEG.GS_VERSION,          # ST03: implementation guide version
        )

    def _add_bht(self, reference_id: str) -> None:
        """
        BHT — Beginning of Hierarchical Transaction.
        Declares the purpose of this transaction and links it to your
        internal reference (appointment ID).
        """
        self._add_transaction_segment(
            "BHT",
            SEG.BHT_STRUCTURE_CODE,          # BHT01: 0022 = eligibility
            SEG.BHT_PURPOSE_CODE,            # BHT02: 13 = request
            reference_id[:30],               # BHT03: your reference (max 30 chars)
            self._now.strftime("%Y%m%d"),    # BHT04: date CCYYMMDD
            self._now.strftime("%H%M"),      # BHT05: time HHMM
        )

    def _add_payer_loop(self, payer: "PayerInfo") -> None:
        """
        HL*1 — Information Source level (the payer).
        HL04 = 1 because provider HL is a child of this.
        NM1*PR identifies the insurance company.
        """
        self._add_transaction_segment(
            "HL",
            "1",                      # HL01: hierarchical ID (sequential, starts at 1)
            "",                       # HL02: parent ID (blank — payer is root)
            SEG.HL_INFORMATION_SOURCE,# HL03: 20 = information source
            SEG.HL_HAS_CHILD,         # HL04: 1 = has child levels below
        )
        self._add_transaction_segment(
            "NM1",
            SEG.NM1_PAYER,            # NM101: PR
            SEG.NM1_NON_PERSON,       # NM102: 2 = organization
            payer.payer_name[:60],    # NM103: payer name (max 60 chars)
            "",                       # NM104: first name (blank for org)
            "",                       # NM105: middle name (blank)
            "",                       # NM106: prefix (blank)
            "",                       # NM107: suffix (blank)
            SEG.NM1_ID_QUAL_PAYER,    # NM108: PI = payer ID
            payer.edi_payer_id,       # NM109: the actual payer EDI ID
        )

    def _add_provider_loop(self, provider: "ProviderInfo") -> None:
        """
        HL*2 — Information Receiver level (the provider/clinic).
        HL04 = 1 because subscriber HL is a child of this.
        NM1*1P identifies the provider by NPI.
        """
        self._add_transaction_segment(
            "HL",
            "2",                          # HL01: 2
            "1",                          # HL02: parent is HL 1 (payer)
            SEG.HL_INFORMATION_RECEIVER,  # HL03: 21 = information receiver
            SEG.HL_HAS_CHILD,             # HL04: 1 = has subscriber below
        )
        self._add_transaction_segment(
            "NM1",
            SEG.NM1_PROVIDER,             # NM101: 1P
            SEG.NM1_NON_PERSON,           # NM102: 2 = organization
            provider.org_name[:60],       # NM103: clinic name
            "",                           # NM104: first name (blank for org)
            "",                           # NM105: middle name
            "",                           # NM106: prefix
            "",                           # NM107: suffix
            SEG.NM1_ID_QUAL_NPI,          # NM108: XX = NPI
            provider.npi,                 # NM109: 10-digit NPI
        )

    def _add_subscriber_loop(self, subscriber: "SubscriberInfo", has_dependent: bool) -> None:
        """
        HL*3 — Subscriber level.
        HL04 = 1 if patient is a dependent (dependent HL follows), else 0.
        NM1*IL identifies the subscriber (policyholder).
        DMG carries date of birth and gender — required for identity matching.
        """
        hl04 = SEG.HL_HAS_CHILD if has_dependent else SEG.HL_NO_CHILD

        self._add_transaction_segment(
            "HL",
            "3",                    # HL01: 3
            "2",                    # HL02: parent is HL 2 (provider)
            SEG.HL_SUBSCRIBER,      # HL03: 22
            hl04,                   # HL04: 1 if dependent exists, else 0
        )
        self._add_transaction_segment(
            "NM1",
            SEG.NM1_SUBSCRIBER,     # NM101: IL
            SEG.NM1_PERSON,         # NM102: 1 = individual
            subscriber.last_name[:60],
            subscriber.first_name[:35],
            "",                     # NM105: middle name
            "",                     # NM106: prefix
            "",                     # NM107: suffix
            SEG.NM1_ID_QUAL_MEMBER, # NM108: MI = member ID
            subscriber.member_id,   # NM109: member ID from insurance card
        )
        self._add_transaction_segment(
            "DMG",
            SEG.DMG_DATE_FORMAT,                          # DMG01: D8
            subscriber.date_of_birth.strftime("%Y%m%d"),  # DMG02: CCYYMMDD
            subscriber.gender,                            # DMG03: M / F / U
        )

    def _add_dependent_loop(self, dependent: "DependentInfo") -> None:
        """
        HL*4 — Dependent level.
        Only present when the patient is NOT the subscriber.
        NM1*QC identifies the actual patient (dependent).
        HL04 = 0 because dependent is always a leaf.
        """
        self._add_transaction_segment(
            "HL",
            "4",                    # HL01: 4
            "3",                    # HL02: parent is HL 3 (subscriber)
            SEG.HL_DEPENDENT,       # HL03: 23
            SEG.HL_NO_CHILD,        # HL04: 0 — no children
        )
        self._add_transaction_segment(
            "NM1",
            SEG.NM1_DEPENDENT,      # NM101: QC = patient
            SEG.NM1_PERSON,         # NM102: 1 = individual
            dependent.last_name[:60],
            dependent.first_name[:35],
            "",                     # NM105: middle
            "",                     # NM106: prefix
            "",                     # NM107: suffix
            "",                     # NM108: no ID qualifier for dependent
            "",                     # NM109: no ID for dependent
        )
        self._add_transaction_segment(
            "DMG",
            SEG.DMG_DATE_FORMAT,
            dependent.date_of_birth.strftime("%Y%m%d"),
            dependent.gender,
        )

    def _add_eq(self, service_type_codes: list[str]) -> None:
        """
        EQ — Eligibility or Benefit Inquiry.
        Lists what types of coverage we are asking about.
        One EQ segment per service type code.
        Most inquiries use a single EQ*30~ (general coverage).
        """
        for code in service_type_codes:
            self._add_transaction_segment("EQ", code)

    def _add_se(self) -> None:
        """
        SE — Transaction Set Trailer.
        SE01 = total segments from ST through SE inclusive.
        SE02 must match ST02.
        We add SE last so segment_count is final.
        """
        # +1 because SE itself is included in the count
        total = self._segment_count + 1
        self._segments.append(
            SEG.ELEMENT_SEP.join(["SE", str(total), self._st_control_number])
        )
