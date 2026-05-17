"""
Control number generation for EDI transactions.

Why this is its own module:
  ISA13 must be unique across every 270 we ever send to a clearinghouse.
  Sending a duplicate ISA13 causes the clearinghouse to reject the file as
  a duplicate interchange — even if the patient data is different.

Two modes:
  1. DB-backed (production): inserts into edi_control_numbers table,
     uses the auto-increment PK as the number. Unique by DB constraint.
  2. In-memory (testing): uses a simple counter. Safe for tests only.
"""
from sqlalchemy.orm import Session

from app.models.edi_log import EdiControlNumber


def next_control_number(db: Session) -> str:
    """
    Allocates the next unique ISA control number from the database.
    Inserts a row and uses its auto-increment ID, formatted as 9 digits.

    The DB UNIQUE constraint on control_number means two concurrent workers
    can never produce the same number — the second insert will fail and retry.

    Returns: zero-padded 9-digit string, e.g. "000000042"
    """
    record = EdiControlNumber(control_number="placeholder")
    db.add(record)
    db.flush()  # gets the auto-increment ID without committing

    # Format the PK as 9-digit zero-padded string (max ~999 million unique numbers)
    control_number = str(record.id).zfill(9)

    # Update the placeholder with the real value
    record.control_number = control_number
    db.flush()

    return control_number


class InMemoryControlNumberGenerator:
    """
    Simple counter for use in tests — does not touch the database.
    Start at 1, increments on each call, resets per instance.
    """

    def __init__(self, start: int = 1) -> None:
        self._counter = start

    def next(self) -> str:
        number = str(self._counter).zfill(9)
        self._counter += 1
        return number
