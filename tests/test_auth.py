import pytest

from app.config import settings
from tests.conftest import make_payload


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setattr(settings, "api_key", "s3cret")


def test_api_is_open_when_no_key_configured(client):
    assert client.post("/api/v1/jobs", json=make_payload()).status_code == 202


def test_missing_or_wrong_key_is_rejected(client, secured):
    assert client.post("/api/v1/jobs", json=make_payload()).status_code == 401
    assert client.get("/api/v1/jobs", headers={"X-API-Key": "wrong"}).status_code == 401


def test_correct_key_is_accepted(client, secured):
    response = client.post("/api/v1/jobs", json=make_payload(), headers={"X-API-Key": "s3cret"})
    assert response.status_code == 202


def test_verify_endpoint_stays_public(client, secured):
    headers = {"X-API-Key": "s3cret"}
    body = client.post("/api/v1/jobs", json=make_payload(), headers=headers).json()
    code = client.get(body["certificates_url"], headers=headers).json()["items"][0]["verification_code"]

    assert client.get(f"/api/v1/verify/{code}").status_code == 200
