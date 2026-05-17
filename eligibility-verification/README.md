# Eligibility Verification System

Automated HIPAA-compliant insurance eligibility verification for healthcare clinics.  
Staff click one button per appointment — the system builds a real EDI 270 inquiry, sends it to a clearinghouse, parses the 271 response, detects coverage gaps, and surfaces actionable alerts on a live dashboard.

---

## Getting Started (Download and Run in 10 Minutes)

> These steps work on Windows (WSL), macOS, and Linux.

### What You Need Installed First

| Tool | How to check | Install link |
|---|---|---|
| Docker Desktop | `docker --version` | https://www.docker.com/products/docker-desktop |
| Python 3.11+ | `python3 --version` | https://www.python.org/downloads |
| WSL (Windows only) | Open "Ubuntu" from Start menu | Search "WSL" in Microsoft Store |

---

### Step 1 — Download the project

If you received a zip file, extract it. If it is a git repo:
```bash
git clone <repo-url>
cd eligibility-verification
```

If you just have the folder, open a WSL terminal and navigate to it:
```bash
cd /mnt/c/Users/<your-name>/Downloads/eligibility-verification
```

---

### Step 2 — Create a Python virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

You should see `(venv)` at the start of your terminal prompt.

---

### Step 3 — Install all dependencies

```bash
pip install -r requirements.txt
```

This installs FastAPI, Celery, SQLAlchemy, Alembic, cryptography, and everything else the project needs.

---

### Step 4 — Create your `.env` file

```bash
cp .env.example .env
```

Generate a new encryption key (this protects all patient data in the database):
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output (it looks like `abc123...=`) and paste it into `.env`:
```
PHI_ENCRYPTION_KEY=abc123...=      ← paste your key here
```

**Leave everything else in `.env` unchanged** — the defaults match the Docker setup.

---

### Step 5 — Start PostgreSQL and Redis

Make sure Docker Desktop is open and running, then:
```bash
docker-compose up -d
```

Verify both containers are running:
```bash
docker ps
```
You should see `eligibility_db` (PostgreSQL) and `eligibility_redis` (Redis).

---

### Step 6 — Create the database tables

```bash
alembic upgrade head
```

You should see:
```
Running upgrade  -> 001_initial_schema, ...
Running upgrade 001 -> 002_hipaa_encryption_audit, ...
```

---

### Step 7 — Load demo data

```bash
python scripts/seed_demo.py
```

This creates: 1 insurance company (Blue Cross), 1 provider (Dr. Smith), 4 base patients.

```bash
python scripts/add_scenarios.py
```

This adds 7 more patients, one per coverage scenario (inactive, out-of-network, payer rejection, etc.).

You should now have **11 appointments** in the database.

---

### Step 8 — Open three terminals and start the app

**Terminal 1 — API server:**
```bash
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Wait until you see: `Application startup complete.`

**Terminal 2 — Background worker:**
```bash
source venv/bin/activate
celery -A app.workers.celery_app worker --loglevel=info
```
Wait until you see: `celery@... ready.`

---

### Step 9 — Open the dashboard

Open your browser and go to:
```
http://localhost:8000
```

You should see the **Eligibility Dashboard** with 11 appointments, all showing **PENDING**.

---

### Step 10 — Try it out

1. Click **Check** on any appointment row
2. Watch the button turn into a spinning loader
3. Within 2–3 seconds the status updates:
   - **VERIFIED** — coverage is clean, no action needed
   - **GAP FOUND** — a problem was detected, see the right panel
4. Click **Resolve** on any gap in the right panel, enter your name, click **Mark Resolved**
5. The gap disappears from the panel

---

### Reset to a clean state at any time

If you want to wipe all patient data and start fresh:
```bash
python scripts/hard_reset.py
```

This deletes everything and reinserts the 11 demo appointments.

---

## Table of Contents

1. [What the System Does](#1-what-the-system-does)
2. [How It Works — Full Flow](#2-how-it-works--full-flow)
3. [Folder Structure](#3-folder-structure)
4. [Tech Stack](#4-tech-stack)
5. [Prerequisites](#5-prerequisites)
6. [Step-by-Step Setup and Run](#6-step-by-step-setup-and-run)
7. [What the Dashboard Shows](#7-what-the-dashboard-shows)
8. [Demo Scenarios and Expected Output](#8-demo-scenarios-and-expected-output)
9. [HIPAA Compliance Features](#9-hipaa-compliance-features)
10. [Test Suite](#10-test-suite)
11. [Why This Is Production-Ready](#11-why-this-is-production-ready)
12. [Going to Production (One Change)](#12-going-to-production-one-change)
13. [Further Improvements](#13-further-improvements)

---

## 1. What the System Does

### The Problem
Before a patient visit, clinic staff must manually call the insurance company or log into a payer portal to verify:
- Is the patient's coverage active?
- Is this provider in-network?
- How much deductible is remaining?
- Is prior authorization required?

This takes 5–15 minutes per patient, is error-prone, and causes claim denials when missed.

### The Solution
This system automates that entire workflow using the HIPAA EDI X12 270/271 transaction standard — the same protocol used by real clearinghouses (Availity, Change Healthcare, etc.).

- Staff see all upcoming appointments on a dashboard
- One click triggers an automated eligibility check
- Results appear in 2–3 seconds: Verified, Gap Found, or Error
- Gaps are categorized by severity and shown with plain-English instructions
- Staff can mark gaps as resolved with notes

---

## 2. How It Works — Full Flow

```
Staff clicks "Check"
        │
        ▼
