"""
Clearinghouse interface — defines the contract every clearinghouse client must follow.

Both the mock client (for development) and the real client (for production)
implement ClearinghouseClientBase. Swapping between them requires changing
one line in the dependency injection — nothing else changes.

ClearinghouseResponse is what every caller works with regardless of which
client is behind it.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ClearinghouseResponse:
    """
    The result of submitting a 270 to the clearinghouse.

    success=True does NOT mean coverage is active. It means the network
    call succeeded and a 271 (or valid rejection) came back.
    Parse edi_271 with Parser271 to learn the actual coverage status.

    success=False means a network/infrastructure problem — timeout,
    service down, auth failure. The 270 should be retried.
    """
    success: bool
    transaction_id: str       # clearinghouse's own ID for this submission
    edi_271: str | None       # the raw 271 string (None on network failure)
    error_message: str | None = None


class ClearinghouseClientBase(ABC):
    """
    Abstract clearinghouse client.
    Implement this to connect to any clearinghouse (Availity, Waystar, etc.)
    or to build a mock for testing.
    """

    @abstractmethod
    def submit_270(self, edi_270: str) -> ClearinghouseResponse:
        """
        Submit a 270 eligibility inquiry.
        Returns a ClearinghouseResponse — always check success before using edi_271.
        """
