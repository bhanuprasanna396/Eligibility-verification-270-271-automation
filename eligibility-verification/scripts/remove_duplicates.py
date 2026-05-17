"""
One-time script to remove duplicate scenario patients created by running
add_scenarios.py more than once.

For each scenario note (unencrypted, unique per scenario), it keeps the
OLDEST appointment and deletes every newer duplicate along with its patient.

Run once, then discard:
    python scripts/remove_duplicates.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

env_path = os.path.join(ROOT, ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

if not os.environ.get("PHI_ENCRYPTION_KEY"):
    print("[ERROR] PHI_ENCRYPTION_KEY not set. Check your .env file.")
    sys.exit(1)

from app.database import create_db_session
from app.models import Appointment, Patient, PatientInsurance, EligibilityCheck, CoverageGap

SCENARIO_NOTES = [
    "HMO — high deductible gap",
    "HDHP — $5000 deductible gap",
    "Out-of-network gap",
    "Inactive coverage — critical gap",
    "Payer rejection — member not found",
    "Clearinghouse down — check will fail",
    "Clean PPO — verified, no gaps",
]


def main() -> None:
    db = create_db_session()
    removed = 0
    try:
        for note in SCENARIO_NOTES:
            appts = (
                db.query(Appointment)
                .filter(Appointment.notes == note)
                .order_by(Appointment.created_at.asc())
                .all()
            )
            if len(appts) <= 1:
                continue

            # Keep the first, delete the rest
            for appt in appts[1:]:
                patient_id = appt.patient_id

                # Delete child records first (in case FK constraints aren't cascading)
                for check in db.query(EligibilityCheck).filter(
                    EligibilityCheck.appointment_id == appt.appointment_id
                ).all():
                    db.query(CoverageGap).filter(
                        CoverageGap.check_id == check.check_id
                    ).delete()
                    db.delete(check)

                db.delete(appt)
                db.flush()

                # Delete the patient (and their insurance) if they have no other appointments
                remaining = (
                    db.query(Appointment)
                    .filter(Appointment.patient_id == patient_id)
                    .count()
                )
                if remaining == 0:
                    db.query(PatientInsurance).filter(
                        PatientInsurance.patient_id == patient_id
                    ).delete()
                    patient = db.get(Patient, patient_id)
                    if patient:
                        db.delete(patient)

                removed += 1
                print(f"  removed duplicate for note: {note!r}")

        db.commit()
        print(f"\n[OK] Removed {removed} duplicate appointment(s). Dashboard should be clean now.")

    except Exception as exc:
        db.rollback()
        print(f"[ERROR] {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
