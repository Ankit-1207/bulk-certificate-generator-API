from app.models import JobStatus
from app.schemas import CreateJobRequest
from app.services import jobs as job_service
from app.services import renderer
from tests.conftest import make_payload


def _break_renderer_for(monkeypatch, name):
    real_render = renderer.render_certificate

    def flaky(data, output_path):
        if data.recipient_name == name:
            raise OSError("storage temporarily unavailable")
        return real_render(data, output_path)

    monkeypatch.setattr(renderer, "render_certificate", flaky)
    return real_render


def test_retry_regenerates_only_generation_failures(client, monkeypatch):
    real_render = _break_renderer_for(monkeypatch, "Bob Smith")
    recipients = make_payload()["recipients"] + [{"name": "No Email"}]
    body = client.post("/api/v1/jobs", json=make_payload(recipients)).json()
    assert client.get(body["status_url"]).json()["status"] == "completed_with_errors"

    monkeypatch.setattr(renderer, "render_certificate", real_render)  # the transient issue is fixed
    response = client.post(f"/api/v1/jobs/{body['job_id']}/retry")

    assert response.status_code == 202
    assert response.json()["retried"] == 1
    job = client.get(body["status_url"]).json()
    # Bob is now generated; the validation failure (row 3) is not retried
    assert job["status"] == "completed_with_errors"
    assert job["progress"]["generated"] == 3
    assert job["progress"]["failed"] == 1
    bob = client.get(body["certificates_url"]).json()["items"][1]
    assert bob["status"] == "generated"
    assert bob["error_message"] is None


def test_retry_turns_failed_job_into_completed(client, monkeypatch):
    real_render = renderer.render_certificate
    monkeypatch.setattr(renderer, "render_certificate", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    body = client.post("/api/v1/jobs", json=make_payload()).json()
    assert client.get(body["status_url"]).json()["status"] == "failed"

    monkeypatch.setattr(renderer, "render_certificate", real_render)
    client.post(f"/api/v1/jobs/{body['job_id']}/retry")

    job = client.get(body["status_url"]).json()
    assert job["status"] == "completed"
    assert job["error_message"] is None


def test_retry_without_generation_failures_is_conflict(client):
    body = client.post("/api/v1/jobs", json=make_payload()).json()

    response = client.post(f"/api/v1/jobs/{body['job_id']}/retry")

    assert response.status_code == 409
    assert client.get(body["status_url"]).json()["status"] == "completed"


def test_retry_running_job_is_conflict(client, db):
    job, _ = job_service.create_job(db, CreateJobRequest.model_validate(make_payload()))
    job.status = JobStatus.PROCESSING
    db.commit()

    assert client.post(f"/api/v1/jobs/{job.id}/retry").status_code == 409


def test_retry_unknown_job_is_404(client):
    assert client.post("/api/v1/jobs/nope/retry").status_code == 404
