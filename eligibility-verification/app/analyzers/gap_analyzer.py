"""
GapAnalyzer — finds coverage problems that need staff attention before a visit.

Each rule produces a Gap with three fields:
  gap_type    machine-readable code  (used for filtering and dashboard colour)
  severity    critical | high | warning | info
  description plain English shown to front desk staff

Rules run in priority order. When inactive coverage or a payer rejection
is detected, analysis stops early — financial rules are meaningless without
active coverage and would produce misleading output.

Severity guide:
  critical  Must resolve before the visit can proceed (inactive, date mismatch)
  high      Significant patient cost or workflow blocker (OON, prior auth)
  warning   Staff should inform the patient but visit can proceed (referral, deductible)
  info      Informational — no action required
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.edi.parsed_models import ParsedEligibility

# Deductible thresholds that trigger a gap (configurable here, not in .env,
# because these are clinical policy decisions rather than deployment config).
_DEDUCTIBLE_WARNING_THRESHOLD = Decimal("500.00")
_DEDUCTIBLE_HIGH_THRESHOLD = Decimal("1000.00")


@dataclass
class Gap:
    """One specific coverage problem found for an appointment."""
    gap_type: str       # machine-readable: INACTIVE_COVERAGE, HIGH_DEDUCTIBLE, etc.
    severity: str       # critical | high | warning | info
    description: str    # plain English shown to front desk staff


class GapAnalyzer:
    """
    Analyzes a ParsedEligibility and returns a list of Gaps.
    Each Gap becomes a CoverageGap row in the database.

    Pass appointment_date to enable date-range checks. Without it,
    COVERAGE_DATE_MISMATCH rules are skipped rather than raising.
    """

    def analyze(
        self,
        result: ParsedEligibility,
        appointment_date: date | None = None,
    ) -> list[Gap]:
        """
        Returns gaps found in the coverage result.
        Empty list = no problems = appointment is clear to proceed.
        """
        gaps: list[Gap] = []

        # --- Rule 1: Payer rejection (AAA segment) ---
        # The payer could not answer the inquiry at all (member not found, etc.).
        # No point running further rules — we have no coverage data.
        if result.rejection_reasons:
            gaps.append(Gap(
                gap_type="PAYER_REJECTION",
                severity="critical",
                description=(
                    "Payer rejected the eligibility inquiry: "
                    + "; ".join(result.rejection_reasons)
                    + ". Verify member ID and re-submit, or call the payer directly."
                ),
            ))
            return gaps

        # --- Rule 2: Inactive coverage ---
        # Coverage is explicitly inactive (EB01=6). Financial data is absent.
        if not result.coverage_active:
            gaps.append(Gap(
                gap_type="INACTIVE_COVERAGE",
                severity="critical",
                description=(
                    "Patient's insurance coverage is not active. "
                    "Verify insurance information before the visit."
                ),
            ))
            return gaps

        # --- Rule 3: Coverage date mismatch ---
        # Appointment falls outside the coverage window the payer reported.
        if appointment_date is not None:
            if (
                result.coverage_effective_date is not None
                and appointment_date < result.coverage_effective_date
            ):
                gaps.append(Gap(
                    gap_type="COVERAGE_DATE_MISMATCH",
                    severity="critical",
                    description=(
                        f"Appointment date ({appointment_date}) is before the coverage "
                        f"effective date ({result.coverage_effective_date}). "
                        "Patient may not be covered — confirm with the payer."
                    ),
                ))

            if (
                result.coverage_termination_date is not None
                and appointment_date > result.coverage_termination_date
            ):
                gaps.append(Gap(
                    gap_type="COVERAGE_DATE_MISMATCH",
                    severity="critical",
                    description=(
                        f"Appointment date ({appointment_date}) is after the coverage "
                        f"termination date ({result.coverage_termination_date}). "
                        "Patient may not be covered — confirm with the payer."
                    ),
                ))

        # --- Rule 4: Out of network ---
        # in_network=None means the payer did not send network status —
        # that is NOT the same as being out of network, so we skip it.
        if result.in_network is False:
            gaps.append(Gap(
                gap_type="OUT_OF_NETWORK",
                severity="high",
                description=(
                    "This provider is out-of-network for the patient's plan. "
                    "Patient will face higher cost-sharing (deductibles, coinsurance)."
                ),
            ))

        # --- Rule 5: Prior authorization required ---
        if result.prior_auth_required:
            gaps.append(Gap(
                gap_type="PRIOR_AUTH_REQUIRED",
                severity="high",
                description=(
                    "Prior authorization is required for this service. "
                    "Obtain authorization from the payer before the visit."
                ),
            ))

        # --- Rule 6: Referral required ---
        if result.referral_required:
            gaps.append(Gap(
                gap_type="REFERRAL_REQUIRED",
                severity="warning",
                description=(
                    "A referral is required for this visit. "
                    "Confirm the patient has a valid referral from their primary care physician."
                ),
            ))

        # --- Rule 7: High individual deductible remaining ---
        # Only fires when the payer explicitly returned a remaining amount.
        # Thresholds: ≥$1 000 → high, ≥$500 → warning.
        if result.deductible_individual_remaining is not None:
            remaining = result.deductible_individual_remaining
            if remaining >= _DEDUCTIBLE_HIGH_THRESHOLD:
                gaps.append(Gap(
                    gap_type="HIGH_DEDUCTIBLE",
                    severity="high",
                    description=(
                        f"Patient has ${remaining:.2f} remaining on their individual deductible. "
                        "They will likely owe the full cost of today's visit. "
                        "Collect payment or set up a payment plan before the appointment."
                    ),
                ))
            elif remaining >= _DEDUCTIBLE_WARNING_THRESHOLD:
                gaps.append(Gap(
                    gap_type="HIGH_DEDUCTIBLE",
                    severity="warning",
                    description=(
                        f"Patient has ${remaining:.2f} remaining on their individual deductible. "
                        "Inform the patient they may owe a significant out-of-pocket amount."
                    ),
                ))

        return gaps
