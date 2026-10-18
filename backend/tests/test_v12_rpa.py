"""v12 lane B6 — portal-search RPA: API, engine and job WITHOUT a browser.

The browser half (real Chromium against the local fake HDI portal) lives in
``test_v12_rpa_browser.py``. Here a scripted connector and a fake page stand
in for Playwright, so the engine's bookkeeping — claim, taxonomy, screenshot
document, notifications, ops mail, maintenance streak, credential status,
sanitised steps — is covered in milliseconds.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.activity import Activity
from app.models.catalog import InsuranceSubtype
from app.models.document import Document, DocumentCategory
from app.models.enums import (
    CredentialStatus,
    EntityType,
    MailMessageStatus,
    NotificationKind,
    PortalErrorKind,
    PortalRunStatus,
)
from app.models.mail import MailMessage
from app.models.notification import Notification
from app.models.rpa import PortalCredential, PortalQuote, PortalSearchRun
from app.services.rpa import engine
from app.services.rpa.base import InputInvalid, PortalResult, QuoteOut, RunContext
from app.services.rpa.connectors.hdi import HdiConnector, validate_vehicle_input
from app.services.rpa.errors import LayoutChanged, LoginFailed, PortalError, PortalTimeout
from app.services.rpa.registry import CONNECTORS
from tests.conftest import API, auth_headers, ensure_role_user, make_case_file

VALID_INPUT = {
    "plate": "bcdf-12",
    "brand": "Toyota",
    "model": "Yaris",
    "year": 2022,
    "use": "particular",
    "commune": "Providencia",
    "insured_rut": "11.111.111-1",
    "start_date": "2026-11-01",
}
PASSWORD = "clave-super-secreta-77"
T0 = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _portal_search_feature_on(portal_search_on):
    """This suite covers the feature itself: the product flag is on here."""


@pytest.fixture(autouse=True)
def _media_tmp(tmp_path, monkeypatch):
    """Failure screenshots are real files: keep them out of backend/media."""
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))


# --- shared helpers (the browser suite imports these) -----------------------------

def rpa_on(monkeypatch, *, base_url: str = "http://127.0.0.1:9/fake-hdi", inline: bool = False) -> None:
    monkeypatch.setattr(settings, "RPA_ENABLED", True)
    monkeypatch.setattr(settings, "RPA_HDI_BASE_URL", base_url)
    monkeypatch.setattr(settings, "RPA_INLINE", inline)


def store_credential(db, tenant, *, username: str = "corredor@alfa.cl", password: str = PASSWORD) -> PortalCredential:
    row = PortalCredential(
        broker_id=tenant.broker_id,
        portal="hdi",
        username=username,
        secret_ciphertext=encrypt_secret(password),
    )
    db.add(row)
    db.commit()
    return row


def subtype_id(db, code: str = "VEH-01") -> int:
    return db.scalar(select(InsuranceSubtype.id).where(InsuranceSubtype.code == code))


def make_run(db, tenant, *, user=None, input=None, status=PortalRunStatus.QUEUED, case_file_id=None, **kw) -> PortalSearchRun:
    run = PortalSearchRun(
        broker_id=tenant.broker_id,
        portal="hdi",
        requested_by_user_id=(user or tenant.executive).id,
        input=input or validate_vehicle_input(VALID_INPUT),
        status=status,
        case_file_id=case_file_id,
        **kw,
    )
    db.add(run)
    db.commit()
    return run


class FakePage:
    def __init__(self) -> None:
        self.shots = 0

    def screenshot(self, **_kw) -> bytes:
        self.shots += 1
        return b"\x89PNG\r\n\x1a\nfake-screenshot"


@contextmanager
def fake_browser():
    yield FakePage()


def no_browser():
    raise AssertionError("the browser must not be launched")


class ScriptedConnector(HdiConnector):
    """The real HDI connector's metadata with a scripted ``run``."""

    def __init__(self, *, raises: BaseException | None = None, quotes=None, version: str | None = None,
                 on_run=None, insurer_rut: str = "") -> None:
        super().__init__(base_url="http://127.0.0.1:9/fake-hdi")
        if version:
            self.version = version
        self.raises = raises
        self.quotes = quotes
        self.on_run = on_run
        self.spec["insurer"] = {"rut": insurer_rut, "cmf_code": ""}
        self.calls = 0

    def run(self, ctx: RunContext) -> PortalResult:
        self.calls += 1
        ctx.log_step("open_login", True, op="goto")
        if self.on_run:
            self.on_run(ctx)
        if self.raises is not None:
            ctx.log_step("fill_plate", False, "No se encontró el elemento esperado (#plate).", op="fill")
            raise self.raises
        return PortalResult(quotes=self.quotes if self.quotes is not None else [
            QuoteOut(plan_name="Full", premium_uf=Decimal("24.5000"), premium_clp=950000,
                     deductible="UF 3", coverages=["Daños propios", "Robo"], raw={"plan_name": "Full"}),
        ])


