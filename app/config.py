"""Application settings, read from environment variables with sensible defaults."""

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


@dataclass
class Settings:
    # Any SQLAlchemy URL works, e.g. postgresql+psycopg://user:pass@host/db
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'certificates.db'}")
    )
    # Where generated PDF files are written. Paths stored in the DB are relative to this.
    storage_dir: Path = field(
        default_factory=lambda: Path(os.getenv("STORAGE_DIR", str(BASE_DIR / "storage")))
    )
    # Upper bound on recipients per request, protects the API from oversized payloads.
    max_recipients_per_job: int = field(
        default_factory=lambda: int(os.getenv("MAX_RECIPIENTS_PER_JOB", "5000"))
    )
    # Size of the dedicated worker pool that generates certificates.
    worker_threads: int = field(default_factory=lambda: int(os.getenv("WORKER_THREADS", "2")))
    # Re-queue jobs left pending/processing by a previous run (see README).
    resume_jobs_on_startup: bool = field(
        default_factory=lambda: _env_bool("RESUME_JOBS_ON_STARTUP", "true")
    )
    # When set, every /api/v1 endpoint except /verify requires header `X-API-Key`.
    api_key: str | None = field(default_factory=lambda: os.getenv("API_KEY") or None)


settings = Settings()
