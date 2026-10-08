"""Job lifecycle: create a job from a request, process it, report progress."""

import logging
import secrets
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import (
    Certificate,
    CertificateStatus,
    FailureStage,
    GenerationJob,
    JobStatus,
    utcnow,
)
from app.schemas import CreateJobRequest, FieldError, Progress, RowValidationError, validate_recipient
from app.services import renderer

logger = logging.getLogger(__name__)


def _verification_code() -> str:
    return secrets.token_hex(6).upper()


def _short_repr(value, limit: int = 255) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text[:limit]


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def create_job(db: Session, request: CreateJobRequest) -> tuple[GenerationJob, list[RowValidationError]]:
    """Persist a job plus one Certificate row per recipient.

    Valid recipients start as PENDING. Invalid ones are stored immediately as
    FAILED (stage=validation) so the job result accounts for every input row.
    """
    details = request.certificate
    job = GenerationJob(
        title=details.title,
        course_name=details.course_name,
        issuer=details.issuer,
        issue_date=details.issue_date,
        total_recipients=len(request.recipients),
    )

    row_errors: list[RowValidationError] = []
    seen_emails: set[str] = set()

    for index, raw in enumerate(request.recipients):
        recipient, errors = validate_recipient(raw)

        if recipient is not None and recipient.email in seen_emails:
            recipient = None
            errors = [FieldError(field="email", message="duplicate email in this request")]

        cert = Certificate(row_index=index, raw_input=raw)
        if recipient is None:
            raw_dict = raw if isinstance(raw, dict) else {}
            cert.recipient_name = _short_repr(raw_dict.get("name"))
            cert.recipient_email = _short_repr(raw_dict.get("email"))
            cert.status = CertificateStatus.FAILED
            cert.failure_stage = FailureStage.VALIDATION
            cert.error_message = "; ".join(f"{e.field}: {e.message}" for e in errors)
            row_errors.append(RowValidationError(index=index, errors=errors))
        else:
            seen_emails.add(recipient.email)
            cert.recipient_name = recipient.name
            cert.recipient_email = recipient.email
            cert.status = CertificateStatus.PENDING
            cert.verification_code = _verification_code()
        job.certificates.append(cert)

    if len(row_errors) == job.total_recipients:
        # Nothing to generate; finish immediately instead of queueing an empty job.
        job.status = JobStatus.FAILED
        job.error_message = "No valid recipients in request"
        job.completed_at = utcnow()

    db.add(job)
    db.commit()
    return job, row_errors


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


def certificate_relative_path(cert: Certificate) -> Path:
    return Path(cert.job_id) / f"{cert.id}.pdf"


def _claim_job(db: Session, job_id: str, resume: bool) -> bool:
    """Atomically move the job into PROCESSING so it is never processed twice.

    resume=True also accepts a job already in PROCESSING (used on startup to pick
    up work interrupted by a restart).
    """
    allowed = [JobStatus.PENDING, JobStatus.PROCESSING] if resume else [JobStatus.PENDING]
    result = db.execute(
        update(GenerationJob)
        .where(GenerationJob.id == job_id, GenerationJob.status.in_(allowed))
        .values(status=JobStatus.PROCESSING, started_at=func.coalesce(GenerationJob.started_at, utcnow()))
    )
    db.commit()
    return result.rowcount == 1


def _generate_one(job: GenerationJob, cert: Certificate) -> None:
    relative = certificate_relative_path(cert)
    renderer.render_certificate(
        renderer.CertificateData(
            recipient_name=cert.recipient_name,
            title=job.title,
            course_name=job.course_name,
            issuer=job.issuer,
            issue_date=job.issue_date,
            verification_code=cert.verification_code,
        ),
        settings.storage_dir / relative,
    )
    cert.file_path = relative.as_posix()


def process_job(job_id: str, resume: bool = False) -> None:
    """Generate every pending certificate of a job.

    Each certificate is generated and committed independently: an exception for
    one recipient marks only that certificate as failed, and progress is visible
    to status requests while the job is running.
    """
    with SessionLocal() as db:
        if not _claim_job(db, job_id, resume):
            logger.info("Job %s not claimable (missing or already handled)", job_id)
            return

        job = db.get(GenerationJob, job_id)
        try:
            pending = db.scalars(
                select(Certificate)
                .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING)
                .order_by(Certificate.row_index)
            ).all()

            for cert in pending:
                try:
                    _generate_one(job, cert)
                    cert.status = CertificateStatus.GENERATED
                    cert.generated_at = utcnow()
                except Exception as exc:  # noqa: BLE001 - isolate per-certificate failures
                    logger.exception("Certificate %s (job %s) failed", cert.id, job_id)
                    cert.status = CertificateStatus.FAILED
                    cert.failure_stage = FailureStage.GENERATION
                    cert.error_message = f"{type(exc).__name__}: {exc}"[:1000]
                db.commit()

            _finalize(db, job)
        except Exception as exc:  # noqa: BLE001 - infrastructure failure (DB, disk...)
            logger.exception("Job %s crashed", job_id)
            db.rollback()
            job = db.get(GenerationJob, job_id)
            job.status = JobStatus.FAILED
            job.error_message = f"Job processing crashed: {type(exc).__name__}: {exc}"[:1000]
            job.completed_at = utcnow()
            db.commit()


def _finalize(db: Session, job: GenerationJob) -> None:
    progress = get_progress(db, job.id)
    if progress.failed == 0:
        job.status = JobStatus.COMPLETED
    elif progress.generated == 0:
        job.status = JobStatus.FAILED
        job.error_message = "All certificates failed"
    else:
        job.status = JobStatus.COMPLETED_WITH_ERRORS
    job.completed_at = utcnow()
    db.commit()


def resume_unfinished_jobs() -> None:
    """Called on startup: finish jobs left pending/processing by a previous run."""
    with SessionLocal() as db:
        job_ids = db.scalars(
            select(GenerationJob.id).where(
                GenerationJob.status.in_([JobStatus.PENDING, JobStatus.PROCESSING])
            )
        ).all()
    for job_id in job_ids:
        logger.info("Resuming unfinished job %s", job_id)
        process_job(job_id, resume=True)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def get_progress(db: Session, job_id: str) -> Progress:
    """Counts are derived from certificate rows (single source of truth, no counters to drift)."""
    counts = dict(
        db.execute(
            select(Certificate.status, func.count())
            .where(Certificate.job_id == job_id)
            .group_by(Certificate.status)
        ).all()
    )
    pending = counts.get(CertificateStatus.PENDING, 0)
    generated = counts.get(CertificateStatus.GENERATED, 0)
    failed = counts.get(CertificateStatus.FAILED, 0)
    total = pending + generated + failed
    processed = generated + failed
    return Progress(
        total=total,
        pending=pending,
        generated=generated,
        failed=failed,
        processed=processed,
        percent_complete=round(processed / total * 100, 2) if total else 100.0,
    )
