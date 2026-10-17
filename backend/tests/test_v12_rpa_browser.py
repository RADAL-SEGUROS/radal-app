"""v12 lane B6 — the RPA engine end to end in REAL Chromium (06-rpa §5).

The real :class:`HdiConnector` (driven by ``selectors/hdi.json``) runs against
a LOCAL fake HDI portal (``tests/fixtures/rpa/fake_hdi``) served by a
``ThreadingHTTPServer`` on an ephemeral 127.0.0.1 port — hermetic, no network.
Skipped cleanly when Playwright/Chromium is unavailable (it is installed here
for the PDF engine, so these run on this machine).
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.document import Document, DocumentCategory
from app.models.enums import CredentialStatus, NotificationKind, PortalErrorKind, PortalRunStatus
from app.models.rpa import PortalCredential, PortalQuote, PortalSearchRun
from app.services.rpa import engine
from app.services.rpa.connectors.hdi import HdiConnector
from tests.conftest import API
from tests.test_v12_rpa import (
    VALID_INPUT,
    _notifications,
    _ops_mails,
    make_run,
    rpa_on,
    store_credential,
    subtype_id,
)

pytest.importorskip("playwright.sync_api")

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rpa"
PORTAL_USER = "corredor@alfa.cl"
PORTAL_PASSWORD = "clave-hdi-123"


@pytest.fixture(autouse=True)
def _media_tmp(tmp_path, monkeypatch):
    """Failure screenshots are real files: keep them out of backend/media."""
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))


@pytest.fixture(scope="module", autouse=True)
def _chromium_available():
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            pw.chromium.launch(headless=True).close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Chromium unavailable: {exc}")


class _FakePortal(BaseHTTPRequestHandler):
    """Serves fake_hdi/ (optionally overlaid by a variant dir)."""

    variant: str | None = None
    results_delay_ms: int = 150

    def do_GET(self):  # noqa: N802
        name = Path(urlparse(self.path).path).name or "login.html"
        candidates = ([FIXTURES / self.variant / name] if self.variant else []) + [FIXTURES / "fake_hdi" / name]
        for path in candidates:
            if path.is_file():
                body = path.read_text(encoding="utf-8")
                if name == "results.html":
                    body = body.replace("__RESULTS_DELAY_MS__", str(int(self.results_delay_ms)))
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_args):  # silence
        return


@pytest.fixture(scope="module")
def portal_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakePortal)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture()
def portal(portal_server, monkeypatch):
    """The fake portal, reset per test; RPA switched on and pointed at it."""
    monkeypatch.setattr(_FakePortal, "variant", None)
    monkeypatch.setattr(_FakePortal, "results_delay_ms", 150)
    rpa_on(monkeypatch, base_url=portal_server)
    return _FakePortal


def _run(db, tenant, *, plate: str | None = None, **kw) -> PortalSearchRun:
    from app.services.rpa.connectors.hdi import validate_vehicle_input

    data = {**VALID_INPUT, **({"plate": plate} if plate else {})}
    run = make_run(db, tenant, input=validate_vehicle_input(data))
    return engine.execute_run(db, run, connector=HdiConnector(), **kw)


def test_success_parses_the_results_table(client, db, world, portal, headers_a):
    store_credential(db, world.a, username=PORTAL_USER, password=PORTAL_PASSWORD)
    run = _run(db, world.a)

    assert run.status == PortalRunStatus.SUCCEEDED, (run.error_kind, run.error_detail, run.steps)
    quotes = db.scalars(select(PortalQuote).where(PortalQuote.run_id == run.id).order_by(PortalQuote.id)).all()
    assert [(q.plan_name, str(q.premium_uf), q.premium_clp, q.deductible) for q in quotes] == [
        ("Full Cobertura", "24.5000", 950000, "UF 3"),
        ("Pérdida Total", "12.7500", 494250, "UF 10"),
    ]
    assert quotes[0].coverages == ["Daños propios", "Responsabilidad civil", "Robo"]
    assert all(step["ok"] for step in run.steps)
    assert [s["name"] for s in run.steps][-1] == "read_results"
    assert PORTAL_PASSWORD not in json.dumps(run.steps)
    assert db.scalar(select(PortalCredential)).status == CredentialStatus.VALID
    (note,) = _notifications(db, run.id)
    assert note.kind == NotificationKind.PORTAL_SEARCH_DONE and note.params["quote_count"] == 2

    detail = client.get(f"{API}/portal-searches/{run.id}", headers=headers_a).json()
    assert detail["quote_count"] == 2 and detail["quotes"][1]["premium_clp"] == 494250
    assert detail["alternatives"] == [] and detail["user_message_key"] is None


def test_login_failed(db, world, portal):
    store_credential(db, world.a, username=PORTAL_USER, password="mala-clave")
    run = _run(db, world.a)
    assert run.error_kind == PortalErrorKind.LOGIN_FAILED
    assert run.steps[-1]["name"] == "login_result" and run.steps[-1]["ok"] is False
    assert db.scalar(select(PortalCredential)).status == CredentialStatus.INVALID
    assert run.screenshot_document_id is not None
    assert _ops_mails(db) == []
    assert "mala-clave" not in json.dumps(run.steps) + (run.error_detail or "")


def test_layout_changed_twice_flips_the_portal_to_maintenance(client, db, world, catalog, portal, headers_a, monkeypatch):
    monkeypatch.setattr(portal, "variant", "fake_hdi_layout_changed")
    store_credential(db, world.a, username=PORTAL_USER, password=PORTAL_PASSWORD)

    # A missing field fails after its step timeout; keep it short here.
    fast = {"step_timeouts": {"fill_plate": 1000}}
    first = _run(db, world.a, **fast)
    assert first.error_kind == PortalErrorKind.LAYOUT_CHANGED
    failed_step = first.steps[-1]
    assert failed_step["name"] == "fill_plate" and "#plate" in failed_step["detail"]
    doc = db.get(Document, first.screenshot_document_id)
    assert doc.category == DocumentCategory.PORTAL_SCREENSHOT and (doc.size_bytes or 0) > 1000
    assert client.get(f"{API}/portal-searches/portals", headers=headers_a).json()[0]["status"] == "available"

    second = _run(db, world.a, **fast)
    assert second.error_kind == PortalErrorKind.LAYOUT_CHANGED
    mails = _ops_mails(db)
    assert len(mails) == 2 and "mantención" in mails[-1].subject
    hdi = client.get(f"{API}/portal-searches/portals", headers=headers_a).json()[0]
    assert hdi["status"] == "maintenance" and hdi["maintenance_code"] == "layout_changed"

    res = client.post(
        f"{API}/portal-searches",
        json={"portal": "hdi", "insurance_subtype_id": subtype_id(db), "input": VALID_INPUT},
        headers=headers_a,
    )
    assert res.status_code == 422 and res.json()["detail"]["code"] == "portal_maintenance"


def test_slow_portal_is_a_timeout(db, world, portal, monkeypatch):
    monkeypatch.setattr(portal, "results_delay_ms", 10000)
    store_credential(db, world.a, username=PORTAL_USER, password=PORTAL_PASSWORD)
    run = _run(db, world.a, step_timeouts={"wait_results": 300})
    assert run.error_kind == PortalErrorKind.TIMEOUT
    assert run.steps[-1]["name"] == "wait_results"
    assert len(_ops_mails(db)) == 1


def test_captcha_and_no_results(db, world, portal):
    store_credential(db, world.a, username=PORTAL_USER, password=PORTAL_PASSWORD)
    assert _run(db, world.a, plate="CAPT01").error_kind == PortalErrorKind.CAPTCHA
    no_plans = _run(db, world.a, plate="ZZZZ99")
    assert no_plans.error_kind == PortalErrorKind.NO_RESULTS
    assert len(_ops_mails(db)) == 1  # the captcha pages the team; "no plans" does not


def test_unreachable_portal_is_portal_unavailable(db, world, portal, monkeypatch):
    with socket.socket() as sock:  # a port nobody listens on
        sock.bind(("127.0.0.1", 0))
        dead = f"http://127.0.0.1:{sock.getsockname()[1]}"
    monkeypatch.setattr("app.core.config.settings.RPA_HDI_BASE_URL", dead)
    store_credential(db, world.a, username=PORTAL_USER, password=PORTAL_PASSWORD)
    run = _run(db, world.a)
    assert run.error_kind == PortalErrorKind.PORTAL_UNAVAILABLE
    assert run.steps[-1]["name"] == "open_login"


def test_inline_api_end_to_end(client, db, world, catalog, portal, headers_a_exec, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.RPA_INLINE", True)
    store_credential(db, world.a, username=PORTAL_USER, password=PORTAL_PASSWORD)
    res = client.post(
        f"{API}/portal-searches",
        json={"portal": "hdi", "insurance_subtype_id": subtype_id(db), "input": VALID_INPUT},
        headers=headers_a_exec,
    )
    assert res.status_code == 201, res.text
    detail = client.get(f"{API}/portal-searches/{res.json()['id']}", headers=headers_a_exec).json()
    assert detail["status"] == "succeeded", detail
    assert detail["quote_count"] == 2 and detail["steps"][0]["name"] == "open_login"
    listing = client.get(f"{API}/portal-searches", headers=headers_a_exec).json()
    assert listing["items"][0]["quote_count"] == 2
