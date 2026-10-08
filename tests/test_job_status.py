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


def test_resume_picks_up_interrupted_job(client, db):
    job = _create_unprocessed_job(db)
    job.status = JobStatus.PROCESSING  # simulate a crash mid-run
    db.commit()

    job_service.resume_unfinished_jobs()

    body = client.get(f"/api/v1/jobs/{job.id}").json()
    assert body["status"] == "completed"
    assert body["progress"]["generated"] == 3
