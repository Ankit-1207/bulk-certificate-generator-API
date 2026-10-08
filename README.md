# Bulk Certificate Generator

A FastAPI backend that accepts a list of recipients in **one request**, validates
each one, generates a PDF certificate per valid recipient from a predefined
template, and lets the client track progress and download the results
(individually or as a ZIP).

**Stack:** Python 3.11+ · FastAPI · SQLAlchemy 2 · SQLite (any SQLAlchemy-supported
relational DB via `DATABASE_URL`) · ReportLab (PDF) · pytest

---

## 1. Setup

```bash
cd bulk-certificate-generator
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
```

### Configuration (environment variables, all optional)

| Variable                 | Default                         | Purpose                                      |
|--------------------------|---------------------------------|----------------------------------------------|
| `DATABASE_URL`           | `sqlite:///./certificates.db`   | SQLAlchemy database URL                      |
| `STORAGE_DIR`            | `./storage`                     | Where generated PDFs are written             |
| `MAX_RECIPIENTS_PER_JOB` | `5000`                          | Max recipients accepted in one request       |
| `RESUME_JOBS_ON_STARTUP` | `true`                          | Re-run jobs interrupted by a server restart  |

Tables are created automatically on startup.

## 2. Run the application

```bash
uvicorn app.main:app --reload
```

- API: <http://127.0.0.1:8000>
- Interactive docs (Swagger): <http://127.0.0.1:8000/docs>

## 3. Run the tests

```bash
python -m pytest
```

Every test runs against its own temporary SQLite database and storage folder.

| File                        | Covers                                                                 |
|-----------------------------|------------------------------------------------------------------------|
| `test_create_job.py`        | Creating a job, defaults, a 200-recipient bulk job                     |
| `test_validation.py`        | Request-level 422s, row-level errors (missing/blank/duplicate/etc.)   |
| `test_generation.py`        | PDF content (via pypdf), long names, files on disk, no double processing |
| `test_job_status.py`        | 404, pending / partial / completed progress, resume after crash        |
| `test_failure_handling.py`  | One certificate failing doesn't stop others; all-fail; failure stages  |
| `test_retrieval.py`         | Listing, filtering, pagination, single PDF, ZIP, verification          |

---

## 4. Submitting a generation request

`POST /api/v1/jobs`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
     -H "Content-Type: application/json" \
     --data-binary @sample_request.json
```

Request body (`sample_request.json` is included):

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

`title` defaults to "Certificate of Completion" and `issue_date` to today.

Response — `202 Accepted` (generation continues in the background):

```json
{
  "job_id": "133afd3c-...",
  "status": "pending",
  "total_recipients": 3,
  "accepted": 2,
  "rejected": 1,
  "validation_errors": [
    {"index": 2, "errors": [{"field": "name", "message": "String should have at least 1 character"}]}
  ],
  "status_url": "http://127.0.0.1:8000/api/v1/jobs/133afd3c-...",
  "certificates_url": "http://127.0.0.1:8000/api/v1/jobs/133afd3c-.../certificates"
}
```

### Checking progress

`GET /api/v1/jobs/{job_id}`

```json
{
  "job_id": "133afd3c-...",
  "status": "completed_with_errors",
  "progress": {"total": 3, "pending": 0, "generated": 2, "failed": 1,
               "processed": 3, "percent_complete": 100.0},
  "error_message": null,
  "download_url": "http://127.0.0.1:8000/api/v1/jobs/133afd3c-.../download",
  "...": "..."
}
```

Job statuses: `pending` → `processing` → `completed` | `completed_with_errors` | `failed`.

## 5. Retrieving generated certificates

| Endpoint | Description |
|---|---|
| `GET /api/v1/jobs/{job_id}/certificates?status=generated&limit=100&offset=0` | Per-recipient results (input `index`, status, `failure_stage`, `error_message`, `download_url`). `status` filter: `pending`, `generated`, `failed`. |
| `GET /api/v1/certificates/{certificate_id}` | One certificate's details |
| `GET /api/v1/certificates/{certificate_id}/download` | The PDF (`409` if it failed or is still pending) |
| `GET /api/v1/jobs/{job_id}/download` | ZIP of all generated PDFs (`409` while the job is still running) |
| `GET /api/v1/verify/{verification_code}` | Verify a certificate by the ID printed on it |

```bash
curl -o certs.zip http://127.0.0.1:8000/api/v1/jobs/<job_id>/download
curl -OJ http://127.0.0.1:8000/api/v1/certificates/<certificate_id>/download
```

---

## 6. Design decisions

### Project layout

```
app/
  main.py              FastAPI app, startup (create tables, resume jobs)
  api.py               HTTP endpoints
  schemas.py           Pydantic request/response models + per-recipient validation
  models.py            SQLAlchemy models: GenerationJob, Certificate
  database.py          Engine / session factory
  config.py            Settings from env vars
  services/jobs.py     Job creation, processing, progress
  services/renderer.py The single certificate template (ReportLab → PDF)
