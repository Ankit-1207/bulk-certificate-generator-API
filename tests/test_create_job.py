from tests.conftest import make_payload


def test_create_job_returns_202_with_job_info(client):
    response = client.post("/api/v1/jobs", json=make_payload())

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"]
    assert body["total_recipients"] == 3
    assert body["accepted"] == 3
    assert body["rejected"] == 0
    assert body["validation_errors"] == []
    assert body["status_url"].endswith(f"/api/v1/jobs/{body['job_id']}")
    assert body["certificates_url"].endswith(f"/api/v1/jobs/{body['job_id']}/certificates")


def test_created_job_is_processed_in_background(client):
    job_id = client.post("/api/v1/jobs", json=make_payload()).json()["job_id"]

    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "completed"
    assert job["progress"]["generated"] == 3
    assert job["certificate"]["course_name"] == "Advanced Python"


def test_certificate_title_and_date_have_defaults(client):
    payload = make_payload()
    del payload["certificate"]["title"]
    del payload["certificate"]["issue_date"]

    response = client.post("/api/v1/jobs", json=payload)

    assert response.status_code == 202
    job = client.get(response.json()["status_url"]).json()
    assert job["certificate"]["title"] == "Certificate of Completion"
    assert job["certificate"]["issue_date"]


def test_bulk_job_with_many_recipients(client):
    recipients = [{"name": f"Person {i}", "email": f"person{i}@example.com"} for i in range(200)]

    body = client.post("/api/v1/jobs", json=make_payload(recipients)).json()

    job = client.get(body["status_url"]).json()
    assert job["status"] == "completed"
    assert job["progress"] == {
        "total": 200,
        "pending": 0,
        "generated": 200,
        "failed": 0,
        "processed": 200,
        "percent_complete": 100.0,
    }