def _notifications(db, run_id):
    return db.scalars(
        select(Notification).where(Notification.origin_type == "portal_search", Notification.origin_id == run_id)
    ).all()


def _ops_mails(db):
    return db.scalars(select(MailMessage).where(MailMessage.subject.like("[Radal ops] RPA%"))).all()


# --- input validation ---------------------------------------------------------------

def test_vehicle_input_is_normalised():
    clean = validate_vehicle_input(VALID_INPUT)
    assert clean["plate"] == "BCDF12"
    assert clean["insured_rut"] == "11111111-1"
    assert clean["start_date"] == "2026-11-01"
    assert clean["use"] == "particular"


def test_vehicle_input_errors_are_spanish_and_per_field():
    with pytest.raises(InputInvalid) as err:
        validate_vehicle_input({**VALID_INPUT, "plate": "12", "insured_rut": "11111111-2", "year": "x", "use": "taxi",
                                "start_date": "mañana", "brand": ""})
    fields = {e["field"] for e in err.value.errors}
    assert fields == {"plate", "insured_rut", "year", "use", "start_date", "brand"}
    assert any("Patente" in e["message"] for e in err.value.errors)


# --- portals --------------------------------------------------------------------------

def test_portals_list_reports_disabled_unconfigured_available_and_upcoming(
    client, db, world, catalog, headers_a, monkeypatch
):
    monkeypatch.setattr(settings, "RPA_ENABLED", False)
    hdi = client.get(f"{API}/portal-searches/portals", headers=headers_a).json()[0]
    assert hdi["key"] == "hdi" and hdi["status"] == "maintenance" and hdi["maintenance_code"] == "rpa_disabled"

    monkeypatch.setattr(settings, "RPA_ENABLED", True)
    monkeypatch.setattr(settings, "RPA_HDI_BASE_URL", "")
    hdi = client.get(f"{API}/portal-searches/portals", headers=headers_a).json()[0]
    assert hdi["status"] == "maintenance"
    assert hdi["maintenance_reason"] == "Conector en configuración"

    rpa_on(monkeypatch)
    body = client.get(f"{API}/portal-searches/portals", headers=headers_a).json()
    hdi = body[0]
    assert hdi["status"] == "available" and hdi["maintenance_reason"] is None
    assert hdi["supported_subtypes"] == ["VEH-01"]
    assert hdi["subtypes"][0]["id"] == subtype_id(db) and hdi["subtypes"][0]["name"] == "Livianos"
    assert hdi["requires_credentials"] is True and hdi["has_credentials"] is False
    assert {f["name"] for f in hdi["input_fields"]} == set(VALID_INPUT)
    upcoming = [p for p in body if p["coming_soon"]]
    assert upcoming and all(p["status"] == "maintenance" for p in upcoming)