tests/
```

The API layer is thin; business logic lives in `services/` so it can be tested
and reused without HTTP (and moved to a worker process later).

### Data model

- **`GenerationJob`** — one bulk request: the shared certificate info
  (title, course, issuer, date), status and timestamps.
- **`Certificate`** — one row **per input recipient, including invalid ones**,
  with its `row_index` in the request, status (`pending` / `generated` /
  `failed`), `failure_stage` (`validation` / `generation`), `error_message`,
  `verification_code`, and the PDF path (relative to `STORAGE_DIR`).

Progress counts are computed with a `GROUP BY` over certificate rows rather than
stored counters, so there is a single source of truth that cannot drift.

### Two levels of validation

1. **Request-level → `422`, nothing is created.** The request structure is wrong:
   missing/blank `course_name` or `issuer`, invalid date, empty recipient list,
   more than `MAX_RECIPIENTS_PER_JOB` recipients.
2. **Row-level → job is still created.** A bad recipient (missing/blank name,
   name with no letters, invalid email, duplicate email in the same request,
   non-object entry, characters the template font can't render) is stored as a
   `failed` certificate with `failure_stage = "validation"` and reported in the
   `validation_errors` of the response.

Why: in a list of 1,000 participants, one typo shouldn't force the client to
resubmit everything. Every input row is accounted for and mapped back by `index`.
If *no* recipient is valid, the job is created directly as `failed`.

### Processing: background, in-process

`POST /jobs` stores the job and returns `202` immediately; generation runs via
FastAPI `BackgroundTasks` after the response is sent, and the client polls
`GET /jobs/{id}`.

- Generating thousands of PDFs synchronously would hold the HTTP request open
  far longer than typical client/proxy timeouts.
- `BackgroundTasks` needs no extra infrastructure (no Redis/broker), which keeps
  the project easy to run and test.

**Failure isolation:** each certificate is rendered inside its own
`try/except` and committed individually. A failure marks only that certificate
`failed` (`failure_stage = "generation"`, exception message stored) and the
loop continues. Committing per certificate also makes progress visible live.

**Final status:** `completed` (no failures), `completed_with_errors` (some),
`failed` (none generated). An unexpected job-level crash (e.g. DB error) marks
the job `failed` with `error_message`.

**No double processing:** the worker claims a job with an atomic
`UPDATE ... WHERE status = 'pending'`; only the caller that changes the row
proceeds.

**Restart safety:** in-process tasks are lost if the server stops. On startup,
jobs still `pending`/`processing` are resumed; only certificates still `pending`
are generated, so finished work is never redone.

**Atomic files:** PDFs are written to a `.tmp` file and renamed, so a crash
can't leave a half-written certificate at its final path.

**Trade-off / scaling path:** a single process generates one job at a time per
background task. For high volume, `process_job(job_id)` can be called by a real
task queue (Celery / RQ / Dramatiq) and run on multiple workers — the API and
data model stay the same. SQLite would be swapped for PostgreSQL
(`DATABASE_URL`), and local storage for object storage (S3) with signed URLs.

### Certificate template

One predefined template in `services/renderer.py` (A4 landscape PDF via
ReportLab): title, recipient name, course, issue date, issuer and a unique
**verification code** (also checkable through `GET /api/v1/verify/{code}`).
Long names/courses are shrunk to fit inside the border.

ReportLab's built-in fonts support Latin characters (cp1252, e.g. "Renée",
"François"). Names outside that set (e.g. Chinese) are rejected at validation
time rather than printed as empty boxes. Supporting them would mean registering
a Unicode TTF font in the renderer.

### Optional extras included

- ZIP download of a whole job
- Certificate verification endpoint
- Filtering and pagination on certificate listings
- Resume of interrupted jobs on startup
- Duplicate email detection within a request
