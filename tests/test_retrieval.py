import io
import zipfile

from app.schemas import CreateJobRequest
from app.services import jobs as job_service
from tests.conftest import make_payload


def _completed_job(client, payload=None):
    return client.post("/api/v1/jobs", json=payload or make_payload()).json()


def test_list_certificates_returns_all_rows_in_input_order(client):
    body = _completed_job(client)

    listing = client.get(body["certificates_url"]).json()

    assert listing["total"] == 3
    assert [c["index"] for c in listing["items"]] == [0, 1, 2]
    assert [c["recipient_name"] for c in listing["items"]] == ["Alice Johnson", "Bob Smith", "Carol White"]
    assert all(c["status"] == "generated" and c["download_url"] for c in listing["items"])


def test_list_certificates_filter_and_pagination(client):
    recipients = [{"name": f"Person {i}", "email": f"p{i}@example.com"} for i in range(5)]
    recipients.append({"name": "Broken", "email": "bad"})
    body = _completed_job(client, make_payload(recipients))

    page = client.get(body["certificates_url"], params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 6
    assert [c["index"] for c in page["items"]] == [2, 3]

    generated = client.get(body["certificates_url"], params={"status": "generated"}).json()
    assert generated["total"] == 5

    assert client.get(body["certificates_url"], params={"status": "bogus"}).status_code == 422


def test_list_certificates_for_unknown_job_is_404(client):
    assert client.get("/api/v1/jobs/nope/certificates").status_code == 404


def test_download_single_certificate_pdf(client):
    body = _completed_job(client)
    cert = client.get(body["certificates_url"]).json()["items"][0]

    response = client.get(cert["download_url"])

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "Alice_Johnson" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")


def test_get_certificate_details(client):
    body = _completed_job(client)
    cert = client.get(body["certificates_url"]).json()["items"][1]

    detail = client.get(f"/api/v1/certificates/{cert['id']}").json()

    assert detail == cert
    assert client.get("/api/v1/certificates/nope").status_code == 404
    assert client.get("/api/v1/certificates/nope/download").status_code == 404


def test_download_job_zip_contains_every_generated_certificate(client):
    recipients = make_payload()["recipients"] + [{"name": "Invalid", "email": "bad"}]
    body = _completed_job(client, make_payload(recipients))
    job = client.get(body["status_url"]).json()

    response = client.get(job["download_url"])

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = sorted(archive.namelist())
        assert len(names) == 3
        assert names[0].startswith("0001_Alice_Johnson_")
        assert all(archive.read(n).startswith(b"%PDF") for n in names)


def test_download_filename_transliterates_accents(client):
    body = _completed_job(client, make_payload([{"name": "Renée Françoise", "email": "r@example.com"}]))
    cert = client.get(body["certificates_url"]).json()["items"][0]

    response = client.get(cert["download_url"])

    assert "Renee_Francoise" in response.headers["content-disposition"]


def test_download_zip_of_unfinished_job_is_conflict(client, db):
    job, _ = job_service.create_job(db, CreateJobRequest.model_validate(make_payload()))

    response = client.get(f"/api/v1/jobs/{job.id}/download")

    assert response.status_code == 409


def test_pending_certificate_cannot_be_downloaded(client, db):
    job, _ = job_service.create_job(db, CreateJobRequest.model_validate(make_payload()))

    response = client.get(f"/api/v1/certificates/{job.certificates[0].id}/download")

    assert response.status_code == 409


def test_verify_certificate_by_code(client):
    body = _completed_job(client)
    cert = client.get(body["certificates_url"]).json()["items"][0]

    result = client.get(f"/api/v1/verify/{cert['verification_code'].lower()}").json()

    assert result["valid"] is True
    assert result["recipient_name"] == "Alice Johnson"
    assert result["course_name"] == "Advanced Python"
    assert client.get("/api/v1/verify/UNKNOWN").status_code == 404