def test_credentials_are_write_only_encrypted_and_admin_only(client, db, world, headers_a, headers_a_exec, monkeypatch):
    url = f"{API}/portal-searches/credentials/hdi"
    assert client.put(url, json={"username": "corredor@alfa.cl"}, headers=headers_a).status_code == 422
    assert client.put(url, json={"username": "x", "password": "y"}, headers=headers_a_exec).status_code == 403
    assert client.put(f"{API}/portal-searches/credentials/nope", json={"username": "x", "password": "y"},
                      headers=headers_a).status_code == 404

    res = client.put(url, json={"username": " corredor@alfa.cl ", "password": PASSWORD}, headers=headers_a)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body == {**body, "portal": "hdi", "username": "corredor@alfa.cl", "has_secret": True, "status": "unverified"}
    assert PASSWORD not in res.text

    row = db.scalar(select(PortalCredential).where(PortalCredential.broker_id == world.a.broker_id))
    assert row.secret_ciphertext and PASSWORD not in row.secret_ciphertext
    assert decrypt_secret(row.secret_ciphertext) == PASSWORD

    # Username-only update keeps the secret.
    res = client.put(url, json={"username": "otro@alfa.cl"}, headers=headers_a)
    assert res.status_code == 200 and res.json()["has_secret"] is True
    db.expire_all()
    assert decrypt_secret(db.get(PortalCredential, row.id).secret_ciphertext) == PASSWORD

    activity = db.scalars(select(Activity).where(Activity.action == "portal_credential.updated")).all()
    assert activity and all(PASSWORD not in json.dumps(a.meta) for a in activity)

    hdi = client.get(f"{API}/portal-searches/portals", headers=headers_a_exec).json()[0]
    assert hdi["has_credentials"] is True and hdi["credential"]["username"] == "otro@alfa.cl"
    assert "password" not in json.dumps(hdi["credential"])

    # Broker B sees no credential of A.
    headers_b = auth_headers(client, world.b.admin.email)
    assert client.get(f"{API}/portal-searches/portals", headers=headers_b).json()[0]["has_credentials"] is False

    assert client.delete(url, headers=headers_a_exec).status_code == 403
    assert client.delete(url, headers=headers_a).status_code == 204
    assert client.delete(url, headers=headers_a).status_code == 404


# --- create: validation, permissions, isolation ---------------------------------------------

def test_create_run_validates_everything_before_queueing(client, db, world, catalog, headers_a, monkeypatch):
    post = lambda body: client.post(f"{API}/portal-searches", json=body, headers=headers_a)  # noqa: E731
    base = {"portal": "hdi", "insurance_subtype_id": subtype_id(db), "input": VALID_INPUT}

    monkeypatch.setattr(settings, "RPA_ENABLED", False)
    res = post(base)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "portal_maintenance"

    rpa_on(monkeypatch)
    assert post({**base, "portal": "nope"}).json()["detail"]["code"] == "unknown_portal"
    assert post({**base, "portal": "mapfre"}).json()["detail"]["code"] == "portal_coming_soon"
    assert post({**base, "insurance_subtype_id": 999999}).json()["detail"]["code"] == "unknown_subtype"
    res = post({**base, "insurance_subtype_id": subtype_id(db, "VEH-05")})
    assert res.status_code == 422 and res.json()["detail"]["code"] == "unsupported_subtype"
    res = post({**base, "input": {**VALID_INPUT, "plate": "?"}})
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "invalid_input"
    assert res.json()["detail"]["errors"][0]["field"] == "plate"

    case_b = make_case_file(db, world.b)
    db.commit()
    assert post({**base, "case_file_id": case_b.id}).status_code == 404
    assert db.scalar(select(PortalSearchRun.id)) is None

    case_a = make_case_file(db, world.a)
    db.commit()
    res = post({**base, "case_file_id": case_a.id})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "queued" and body["case_file_id"] == case_a.id
    assert body["input"]["plate"] == "BCDF12" and body["insurance_subtype_code"] == "VEH-01"
    assert body["user_message_key"] is None and body["can_cancel"] is True
    assert db.scalar(select(Activity).where(Activity.action == "portal_search.created")) is not None


def test_permissions_integrations_gate(client, db, world, catalog, headers_a_exec, monkeypatch):
    rpa_on(monkeypatch)
    tech = ensure_role_user(db, world.a, "broker_technician", "technician")
    headers_tech = auth_headers(client, tech.email)
    body = {"portal": "hdi", "insurance_subtype_id": subtype_id(db), "input": VALID_INPUT}

    assert client.get(f"{API}/portal-searches/portals", headers=headers_tech).status_code == 403
    assert client.get(f"{API}/portal-searches", headers=headers_tech).status_code == 403
    assert client.post(f"{API}/portal-searches", json=body, headers=headers_tech).status_code == 403
    assert client.get(f"{API}/portal-searches/portals").status_code == 401

    res = client.post(f"{API}/portal-searches", json=body, headers=headers_a_exec)
    assert res.status_code == 201
    run_id = res.json()["id"]
    assert client.get(f"{API}/portal-searches/{run_id}", headers=headers_tech).status_code == 403
    assert client.post(f"{API}/portal-searches/{run_id}/cancel", headers=headers_tech).status_code == 403