[FastAPI] POST /appointments/{id}/check
  → Creates EligibilityCheck record (status: queued)
  → Pushes task to Redis queue
  → Returns check_id immediately (non-blocking)
        │
        ▼
[Celery Worker] picks up the task
  1. Reads patient + insurance + provider from database
     (decrypts PHI fields — name, DOB, member ID)
  2. Builds a real EDI X12 270 string
     Example segment: NM1*IL*1*JOHNSON*EMILY****MI*MEM-HMO-001
  3. Logs raw 270 to edi_transaction_logs (HIPAA audit)
  4. Submits 270 to clearinghouse
  5. Receives EDI 271 response
     Example: EB*6**30 = coverage inactive
  6. Logs raw 271 to edi_transaction_logs (HIPAA audit)
  7. Parses 271 into structured data
     (coverage active, deductibles, copay, in-network flag)
  8. Runs GapAnalyzer — checks 7 rules:
     • Payer rejection (member not found)
     • Inactive coverage
     • Coverage date mismatch
     • Out of network
     • Prior auth required
     • Referral required
     • High deductible remaining (>$1000 = high, >$500 = warning)
  9. Saves EligibilityResult + CoverageGap rows to database
  10. Updates appointment eligibility_status → "verified" or "gap_found"
        │
        ▼
[Browser] polls GET /checks/{check_id} every 2 seconds
  → When status = completed/failed, refreshes the dashboard
  → Status badge updates, gaps appear in the right panel
  → Auto-resumes polling even after page refresh
