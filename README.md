# Bulk Certificate Generator

[![tests](https://github.com/Ankit-1207/bulk-certificate-generator-API/actions/workflows/tests.yml/badge.svg)](https://github.com/Ankit-1207/bulk-certificate-generator-API/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.x-red)

A backend API that generates certificates **in bulk**. A client submits one request
with a list of recipients. The API validates every recipient, generates a PDF
certificate for each valid one in the background, reports progress, and serves
the results one at a time or as a single ZIP.

<p align="center">
  <img src="docs/sample-certificate.png" alt="Sample generated certificate" width="720">
</p>

---

## Contents

- [Features](#features)
- [Tech stack](#tech-stack)
- [Quick start](#quick-start)
- [Usage walkthrough](#usage-walkthrough)
- [API reference](#api-reference)
- [How it works](#how-it-works)
- [Design decisions](#design-decisions)
- [Testing](#testing)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [Limitations and next steps](#limitations-and-next-steps)

---

## Features

**Core requirements**
- **Bulk submission:** one `POST` request carries up to 5,000 recipients (configurable).
- **Validation in two layers:** a malformed request is rejected outright. A bad recipient row is reported but doesn't block the rest.
- **Background generation:** the API answers `202 Accepted` immediately, and a dedicated worker pool renders the PDFs.
- **Failure isolation:** each certificate is generated and committed on its own, so one failure never stops the batch.
- **Progress tracking:** live counts of pending, generated and failed certificates, plus a completion percentage.
- **Retrieval:** list results (filter by status, with paging), download a single PDF, or download a ZIP of the whole job.

**Extras**
- **Retry:** `POST /jobs/{id}/retry` regenerates only the certificates that failed during generation.
- **Verification:** every certificate carries a unique code that third parties can check through a public endpoint.
- **Restart recovery:** jobs interrupted by a shutdown are re-queued on startup without redoing finished work.
- **Optional API key:** set `API_KEY` to require an `X-API-Key` header.
- **Demo script:** an end-to-end demo script and CI on GitHub Actions.

## Tech stack

| Concern | Choice | Why |
|---|---|---|
| Web framework | **FastAPI** | Pydantic validation, automatic OpenAPI docs, simple dependency injection |
| Database | **SQLite** via **SQLAlchemy 2.0** | Relational and zero-setup. Switch to PostgreSQL by changing `DATABASE_URL` |
| PDF generation | **ReportLab** | Pure Python with no system dependencies (unlike HTML-to-PDF tools that need a browser or wkhtmltopdf) |
| Background work | `ThreadPoolExecutor` | Needs no broker, keeps the API responsive, easy to replace with Celery or RQ (see [design decisions](#design-decisions)) |
| Tests | **pytest**, FastAPI `TestClient`, **pypdf** | pypdf reads generated PDFs back to check their content |

---

## Quick start

Requires **Python 3.11+**.

```bash
git clone https://github.com/Ankit-1207/bulk-certificate-generator-API.git
cd bulk-certificate-generator-API

python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -r requirements.txt
```

**Run the API**

```bash
uvicorn app.main:app --reload
```

- Swagger UI: <http://127.0.0.1:8000/docs> (try every endpoint from the browser)
- Health check: <http://127.0.0.1:8000/health>

The SQLite database and the `storage/` folder are created automatically on first start.

**Run the tests**

```bash
python -m pytest
```

**Run the end-to-end demo** (with the server running, in a second terminal)

```bash
python scripts/demo.py
```

```text
Job f2586d75-...: 3 accepted, 2 rejected
  row 3: name: String should have at least 1 character
  row 4: email: is not a valid email address
  processing             4/5 (80.0%)
  completed_with_errors  5/5 (100.0%)

Results:
  [0] generated Alice Johnson                A0DD420F10BA
  [1] generated Bob Smith                    5D2FE3E0AD84
  [2] generated Renée Françoise Dubois       B1475BA2E6EB
  [3] failed    -                            name: String should have at least 1 character
  [4] failed    Bad Email                    email: is not a valid email address

Saved 3 certificate(s) to demo_output/certificates_f2586d75-....zip
```

---

## Usage walkthrough

> On Windows PowerShell, use `curl.exe` instead of `curl`.

### 1. Submit a generation request

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
     -H "Content-Type: application/json" \
     --data-binary @sample_request.json
```

```json
{
  "certificate": {
    "title": "Certificate of Completion",
    "course_name": "Backend Engineering with FastAPI",
    "issuer": "Acme Academy",
    "issue_date": "2026-10-01"
  },
  "recipients": [
    {"name": "Alice Johnson", "email": "alice@example.com"},
    {"name": "Bob Smith",     "email": "bob@example.com"},
    {"name": "",              "email": "missing-name@example.com"}
  ]
}
```

`title` defaults to *Certificate of Completion* and `issue_date` defaults to today.

**Response: `202 Accepted`**

```json
{
  "job_id": "f2586d75-65ec-42d1-b1d9-f1dab653fd64",
  "status": "pending",
  "total_recipients": 3,
  "accepted": 2,
  "rejected": 1,
  "validation_errors": [
    {"index": 2, "errors": [{"field": "name", "message": "String should have at least 1 character"}]}
  ],
  "status_url": "http://127.0.0.1:8000/api/v1/jobs/f2586d75-...",
  "certificates_url": "http://127.0.0.1:8000/api/v1/jobs/f2586d75-.../certificates"
}
```

`index` is the recipient's position in the submitted list, so the client can map
each error straight back to its input row.

### 2. Track progress

```bash
curl http://127.0.0.1:8000/api/v1/jobs/<job_id>
```

```json
{
  "job_id": "f2586d75-...",
  "status": "completed_with_errors",
  "progress": {"total": 3, "pending": 0, "generated": 2, "failed": 1,
               "processed": 3, "percent_complete": 100.0},
  "error_message": null,
  "created_at": "2026-10-08T09:27:41.120Z",
  "started_at": "2026-10-08T09:27:41.131Z",
  "completed_at": "2026-10-08T09:27:41.170Z",
  "download_url": "http://127.0.0.1:8000/api/v1/jobs/f2586d75-.../download"
}
```

### 3. See per-recipient results

```bash
curl "http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates?status=failed"
```

```json
{
  "job_id": "f2586d75-...",
  "total": 1, "limit": 100, "offset": 0,
  "items": [{
    "id": "8c1d...", "index": 2,
    "recipient_name": "", "recipient_email": "missing-name@example.com",
    "status": "failed", "failure_stage": "validation",
    "error_message": "name: String should have at least 1 character",
    "verification_code": null, "download_url": null
  }]
}
```

### 4. Download certificates

```bash
# every generated certificate of the job, as a ZIP
curl -o certificates.zip http://127.0.0.1:8000/api/v1/jobs/<job_id>/download

# a single certificate (use download_url from the listing)
curl -OJ http://127.0.0.1:8000/api/v1/certificates/<certificate_id>/download
```

ZIP entries are named `0001_Alice_Johnson_A0DD420F10BA.pdf`: input position,
recipient name, then verification code.

### 5. Retry and verify

```bash
# re-run certificates that failed during generation (e.g. a transient disk error)
curl -X POST http://127.0.0.1:8000/api/v1/jobs/<job_id>/retry

# anyone can check a certificate by the code printed on it
curl http://127.0.0.1:8000/api/v1/verify/A0DD420F10BA
```

---

## API reference

All endpoints are under `/api/v1`. Interactive docs are at `/docs`.

| Method | Endpoint | Description | Status codes |
|---|---|---|---|
| `POST` | `/jobs` | Submit a bulk generation request | `202`, `401`, `422` |
| `GET` | `/jobs` | List jobs, newest first (`?status=`, `limit`, `offset`) | `200` |
| `GET` | `/jobs/{job_id}` | Job status and progress | `200`, `404` |
| `POST` | `/jobs/{job_id}/retry` | Retry generation failures | `202`, `404`, `409` |
| `GET` | `/jobs/{job_id}/certificates` | Per-recipient results (`?status=pending\|generated\|failed`, `limit`, `offset`) | `200`, `404` |
| `GET` | `/jobs/{job_id}/download` | ZIP of all generated PDFs | `200`, `404`, `409` while running |
| `GET` | `/certificates/{id}` | One certificate's details | `200`, `404` |
| `GET` | `/certificates/{id}/download` | The PDF | `200`, `404`, `409` if failed or pending |
| `GET` | `/verify/{code}` | Verify a certificate (**always public**) | `200`, `404` |
| `GET` | `/health` | Liveness check, including the database | `200` |

### Job lifecycle

```mermaid
stateDiagram-v2
    [*] --> pending: POST /jobs (≥1 valid recipient)
    [*] --> failed: POST /jobs (no valid recipients)
    pending --> processing: worker claims job
    processing --> completed: every certificate generated
    processing --> completed_with_errors: some failed
    processing --> failed: none generated
    completed_with_errors --> pending: POST /retry
    failed --> pending: POST /retry
    processing --> pending: server restart (re-queued)
```

---

## How it works

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API (FastAPI)
    participant D as Database
    participant W as Worker pool
    participant S as File storage

    C->>A: POST /api/v1/jobs (N recipients)
    A->>A: validate request + each recipient
    A->>D: insert job + N certificate rows (valid → pending, invalid → failed)
    A->>W: submit(job_id)
    A-->>C: 202 Accepted {job_id, validation_errors}
    W->>D: claim job (pending → processing)
    loop each pending certificate
        W->>S: render PDF (temp file, then atomic rename)
        W->>D: mark generated / failed, commit
    end
    W->>D: set final job status
    C->>A: GET /jobs/{id}   (poll)
    A->>D: COUNT(*) GROUP BY status
    A-->>C: progress
    C->>A: GET /jobs/{id}/download
    A-->>C: ZIP of PDFs
```

### Data model

```mermaid
erDiagram
    GENERATION_JOB ||--o{ CERTIFICATE : contains
    GENERATION_JOB {
        string id PK
        string status
        string title
        string course_name
        string issuer
        date issue_date
        int total_recipients
        string error_message
        datetime created_at
        datetime started_at
        datetime completed_at
    }
    CERTIFICATE {
        string id PK
        string job_id FK
        int row_index
        string recipient_name
        string recipient_email
        json raw_input
        string status
        string failure_stage
        string error_message
        string verification_code UK
        string file_path
        datetime generated_at
    }
```

---

## Design decisions

### 1. Background processing on a dedicated worker pool

Generating thousands of PDFs inside the HTTP request would outlast typical client
and proxy timeouts, so `POST /jobs` stores the job, hands it to a worker, and
returns **`202 Accepted`**. The client then polls the status URL.

I used a dedicated, bounded **`ThreadPoolExecutor`** (`WORKER_THREADS`, default 2)
rather than FastAPI `BackgroundTasks`. Synchronous background tasks run on the
same thread pool that serves synchronous endpoints, so a few large jobs could
starve status requests. A separate pool keeps the API responsive and caps how
much generation runs at once. Extra jobs simply wait in the queue.

**Trade-off:** the queue lives in memory, so I treat the **database as the
durable record**. On startup, jobs left `processing` are reset to `pending` and
re-queued, and only certificates still `pending` are rendered, so finished work
is never redone. For production scale the next step is a real task queue
(Celery, RQ or Dramatiq) with multiple worker processes. `process_job(job_id)` is
already a self-contained function that takes only an ID, so it can be called
from a queue unchanged.

### 2. Every input row is tracked as a certificate

Each recipient, **including invalid ones**, becomes a row in `certificates` with
its original `row_index`. The job result therefore accounts for 100% of the
input, and the client can match every success and failure back to its row.

### 3. Two layers of validation

| Layer | Examples | Result |
|---|---|---|
| **Request** | missing `certificate` or `recipients`, blank `course_name` or `issuer`, invalid date, empty list, more than `MAX_RECIPIENTS_PER_JOB` | **`422`**, nothing is created |
| **Recipient row** | missing or blank name, name without letters, invalid email, duplicate email in the same request, non-object entry, characters the template font can't render | Job is created. The row is stored as `failed`, `failure_stage: "validation"`, and listed in `validation_errors` |

In a list of 1,000 participants, one typo shouldn't force the client to resubmit
everything. Valid rows go ahead, and the response says exactly what to fix.
Names and emails are normalised: whitespace is trimmed and emails are lowercased.

### 4. Failure isolation and two kinds of failure

Each certificate is rendered inside its own `try/except` and **committed
individually**:
- A failure marks only that certificate as `failed`, with `failure_stage: "generation"` and the error message. The loop carries on.
- Per-certificate commits also make progress visible while the job is running.

The final job status is `completed` (no failures), `completed_with_errors` (some
failed), or `failed` (nothing generated). An unexpected job-level crash, such as
the database going away, also marks the job `failed` and records an `error_message`.

`failure_stage` matters for **retry**. Generation failures (disk full, a renderer
bug) can be retried once the cause is fixed. Validation failures cannot, because
the input itself is wrong.

### 5. Concurrency safety

- **No double processing:** a worker claims a job with an atomic
  `UPDATE … SET status='processing' WHERE id=? AND status='pending'`. Only the
  caller that gets `rowcount == 1` proceeds, which guards against races between
  a retry and a restart.
- **Retry can't hit a running job:** the reset is likewise conditional on the
  job's status being finished.
- **Atomic files:** PDFs are written to a `.tmp` file and then `os.replace`d, so a
  crash never leaves a half-written certificate. The temp file is removed if
  rendering fails.
- **SQLite WAL mode:** status reads continue while the worker writes.

### 6. Progress is derived, not stored

Counts come from `SELECT status, COUNT(*) … GROUP BY status`, backed by a
composite `(job_id, status)` index, instead of counters on the job row. That
leaves a single source of truth that can't drift, and no counter updates to race on.

### 7. Certificate template

There is one predefined template in [`app/services/renderer.py`](app/services/renderer.py):
A4 landscape with title, recipient name, course, date, issuer and a unique
verification code. Long names and course titles are scaled down to fit inside
the border.

### 8. Smaller details

- **Timestamps** are always returned as timezone-aware UTC. A custom column type normalises them, because SQLite drops timezone information.
- **The job ZIP** is built in a temporary file, not in memory, and deleted after the response is sent.
- **File paths** in the database are relative to `STORAGE_DIR`, so the storage folder can be moved.
- **The API key** is compared with `secrets.compare_digest` to avoid timing attacks. `/verify` stays public because it is meant for third parties.

---

## Testing

```bash
python -m pytest            # 55 tests, ~6 s
```

Every test gets its own temporary SQLite database and storage folder. In most
tests the worker runs jobs synchronously so assertions are deterministic;
`test_worker.py` covers the real thread pool.

| File | What it covers |
|---|---|
| `test_create_job.py` | Creating a job, defaults, a 200-recipient bulk job, health check |
| `test_validation.py` | 422 for bad requests; per-row errors (missing, blank, bad email, duplicate, non-object, unrenderable), normalisation, all-invalid job |
| `test_generation.py` | PDF content checked with pypdf, long and accented names, one file per recipient, no double processing, no partial files after a crash |
| `test_job_status.py` | 404, pending/partial/completed progress, restart recovery without redoing work, UTC timestamps, job listing |
| `test_failure_handling.py` | One failure doesn't stop the rest; all-fail → `failed`; validation vs. generation failures |
| `test_retrieval.py` | Listing order, filter and pagination, single PDF, ZIP contents, 409 while pending, verification |
| `test_retry.py` | Retry fixes generation failures only, failed → completed, 409 and 404 cases |
| `test_worker.py` | Real background pool, concurrent jobs, pending jobs resumed on startup |
| `test_auth.py` | API key off, missing, wrong and correct; `/verify` stays public |

Failures are simulated by monkeypatching the renderer to raise for specific
recipients, which is exactly what a disk or rendering error looks like to the job.

---

## Configuration

All settings are optional environment variables.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./certificates.db` | Any SQLAlchemy URL, e.g. `postgresql+psycopg://user:pass@host/db` |
| `STORAGE_DIR` | `./storage` | Where PDFs are written |
| `MAX_RECIPIENTS_PER_JOB` | `5000` | Maximum recipients per request |
| `WORKER_THREADS` | `2` | Size of the generation worker pool |
| `RESUME_JOBS_ON_STARTUP` | `true` | Re-queue unfinished jobs on startup |
| `API_KEY` | *(unset)* | If set, `/api/v1/*` (except `/verify`) requires an `X-API-Key` header |

---

## Project structure

```text
bulk-certificate-generator/
├── app/
│   ├── main.py              # app factory, startup: create tables, start worker, re-queue jobs
│   ├── api.py               # HTTP endpoints (thin: parse → call service → shape response)
│   ├── schemas.py           # Pydantic request/response models + per-recipient validation
│   ├── models.py            # SQLAlchemy models: GenerationJob, Certificate
│   ├── database.py          # engine, session factory, SQLite pragmas
│   ├── config.py            # settings from environment variables
│   └── services/
│       ├── jobs.py          # create / process / retry jobs, progress
│       ├── renderer.py      # the certificate template (ReportLab → PDF)
│       └── worker.py        # bounded background worker pool
├── tests/                   # pytest suite (55 tests)
├── scripts/demo.py          # end-to-end demo client
├── docs/sample-certificate.png
├── sample_request.json
└── requirements.txt
```

Business logic lives in `services/` and doesn't depend on HTTP, so it is tested
directly and can move into a separate worker process without changes.

---

## Limitations and next steps

| Current | Production next step |
|---|---|
| In-process thread pool, single API process | Celery, RQ or Dramatiq with a broker and horizontally scaled workers |
| SQLite | PostgreSQL (just `DATABASE_URL`), plus Alembic migrations instead of `create_all` |
| Local disk storage | S3 or other object storage with pre-signed download URLs |
| Built-in PDF fonts (Latin/cp1252 only; other scripts are rejected at validation) | Register a Unicode TTF font such as Noto Sans to support every script |
| Single static API key | Per-client keys or OAuth, with jobs scoped to their owner |
| Client polls for status | Optional webhook `callback_url` called when a job finishes |
| JSON input only | CSV upload endpoint for recipient lists exported from spreadsheets |
| No idempotency | `Idempotency-Key` header so a retried `POST` doesn't create a duplicate job |
