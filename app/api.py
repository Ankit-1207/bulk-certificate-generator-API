"""HTTP endpoints."""

import io
import re
import unicodedata
import zipfile

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Certificate, CertificateStatus, GenerationJob, JobStatus
from app.schemas import (
    CertificateDetails,
    CertificateList,
    CertificateOut,
    CreateJobRequest,
    JobCreatedResponse,
    JobStatusResponse,
    VerificationResponse,
)
from app.services import jobs as job_service

router = APIRouter(prefix="/api/v1")


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
    summary="Submit a bulk certificate generation request",
)
def create_job(
    payload: CreateJobRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    job, row_errors = job_service.create_job(db, payload)
    if job.status == JobStatus.PENDING:
        # Generation runs after the response is sent; the client polls the status URL.
        background_tasks.add_task(job_service.process_job, job.id)

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


@router.get("/jobs/{job_id}", response_model=JobStatusResponse, summary="Job status and progress")
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


@router.get(
    "/jobs/{job_id}/certificates",
    response_model=CertificateList,
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
    summary="Download all generated certificates of a finished job as a ZIP",
    response_class=Response,
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

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for cert in certs:
            path = settings.storage_dir / cert.file_path
            if path.exists():
                archive.write(path, arcname=_download_filename(cert))

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="certificates_{job_id}.zip"'},
    )


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------


@router.get("/certificates/{certificate_id}", response_model=CertificateOut, summary="Certificate details")
def get_certificate(certificate_id: str, request: Request, db: Session = Depends(get_db)):
    return _certificate_out(request, _get_certificate_or_404(db, certificate_id))


@router.get(
    "/certificates/{certificate_id}/download",
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


@router.get(
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
