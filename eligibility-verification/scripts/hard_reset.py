"""
Wipes ALL patient/appointment/check/gap/result data and reseeds from scratch.
Keeps payer row. Recreates all providers fresh.

    python scripts/hard_reset.py
"""
import os, sys

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
    print("[ERROR] PHI_ENCRYPTION_KEY not set.")
    sys.exit(1)

from datetime import date, datetime, timedelta, timezone
from sqlalchemy import text
from app.database import create_db_session
from app.models import Appointment, Patient, PatientInsurance, Payer, Provider

# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
PROVIDERS = [
    dict(npi="1234567890", first="Jane",    last="Smith",   specialty="Family Medicine"),
    dict(npi="2345678901", first="Michael", last="Patel",   specialty="Cardiology"),
    dict(npi="3456789012", first="Sarah",   last="Lee",     specialty="Orthopedics"),
    dict(npi="4567890123", first="James",   last="Wilson",  specialty="Psychiatry"),
]

# provider_npi identifies which provider each patient is assigned to
BASE_PATIENTS = [
    dict(first="John",   last="Doe",    dob=date(1982,  3, 15), gender="M",
         member="MEM-001-BASE", days=1, npi="1234567890",
         appt_type="Annual Physical",         service_code="98"),
    dict(first="Sarah",  last="Miller", dob=date(1975,  8, 22), gender="F",
         member="MEM-002-BASE", days=2, npi="2345678901",
         appt_type="Cardiology Consultation", service_code="3"),
    dict(first="Robert", last="Chen",   dob=date(1990, 11,  5), gender="M",
         member="MEM-003-BASE", days=3, npi="3456789012",
         appt_type="Orthopedic Consultation", service_code="3"),
    dict(first="Linda",  last="Torres", dob=date(1968,  1, 30), gender="F",
         member="MEM-004-BASE", days=4, npi="1234567890",
         appt_type="Diagnostic Lab Work",     service_code="5"),
]

SCENARIOS = [
    dict(first="Emily",  last="Johnson",  dob=date(1988,  4, 12), gender="F",
         member="MEM-HMO-001",      days=1, npi="1234567890",
         appt_type="Office Visit",              service_code="98",
         note="HMO — high deductible gap"),
    dict(first="Marcus", last="Williams", dob=date(1975,  9, 30), gender="M",
         member="MEM-HDHP-001",     days=2, npi="2345678901",
         appt_type="Cardiac Stress Test",       service_code="1",
         note="HDHP — $5000 deductible gap"),
    dict(first="Priya",  last="Patel",    dob=date(1992,  2, 18), gender="F",
         member="MEM-OON-001",      days=3, npi="3456789012",
         appt_type="Joint Replacement Consult", service_code="2",
         note="Out-of-network gap"),
    dict(first="David",  last="Kim",      dob=date(1965, 11,  5), gender="M",
         member="MEM-INACTIVE-001", days=4, npi="4567890123",
         appt_type="Psychiatric Evaluation",    service_code="MH",
         note="Inactive coverage — critical gap"),
    dict(first="Ana",    last="Gonzalez", dob=date(1980,  7, 22), gender="F",
         member="MEM-REJECT-001",   days=5, npi="1234567890",
         appt_type="Preventive Care",           service_code="98",
         note="Payer rejection — member not found"),
    dict(first="Tom",    last="Baker",    dob=date(1970,  3, 14), gender="M",
         member="MEM-DOWN-001",     days=6, npi="2345678901",
         appt_type="Echocardiogram",            service_code="1",
         note="Clearinghouse down — check will fail"),
    dict(first="James",  last="Carter",   dob=date(1995,  8,  8), gender="M",
         member="MEM-001",          days=7, npi="1234567890",
         appt_type="Follow-up Visit",           service_code="98",
         note="Clean PPO — verified, no gaps"),
]


def main():
    db = create_db_session()
    try:
        # ── 1. Wipe everything ───────────────────────────────────────────────
        print("Wiping all data…")
        db.execute(text(
            "TRUNCATE TABLE coverage_gaps, eligibility_results, eligibility_checks, "
            "appointments, patient_insurance, patients, providers "
            "RESTART IDENTITY CASCADE"
        ))
        db.commit()
        print("  done")

        # ── 2. Ensure payer exists (kept across resets) ──────────────────────
        payer = db.query(Payer).first()
        if not payer:
            payer = Payer(edi_payer_id="00050", payer_name="Blue Cross Blue Shield")
            db.add(payer)
            db.commit()
            print("  created payer: Blue Cross Blue Shield")

        # ── 3. Create providers and COMMIT so we can query them back by NPI ──
        print("\nCreating providers…")
        for p in PROVIDERS:
            db.add(Provider(
                npi=p["npi"],
                first_name=p["first"],
                last_name=p["last"],
                taxonomy_code=p["specialty"],
                provider_type="individual",
            ))
        db.commit()

        # Query back by NPI so we have guaranteed DB-backed IDs
        provider_by_npi = {
            prov.npi: prov
            for prov in db.query(Provider).all()
        }
        for p in PROVIDERS:
            prov = provider_by_npi[p["npi"]]
            print(f"  + Dr. {p['first']} {p['last']} — {p['specialty']}  (id: {str(prov.provider_id)[:8]}…)")

        # ── 4. Insert patients + appointments ────────────────────────────────
        now = datetime.now(timezone.utc)

        def add_patient(rec):
            provider = provider_by_npi[rec["npi"]]
            patient = Patient(
                first_name=rec["first"], last_name=rec["last"],
                date_of_birth=rec["dob"], gender=rec["gender"],
            )
            db.add(patient)
            db.flush()

            ins = PatientInsurance(
                patient_id=patient.patient_id, payer_id=payer.payer_id,
                member_id=rec["member"], relationship_to_subscriber="self",
                coverage_type="primary",
            )
            db.add(ins)
            db.flush()

            db.add(Appointment(
                patient_id=patient.patient_id,
                provider_id=provider.provider_id,
                insurance_id=ins.insurance_id,
                appointment_datetime=now + timedelta(days=rec["days"]),
                status="scheduled",
                eligibility_status="pending",
                appointment_type=rec["appt_type"],
                service_type_code=rec["service_code"],
                notes=rec.get("note"),
            ))
            prov_label = f"Dr. {provider.last_name} ({provider.taxonomy_code})"
            print(f"  + {rec['first']} {rec['last']:12} → {prov_label:35} | {rec['appt_type']}")

        print("\nAdding base patients (→ VERIFIED after Check)…")
        for rec in BASE_PATIENTS:
            add_patient(rec)

        print("\nAdding scenario patients…")
        for rec in SCENARIOS:
            add_patient(rec)

        db.commit()
        print("\n[OK] 11 clean appointments ready. Open localhost:8000 and click Check on each.")

    except Exception as exc:
        db.rollback()
        print(f"\n[ERROR] {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