def test_another_tenants_run_is_404_everywhere(client, db, world, headers_a, headers_b, monkeypatch):
    rpa_on(monkeypatch)
    case_a = make_case_file(db, world.a)
    db.commit()
    run = make_run(db, world.a, status=PortalRunStatus.FAILED, error_kind=PortalErrorKind.TIMEOUT, case_file_id=case_a.id)
    db.add(PortalQuote(broker_id=world.a.broker_id, run_id=run.id, plan_name="Secreto A"))
    db.commit()

    for method, path in (
        ("get", f"/portal-searches/{run.id}"),
        ("post", f"/portal-searches/{run.id}/retry"),
        ("post", f"/portal-searches/{run.id}/cancel"),
    ):
        assert getattr(client, method)(f"{API}{path}", headers=headers_b).status_code == 404
    listing = client.get(f"{API}/portal-searches", headers=headers_b)
    assert listing.status_code == 200 and listing.json()["total"] == 0 and "Secreto A" not in listing.text
    assert client.get(f"{API}/portal-searches?case_file_id={case_a.id}", headers=headers_b).status_code == 404

    mine = client.get(f"{API}/portal-searches?case_file_id={case_a.id}", headers=headers_a).json()
    assert mine["total"] == 1 and mine["items"][0]["quote_count"] == 1
    detail = client.get(f"{API}/portal-searches/{run.id}", headers=headers_a).json()
    assert detail["quotes"][0]["plan_name"] == "Secreto A"
    assert detail["user_message_key"] == "timeout"
    assert "error_detail" not in detail
    kinds = [a["kind"] for a in detail["alternatives"]]
    assert kinds[:2] == ["retry", "upload_quote_pdf"] and "other_portal" in kinds
    assert detail["alternatives"][0]["available"] is True


# --- engine (scripted connector, fake page) -------------------------------------------------

def test_credentials_missing_fails_without_a_browser_inline(client, db, world, catalog, headers_a_exec, monkeypatch):
    rpa_on(monkeypatch, inline=True)
    monkeypatch.setattr(engine, "default_browser_factory", no_browser)
    res = client.post(
        f"{API}/portal-searches",
        json={"portal": "hdi", "insurance_subtype_id": subtype_id(db), "input": VALID_INPUT},
        headers=headers_a_exec,
    )
    assert res.status_code == 201
    run_id = res.json()["id"]
    detail = client.get(f"{API}/portal-searches/{run_id}", headers=headers_a_exec).json()
    assert detail["status"] == "failed"
    assert detail["error_kind"] == detail["user_message_key"] == "credentials_missing"
    assert detail["steps"][-1]["ok"] is False and detail["screenshot_document_id"] is None

    notes = _notifications(db, run_id)
    assert [(n.user_id, n.kind, n.tray.value) for n in notes] == [
        (world.a.executive.id, NotificationKind.PORTAL_SEARCH_FAILED, "fyi")
    ]
    assert notes[0].params["error_kind"] == "credentials_missing"
    assert _ops_mails(db) == []  # broker-actionable: the RADAL team is not paged


def test_success_persists_quotes_marks_credential_valid_and_notifies(db, world):
    store_credential(db, world.a)
    run = make_run(db, world.a)
    connector = ScriptedConnector(insurer_rut=world.insurer.rut)
    out = engine.execute_run(db, run, connector=connector, browser_factory=fake_browser, now=T0)

    assert out.status == PortalRunStatus.SUCCEEDED and out.error_kind is None
    assert out.connector_version == connector.version
    quotes = db.scalars(select(PortalQuote).where(PortalQuote.run_id == run.id)).all()
    assert [(q.plan_name, q.premium_uf, q.premium_clp, q.insurer_id, q.broker_id) for q in quotes] == [
        ("Full", Decimal("24.5000"), 950000, world.insurer.id, world.a.broker_id)
    ]
    cred = db.scalar(select(PortalCredential))
    assert cred.status == CredentialStatus.VALID and cred.last_verified_at is not None
    (note,) = _notifications(db, run.id)
    assert note.kind == NotificationKind.PORTAL_SEARCH_DONE and note.params["quote_count"] == 1
    assert note.link_path == f"/portal-search?run={run.id}"
    # Executing again is a no-op (claim is queued-only).
    assert engine.execute_run(db, out, connector=connector, browser_factory=fake_browser).status == PortalRunStatus.SUCCEEDED
    assert connector.calls == 1


