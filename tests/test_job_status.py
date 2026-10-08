from app.models import Certificate, CertificateStatus, JobStatus
from app.schemas import CreateJobRequest
from app.services import jobs as job_service
from tests.conftest import make_payload


def _create_unprocessed_job(db, payload=None):
    """Create a job without running the worker, to observe intermediate states."""
    job, _ = job_service.create_job(db, CreateJobRequest.model_validate(payload or make_payload()))
    return job


def test_unknown_job_returns_404(client):
    assert client.get("/api/v1/jobs/does-not-exist").status_code == 404


def test_new_job_is_pending_with_zero_progress(client, db):
    job = _create_unprocessed_job(db)

    body = client.get(f"/api/v1/jobs/{job.id}").json()

    assert body["status"] == "pending"
    assert body["started_at"] is None
    assert body["download_url"] is None
    assert body["progress"] == {
        "total": 3, "pending": 3, "generated": 0, "failed": 0, "processed": 0, "percent_complete": 0.0,
    }


def test_progress_reflects_partially_processed_job(client, db):
    job = _create_unprocessed_job(db)
    first = db.query(Certificate).filter_by(job_id=job.id, row_index=0).one()
    first.status = CertificateStatus.GENERATED
    job.status = JobStatus.PROCESSING
    db.commit()

    body = client.get(f"/api/v1/jobs/{job.id}").json()

    assert body["status"] == "processing"
    assert body["progress"]["generated"] == 1
    assert body["progress"]["pending"] == 2
    assert body["progress"]["percent_complete"] == 33.33
    assert body["download_url"] is None


def test_completed_job_status(client, db):
    job = _create_unprocessed_job(db)
    job_service.process_job(job.id)

    body = client.get(f"/api/v1/jobs/{job.id}").json()

    assert body["status"] == "completed"
    assert body["progress"]["percent_complete"] == 100.0
    assert body["started_at"] is not None
    assert body["completed_at"] is not None
    assert body["download_url"].endswith(f"/api/v1/jobs/{job.id}/download")


def test_interrupted_job_is_requeued_and_finished(client, db, monkeypatch):
    job = _create_unprocessed_job(db)
    first = db.query(Certificate).filter_by(job_id=job.id, row_index=0).one()
    first.status = CertificateStatus.GENERATED  # finished before the "crash"
    job.status = JobStatus.PROCESSING  # simulate a shutdown mid-run
    db.commit()

    assert job_service.requeue_interrupted_jobs(db) == [job.id]
    rendered = []
    real_render = job_service.renderer.render_certificate
    monkeypatch.setattr(
        job_service.renderer,
        "render_certificate",
        lambda data, path: rendered.append(data.recipient_name) or real_render(data, path),
    )
    job_service.process_job(job.id)

    assert rendered == ["Bob Smith", "Carol White"]  # finished work is not redone
    body = client.get(f"/api/v1/jobs/{job.id}").json()
    assert body["status"] == "completed"
    assert body["progress"]["generated"] == 3


def test_timestamps_are_timezone_aware(client):
    body = client.post("/api/v1/jobs", json=make_payload()).json()
    job = client.get(body["status_url"]).json()

    for field in ("created_at", "started_at", "completed_at"):
        assert job[field].endswith("Z") or job[field].endswith("+00:00"), job[field]


def test_list_jobs_newest_first_with_status_filter(client):
    first = client.post("/api/v1/jobs", json=make_payload(course_name="First")).json()
    second = client.post("/api/v1/jobs", json=make_payload(course_name="Second")).json()
    client.post("/api/v1/jobs", json=make_payload([{"name": "", "email": "x"}]))  # fails validation

    listing = client.get("/api/v1/jobs").json()
    assert listing["total"] == 3
    assert [j["job_id"] for j in listing["items"][1:]] == [second["job_id"], first["job_id"]]

    completed = client.get("/api/v1/jobs", params={"status": "completed"}).json()
    assert completed["total"] == 2
