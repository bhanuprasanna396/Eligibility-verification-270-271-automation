"""
Adds one appointment per mock scenario so every gap type is visible
in the dashboard gaps panel.

Run AFTER seed_demo.py (payer + provider must already exist):
    python scripts/add_scenarios.py

What each scenario produces in the dashboard
─────────────────────────────────────────────
  Emily Johnson   MEM-HMO-001      → GAP: HIGH_DEDUCTIBLE  ($1500 remaining, high)
  Marcus Williams MEM-HDHP-001     → GAP: HIGH_DEDUCTIBLE  ($5000 remaining, high)
  Priya Patel     MEM-OON-001      → GAP: OUT_OF_NETWORK   (provider not in plan)
  David Kim       MEM-INACTIVE-001 → GAP: INACTIVE_COVERAGE (terminated)
  Ana Gonzalez    MEM-REJECT-001   → GAP: PAYER_REJECTION   (member not found)
  Tom Baker       MEM-DOWN-001     → CHECK FAILED           (clearinghouse down → retry)
  James Carter    MEM-001          → VERIFIED               (clean PPO, no gaps)
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

from datetime import date, datetime, timedelta, timezone

from app.database import create_db_session
from app.models import Appointment, Patient, PatientInsurance, Payer, Provider


SCENARIOS = [
    dict(first="Emily",   last="Johnson",  dob=date(1988,  4, 12), gender="F", member="MEM-HMO-001",      days=1,  note="HMO — high deductible gap"),
    dict(first="Marcus",  last="Williams", dob=date(1975,  9, 30), gender="M", member="MEM-HDHP-001",     days=2,  note="HDHP — $5000 deductible gap"),
    dict(first="Priya",   last="Patel",    dob=date(1992,  2, 18), gender="F", member="MEM-OON-001",      days=3,  note="Out-of-network gap"),
    dict(first="David",   last="Kim",      dob=date(1965, 11,  5), gender="M", member="MEM-INACTIVE-001", days=4,  note="Inactive coverage — critical gap"),
    dict(first="Ana",     last="Gonzalez", dob=date(1980,  7, 22), gender="F", member="MEM-REJECT-001",   days=5,  note="Payer rejection — member not found"),
    dict(first="Tom",     last="Baker",    dob=date(1970,  3, 14), gender="M", member="MEM-DOWN-001",     days=6,  note="Clearinghouse down — check will fail"),
    dict(first="James",   last="Carter",   dob=date(1995,  8,  8), gender="M", member="MEM-001",          days=7,  note="Clean PPO — verified, no gaps"),
]


def main() -> None:
    db = create_db_session()
    try:
        payer = db.query(Payer).first()
        provider = db.query(Provider).first()
        if not payer or not provider:
            print("[ERROR] Run seed_demo.py first to create the payer and provider.")
            return

        now = datetime.now(timezone.utc)
        added = 0

        for s in SCENARIOS:
            # Deduplicate on the appointment note — it's unencrypted and unique per scenario.
            # We cannot filter on member_id because Fernet uses random IVs, so every
            # encryption of the same plaintext produces a different ciphertext and the
            # SQLAlchemy WHERE clause would never match stored rows.
            existing_appt = (
                db.query(Appointment)
                .filter(Appointment.notes == s["note"])
                .first()
            )
            if existing_appt:
                print(f"  skip  {s['first']} {s['last']} ({s['member']}) — already exists")
                continue

            patient = Patient(
                first_name=s["first"],
                last_name=s["last"],
                date_of_birth=s["dob"],
                gender=s["gender"],
            )
            db.add(patient)
            db.flush()

            insurance = PatientInsurance(
                patient_id=patient.patient_id,
                payer_id=payer.payer_id,
                member_id=s["member"],
                relationship_to_subscriber="self",
                coverage_type="primary",
            )
            db.add(insurance)
            db.flush()

            appointment = Appointment(
                patient_id=patient.patient_id,
                provider_id=provider.provider_id,
                insurance_id=insurance.insurance_id,
                appointment_datetime=now + timedelta(days=s["days"]),
                status="scheduled",
                eligibility_status="pending",
                service_type_code="30",
                notes=s["note"],
            )
            db.add(appointment)
            print(f"  added {s['first']} {s['last']:<12} ({s['member']:<20}) → {s['note']}")
            added += 1

        db.commit()
        print(f"\n[OK] Added {added} appointments. Now click Check on each in the dashboard.")

    except Exception as exc:
        db.rollback()
        print(f"[ERROR] {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