def test_login_failed_marks_credential_invalid_without_paging_ops(db, world):
    store_credential(db, world.a)
    run = make_run(db, world.a)
    out = engine.execute_run(db, run, connector=ScriptedConnector(raises=LoginFailed("bad", step="login_result")),
                             browser_factory=fake_browser, now=T0)
    assert out.error_kind == PortalErrorKind.LOGIN_FAILED
    cred = db.scalar(select(PortalCredential))
    assert cred.status == CredentialStatus.INVALID and "rechazó" in cred.last_error
    assert out.screenshot_document_id is not None
    assert _ops_mails(db) == []


def test_layout_changed_pages_ops_stores_screenshot_and_flips_maintenance(client, db, world, headers_a, caplog, monkeypatch):
    rpa_on(monkeypatch)
    store_credential(db, world.a)
    connector = ScriptedConnector(raises=LayoutChanged("selector not found: #plate", step="fill_plate"))
    monkeypatch.setitem(CONNECTORS, "hdi", connector)

    caplog.set_level(logging.ERROR, logger="app.services.rpa.engine")
    first = engine.execute_run(db, make_run(db, world.a), connector=connector, browser_factory=fake_browser, now=T0)
    assert first.error_kind == PortalErrorKind.LAYOUT_CHANGED
    doc = db.get(Document, first.screenshot_document_id)
    assert doc.category == DocumentCategory.PORTAL_SCREENSHOT
    assert doc.entity_type == EntityType.PORTAL_SEARCH and doc.entity_id == first.id
    assert doc.broker_id == world.a.broker_id and doc.mime_type == "image/png"
    (mail,) = _ops_mails(db)
    assert mail.status == MailMessageStatus.SKIPPED and mail.broker_id is None
    assert "layout_changed" in mail.subject and "mantención" not in mail.subject
    assert any("error_kind=layout_changed" in r.getMessage() and f"version={connector.version}" in r.getMessage()
               for r in caplog.records if r.levelno == logging.ERROR)
    assert client.get(f"{API}/portal-searches/portals", headers=headers_a).json()[0]["status"] == "available"

    # A broker-actionable failure in between does not break the streak.
    engine.execute_run(db, make_run(db, world.b), connector=connector, browser_factory=fake_browser,
                       now=T0 + timedelta(minutes=1))  # broker B: no credentials
    second = engine.execute_run(db, make_run(db, world.a), connector=connector, browser_factory=fake_browser,
                                now=T0 + timedelta(minutes=2))
    assert second.error_kind == PortalErrorKind.LAYOUT_CHANGED
    assert "mantención" in _ops_mails(db)[-1].subject

    monkeypatch.setattr(engine, "utcnow", lambda: T0 + timedelta(minutes=3))
    hdi = client.get(f"{API}/portal-searches/portals", headers=headers_a).json()[0]
    assert hdi["status"] == "maintenance" and hdi["maintenance_code"] == "layout_changed"
    res = client.post(f"{API}/portal-searches/{second.id}/retry", headers=headers_a)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "portal_maintenance"

    # The team fixes the map and bumps the version: the flag clears.
    monkeypatch.setattr(connector, "version", "2099.1.1")
    assert client.get(f"{API}/portal-searches/portals", headers=headers_a).json()[0]["status"] == "available"
    # ... and the window is 24 h.
    monkeypatch.setattr(connector, "version", second.connector_version)
    monkeypatch.setattr(engine, "utcnow", lambda: T0 + timedelta(hours=25))
    assert client.get(f"{API}/portal-searches/portals", headers=headers_a).json()[0]["status"] == "available"


def _hdi(client, headers):
    return client.get(f"{API}/portal-searches/portals", headers=headers).json()[0]


