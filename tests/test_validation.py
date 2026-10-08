import pytest

from app.config import settings
from tests.conftest import make_payload


# ---- Request-level validation: the whole request is rejected (422) ---------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("certificate"),
        lambda p: p.pop("recipients"),
        lambda p: p["certificate"].pop("course_name"),
        lambda p: p["certificate"].pop("issuer"),
        lambda p: p["certificate"].update(course_name="   "),
        lambda p: p["certificate"].update(issue_date="not-a-date"),
        lambda p: p["certificate"].update(course_name="x" * 121),
        lambda p: p.update(recipients=[]),
        lambda p: p.update(recipients="alice@example.com"),
    ],
    ids=[
        "missing-certificate",
        "missing-recipients",
        "missing-course",
        "missing-issuer",
        "blank-course",
        "bad-date",
        "course-too-long",
        "empty-recipients",
        "recipients-not-a-list",
    ],
)
def test_invalid_request_is_rejected(client, mutate):
    payload = make_payload()
    mutate(payload)

    response = client.post("/api/v1/jobs", json=payload)

    assert response.status_code == 422


def test_too_many_recipients_is_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "max_recipients_per_job", 2)

    response = client.post("/api/v1/jobs", json=make_payload())

    assert response.status_code == 422
    assert "at most 2 recipients" in response.text


# ---- Row-level validation: bad rows fail, the rest are still generated ------


def test_invalid_recipients_are_reported_and_valid_ones_generated(client):
    recipients = [
        {"name": "Alice Johnson", "email": "alice@example.com"},  # 0 valid
        {"email": "noname@example.com"},  # 1 missing name
        {"name": "Bad Email", "email": "not-an-email"},  # 2 invalid email
        {"name": "   ", "email": "blank@example.com"},  # 3 blank name
        "just a string",  # 4 not an object
        {"name": "12345", "email": "digits@example.com"},  # 5 no letters
        {"name": "Alice Again", "email": "ALICE@example.com"},  # 6 duplicate email
        {"name": "Zhang 伟", "email": "zhang@example.com"},  # 7 not renderable
        {"name": "Bob Smith", "email": "bob@example.com"},  # 8 valid
    ]

    response = client.post("/api/v1/jobs", json=make_payload(recipients))

    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] == 2
    assert body["rejected"] == 7
    errors = {e["index"]: e["errors"] for e in body["validation_errors"]}
    assert set(errors) == {1, 2, 3, 4, 5, 6, 7}
    assert errors[1][0]["field"] == "name"
    assert errors[2][0] == {"field": "email", "message": "is not a valid email address"}
    assert errors[4][0]["field"] == "recipient"
    assert "duplicate" in errors[6][0]["message"]
    assert "cannot render" in errors[7][0]["message"]

    job = client.get(body["status_url"]).json()
    assert job["status"] == "completed_with_errors"
    assert job["progress"]["generated"] == 2
    assert job["progress"]["failed"] == 7

    failed = client.get(body["certificates_url"], params={"status": "failed"}).json()
    assert {c["index"] for c in failed["items"]} == {1, 2, 3, 4, 5, 6, 7}
    assert all(c["failure_stage"] == "validation" for c in failed["items"])
    assert all(c["download_url"] is None for c in failed["items"])


def test_recipient_values_are_normalised(client):
    recipients = [{"name": "  Alice   Johnson ", "email": " Alice@Example.COM "}]

    body = client.post("/api/v1/jobs", json=make_payload(recipients)).json()

    cert = client.get(body["certificates_url"]).json()["items"][0]
    assert cert["recipient_name"] == "Alice Johnson"
    assert cert["recipient_email"] == "alice@example.com"


def test_job_with_no_valid_recipients_fails_immediately(client):
    recipients = [{"name": "", "email": "x"}, {"foo": "bar"}]

    body = client.post("/api/v1/jobs", json=make_payload(recipients)).json()

    assert body["status"] == "failed"
    job = client.get(body["status_url"]).json()
    assert job["status"] == "failed"
    assert job["error_message"] == "No valid recipients in request"
    assert job["progress"]["failed"] == 2
