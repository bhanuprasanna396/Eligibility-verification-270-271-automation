"""
EDI X12 constants used when building 270 transactions.

Every code here comes from the X12 005010X279A1 implementation guide.
When a code looks cryptic (e.g. "1P", "IL", "PR") this file is the
single source of truth for what it means.
"""

# ---------------------------------------------------------------------------
# Delimiters — must be characters that never appear in the data
# ---------------------------------------------------------------------------
ELEMENT_SEP = "*"       # separates fields within a segment
SEGMENT_TERM = "~"      # marks end of a segment
COMPONENT_SEP = ":"     # separates sub-elements within a field
REPETITION_SEP = "^"    # separates repeated values within a field

# ---------------------------------------------------------------------------
# ISA fixed values
# ---------------------------------------------------------------------------
ISA_VERSION = "00501"   # X12 version 005010
ISA_AUTH_QUALIFIER = "00"   # no authorization info
ISA_SECURITY_QUALIFIER = "00"   # no security info
ISA_ID_QUALIFIER = "ZZ"     # "mutually defined" — standard for most clearinghouses

# ---------------------------------------------------------------------------
# GS functional group ID
# HS = Health and Human Services eligibility (270/271 group)
# ---------------------------------------------------------------------------
GS_FUNCTIONAL_ID = "HS"
GS_RESPONSIBILITY_AGENCY = "X"     # X12 accredited standards committee
GS_VERSION = "005010X279A1"        # implementation guide version

# ---------------------------------------------------------------------------
# Transaction set ID
# ---------------------------------------------------------------------------
TRANSACTION_270 = "270"     # eligibility inquiry
TRANSACTION_271 = "271"     # eligibility response

# ---------------------------------------------------------------------------
# BHT — Beginning of Hierarchical Transaction
# ---------------------------------------------------------------------------
BHT_STRUCTURE_CODE = "0022"     # eligibility transaction
BHT_PURPOSE_CODE = "13"         # 13 = request, 11 = response

# ---------------------------------------------------------------------------
# HL — Hierarchical Level type codes
# ---------------------------------------------------------------------------
HL_INFORMATION_SOURCE = "20"    # payer (the entity that has the data)
HL_INFORMATION_RECEIVER = "21"  # provider (the entity asking)
HL_SUBSCRIBER = "22"            # the insured person (policyholder)
HL_DEPENDENT = "23"             # a dependent of the subscriber

# HL04 child code: 1 = has children below it, 0 = leaf node
HL_HAS_CHILD = "1"
HL_NO_CHILD = "0"

# ---------------------------------------------------------------------------
# NM1 — entity type qualifiers (NM102)
# ---------------------------------------------------------------------------
NM1_PERSON = "1"            # individual person
NM1_NON_PERSON = "2"        # organization

# ---------------------------------------------------------------------------
# NM1 — entity identifier codes (NM101)
# ---------------------------------------------------------------------------
NM1_PAYER = "PR"            # payer / insurance company
NM1_PROVIDER = "1P"         # rendering provider
NM1_SUBSCRIBER = "IL"       # insured or subscriber (IL = Insured or Subscriber)
NM1_DEPENDENT = "QC"        # patient (when different from subscriber)

# ---------------------------------------------------------------------------
# NM1 — identification code qualifiers (NM108)
# ---------------------------------------------------------------------------
NM1_ID_QUAL_NPI = "XX"      # NPI (National Provider Identifier)
NM1_ID_QUAL_PAYER = "PI"    # payer-assigned identifier (EDI payer ID)
NM1_ID_QUAL_MEMBER = "MI"   # member identification number (from insurance card)

# ---------------------------------------------------------------------------
# DMG — date/time period format qualifier
# ---------------------------------------------------------------------------
DMG_DATE_FORMAT = "D8"      # D8 = CCYYMMDD (8-digit date)

# ---------------------------------------------------------------------------
# Gender codes used in DMG03
# ---------------------------------------------------------------------------
GENDER_MALE = "M"
GENDER_FEMALE = "F"
GENDER_UNKNOWN = "U"

# ---------------------------------------------------------------------------
# EQ — service type codes (what we are asking about)
# The most common ones — full list has 100+ codes
# ---------------------------------------------------------------------------
SERVICE_TYPE_CODES = {
    "30": "Health Benefit Plan Coverage (general)",
    "98": "Professional (physician office visit)",
    "48": "Hospital — Inpatient",
    "50": "Hospital — Outpatient",
    "1": "Medical Care",
    "35": "Dental Care",
    "A4": "Vision (Optometry)",
    "MH": "Mental Health",
    "UC": "Urgent Care",
}

# ---------------------------------------------------------------------------
# AAA — rejection reason codes returned in 271
# ---------------------------------------------------------------------------
REJECTION_REASONS = {
    "15": "Required application data missing",
    "42": "Unable to respond at current time",
    "43": "Invalid/missing provider identification",
    "45": "Invalid/missing provider name",
    "72": "Invalid/missing subscriber/member ID",
    "73": "Invalid/missing subscriber/insured name",
    "75": "Subscriber/insured not found",
    "76": "Duplicate inquiry",
}