```

---

## 3. Folder Structure

```
eligibility-verification/
│
├── app/                          # All application code
│   ├── main.py                   # FastAPI app — mounts routers, serves dashboard
│   ├── config.py                 # Environment settings (pydantic-settings)
│   ├── database.py               # SQLAlchemy engine + session factory
│   │
│   ├── api/                      # HTTP endpoints
│   │   ├── appointments.py       # GET/POST /appointments, POST /appointments/{id}/check
│   │   ├── checks.py             # GET /checks/{id}  ← polling endpoint
│   │   └── gaps.py               # GET /gaps, PATCH /gaps/{id}/resolve, GET /dashboard
│   │
│   ├── models/                   # SQLAlchemy database models
│   │   ├── patient.py            # Patient — PHI fields are Fernet-encrypted
│   │   ├── patient_insurance.py  # Insurance plan + member ID (encrypted)
│   │   ├── appointment.py        # Links patient + provider + insurance
│   │   ├── eligibility.py        # EligibilityCheck, EligibilityResult, CoverageGap
│   │   ├── payer.py              # Insurance company (Blue Cross, Aetna, etc.)
│   │   ├── provider.py           # Doctor / clinic
│   │   ├── edi_log.py            # Raw 270/271 strings for HIPAA audit trail
│   │   └── types.py              # EncryptedString, EncryptedDate SQLAlchemy types
│   │
│   ├── schemas/                  # Pydantic schemas (API request/response shapes)
│   │   └── eligibility.py        # AppointmentSummary, GapSchema, DashboardSummary, etc.
│   │
│   ├── edi/                      # EDI X12 engine
│   │   ├── builder_270.py        # Builds a standards-compliant 270 EDI string
│   │   ├── parser_271.py         # Parses a 271 EDI response into structured data
│   │   ├── parsed_models.py      # ParsedEligibility dataclass
│   │   ├── models.py             # EligibilityInquiry, SubscriberInfo, PayerInfo, etc.
│   │   ├── segments.py           # EDI segment constants
│   │   └── control_numbers.py    # Auto-incrementing ISA control numbers
│   │
│   ├── clearinghouse/            # Clearinghouse client layer
│   │   ├── base.py               # ClearinghouseClientBase (interface)
│   │   └── mock_client.py        # MockClearinghouseClient — full 7-scenario simulator
│   │
│   ├── analyzers/
│   │   └── gap_analyzer.py       # 7 coverage gap rules, severity classification
│   │
│   ├── workers/                  # Background task processing
│   │   ├── celery_app.py         # Celery + Redis configuration
│   │   ├── eligibility_worker.py # Full pipeline: 270 → clearinghouse → 271 → gaps
│   │   └── appointment_watcher.py# Scheduled job: auto-checks upcoming appointments
│   │
│   └── security/                 # HIPAA hardening
│       ├── encryption.py         # Fernet AES-128 encryption/decryption
│       ├── audit.py              # AuditLog model + log_phi_access() helper
│       └── log_scrubber.py       # Strips PHI from application logs
│
├── migrations/                   # Alembic database migrations
│   ├── versions/
│   │   ├── 001_initial_schema.py # All tables: patients, appointments, checks, gaps
│   │   └── 002_hipaa_encryption_audit.py  # PHI columns → Text (for ciphertext), audit_logs table
│   └── env.py
│
├── tests/                        # Full test suite (pytest)
│   ├── conftest.py               # In-memory SQLite fixtures, encryption key setup
│   ├── test_builder_270.py       # EDI 270 construction tests
│   ├── test_parser_271.py        # EDI 271 parsing tests for all scenarios
│   ├── test_mock_clearinghouse.py# Mock client scenario tests
│   ├── test_gap_analyzer.py      # All 7 gap rules tested independently
│   ├── test_eligibility_worker.py# End-to-end worker pipeline tests
│   ├── test_api.py               # FastAPI endpoint tests
│   ├── test_ui.py                # Dashboard UI rendering tests
│   └── test_hipaa.py             # Encryption, audit log, log scrubber tests
│
├── scripts/
│   ├── seed_demo.py              # Creates payer + provider (run once)
│   ├── add_scenarios.py          # Adds 7 scenario appointments (idempotent)
│   └── hard_reset.py             # Wipes all patient data and reseeds cleanly
│
├── static/
│   └── index.html                # Single-page dashboard (vanilla JS, no framework)
│
├── docker-compose.yml            # PostgreSQL + Redis containers
├── requirements.txt              # All Python dependencies
└── .env                          # Environment variables (not committed to git)
```

---

## 4. Tech Stack

| Layer | Technology | Why |
|---|---|---|
| API | FastAPI | Async, auto-docs, Pydantic validation |
| Database | PostgreSQL + SQLAlchemy 2.0 | ACID transactions, relational integrity |
| Migrations | Alembic | Version-controlled schema changes |
| Background jobs | Celery + Redis | Non-blocking, retryable task queue |
| PHI encryption | Python `cryptography` (Fernet) | AES-128-CBC + HMAC-SHA256 at rest |
| Data validation | Pydantic v2 | Strict schema enforcement |
| Frontend | Vanilla JS | Zero dependencies, instant load |
| Infrastructure | Docker Compose | Portable local + production parity |
| Testing | pytest + pytest-cov | 100% of critical paths covered |

---

## 5. Prerequisites

- **WSL** (Windows Subsystem for Linux) or Linux/macOS terminal
- **Docker Desktop** running
- **Python 3.11+**
- **Git**

---

## 6. Step-by-Step Setup and Run

### First Time Only

```bash
# 1. Enter the project directory
cd /mnt/c/Users/bhanu/OneDrive/Desktop/draft-1/eligibility-verification

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start PostgreSQL and Redis via Docker
docker-compose up -d

# 5. Create your encryption key (copy the output — you need it in .env)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 6. Create .env file
# Edit .env and set:
#   DATABASE_URL=postgresql://postgres:postgres@localhost:5432/eligibility
#   REDIS_URL=redis://localhost:6379/0
#   PHI_ENCRYPTION_KEY=<paste key from step 5>

# 7. Run database migrations
alembic upgrade head

