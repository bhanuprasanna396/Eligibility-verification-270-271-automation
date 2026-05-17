"""
Input data structures for the 270 builder.

These dataclasses define exactly what the builder needs to know.
They are populated from the database records before calling Builder270.
"""
from dataclasses import dataclass, field
from datetime import date


@dataclass
class ProviderInfo:
    """
    The clinic or doctor sending the eligibility inquiry.
    npi      — 10-digit National Provider Identifier (required by X12)
    edi_id   — the sender ID used in the ISA06 field (assigned by clearinghouse)
    org_name — full organization name as registered with the payer
    """
    npi: str
    edi_id: str
    org_name: str


@dataclass
class PayerInfo:
    """
    The insurance company being queried.
    edi_payer_id — the payer's X12 ID (e.g. "00050" for Aetna)
                   This is NOT the member's insurance card number.
                   It identifies the payer in the EDI network.
    """
    edi_payer_id: str
    payer_name: str


@dataclass
class SubscriberInfo:
    """
    The policyholder — the person whose name the insurance plan is under.
    When the patient IS the subscriber (most common), patient == subscriber.
    When the patient is a dependent (child on parent's plan), this holds the
    parent's info, and DependentInfo holds the patient's info.
    """
    last_name: str
    first_name: str
    date_of_birth: date
    gender: str          # M, F, or U
    member_id: str       # from the insurance card


@dataclass
class DependentInfo:
    """
    The actual patient when they are a dependent on someone else's plan.
    Only populated when the patient is NOT the subscriber.
    Example: child (patient) on parent's (subscriber) insurance.
    """
    last_name: str
    first_name: str
    date_of_birth: date
    gender: str          # M, F, or U


@dataclass
class EligibilityInquiry:
    """
    Everything the 270 builder needs to construct one eligibility inquiry.
    One EligibilityInquiry = one 270 transaction = one call to the clearinghouse.

    reference_id     — your internal ID (appointment UUID) used to match
                       the 271 response back to this request
    service_type_codes — what you are asking about (["30"] for general coverage,
                         ["98"] for professional visit, etc.)
    dependent        — only set when patient is a dependent, None otherwise
    """
    provider: ProviderInfo
    payer: PayerInfo
    subscriber: SubscriberInfo
    reference_id: str
    service_type_codes: list[str] = field(default_factory=lambda: ["30"])
    dependent: DependentInfo | None = None