def test_one_brokers_layout_streak_holds_back_only_that_broker(client, db, world, catalog, headers_a, headers_b, monkeypatch):
    """A streak from ONE broker (e.g. its own HDI account shows a forced
    password change page) must never lock the portal for every tenant."""
    rpa_on(monkeypatch)
    store_credential(db, world.a)
    store_credential(db, world.b)
    connector = ScriptedConnector(raises=LayoutChanged("login_result never appeared", step="login_result"))
    monkeypatch.setitem(CONNECTORS, "hdi", connector)
    for minute in (0, 1):
        engine.execute_run(db, make_run(db, world.b), connector=connector, browser_factory=fake_browser,
                           now=T0 + timedelta(minutes=minute))
    mail = _ops_mails(db)[-1]
    assert f"corredora #{world.b.broker_id}" in mail.subject and "conector en mantención" not in mail.subject
    monkeypatch.setattr(engine, "utcnow", lambda: T0 + timedelta(minutes=5))

    hdi_b = _hdi(client, headers_b)
    assert hdi_b["status"] == "maintenance" and hdi_b["maintenance_scope"] == "broker"
    assert "tu corredora" in hdi_b["maintenance_reason"]
    body = {"portal": "hdi", "insurance_subtype_id": subtype_id(db), "input": VALID_INPUT}
    res = client.post(f"{API}/portal-searches", json=body, headers=headers_b)
    assert res.status_code == 422 and res.json()["detail"]["maintenance_scope"] == "broker"

    # Broker A is untouched.
    assert _hdi(client, headers_a)["status"] == "available"
    res = client.post(f"{API}/portal-searches", json=body, headers=headers_a)
    assert res.status_code == 201, res.text
    client.post(f"{API}/portal-searches/{res.json()['id']}/cancel", headers=headers_a)
    assert engine.portal_availability(db, connector).available  # no global flag

    # A second broker hitting the same wall: now it is the portal, for everyone.
    engine.execute_run(db, make_run(db, world.a), connector=connector, browser_factory=fake_browser,
                       now=T0 + timedelta(minutes=6))
    assert "conector en mantención" in _ops_mails(db)[-1].subject
    monkeypatch.setattr(engine, "utcnow", lambda: T0 + timedelta(minutes=7))
    for headers in (headers_a, headers_b):
        hdi = _hdi(client, headers)
        assert hdi["status"] == "maintenance" and hdi["maintenance_scope"] == "global"


def test_maintenance_self_heals_with_one_probe_after_the_cool_down(client, db, world, catalog, headers_a, monkeypatch):
    rpa_on(monkeypatch)
    store_credential(db, world.a)
    store_credential(db, world.b)
    broken = ScriptedConnector(raises=LayoutChanged("selector not found: #plate", step="fill_plate"))
    monkeypatch.setitem(CONNECTORS, "hdi", broken)
    engine.execute_run(db, make_run(db, world.b), connector=broken, browser_factory=fake_browser, now=T0)
    engine.execute_run(db, make_run(db, world.a), connector=broken, browser_factory=fake_browser,
                       now=T0 + timedelta(minutes=1))
    body = {"portal": "hdi", "insurance_subtype_id": subtype_id(db), "input": VALID_INPUT}

    monkeypatch.setattr(engine, "utcnow", lambda: T0 + timedelta(minutes=1) + engine.MAINTENANCE_PROBE_AFTER
                        - timedelta(minutes=1))
    assert _hdi(client, headers_a)["maintenance_scope"] == "global"

    # Cool-down over: exactly one probe gets through.
    probe_at = T0 + timedelta(minutes=2) + engine.MAINTENANCE_PROBE_AFTER
    monkeypatch.setattr(engine, "utcnow", lambda: probe_at)
    assert _hdi(client, headers_a)["status"] == "available"
    res = client.post(f"{API}/portal-searches", json=body, headers=headers_a)
    assert res.status_code == 201, res.text
    probe_id = res.json()["id"]
    res = client.post(f"{API}/portal-searches", json=body, headers=headers_a)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "portal_maintenance"

    # The probe fails the same way: the cool-down restarts.
    probe = engine.execute_run(db, db.get(PortalSearchRun, probe_id), connector=broken,
                               browser_factory=fake_browser, now=probe_at)
    assert probe.error_kind == PortalErrorKind.LAYOUT_CHANGED
    monkeypatch.setattr(engine, "utcnow", lambda: probe_at + timedelta(minutes=5))
    assert _hdi(client, headers_a)["status"] == "maintenance"

    # Next window: the probe succeeds and the streak is broken for everyone.
    later = probe_at + timedelta(minutes=1) + engine.MAINTENANCE_PROBE_AFTER
    monkeypatch.setattr(engine, "utcnow", lambda: later)
    fixed = ScriptedConnector(quotes=[QuoteOut(plan_name="Full", premium_uf=Decimal("20.5"))])
    monkeypatch.setitem(CONNECTORS, "hdi", fixed)
    fixed.version = broken.version
    res = client.post(f"{API}/portal-searches", json=body, headers=headers_a)
    assert res.status_code == 201, res.text
    ok = engine.execute_run(db, db.get(PortalSearchRun, res.json()["id"]), connector=fixed,
                            browser_factory=fake_browser, now=later)
    assert ok.status == PortalRunStatus.SUCCEEDED
    assert _hdi(client, headers_a)["status"] == "available"
    assert client.post(f"{API}/portal-searches", json=body, headers=headers_a).status_code == 201


