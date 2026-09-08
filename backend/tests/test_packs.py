"""Expediente packs: the master PDF, the ZIP, recipients and the failure modes.

Everything here runs with ``MEDIA_BACKEND=local`` (the conftest default), which
is exactly the acceptance criterion: the submission pack must download as PDF +
ZIP without any AWS credentials.
"""
from __future__ import annotations

import io
import os
import zipfile

import pytest

from app.core.config import settings
from app.models.document import Document, DocumentCategory
from app.models.enums import CaseSection, EntityType, PackStatus
from app.models.insurer import InsurerContact
from app.services import packs as pack_service
from tests.conftest import API, make_case_file


@pytest.fixture()
def local_media(tmp_path, monkeypatch):
    """Point the media/document store at a temp dir for the whole test."""
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))
    return tmp_path


def _attach(db, world, case, *, code, category, section, body=b"%PDF-1.4 fake\n", root=None):
    """Register a document AND write its bytes where the local backend expects."""
    key = f"documents/case_file/{case.id}/{category.value}-{code}.pdf"
    doc = Document(
        broker_id=world.a.broker_id,
        entity_type=EntityType.CASE_FILE,
        entity_id=case.id,
        case_file_id=case.id,
        section=section,
        document_code=code,
        s3_key=key,
        bucket="radal-test-bucket",
        original_name=f"{code} documento.pdf",
        mime_type="application/pdf",
        size_bytes=len(body),
        category=category,
    )
    db.add(doc)
    if root is not None:
        path = os.path.join(str(root), key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(body)
    return doc


def _read_stored(root, key: str) -> bytes:
    with open(os.path.join(str(root), key), "rb") as fh:
        return fh.read()


# --- Recipients -------------------------------------------------------------------

def test_recipients_use_the_existing_precedence(client, world, db, headers_a, local_media):
    case = make_case_file(db, world.a)
    db.add_all(
        [
            InsurerContact(
                insurer_id=world.insurer.id, name="Mesa global", email="global@hdi.cl"
            ),
            InsurerContact(
                insurer_id=world.insurer.id,
                broker_id=world.a.broker_id,
                insurance_line_id=world.a.line.id,
                name="Ejecutivo del ramo",
                email="ramo@hdi.cl",
            ),
        ]
    )
    db.commit()

    body = client.get(f"{API}/case-files/{case.id}/recipients", headers=headers_a).json()
    assert len(body["items"]) == 1
    only = body["items"][0]
    assert only["resolution_level"] == "broker_line"
    assert only["contact_email"] == "ramo@hdi.cl"
    assert only["is_native"] is True


# --- Submission pack ----------------------------------------------------------------

def test_submission_pack_produces_pdf_and_zip_locally(
    client, world, db, headers_a, local_media
):
    case = make_case_file(db, world.a)
    _attach(
        db, world, case, code="00A", category=DocumentCategory.PROSPECT_REQUEST,
        section=CaseSection.ROOT_PROSPECT, root=local_media,
    )
    _attach(
        db, world, case, code="01", category=DocumentCategory.TECHNICAL_BRIEF,
        section=CaseSection.SUBMISSION, root=local_media,
    )
    # A quotation lives in another section and must NOT be bundled.
    _attach(
        db, world, case, code="03", category=DocumentCategory.INSURER_QUOTATION,
        section=CaseSection.INSURER_QUOTES, root=local_media,
    )
    db.commit()

    response = client.post(
        f"{API}/case-files/{case.id}/packs/submission", headers=headers_a
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "generated"
    assert body["pdf"]["s3_key"] == f"documents/case_file/{case.id}/submission_pack-1.pdf"
    assert body["zip"]["s3_key"] == f"documents/case_file/{case.id}/submission_pack-1.zip"

    pdf = _read_stored(local_media, body["pdf"]["s3_key"])
    assert pdf.startswith(b"%PDF")

    archive = zipfile.ZipFile(io.BytesIO(_read_stored(local_media, body["zip"]["s3_key"])))
    names = archive.namelist()
    assert any(name.endswith("Carpeta de cotización.pdf") for name in names)
    assert any("00A" in name for name in names)
    assert any("01" in name for name in names)
    assert not any("03 " in name for name in names)


def test_regenerating_overwrites_at_the_fixed_key(client, world, db, headers_a, local_media):
    case = make_case_file(db, world.a)
    db.commit()
    first = client.post(f"{API}/case-files/{case.id}/packs/submission", headers=headers_a).json()
    second = client.post(f"{API}/case-files/{case.id}/packs/submission", headers=headers_a).json()

    assert first["id"] == second["id"]
    assert first["pdf"]["id"] == second["pdf"]["id"]
    listing = client.get(f"{API}/case-files/{case.id}/packs", headers=headers_a).json()
    assert len(listing["items"]) == 1


def test_pack_download_serves_each_part(client, world, db, headers_a, local_media):
    case = make_case_file(db, world.a)
    db.commit()
    pack = client.post(f"{API}/case-files/{case.id}/packs/submission", headers=headers_a).json()

    pdf = client.get(f"{API}/packs/{pack['id']}/download?part=pdf", headers=headers_a)
    assert pdf.status_code == 200
    assert pdf.json()["s3_key"].endswith(".pdf")

    zip_part = client.get(f"{API}/packs/{pack['id']}/download?part=zip", headers=headers_a)
    assert zip_part.status_code == 200
    assert zip_part.json()["s3_key"].endswith(".zip")

    assert client.get(
        f"{API}/packs/{pack['id']}/download?part=csv", headers=headers_a
    ).status_code == 422


def test_comparison_pack_is_pdf_only(client, world, db, headers_a, local_media):
    case = make_case_file(db, world.a)
    db.commit()
    body = client.post(
        f"{API}/case-files/{case.id}/packs/comparison", headers=headers_a
    ).json()
    assert body["pdf"] is not None
    assert body["zip"] is None

    missing = client.get(f"{API}/packs/{body['id']}/download?part=zip", headers=headers_a)
    assert missing.status_code == 404


def test_proposal_pack_is_generated(client, world, db, headers_a, local_media):
    case = make_case_file(db, world.a)
    _attach(
        db, world, case, code="07", category=DocumentCategory.ISSUANCE_PROPOSAL,
        section=CaseSection.BROKER_PROPOSAL, root=local_media,
    )
    db.commit()
    body = client.post(
        f"{API}/case-files/{case.id}/packs/proposal", headers=headers_a
    ).json()
    assert body["status"] == "generated"
    assert body["pdf"] and body["zip"]


# --- Failure modes --------------------------------------------------------------------

def test_oversized_pack_is_413_and_marks_the_pack_failed(
    client, world, db, headers_a, local_media, monkeypatch
):
    monkeypatch.setattr(settings, "PACK_MAX_MB", 1)
    case = make_case_file(db, world.a)
    # 1.5 MB of incompressible bytes: over the 1 MB cap even zipped.
    _attach(
        db, world, case, code="00A", category=DocumentCategory.PROSPECT_REQUEST,
        section=CaseSection.ROOT_PROSPECT, body=os.urandom(1_500_000), root=local_media,
    )
    db.commit()

    response = client.post(
        f"{API}/case-files/{case.id}/packs/submission", headers=headers_a
    )
    assert response.status_code == 413
    assert "individually" in response.json()["detail"]

    listing = client.get(f"{API}/case-files/{case.id}/packs", headers=headers_a).json()
    assert listing["items"][0]["status"] == PackStatus.FAILED.value
    assert listing["items"][0]["error"]


def test_generation_failure_is_502_and_never_a_500(
    client, world, db, headers_a, local_media, monkeypatch
):
    def _boom(*args, **kwargs):
        raise RuntimeError("reportlab exploded")

    monkeypatch.setattr(pack_service, "_render_pdf", _boom)
    case = make_case_file(db, world.a)
    db.commit()

    response = client.post(
        f"{API}/case-files/{case.id}/packs/submission", headers=headers_a
    )
    assert response.status_code == 502
    assert "reportlab exploded" in response.json()["detail"]

    listing = client.get(f"{API}/case-files/{case.id}/packs", headers=headers_a).json()
    assert listing["items"][0]["status"] == PackStatus.FAILED.value


# --- Permissions and tenancy -------------------------------------------------------------

def test_pack_generation_needs_submit(
    client, world, db, headers_a_exec, headers_a_inspector, local_media
):
    """The pack is the headline deliverable: gated on ``CaseFiles.Submit``.

    The executive (who runs the expediente and holds Submit) CAN generate; the
    inspector (View-partial only, no Submit) still gets a 403.
    """
    case = make_case_file(db, world.a)
    db.commit()

    allowed = client.post(
        f"{API}/case-files/{case.id}/packs/submission", headers=headers_a_exec
    )
    assert allowed.status_code == 201, allowed.text

    denied = client.post(
        f"{API}/case-files/{case.id}/packs/submission", headers=headers_a_inspector
    )
    assert denied.status_code == 403


def test_pack_of_another_tenant_is_404(client, world, db, headers_a, headers_b, local_media):
    case = make_case_file(db, world.a)
    db.commit()
    pack = client.post(f"{API}/case-files/{case.id}/packs/submission", headers=headers_a).json()
    assert client.get(f"{API}/packs/{pack['id']}", headers=headers_b).status_code == 404
    assert (
        client.post(
            f"{API}/case-files/{case.id}/packs/submission", headers=headers_b
        ).status_code
        == 404
    )


def test_summary_confirmation_is_suggest_then_confirm(
    client, world, db, headers_a, local_media
):
    case = make_case_file(db, world.a)
    db.commit()
    pack = client.post(f"{API}/case-files/{case.id}/packs/submission", headers=headers_a).json()
    assert pack["is_summary_confirmed"] is False

    empty = client.post(
        f"{API}/packs/{pack['id']}/summary/confirm",
        headers=headers_a,
        json={"is_summary_confirmed": True},
    )
    assert empty.status_code == 422

    confirmed = client.post(
        f"{API}/packs/{pack['id']}/summary/confirm",
        headers=headers_a,
        json={"summary": "Cuenta industrial con TIV de UF 17.920.", "is_summary_confirmed": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["is_summary_confirmed"] is True
    assert confirmed.json()["summary"].startswith("Cuenta industrial")
