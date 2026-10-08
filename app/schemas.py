"""Request / response schemas and recipient validation."""

import re
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.config import settings
from app.models import CertificateStatus, FailureStage, JobStatus

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def ensure_renderable(value: str) -> str:
    """The template uses the built-in PDF fonts (WinAnsi / cp1252 encoding).

    Characters outside that set would render as empty boxes, so reject them at
    validation time instead of silently producing a broken certificate.
    """
    try:
        value.encode("cp1252")
    except UnicodeEncodeError as exc:
        bad = value[exc.start : exc.end]
        raise ValueError(f"contains characters the certificate template cannot render: {bad!r}")
    return value


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class CertificateDetails(BaseModel):
    """Information shared by every certificate in the job."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field("Certificate of Completion", min_length=1, max_length=80)
    course_name: str = Field(..., min_length=1, max_length=120)
    issuer: str = Field(..., min_length=1, max_length=100)
    issue_date: date = Field(default_factory=date.today)

    @field_validator("title", "course_name", "issuer")
    @classmethod
    def _renderable(cls, value: str) -> str:
        return ensure_renderable(value)


class RecipientIn(BaseModel):
    """A single recipient. Validated row-by-row (see validate_recipient)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=254)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not any(ch.isalpha() for ch in value):
            raise ValueError("must contain at least one letter")
        return ensure_renderable(" ".join(value.split()))  # collapse inner whitespace

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        if not EMAIL_RE.match(value):
            raise ValueError("is not a valid email address")
        return value.lower()


class CreateJobRequest(BaseModel):
    certificate: CertificateDetails
    # Deliberately loose: each row is validated individually so that one bad
    # recipient does not reject the whole batch.
    recipients: list[Any] = Field(..., min_length=1)

    @field_validator("recipients")
    @classmethod
    def _limit_size(cls, value: list[Any]) -> list[Any]:
        limit = settings.max_recipients_per_job
        if len(value) > limit:
            raise ValueError(f"at most {limit} recipients are allowed per job, got {len(value)}")
        return value


class FieldError(BaseModel):
    field: str
    message: str


def validate_recipient(raw: Any) -> tuple[RecipientIn | None, list[FieldError]]:
    """Validate one recipient row. Returns (recipient, []) or (None, errors)."""
    if not isinstance(raw, dict):
        return None, [FieldError(field="recipient", message="must be a JSON object")]
    try:
        return RecipientIn.model_validate(raw), []
    except ValidationError as exc:
        errors = []
        for err in exc.errors():
            field = ".".join(str(part) for part in err["loc"]) or "recipient"
            message = err["msg"].removeprefix("Value error, ")
            errors.append(FieldError(field=field, message=message))
        return None, errors


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class RowValidationError(BaseModel):
    index: int
    errors: list[FieldError]


class Progress(BaseModel):
    total: int
    pending: int
    generated: int
    failed: int
    processed: int
    percent_complete: float


class JobCreatedResponse(BaseModel):
    job_id: str
    status: JobStatus
    total_recipients: int
    accepted: int
    rejected: int
    validation_errors: list[RowValidationError]
    status_url: str
    certificates_url: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    certificate: CertificateDetails
    progress: Progress
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    certificates_url: str
    download_url: str | None  # zip of all generated certificates, once the job is finished


class CertificateOut(BaseModel):
    id: str
    job_id: str
    index: int
    recipient_name: str | None
    recipient_email: str | None
    status: CertificateStatus
    failure_stage: FailureStage | None
    error_message: str | None
    verification_code: str | None
    generated_at: datetime | None
    download_url: str | None


class CertificateList(BaseModel):
    job_id: str
    total: int
    limit: int
    offset: int
    items: list[CertificateOut]


class VerificationResponse(BaseModel):
    valid: bool
    verification_code: str
    recipient_name: str
    course_name: str
    issuer: str
    issue_date: date
    certificate_id: str