def test_retrying_the_same_failed_run_twice_queues_one_session(client, db, world, catalog, headers_a, headers_a_exec, monkeypatch):
    rpa_on(monkeypatch)
    failed = make_run(db, world.a, status=PortalRunStatus.FAILED, error_kind=PortalErrorKind.TIMEOUT,
                      insurance_subtype_id=subtype_id(db))
    first = client.post(f"{API}/portal-searches/{failed.id}/retry", headers=headers_a_exec)
    assert first.status_code == 201, first.text
    # A double click, or a colleague: the live clone comes back, nothing new is queued.
    for headers in (headers_a_exec, headers_a):
        again = client.post(f"{API}/portal-searches/{failed.id}/retry", headers=headers)
        assert again.status_code == 200, again.text
        assert again.json()["id"] == first.json()["id"]
    live = db.scalars(select(PortalSearchRun).where(PortalSearchRun.status == PortalRunStatus.QUEUED)).all()
    assert [r.id for r in live] == [first.json()["id"]]
    assert db.scalar(select(func.count(Activity.id)).where(Activity.action == "portal_search.retried")) == 1

    detail = client.get(f"{API}/portal-searches/{failed.id}", headers=headers_a).json()
    assert detail["can_retry"] is False
    retry_alt = next(a for a in detail["alternatives"] if a["kind"] == "retry")
    assert retry_alt["available"] is False and "en curso" in retry_alt["reason"]
    listed = {r["id"]: r for r in client.get(f"{API}/portal-searches", headers=headers_a).json()["items"]}
    assert listed[failed.id]["can_retry"] is False and listed[first.json()["id"]]["can_cancel"] is True

    # Once the clone is over, the source can be retried again.
    client.post(f"{API}/portal-searches/{first.json()['id']}/cancel", headers=headers_a)
    assert client.get(f"{API}/portal-searches/{failed.id}", headers=headers_a).json()["can_retry"] is True
    assert client.post(f"{API}/portal-searches/{failed.id}/retry", headers=headers_a).status_code == 201


def test_steps_and_error_detail_never_carry_secrets(client, db, world, headers_a):
    store_credential(db, world.a)

    def leak(ctx):
        ctx.log_step("fill_password", True, f"typed {ctx.credentials['password']} <input value='x'>", op="fill")

    connector = ScriptedConnector(raises=PortalTimeout(f"timeout while typing {PASSWORD}"), on_run=leak)
    run = engine.execute_run(db, make_run(db, world.a), connector=connector, browser_factory=fake_browser, now=T0)
    stored = json.dumps(run.steps) + (run.error_detail or "")
    assert PASSWORD not in stored and "***" in stored and "<input" not in stored
    assert run.error_kind == PortalErrorKind.TIMEOUT
    body = client.get(f"{API}/portal-searches/{run.id}", headers=headers_a).text
    assert PASSWORD not in body and "timeout while typing" not in body
    assert PASSWORD not in json.dumps([m.body_text for m in _ops_mails(db)])


def test_unexpected_exceptions_and_launch_failures_are_typed(db, world):
    store_credential(db, world.a)
    run = engine.execute_run(db, make_run(db, world.a), connector=ScriptedConnector(raises=ZeroDivisionError("boom")),
                             browser_factory=fake_browser, now=T0)
    assert run.status == PortalRunStatus.FAILED and run.error_kind == PortalErrorKind.UNEXPECTED

    @contextmanager
    def broken_launch():
        raise RuntimeError("chromium missing")
        yield  # pragma: no cover

    run = engine.execute_run(db, make_run(db, world.a), connector=ScriptedConnector(), browser_factory=broken_launch, now=T0)
    assert run.error_kind == PortalErrorKind.UNEXPECTED and run.steps[-1]["ok"] is False
    assert "chromium missing" in run.error_detail


