"""
HIPAA-hardening tests: PHI field encryption, audit logging, and log scrubbing.

conftest.py sets PHI_ENCRYPTION_KEY before any app import, so the Fernet
singleton is available for all tests in this file.

How to run:
    pytest tests/test_hipaa.py -v
"""
import logging
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.security.audit import AuditLog, log_phi_access
from app.security.encryption import EncryptedDate, EncryptedString, _get_fernet
from app.security.log_scrubber import PhiLogScrubber


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_local_session():
    """Fresh in-memory SQLite session with all tables created."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session(), engine


# ---------------------------------------------------------------------------
# EncryptedString
# ---------------------------------------------------------------------------

class TestEncryptedString:

    def test_encrypted_ciphertext_differs_from_plaintext(self):
        fernet = _get_fernet()
        ciphertext = fernet.encrypt(b"John Doe").decode()
        assert ciphertext != "John Doe"

    def test_decrypt_returns_original_value(self):
        fernet = _get_fernet()
        ciphertext = fernet.encrypt(b"John Doe").decode()
        assert fernet.decrypt(ciphertext.encode()).decode() == "John Doe"

    def test_different_calls_produce_different_ciphertext(self):
        fernet = _get_fernet()
        ct1 = fernet.encrypt(b"Alice").decode()
        ct2 = fernet.encrypt(b"Alice").decode()
        assert ct1 != ct2  # Fernet uses a fresh random IV each time

    def test_none_bind_stays_none(self):
        col = EncryptedString()
        assert col.process_bind_param(None, None) is None

    def test_none_result_stays_none(self):
        col = EncryptedString()
        assert col.process_result_value(None, None) is None

    def test_roundtrip_via_type_decorator(self):
        col = EncryptedString()
        bound = col.process_bind_param("Hello PHI", None)
        assert col.process_result_value(bound, None) == "Hello PHI"


# ---------------------------------------------------------------------------
# EncryptedDate
# ---------------------------------------------------------------------------

class TestEncryptedDate:

    def test_roundtrip_date(self):
        col = EncryptedDate()
        original = date(1985, 1, 15)
        bound = col.process_bind_param(original, None)
        assert col.process_result_value(bound, None) == original

    def test_encrypted_differs_from_iso_string(self):
        col = EncryptedDate()
        bound = col.process_bind_param(date(1985, 1, 15), None)
        assert bound != "1985-01-15"

    def test_none_bind_stays_none(self):
        col = EncryptedDate()
        assert col.process_bind_param(None, None) is None

    def test_none_result_stays_none(self):
        col = EncryptedDate()
        assert col.process_result_value(None, None) is None

    def test_returns_date_object_not_string(self):
        col = EncryptedDate()
        bound = col.process_bind_param(date(2000, 6, 15), None)
        result = col.process_result_value(bound, None)
        assert isinstance(result, date)


# ---------------------------------------------------------------------------
# PHI encrypted in the database (raw SQL inspection)
# ---------------------------------------------------------------------------

class TestPhiFieldsEncryptedInDb:

    def test_patient_first_name_not_stored_as_plaintext(self):
        from app.models import Patient
        session, _ = _make_local_session()
        try:
            patient = Patient(
                first_name="John",
                last_name="Doe",
                date_of_birth=date(1985, 1, 15),
                gender="M",
            )
            session.add(patient)
            session.commit()
            row = session.execute(
                text("SELECT first_name FROM patients LIMIT 1")
            ).fetchone()
            assert row[0] != "John"
        finally:
            session.close()

    def test_patient_dob_not_stored_as_iso_string(self):
        from app.models import Patient
        session, _ = _make_local_session()
        try:
            patient = Patient(
                first_name="Jane",
                last_name="Smith",
                date_of_birth=date(1990, 6, 1),
                gender="F",
            )
            session.add(patient)
            session.commit()
            row = session.execute(
                text("SELECT date_of_birth FROM patients LIMIT 1")
            ).fetchone()
            assert row[0] != "1990-06-01"
        finally:
            session.close()

    def test_patient_readable_after_orm_roundtrip(self):
        from app.models import Patient
        session, _ = _make_local_session()
        try:
            patient = Patient(
                first_name="Alice",
                last_name="Cooper",
                date_of_birth=date(1975, 3, 20),
                gender="F",
            )
            session.add(patient)
            session.commit()
            patient_id = patient.patient_id
            session.expire_all()
            fetched = session.get(Patient, patient_id)
            assert fetched.first_name == "Alice"
            assert fetched.last_name == "Cooper"
            assert fetched.date_of_birth == date(1975, 3, 20)
        finally:
            session.close()

    def test_member_id_not_stored_as_plaintext(self):
        from app.models import Patient, PatientInsurance, Payer
        session, _ = _make_local_session()
        try:
            payer = Payer(edi_payer_id="00050", payer_name="Blue Cross")
            patient = Patient(
                first_name="Bob",
                last_name="Jones",
                date_of_birth=date(1975, 3, 20),
                gender="M",
            )
            session.add_all([payer, patient])
            session.flush()
            insurance = PatientInsurance(
                patient_id=patient.patient_id,
                payer_id=payer.payer_id,
                member_id="MEM999",
                relationship_to_subscriber="self",
                coverage_type="primary",
            )
            session.add(insurance)
            session.commit()
            row = session.execute(
                text("SELECT member_id FROM patient_insurance LIMIT 1")
            ).fetchone()
            assert row[0] != "MEM999"
        finally:
            session.close()

    def test_member_id_readable_after_orm_roundtrip(self):
        from app.models import Patient, PatientInsurance, Payer
        session, _ = _make_local_session()
        try:
            payer = Payer(edi_payer_id="00051", payer_name="Aetna")
            patient = Patient(
                first_name="Carol",
                last_name="White",
                date_of_birth=date(1988, 11, 5),
                gender="F",
            )
            session.add_all([payer, patient])
            session.flush()
            insurance = PatientInsurance(
                patient_id=patient.patient_id,
                payer_id=payer.payer_id,
                member_id="MEM-XYZ-001",
                relationship_to_subscriber="self",
                coverage_type="primary",
            )
            session.add(insurance)
            session.commit()
            ins_id = insurance.insurance_id
            session.expire_all()
            fetched = session.get(PatientInsurance, ins_id)
            assert fetched.member_id == "MEM-XYZ-001"
        finally:
            session.close()


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class TestAuditLog:

    @pytest.fixture()
    def audit_db(self):
        session, engine = _make_local_session()
        try:
            yield session
        finally:
            session.close()
            Base.metadata.drop_all(bind=engine)

    def test_log_phi_access_creates_record(self, audit_db):
        log_phi_access(
            audit_db,
            event_type="read",
            resource_type="patient",
            resource_id=str(uuid.uuid4()),
            actor="staff@clinic.example",
        )
        assert audit_db.query(AuditLog).count() == 1

    def test_event_type_stored(self, audit_db):
        log_phi_access(audit_db, event_type="write", resource_type="appointment", actor="scheduler")
        assert audit_db.query(AuditLog).first().event_type == "write"

    def test_resource_type_stored(self, audit_db):
        log_phi_access(audit_db, event_type="read", resource_type="eligibility_result", actor="api")
        assert audit_db.query(AuditLog).first().resource_type == "eligibility_result"

    def test_resource_id_stored(self, audit_db):
        rid = str(uuid.uuid4())
        log_phi_access(audit_db, event_type="read", resource_type="patient", resource_id=rid, actor="api")
        assert audit_db.query(AuditLog).first().resource_id == rid

    def test_actor_stored(self, audit_db):
        log_phi_access(audit_db, event_type="read", resource_type="patient", actor="nurse@clinic.example")
        assert audit_db.query(AuditLog).first().actor == "nurse@clinic.example"

    def test_ip_address_stored(self, audit_db):
        log_phi_access(audit_db, event_type="read", resource_type="patient", actor="api", ip_address="10.0.0.1")
        assert audit_db.query(AuditLog).first().ip_address == "10.0.0.1"

    def test_created_at_is_set(self, audit_db):
        log_phi_access(audit_db, event_type="read", resource_type="patient", actor="api")
        assert audit_db.query(AuditLog).first().created_at is not None

    def test_detail_json_stored(self, audit_db):
        log_phi_access(
            audit_db,
            event_type="export",
            resource_type="patient",
            actor="admin",
            detail={"reason": "audit request", "record_count": 5},
        )
        entry = audit_db.query(AuditLog).first()
        assert entry.detail["reason"] == "audit request"
        assert entry.detail["record_count"] == 5

    def test_optional_fields_default_to_none(self, audit_db):
        log_phi_access(audit_db, event_type="read", resource_type="patient", actor="api")
        entry = audit_db.query(AuditLog).first()
        assert entry.resource_id is None
        assert entry.ip_address is None
        assert entry.detail is None

    def test_multiple_events_accumulate(self, audit_db):
        for i in range(3):
            log_phi_access(audit_db, event_type="read", resource_type="patient", actor=f"user{i}")
        assert audit_db.query(AuditLog).count() == 3


# ---------------------------------------------------------------------------
# Log scrubber
# ---------------------------------------------------------------------------

class TestPhiLogScrubber:

    def _record(self, msg: str) -> logging.LogRecord:
        return logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg=msg,
            args=(), exc_info=None,
        )

    def test_is_logging_filter_subclass(self):
        assert issubclass(PhiLogScrubber, logging.Filter)

    def test_filter_returns_true(self):
        record = self._record("Starting worker")
        assert PhiLogScrubber().filter(record) is True

    def test_ssn_redacted(self):
        record = self._record("SSN on file: 123-45-6789")
        PhiLogScrubber().filter(record)
        assert "123-45-6789" not in record.msg
        assert "REDACTED" in record.msg

    def test_dob_redacted(self):
        record = self._record("Patient DOB is 1985-01-15")
        PhiLogScrubber().filter(record)
        assert "1985-01-15" not in record.msg
        assert "REDACTED" in record.msg

    def test_member_id_redacted(self):
        record = self._record("Checking eligibility for member MEM001")
        PhiLogScrubber().filter(record)
        assert "MEM001" not in record.msg
        assert "REDACTED" in record.msg

    def test_email_redacted(self):
        record = self._record("Notification sent to patient@hospital.com")
        PhiLogScrubber().filter(record)
        assert "patient@hospital.com" not in record.msg
        assert "REDACTED" in record.msg

    def test_clean_message_unchanged(self):
        msg = "Starting eligibility check worker"
        record = self._record(msg)
        PhiLogScrubber().filter(record)
        assert record.msg == msg

    def test_multiple_patterns_in_one_message(self):
        record = self._record("DOB 1990-05-22 SSN 999-88-7777")
        PhiLogScrubber().filter(record)
        assert "1990-05-22" not in record.msg
        assert "999-88-7777" not in record.msg

    def test_args_tuple_scrubbed(self):
        record = self._record("Member %s has SSN %s")
        record.args = ("MEM123", "123-45-6789")
        PhiLogScrubber().filter(record)
        assert "MEM123" not in record.args
        assert "123-45-6789" not in record.args

    def test_args_dict_scrubbed(self):
        record = self._record("Event %(event)s")
        record.args = {"event": "member MEM999 checked"}
        PhiLogScrubber().filter(record)
        assert "MEM999" not in record.args["event"]
