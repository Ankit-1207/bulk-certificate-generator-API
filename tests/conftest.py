import pytest
from fastapi.testclient import TestClient

from app import database
from app.config import settings
from app.main import app
from app.services.jobs import process_job
from app.services.worker import worker


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Fresh SQLite database and storage directory for every test."""
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")
    monkeypatch.setattr(settings, "resume_jobs_on_startup", False)
    monkeypatch.setattr(settings, "api_key", None)
    database.configure_database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.init_db()
    yield
    database.engine.dispose()


@pytest.fixture
def client(monkeypatch):
    """API client whose worker runs jobs synchronously inside the request,
    so a job is fully processed by the time client.post() returns.
    (tests/test_worker.py exercises the real thread pool.)"""
    with TestClient(app) as test_client:
        monkeypatch.setattr(worker, "submit", process_job)
        yield test_client


@pytest.fixture
def db():
    session = database.SessionLocal()
    yield session
    session.close()


def make_payload(recipients=None, **certificate_overrides):
    certificate = {
        "title": "Certificate of Completion",
        "course_name": "Advanced Python",
        "issuer": "Acme Academy",
        "issue_date": "2026-10-01",
    }
    certificate.update(certificate_overrides)
    if recipients is None:
        recipients = [
            {"name": "Alice Johnson", "email": "alice@example.com"},
            {"name": "Bob Smith", "email": "bob@example.com"},
            {"name": "Carol White", "email": "carol@example.com"},
        ]
    return {"certificate": certificate, "recipients": recipients}