def test_cancel_retry_semantics(client, db, world, catalog, headers_a, headers_a_exec, monkeypatch):
    rpa_on(monkeypatch)
    queued = make_run(db, world.a)
    res = client.post(f"{API}/portal-searches/{queued.id}/cancel", headers=headers_a_exec)
    assert res.status_code == 200 and res.json()["status"] == "cancelled"
    # A cancelled run is never executed.
    db.expire_all()
    run = engine.execute_run(db, db.get(PortalSearchRun, queued.id), connector=ScriptedConnector(),
                             browser_factory=no_browser)
    assert run.status == PortalRunStatus.CANCELLED

    failed = make_run(db, world.a, status=PortalRunStatus.FAILED, error_kind=PortalErrorKind.TIMEOUT,
                      insurance_subtype_id=subtype_id(db))
    from app.services.notifications import notify

    notify(db, broker_id=world.a.broker_id, user_ids=[world.a.executive.id], kind="portal_search_failed",
           tray="fyi", origin_type="portal_search", origin_id=failed.id)
    db.commit()
    res = client.post(f"{API}/portal-searches/{failed.id}/retry", headers=headers_a_exec)
    assert res.status_code == 201, res.text
    clone = res.json()
    assert clone["id"] != failed.id and clone["status"] == "queued"
    assert clone["input"] == failed.input and clone["insurance_subtype_code"] == "VEH-01"
    assert clone["requested_by_user_id"] == world.a.executive.id
    assert all(n.status.value == "closed" for n in _notifications(db, failed.id))

    assert client.post(f"{API}/portal-searches/{clone['id']}/retry", headers=headers_a).status_code == 422
    done = make_run(db, world.a, status=PortalRunStatus.SUCCEEDED)
    res = client.post(f"{API}/portal-searches/{done.id}/cancel", headers=headers_a)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "not_cancellable"


def test_cancel_while_running_discards_the_outcome(db, world):
    store_credential(db, world.a)
    run = make_run(db, world.a)

    def cancel_midway(ctx):
        from app.db.session import SessionLocal

        other = SessionLocal()
        try:
            row = other.get(PortalSearchRun, run.id)
            row.status = PortalRunStatus.CANCELLED
            other.commit()
        finally:
            other.close()

    out = engine.execute_run(db, run, connector=ScriptedConnector(on_run=cancel_midway), browser_factory=fake_browser)
    assert out.status == PortalRunStatus.CANCELLED
    assert db.scalar(select(PortalQuote.id)) is None and _notifications(db, run.id) == []


# --- job ------------------------------------------------------------------------------------

def test_job_rpa_run_skips_when_disabled_and_runs_oldest_three(db, world, monkeypatch):
    from app.jobs import run_job

    monkeypatch.setattr(settings, "RPA_ENABLED", False)
    make_run(db, world.a)
    assert run_job(db, "rpa.run", T0).status.value == "skipped"

    rpa_on(monkeypatch)
    store_credential(db, world.a)
    for _ in range(3):
        make_run(db, world.a)
    stale = make_run(db, world.a, status=PortalRunStatus.RUNNING, started_at=T0 - timedelta(hours=1))
    connector = ScriptedConnector()
    monkeypatch.setitem(CONNECTORS, "hdi", connector)
    monkeypatch.setattr(engine, "default_browser_factory", fake_browser)

    job = run_job(db, "rpa.run", T0 + timedelta(minutes=1))
    assert job.status.value == "succeeded", job.error
    assert job.stats == {"executed": 3, "succeeded": 3, "failed": 0, "reaped": 1}
    db.expire_all()
    statuses = [r.status for r in db.scalars(select(PortalSearchRun).order_by(PortalSearchRun.id)).all()]
    assert statuses.count(PortalRunStatus.SUCCEEDED) == 3 and statuses.count(PortalRunStatus.QUEUED) == 1
    assert db.get(PortalSearchRun, stale.id).status == PortalRunStatus.FAILED
    # Same minute slot: idempotent.
    assert run_job(db, "rpa.run", T0 + timedelta(minutes=1, seconds=30)).id == job.id
    assert connector.calls == 3


def test_portal_error_subclasses_map_one_to_one():
    from app.services.rpa import errors

    kinds = {cls.kind for cls in PortalError.__subclasses__()}
    assert kinds == set(PortalErrorKind) - {PortalErrorKind.UNEXPECTED}
    assert errors.kind_of(ValueError()) == PortalErrorKind.UNEXPECTED
