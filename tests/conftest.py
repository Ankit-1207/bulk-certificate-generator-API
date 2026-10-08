import pytest
from fastapi.testclient import TestClient

from app import database
from app.config import settings
from app.main import app


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Fresh SQLite database and storage directory for every test."""
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")
    monkeypatch.setattr(settings, "resume_jobs_on_startup", False)
    database.configure_database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.init_db()
    yield
    database.engine.dispose()


@pytest.fixture
def client():
    # Starlette's TestClient runs BackgroundTasks before client.post() returns,
    # so a job created through the API is fully processed by the time we assert.
    with TestClient(app) as test_client:
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