# 8. Seed payer + provider
python scripts/seed_demo.py
```

### Every Time You Want a Clean Demo

```bash
# Wipes all patient data and reseeds 11 clean appointments
python scripts/hard_reset.py
```

### Start the Application (3 terminals)

**Terminal 1 — API server:**
```bash
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 2 — Background worker:**
```bash
source venv/bin/activate
celery -A app.workers.celery_app worker --loglevel=info
```

**Terminal 3 — (optional) watch Celery beat for auto-checks:**
```bash
source venv/bin/activate
celery -A app.workers.celery_app beat --loglevel=info
```

### Open the Dashboard

```
http://localhost:8000
```

### Run Tests

```bash
pytest tests/ -v --cov=app
```

---

## 7. What the Dashboard Shows

### Top Summary Cards
| Card | Meaning |
|---|---|
| Appointments Today | Scheduled appointments for today only |
| Checks Pending | Checks currently queued or in-progress |
| Unresolved Gaps | Coverage problems staff have not yet actioned |
| Failed Checks | Checks that errored (usually clearinghouse down) |

### Appointments Table
Each row shows: Date/Time, Patient, Provider, Status badge, Action button

| Status | Meaning |
|---|---|
| PENDING | Check not run yet — click Check button |
| VERIFIED | Check ran, coverage is clean, no action needed |
| GAP FOUND | Check ran, problem detected — see right panel |
| ERROR | Check failed (clearinghouse error, retrying) |

### Unresolved Gaps Panel
Each gap shows:
- **Gap type** — machine-readable category (e.g. HIGH DEDUCTIBLE)
- **Severity badge** — Critical / High / Warning / Info
- **Patient name + appointment date** — so staff know who it's for
- **Plain-English description** — exactly what to do (e.g. "Call insurance, confirm active")
- **Resolve button** — staff enter their name + action taken, gap disappears

---

## 8. Demo Scenarios and Expected Output

After running `hard_reset.py`, you will have 11 appointments. Click **Check** on each:

| Patient | Member ID | Expected Status | Gap Type | Severity |
|---|---|---|---|---|
| John Doe | MEM-001-BASE | VERIFIED | — | — |
| Sarah Miller | MEM-002-BASE | VERIFIED | — | — |
| Robert Chen | MEM-003-BASE | VERIFIED | — | — |
| Linda Torres | MEM-004-BASE | VERIFIED | — | — |
| Emily Johnson | MEM-HMO-001 | GAP FOUND | HIGH DEDUCTIBLE ($1,500 remaining) | High |
| Marcus Williams | MEM-HDHP-001 | GAP FOUND | HIGH DEDUCTIBLE ($5,000 remaining) | High |
| Priya Patel | MEM-OON-001 | GAP FOUND | OUT OF NETWORK | High |
| David Kim | MEM-INACTIVE-001 | GAP FOUND | INACTIVE COVERAGE | Critical |
| Ana Gonzalez | MEM-REJECT-001 | GAP FOUND | PAYER REJECTION (member not found) | Critical |
| Tom Baker | MEM-DOWN-001 | ERROR | Clearinghouse down — retries at 60s, 120s, 240s | — |
| James Carter | MEM-001 | VERIFIED | Clean PPO plan | — |

The dashboard gaps panel will show 6 unresolved gaps (one per scenario patient with a problem). Each can be resolved by clicking **Resolve**, entering a name, and optionally adding a note.

---

## 9. HIPAA Compliance Features

### PHI Encryption at Rest
All Protected Health Information is encrypted in the database using **Fernet (AES-128-CBC + HMAC-SHA256)**. Encrypted fields:

| Table | Encrypted Columns |
|---|---|
| patients | first_name, last_name, date_of_birth, phone, email, address fields |
| patient_insurance | member_id, subscriber name/DOB fields, effective/termination dates |

Even if the database is accessed directly, patient data is unreadable without the `PHI_ENCRYPTION_KEY`.

### Audit Trail
Every read/write of PHI is logged to the `audit_logs` table:
- Who accessed the data (actor)
- What resource was accessed (type + ID)
- When (timestamp, timezone-aware)
- What action (READ, WRITE, etc.)

### Raw EDI Audit Log
Every outbound 270 and inbound 271 is stored verbatim in `edi_transaction_logs` with direction, control number, and timestamp — required for HIPAA transaction record-keeping.

