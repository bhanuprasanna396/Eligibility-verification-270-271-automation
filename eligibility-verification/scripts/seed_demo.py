"""
Demo seed script — inserts realistic sample data so you can observe the
eligibility verification pipeline end-to-end via the dashboard.

What it creates
---------------
  1 payer   : Blue Cross Blue Shield (EDI payer 00050)
  1 provider: Dr. Jane Smith NPI 1234567890
  4 patients + insurance records + scheduled appointments:

  Patient            Member ID    Days ahead  Expected mock result
  ─────────────────  ───────────  ──────────  ────────────────────
  John Doe           MEM-001      1           Active PPO  → verified (small deductible, no gaps)
  Sarah Miller       MEM-002      2           Active PPO  → verified
  Robert Chen        MEM-003      3           Active PPO  → verified
  Linda Torres       MEM-004      5           Active PPO  → verified (outside 3-day window, watcher skips)

The mock clearinghouse returns an ACTIVE_PPO response for all unknown
member IDs.  After the worker processes each check the dashboard shows:
  eligibility_status = verified, no open gaps.

How to run
----------
  1. docker-compose up -d          (start postgres + redis)
  2. alembic upgrade head          (create tables)
  3. python scripts/seed_demo.py   (insert sample data)
  4. uvicorn app.main:app --reload (API + dashboard on :8000)
  5. celery -A app.workers.celery_app worker --loglevel=info
  6. Open http://localhost:8000 and click "Check" on any appointment
"""
import os
import sys

# ── Make sure the project root is on sys.path ──────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ── Load .env so config.py picks up POSTGRES_* / PHI_ENCRYPTION_KEY ───────
env_path = os.path.join(ROOT, ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

# ── Abort early if encryption key is missing ──────────────────────────────
if not os.environ.get("PHI_ENCRYPTION_KEY"):
    print(
        "\n[ERROR] PHI_ENCRYPTION_KEY is not set.\n"
        "Generate one and add it to .env:\n\n"
        "  python -c \"from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())\"\n"
    )
    sys.exit(1)

from datetime import date, datetime, timedelta, timezone

from app.database import create_db_session
from app.models import Appointment, Patient, PatientInsurance, Payer, Provider


def seed() -> None:
    db = create_db_session()
    try:
        # ── Check if data already exists ──────────────────────────────────
        if db.query(Payer).count() > 0:
            print("Database already has data. Drop and recreate tables to re-seed.")
            return

        # ── Payer ─────────────────────────────────────────────────────────
        payer = Payer(edi_payer_id="00050", payer_name="Blue Cross Blue Shield")
        db.add(payer)

        # ── Provider ──────────────────────────────────────────────────────
        provider = Provider(
            npi="1234567890",
            first_name="Jane",
            last_name="Smith",
            provider_type="individual",
        )
        db.add(provider)
        db.flush()

        # ── Patients + insurance + appointments ───────────────────────────
        now = datetime.now(timezone.utc)

        records = [
            dict(first_name="John",  last_name="Doe",    dob=date(1982, 3, 15), gender="M", member="MEM-001", days=1),
            dict(first_name="Sarah", last_name="Miller", dob=date(1975, 8, 22), gender="F", member="MEM-002", days=2),
            dict(first_name="Robert",last_name="Chen",   dob=date(1990,11,  5), gender="M", member="MEM-003", days=3),
            dict(first_name="Linda", last_name="Torres", dob=date(1968, 1, 30), gender="F", member="MEM-004", days=5),
        ]

        for r in records:
            patient = Patient(
                first_name=r["first_name"],
                last_name=r["last_name"],
                date_of_birth=r["dob"],
                gender=r["gender"],
            )
            db.add(patient)
            db.flush()

            insurance = PatientInsurance(
                patient_id=patient.patient_id,
                payer_id=payer.payer_id,
                member_id=r["member"],
                relationship_to_subscriber="self",
                coverage_type="primary",
            )
            db.add(insurance)
            db.flush()

            appointment = Appointment(
                patient_id=patient.patient_id,
                provider_id=provider.provider_id,
                insurance_id=insurance.insurance_id,
                appointment_datetime=now + timedelta(days=r["days"]),
                status="scheduled",
                eligibility_status="pending",
                service_type_code="30",
            )
            db.add(appointment)

        db.commit()
        print(
            "\n[OK] Seeded demo data:\n"
            "  1 payer   : Blue Cross Blue Shield\n"
            "  1 provider: Dr. Jane Smith\n"
            "  4 patients + insurance records + scheduled appointments\n"
            "\nNext steps:\n"
            "  uvicorn app.main:app --reload\n"
            "  celery -A app.workers.celery_app worker --loglevel=info\n"
            "  open http://localhost:8000\n"
        )

    except Exception as exc:
        db.rollback()
        print(f"[ERROR] Seed failed: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
