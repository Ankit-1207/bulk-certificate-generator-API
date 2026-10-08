from datetime import date

import pytest
from pypdf import PdfReader

from app.config import settings
from app.models import Certificate, CertificateStatus
from app.schemas import CreateJobRequest
from app.services import jobs as job_service
from app.services.renderer import CertificateData, render_certificate
from tests.conftest import make_payload


def _pdf_text(path) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(path).pages)


def test_renderer_produces_pdf_with_recipient_details(tmp_path):
    output = tmp_path / "out" / "cert.pdf"
    data = CertificateData(
        recipient_name="Alice Johnson",
        title="Certificate of Completion",
        course_name="Advanced Python",
        issuer="Acme Academy",
        issue_date=date(2026, 10, 1),
        verification_code="ABC123DEF456",
    )

    render_certificate(data, output)

    assert output.read_bytes().startswith(b"%PDF")
    assert not output.with_name("cert.pdf.tmp").exists()
    text = _pdf_text(output)
    for expected in ["Alice Johnson", "CERTIFICATE OF COMPLETION", "Advanced Python",
                     "Acme Academy", "October 01, 2026", "ABC123DEF456"]:
        assert expected in text


def test_renderer_handles_long_names_and_accents(tmp_path):
    output = tmp_path / "long.pdf"
    name = "Renée Françoise " + "Montgomery-" * 8 + "Smith"

    render_certificate(
        CertificateData(name, "Award", "Course", "Issuer", date(2026, 1, 1), "CODE"), output
    )

    assert len(PdfReader(output).pages) == 1
    assert "Renée Françoise" in _pdf_text(output)


def test_process_job_generates_one_file_per_valid_recipient(db):
    job, _ = job_service.create_job(db, CreateJobRequest.model_validate(make_payload()))

    job_service.process_job(job.id)

    db.expire_all()
    certs = db.query(Certificate).filter_by(job_id=job.id).order_by(Certificate.row_index).all()
    assert [c.status for c in certs] == [CertificateStatus.GENERATED] * 3
    for cert in certs:
        path = settings.storage_dir / cert.file_path
        assert path.exists()
        assert cert.recipient_name in _pdf_text(path)
        assert cert.verification_code in _pdf_text(path)
        assert cert.generated_at is not None


def test_process_job_is_not_run_twice(db, monkeypatch):
    job, _ = job_service.create_job(db, CreateJobRequest.model_validate(make_payload()))
    job_service.process_job(job.id)

    calls = []
    monkeypatch.setattr(job_service.renderer, "render_certificate", lambda *a: calls.append(a))
    job_service.process_job(job.id)

    assert calls == []


def test_failed_render_leaves_no_partial_files(tmp_path, monkeypatch):
    output = tmp_path / "renders" / "cert.pdf"

    def explode(data, path):
        path.write_bytes(b"%PDF partial")
        raise RuntimeError("renderer crashed")

    monkeypatch.setattr("app.services.renderer._draw", explode)
    data = CertificateData("Alice", "Award", "Course", "Issuer", date(2026, 1, 1), "CODE")

    with pytest.raises(RuntimeError):
        render_certificate(data, output)

    assert list(output.parent.iterdir()) == []
