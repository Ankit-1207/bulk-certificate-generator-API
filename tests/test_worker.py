"""Exercises the real thread pool (other tests run jobs synchronously)."""

import time

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from tests.conftest import make_payload


@pytest.fixture
def async_client():
    with TestClient(app) as test_client:
        yield test_client


def _wait_until_finished(client, status_url, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(status_url).json()
        if job["status"] not in ("pending", "processing"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")


def test_job_is_generated_by_background_worker(async_client):
    recipients = [{"name": f"Person {i}", "email": f"p{i}@example.com"} for i in range(30)]

    body = async_client.post("/api/v1/jobs", json=make_payload(recipients)).json()

    assert body["status"] == "pending"  # returned before generation
    job = _wait_until_finished(async_client, body["status_url"])
    assert job["status"] == "completed"
    assert job["progress"]["generated"] == 30


def test_concurrent_jobs_all_complete(async_client):
    status_urls = [
        async_client.post(
            "/api/v1/jobs",
            json=make_payload([{"name": f"P{j} {i}", "email": f"p{j}_{i}@example.com"} for i in range(10)]),
        ).json()["status_url"]
        for j in range(5)
    ]

    for url in status_urls:
        assert _wait_until_finished(async_client, url)["status"] == "completed"


def test_pending_jobs_are_resumed_on_startup(monkeypatch, db):
    from app.schemas import CreateJobRequest
    from app.services import jobs as job_service

    job, _ = job_service.create_job(db, CreateJobRequest.model_validate(make_payload()))
    monkeypatch.setattr(settings, "resume_jobs_on_startup", True)

    with TestClient(app) as client:  # startup re-queues the pending job
        finished = _wait_until_finished(client, f"/api/v1/jobs/{job.id}")

    assert finished["status"] == "completed"
