from app.services import renderer
from tests.conftest import make_payload


def _fail_for(monkeypatch, *names):
    """Make the renderer raise for the given recipient names only."""
    real_render = renderer.render_certificate

    def flaky_render(data, output_path):
        if data.recipient_name in names:
            raise OSError(f"disk error while writing {data.recipient_name}")
        return real_render(data, output_path)

    monkeypatch.setattr(renderer, "render_certificate", flaky_render)


def test_one_failure_does_not_stop_other_certificates(client, monkeypatch):
    _fail_for(monkeypatch, "Bob Smith")

    body = client.post("/api/v1/jobs", json=make_payload()).json()

    job = client.get(body["status_url"]).json()
    assert job["status"] == "completed_with_errors"
    assert job["progress"]["generated"] == 2
    assert job["progress"]["failed"] == 1

    certs = {c["recipient_name"]: c for c in client.get(body["certificates_url"]).json()["items"]}
    bob = certs["Bob Smith"]
    assert bob["status"] == "failed"
    assert bob["failure_stage"] == "generation"
    assert bob["error_message"] == "OSError: disk error while writing Bob Smith"
    assert bob["download_url"] is None
    # recipients after the failing one were still generated
    assert certs["Carol White"]["status"] == "generated"
    assert certs["Alice Johnson"]["status"] == "generated"

    download = client.get(f"/api/v1/certificates/{bob['id']}/download")
    assert download.status_code == 409
    assert "disk error" in download.json()["detail"]


def test_job_fails_when_every_certificate_fails(client, monkeypatch):
    _fail_for(monkeypatch, "Alice Johnson", "Bob Smith", "Carol White")

    body = client.post("/api/v1/jobs", json=make_payload()).json()

    job = client.get(body["status_url"]).json()
    assert job["status"] == "failed"
    assert job["error_message"] == "All certificates failed"
    assert job["download_url"] is None
    assert client.get(f"/api/v1/jobs/{body['job_id']}/download").status_code == 404


def test_validation_and_generation_failures_are_distinguished(client, monkeypatch):
    _fail_for(monkeypatch, "Bob Smith")
    recipients = [
        {"name": "Alice Johnson", "email": "alice@example.com"},
        {"name": "Bob Smith", "email": "bob@example.com"},
        {"name": "No Email"},
    ]

    body = client.post("/api/v1/jobs", json=make_payload(recipients)).json()

    failed = client.get(body["certificates_url"], params={"status": "failed"}).json()["items"]
    stages = {c["index"]: c["failure_stage"] for c in failed}
    assert stages == {1: "generation", 2: "validation"}