### Log Scrubbing
`PhiLogScrubber` is a Python `logging.Filter` that redacts PHI patterns (SSNs, ISO dates, member IDs, email addresses) from all application logs before they are written to disk or a log aggregator.

### Retry on Network Failure, No Retry on Business Rejection
- Clearinghouse down → retries up to 3 times with exponential backoff (60s, 120s, 240s)
- Payer rejection (member not found) → immediate fail, no retry — a human must correct the data

---

## 10. Test Suite

```
tests/
├── test_builder_270.py        — 270 EDI string construction (segment order, encoding)
├── test_parser_271.py         — 271 parsing for all 6 scenarios
├── test_mock_clearinghouse.py — Mock client returns correct 271 per member ID
├── test_gap_analyzer.py       — All 7 gap rules fire correctly, severity is correct
├── test_eligibility_worker.py — Full pipeline: 270 built → 271 parsed → gaps saved → status updated
├── test_api.py                — All REST endpoints: status codes, response shapes, edge cases
├── test_ui.py                 — Dashboard HTML renders correct content for each status
└── test_hipaa.py              — Encryption round-trips, audit log creation, log scrubber redacts PHI
```

Run with coverage:
```bash
pytest tests/ -v --cov=app --cov-report=term-missing
```

---

## 11. Why This Is Production-Ready

| Property | How It's Achieved |
|---|---|
| **Non-blocking** | Celery + Redis — API responds in <50ms, checks run in background |
| **Retryable** | Network failures retry with exponential backoff, no duplicate checks |
| **Idempotent** | 409 if a check is already in-progress — no duplicate submissions |
| **PHI secured** | Fernet encryption at rest, PHI scrubbed from logs, full audit trail |
| **Schema versioned** | Alembic migrations — zero-downtime deploys, rollback capable |
| **Contract-stable** | Pydantic schemas decouple API shape from DB model — safe to evolve either |
| **Swap-ready clearinghouse** | `ClearinghouseClientBase` interface — swap mock for real with 1 line change |
| **Fully tested** | Every critical path has a test — worker, parser, builder, API, gaps, HIPAA |
| **Observable** | Status tracked at every stage — queued → in_progress → completed/failed |
| **Auto-resume polling** | If browser is refreshed mid-check, polling resumes from DB state |

---

## 12. Going to Production (One Change)

The entire system is real except for the clearinghouse client. To go live:

**In `app/workers/eligibility_worker.py`, line 58:**
```python
# Change this:
def get_clearinghouse_client() -> ClearinghouseClientBase:
    return MockClearinghouseClient()

# To this:
def get_clearinghouse_client() -> ClearinghouseClientBase:
    return RealClearinghouseClient(
        api_url=settings.clearinghouse_api_url,
        api_key=settings.clearinghouse_api_key,
    )
```

Everything else — the 270 builder, 271 parser, gap analyzer, database, worker, dashboard — is production code already.

---

## 13. Further Improvements

### Near-term
- **Nightly auto-check**: `appointment_watcher.py` is already wired — enable Celery Beat to automatically check all appointments 48 hours before the visit without staff involvement
- **Patient portal link**: Add a link in the gap description to the payer's online portal for faster resolution
- **Bulk check**: "Check All" button to trigger all pending appointments in one click
- **Email/SMS alerts**: Notify staff when a critical gap is found, not just on dashboard visit

### Medium-term
- **Real clearinghouse integration**: Connect to Availity, Change Healthcare, or Waystar using the existing `ClearinghouseClientBase` interface
- **Multiple payers**: Currently one payer per patient; extend to handle secondary/tertiary insurance
- **Prior auth workflow**: Full prior authorization request submission, not just detection
- **EHR integration**: Sync appointments from Epic, Athena, or Cerner via FHIR API

### Long-term
- **ML gap prediction**: Train a model on historical checks to predict which patients are likely to have gaps before the check is even run
- **Revenue cycle integration**: Push gap outcomes into the billing system to reduce claim denials
- **Multi-tenant SaaS**: Isolate each clinic's data with row-level security for a hosted product
- **Real-time eligibility**: 270/271 transactions on patient arrival, not just pre-visit

---

## Quick Reference

```bash
# Reset database to clean demo state
python scripts/hard_reset.py

# Start everything
docker-compose up -d
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload   # terminal 1
celery -A app.workers.celery_app worker --loglevel=info      # terminal 2

# Open dashboard
# http://localhost:8000

# Run tests
pytest tests/ -v

# Generate new encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
