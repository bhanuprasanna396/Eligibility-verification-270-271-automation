"""
Eligibility check status endpoint.

GET /checks/{check_id}  — poll for status after triggering a check
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import EligibilityCheck
from app.schemas.eligibility import CheckSummary, EligibilityResultSchema, GapSchema

router = APIRouter(prefix="/checks", tags=["checks"])

DbDep = Annotated[Session, Depends(get_db)]


@router.get("/{check_id}", response_model=CheckSummary)
def get_check(check_id: uuid.UUID, db: DbDep):
    """
    Returns the current status of a single eligibility check.

    Intended for polling after POST /appointments/{id}/check:

        1. Caller receives check_id from the 202 response.
        2. Caller polls this endpoint until status is 'completed' or 'failed'.
        3. On 'completed', fetch full appointment detail for the result.

    This keeps the trigger endpoint non-blocking (202 Accepted) while giving
    callers a clear way to track progress.
    """
    check = db.get(
        EligibilityCheck,
        check_id,
        options=[
            selectinload(EligibilityCheck.result),
            selectinload(EligibilityCheck.gaps),
        ],
    )
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")

    return check
