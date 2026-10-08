"""ORM models.

A GenerationJob is one bulk request. Every recipient row in the request becomes a
Certificate row, including invalid ones, so the client can see exactly which rows
succeeded or failed and why.
"""

import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class JobStatus(str, enum.Enum):
    PENDING = "pending"  # accepted, waiting for the worker
    PROCESSING = "processing"  # worker is generating certificates
    COMPLETED = "completed"  # every certificate generated
    COMPLETED_WITH_ERRORS = "completed_with_errors"  # some generated, some failed
    FAILED = "failed"  # nothing could be generated

    @property
    def is_finished(self) -> bool:
        return self in {JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS, JobStatus.FAILED}


class CertificateStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATED = "generated"
    FAILED = "failed"


class FailureStage(str, enum.Enum):
    VALIDATION = "validation"  # recipient data was invalid; never attempted
    GENERATION = "generation"  # data was valid but rendering/storing the PDF failed


class GenerationJob(Base):
    __tablename__ = "generation_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, values_callable=lambda e: [m.value for m in e], native_enum=False),
        default=JobStatus.PENDING,
        index=True,
    )

    # Certificate information shared by every recipient in the job.
    title: Mapped[str] = mapped_column(String(80))
    course_name: Mapped[str] = mapped_column(String(120))
    issuer: Mapped[str] = mapped_column(String(100))
    issue_date: Mapped[date] = mapped_column(Date)

    total_recipients: Mapped[int] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)  # job-level crash

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Certificate.row_index"
    )


class Certificate(Base):
    __tablename__ = "certificates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="CASCADE"), index=True
    )
    # Position of the recipient in the submitted list, so failures map back to input rows.
    row_index: Mapped[int] = mapped_column(Integer)

    recipient_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_input: Mapped[dict | list | str | None] = mapped_column(JSON, nullable=True)

    status: Mapped[CertificateStatus] = mapped_column(
        Enum(CertificateStatus, values_callable=lambda e: [m.value for m in e], native_enum=False),
        default=CertificateStatus.PENDING,
        index=True,
    )
    failure_stage: Mapped[FailureStage | None] = mapped_column(
        Enum(FailureStage, values_callable=lambda e: [m.value for m in e], native_enum=False),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Printed on the certificate; lets a third party verify it via the API.
    verification_code: Mapped[str | None] = mapped_column(String(16), unique=True, nullable=True)
    # Relative to settings.storage_dir.
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[GenerationJob] = relationship(back_populates="certificates")
