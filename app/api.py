"""HTTP endpoints."""

import logging
import os
import re
import secrets
import tempfile
import unicodedata
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security, status
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.config import settings
from app.database import get_db
from app.models import Certificate, CertificateStatus, GenerationJob, JobStatus
from app.schemas import (
    CertificateDetails,
    CertificateList,
    CertificateOut,
    CreateJobRequest,
    JobCreatedResponse,
    JobList,
    JobStatusResponse,
    JobSummary,
    RetryResponse,
    VerificationResponse,
)
from app.services import jobs as job_service
from app.services.worker import worker

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: str | None = Security(api_key_header)) -> None:
    """No-op unless API_KEY is configured. compare_digest avoids timing leaks."""
    if settings.api_key and not secrets.compare_digest(api_key or "", settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")


router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])
# Verification is meant for third parties (e.g. an employer checking a certificate).
public_router = APIRouter(prefix="/api/v1", tags=["verification"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_job_or_404(db: Session, job_id: str) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")
    return job


def _get_certificate_or_404(db: Session, certificate_id: str) -> Certificate:
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Certificate {certificate_id} not found")
    return cert


def _certificate_out(request: Request, cert: Certificate) -> CertificateOut:
    download_url = None
    if cert.status == CertificateStatus.GENERATED:
        download_url = str(request.url_for("download_certificate", certificate_id=cert.id))
    return CertificateOut(
        id=cert.id,
        job_id=cert.job_id,
        index=cert.row_index,
        recipient_name=cert.recipient_name,
        recipient_email=cert.recipient_email,
        status=cert.status,
        failure_stage=cert.failure_stage,
        error_message=cert.error_message,
        verification_code=cert.verification_code,
        generated_at=cert.generated_at,
        download_url=download_url,
    )


def _download_filename(cert: Certificate) -> str:
    ascii_name = unicodedata.normalize("NFKD", cert.recipient_name or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_name).strip("_") or "certificate"
    return f"{cert.row_index + 1:04d}_{slug}_{cert.verification_code}.pdf"


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


@router.post(
    "/jobs",
    response_model=JobCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["jobs"],
    summary="Submit a bulk certificate generation request",
)
def create_job(payload: CreateJobRequest, request: Request, db: Session = Depends(get_db)):
    job, row_errors = job_service.create_job(db, payload)
    if job.status == JobStatus.PENDING:
        # Generation happens on the worker pool; the client polls status_url.
        worker.submit(job.id)

    return JobCreatedResponse(
        job_id=job.id,
        status=job.status,
        total_recipients=job.total_recipients,
        accepted=job.total_recipients - len(row_errors),
        rejected=len(row_errors),
        validation_errors=row_errors,
        status_url=str(request.url_for("get_job", job_id=job.id)),
        certificates_url=str(request.url_for("list_job_certificates", job_id=job.id)),
    )


@router.get("/jobs", response_model=JobList, tags=["jobs"], summary="List jobs, newest first")
def list_jobs(
    request: Request,
    status_filter: JobStatus | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    conditions = [GenerationJob.status == status_filter] if status_filter else []
    total = db.scalar(select(func.count()).select_from(GenerationJob).where(*conditions))
    found = db.scalars(
        select(GenerationJob)
        .where(*conditions)
        .order_by(GenerationJob.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return JobList(
        total=total,
        limit=limit,
        offset=offset,
        items=[
            JobSummary(
                job_id=job.id,
                status=job.status,
                title=job.title,
                course_name=job.course_name,
                total_recipients=job.total_recipients,
                created_at=job.created_at,
                completed_at=job.completed_at,
                status_url=str(request.url_for("get_job", job_id=job.id)),
            )
            for job in found
        ],
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse, tags=["jobs"], summary="Job status and progress")
def get_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    progress = job_service.get_progress(db, job.id)
    download_url = None
    if job.status.is_finished and progress.generated > 0:
        download_url = str(request.url_for("download_job_certificates", job_id=job.id))

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        certificate=CertificateDetails(
            title=job.title, course_name=job.course_name, issuer=job.issuer, issue_date=job.issue_date
        ),
        progress=progress,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        certificates_url=str(request.url_for("list_job_certificates", job_id=job.id)),
        download_url=download_url,
    )


@router.post(
    "/jobs/{job_id}/retry",
    response_model=RetryResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["jobs"],
    summary="Retry certificates that failed during generation",
)
def retry_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    _get_job_or_404(db, job_id)
    try:
        retried = job_service.reset_failed_certificates(db, job_id)
    except job_service.JobConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    worker.submit(job_id)
    return RetryResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        retried=retried,
        status_url=str(request.url_for("get_job", job_id=job_id)),
    )


@router.get(
    "/jobs/{job_id}/certificates",
    response_model=CertificateList,
    tags=["certificates"],
    summary="List certificates of a job (filterable by status)",
)
def list_job_certificates(
    job_id: str,
    request: Request,
    status_filter: CertificateStatus | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    _get_job_or_404(db, job_id)
    conditions = [Certificate.job_id == job_id]
    if status_filter is not None:
        conditions.append(Certificate.status == status_filter)

    total = db.scalar(select(func.count()).select_from(Certificate).where(*conditions))
    certs = db.scalars(
        select(Certificate).where(*conditions).order_by(Certificate.row_index).limit(limit).offset(offset)
    ).all()
    return CertificateList(
        job_id=job_id,
        total=total,
        limit=limit,
        offset=offset,
        items=[_certificate_out(request, c) for c in certs],
    )


@router.get(
    "/jobs/{job_id}/download",
    tags=["certificates"],
    summary="Download all generated certificates of a finished job as a ZIP",
    response_class=FileResponse,
    responses={200: {"content": {"application/zip": {}}}},
)
def download_job_certificates(job_id: str, db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    if not job.status.is_finished:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Job is still {job.status.value}")

    certs = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.GENERATED)
        .order_by(Certificate.row_index)
    ).all()
    if not certs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job has no generated certificates")

    # Build the archive in a temp file (not memory) so large jobs don't blow up RAM;
    # it is deleted once the response has been sent.
    fd, zip_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for cert in certs:
                path = settings.storage_dir / cert.file_path
                if path.exists():
                    archive.write(path, arcname=_download_filename(cert))
                else:
                    logger.warning("Certificate %s file missing from storage: %s", cert.id, path)
    except Exception:
        os.unlink(zip_path)
        raise

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"certificates_{job_id}.zip",
        background=BackgroundTask(os.unlink, zip_path),
    )


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------


@router.get(
    "/certificates/{certificate_id}",
    response_model=CertificateOut,
    tags=["certificates"],
    summary="Certificate details",
)
def get_certificate(certificate_id: str, request: Request, db: Session = Depends(get_db)):
    return _certificate_out(request, _get_certificate_or_404(db, certificate_id))


@router.get(
    "/certificates/{certificate_id}/download",
    tags=["certificates"],
    summary="Download a single certificate PDF",
    response_class=FileResponse,
    responses={200: {"content": {"application/pdf": {}}}},
)
def download_certificate(certificate_id: str, db: Session = Depends(get_db)):
    cert = _get_certificate_or_404(db, certificate_id)
    if cert.status != CertificateStatus.GENERATED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Certificate is {cert.status.value}" + (f": {cert.error_message}" if cert.error_message else ""),
        )
    path = settings.storage_dir / cert.file_path
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Certificate file is missing from storage")
    return FileResponse(path, media_type="application/pdf", filename=_download_filename(cert))


@public_router.get(
    "/verify/{verification_code}",
    response_model=VerificationResponse,
    summary="Verify a certificate by the code printed on it",
)
def verify_certificate(verification_code: str, db: Session = Depends(get_db)):
    cert = db.scalar(
        select(Certificate).where(
            Certificate.verification_code == verification_code.strip().upper(),
            Certificate.status == CertificateStatus.GENERATED,
        )
    )
    if cert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No certificate with this verification code")
    job = cert.job
    return VerificationResponse(
        valid=True,
        verification_code=cert.verification_code,
        recipient_name=cert.recipient_name,
        course_name=job.course_name,
        issuer=job.issuer,
        issue_date=job.issue_date,
        certificate_id=cert.id,
    )
