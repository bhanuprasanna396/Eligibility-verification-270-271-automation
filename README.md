# Eligibility Verification System

A HIPAA-compliant tool that automates insurance eligibility checks for healthcare clinics. Staff click one button per appointment — the system sends a real EDI 270 inquiry to a clearinghouse, parses the 271 response, and surfaces any coverage issues on a live dashboard.

---

## Prerequisites

Make sure you have the following installed before getting started:

| Tool | Check if installed | Install |
|---|---|---|
| Docker Desktop | `docker --version` | [docker.com](https://www.docker.com/products/docker-desktop) |
| Python 3.11+ | `python3 --version` | [python.org](https://www.python.org/downloads) |
| WSL (Windows only) | Open "Ubuntu" from Start menu | Microsoft Store → search "WSL" |

---

## Setup

### 1. Get the project

```bash
git clone <repo-url>
cd eligibility-verification
```

On Windows (WSL), navigate to the folder:
```bash
cd /mnt/c/Users/<your-name>/Downloads/eligibility-verification
```

### 2. Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

You should see `(venv)` at the start of your terminal prompt.

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure your environment

```bash
cp .env.example .env
```

Generate an encryption key for patient data:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Open `.env` and paste the output as the value for `PHI_ENCRYPTION_KEY`. Leave everything else unchanged — the defaults match the Docker setup.

### 5. Start the database and cache

```bash
docker-compose up -d
```

Verify both containers are running:
```bash
docker ps
```
You should see `eligibility_db` (PostgreSQL) and `eligibility_redis` (Redis).

### 6. Run database migrations

```bash
alembic upgrade head
```

### 7. Load demo data

```bash
python scripts/seed_demo.py    # Creates 1 insurance company, 1 provider, 4 base patients
python scripts/add_scenarios.py # Adds 7 patients covering every coverage scenario
```

You'll end up with **11 appointments** ready to check.

### 8. Start the application

Open three terminals, each with `source venv/bin/activate`, then run:

**Terminal 1 — API server:**
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 2 — Background worker:**
```bash
celery -A app.workers.celery_app worker --loglevel=info
```

**Terminal 3 — (Optional) Scheduled auto-checks:**
```bash
celery -A app.workers.celery_app beat --loglevel=info
```

### 9. Open the dashboard

```
http://localhost:8000
```

You should see 11 appointments, all showing **PENDING**.

---

## Using the Dashboard

1. Click **Check** on any appointment row
2. Watch the status update within 2–3 seconds:
   - **VERIFIED** — coverage is clean, no action needed
   - **GAP FOUND** — a problem was detected, details appear in the right panel
   - **ERROR** — the clearinghouse was unreachable; the system will retry automatically
3. Click **Resolve** on any gap, enter your name and notes, then click **Mark Resolved**

To reset to a clean state at any time:
```bash
python scripts/hard_reset.py
```

---

## How It Works

```
Staff clicks "Check"
        │
        ▼
[FastAPI] POST /appointments/{id}/check
  → Creates an EligibilityCheck record (status: queued)
  → Pushes the task to Redis
  → Returns immediately (non-blocking)
        │
        ▼
[Celery Worker] picks up the task
  1. Reads patient, insurance, and provider from the database (decrypts PHI)
  2. Builds a real EDI X12 270 inquiry string
  3. Logs the raw 270 to edi_transaction_logs (HIPAA audit)
  4. Submits to the clearinghouse and receives a 271 response
  5. Logs the raw 271 to edi_transaction_logs
  6. Parses the 271 into structured coverage data
  7. Runs the GapAnalyzer against 7 rules:
       • Payer rejection (member not found)
       • Inactive coverage
       • Coverage date mismatch
       • Out of network
       • Prior authorization required
       • Referral required
       • High deductible remaining (Warning >$500 / High >$1,000)
  8. Saves results and any coverage gaps to the database
  9. Updates the appointment status to "verified" or "gap_found"
        │
        ▼
[Browser] polls GET /checks/{check_id} every 2 seconds
  → Refreshes the dashboard when the check completes
  → Polling resumes automatically after a page refresh
```

---

## Demo Scenarios

After running `hard_reset.py`, clicking **Check** on each appointment will produce:

| Patient | Member ID | Expected Status | Gap | Severity |
|---|---|---|---|---|
| John Doe | MEM-001-BASE | VERIFIED | — | — |
| Sarah Miller | MEM-002-BASE | VERIFIED | — | — |
| Robert Chen | MEM-003-BASE | VERIFIED | — | — |
| Linda Torres | MEM-004-BASE | VERIFIED | — | — |
| Emily Johnson | MEM-HMO-001 | GAP FOUND | High deductible ($1,500 remaining) | High |
| Marcus Williams | MEM-HDHP-001 | GAP FOUND | High deductible ($5,000 remaining) | High |
| Priya Patel | MEM-OON-001 | GAP FOUND | Out of network | High |
| David Kim | MEM-INACTIVE-001 | GAP FOUND | Inactive coverage | Critical |
| Ana Gonzalez | MEM-REJECT-001 | GAP FOUND | Payer rejection — member not found | Critical |
| Tom Baker | MEM-DOWN-001 | ERROR | Clearinghouse unreachable — retries at 60s, 120s, 240s | — |
| James Carter | MEM-001 | VERIFIED | — | — |

---

## Project Structure

```
eligibility-verification/
│
├── app/
│   ├── main.py                   # FastAPI app entry point
│   ├── config.py                 # Environment settings
│   ├── database.py               # SQLAlchemy engine and session factory
│   │
│   ├── api/                      # HTTP endpoints
│   │   ├── appointments.py       # Appointment listing and check trigger
│   │   ├── checks.py             # Polling endpoint
│   │   └── gaps.py               # Gap listing, resolution, and dashboard summary
│   │
│   ├── models/                   # Database models
│   │   ├── patient.py            # PHI fields encrypted at rest
│   │   ├── patient_insurance.py  # Insurance plan and member ID (encrypted)
│   │   ├── appointment.py        # Links patient, provider, and insurance
│   │   ├── eligibility.py        # Check, result, and gap records
│   │   ├── payer.py              # Insurance company
│   │   ├── provider.py           # Doctor or clinic
│   │   ├── edi_log.py            # Raw 270/271 strings for audit
│   │   └── types.py              # Custom encrypted SQLAlchemy column types
│   │
│   ├── schemas/                  # Pydantic request/response schemas
│   │
│   ├── edi/                      # EDI X12 engine
│   │   ├── builder_270.py        # Constructs standards-compliant 270 strings
│   │   ├── parser_271.py         # Parses 271 responses into structured data
│   │   ├── parsed_models.py      # ParsedEligibility dataclass
│   │   └── control_numbers.py    # Auto-incrementing ISA control numbers
│   │
│   ├── clearinghouse/
│   │   ├── base.py               # Abstract clearinghouse client interface
│   │   └── mock_client.py        # Full 7-scenario simulator for local development
│   │
│   ├── analyzers/
│   │   └── gap_analyzer.py       # 7 coverage gap rules with severity classification
│   │
│   ├── workers/
│   │   ├── celery_app.py         # Celery and Redis configuration
│   │   ├── eligibility_worker.py # Full pipeline: 270 → clearinghouse → 271 → gaps
│   │   └── appointment_watcher.py# Scheduled pre-visit eligibility checks
│   │
│   └── security/
│       ├── encryption.py         # Fernet AES-128 encryption helpers
│       ├── audit.py              # PHI access audit logging
│       └── log_scrubber.py       # Strips PHI from application logs
│
├── migrations/                   # Alembic schema migrations
├── tests/                        # Full pytest test suite
├── scripts/                      # Seed and reset utilities
├── static/index.html             # Single-page dashboard (vanilla JS)
├── docker-compose.yml
├── requirements.txt
└── .env
```

---

## Tech Stack

| Layer | Technology | Reason |
|---|---|---|
| API | FastAPI | Async, auto-docs, Pydantic validation |
| Database | PostgreSQL + SQLAlchemy 2.0 | ACID transactions, relational integrity |
| Migrations | Alembic | Version-controlled schema changes |
| Background jobs | Celery + Redis | Non-blocking, retryable task queue |
| PHI encryption | Python `cryptography` (Fernet) | AES-128-CBC + HMAC-SHA256 at rest |
| Frontend | Vanilla JS | Zero dependencies, instant load |
| Infrastructure | Docker Compose | Consistent local and production environments |
| Testing | pytest + pytest-cov | Full coverage of all critical paths |

---

## HIPAA Compliance

### Encryption at Rest
All Protected Health Information is encrypted in the database using Fernet (AES-128-CBC + HMAC-SHA256). The encrypted fields are:

| Table | Columns |
|---|---|
| `patients` | first_name, last_name, date_of_birth, phone, email, address |
| `patient_insurance` | member_id, subscriber name/DOB, effective and termination dates |

Without the `PHI_ENCRYPTION_KEY`, data in the database is unreadable.

### Audit Trail
Every PHI read or write is recorded in `audit_logs` with the actor, resource, action, and a timezone-aware timestamp.

### EDI Audit Log
Every outbound 270 and inbound 271 is stored verbatim in `edi_transaction_logs`, as required for HIPAA transaction record-keeping.

### Log Scrubbing
A custom `logging.Filter` redacts PHI patterns — SSNs, dates of birth, member IDs, and email addresses — from all application logs before they reach disk or a log aggregator.

### Retry Behavior
- Clearinghouse unreachable → retries up to 3 times with exponential backoff (60s, 120s, 240s)
- Payer rejection (member not found) → immediate failure, no retry — a staff member must correct the data

---

## Tests

```bash
pytest tests/ -v --cov=app --cov-report=term-missing
```

| File | What it covers |
|---|---|
| `test_builder_270.py` | 270 EDI string construction and segment ordering |
| `test_parser_271.py` | 271 parsing across all 6 response scenarios |
| `test_mock_clearinghouse.py` | Mock client returns the correct 271 per member ID |
| `test_gap_analyzer.py` | All 7 gap rules fire correctly with the right severity |
| `test_eligibility_worker.py` | Full pipeline from 270 build through gap save and status update |
| `test_api.py` | All REST endpoints — status codes, response shapes, edge cases |
| `test_ui.py` | Dashboard renders correct content for each appointment status |
| `test_hipaa.py` | Encryption round-trips, audit log creation, log scrubber redaction |

---

## Going to Production

The only change required is swapping the mock clearinghouse client for a real one. The `ClearinghouseClientBase` interface in `clearinghouse/base.py` is designed for this — implement it against Availity, Change Healthcare, or Waystar and point the worker at the new client. Everything else — encryption, audit logging, retries, gap detection — carries over unchanged.

---

## Roadmap

**Near-term**
- Enable Celery Beat to trigger `appointment_watcher.py` automatically 48 hours before each visit
- Add a "Check All" button to trigger all pending appointments in one click
- Email or SMS alerts when a critical gap is found

**Medium-term**
- Connect to a real clearinghouse via the existing `ClearinghouseClientBase` interface
- Handle secondary and tertiary insurance per patient
- Sync appointments from Epic, Athena, or Cerner via FHIR

**Long-term**
- Predict likely coverage gaps before a check is run, using historical data
- Push gap outcomes into the billing system to reduce claim denials
- Multi-tenant row-level isolation for a hosted clinic product

---

## Quick Reference

```bash
# Reset to clean demo state
python scripts/hard_reset.py

# Start infrastructure
docker-compose up -d

# Start API server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Start background worker
celery -A app.workers.celery_app worker --loglevel=info

# Run tests
pytest tests/ -v

# Generate a new encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
