"""RADAL's maintainer console ``/platform`` (v12, 02 §5) — platform admins only.

Broker users are 403 on every route; secrets are write-only and never come back;
``webhook_url`` is a working URL; rotation keeps the old ``sig`` for 30 days;
channels drive the broker-side level; the event inbox reprocesses idempotently.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.core.crypto import decrypt_secret
from app.jobs import run_job
from app.models.enums import EventProcessing, JobStatus
from app.models.insurer_collection import InsurerCollectionProfile
from app.models.payment import Payment
from app.models.provider import ProviderAccount, ProviderEvent
from app.services.payments.providers.base import PaymentProviderError
from app.services.payments.providers.fake import FakePaymentProvider
from tests.conftest import API, auth_headers
from tests.fakes_v12 import TEST_HMAC_KEY, make_insurer_profile, make_provider_account
from tests.test_v12_payments import create_intent, installments, make_platform_user, native_policy
from tests.test_v12_webhooks import paid_payload, post_hook

SECRETS = {
    "api_key": "SUPER-API-KEY-123",
    "password": "SUPER-PASSWORD-456",
    "hmac_key": "SUPER-HMAC-789",
    "public_key_pem": "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA\n-----END PUBLIC KEY-----",
}


@pytest.fixture()
def headers_p(client, db, world):
    return auth_headers(client, make_platform_user(db).email)


def _create(client, headers, **overrides):
    body = {
        "label": "Radal Mall sandbox",
        "owner": "radal_mall",
        "commerce": "RADAL",
        "channel_header": "WEB",
        "username": "76111111.1",
        **SECRETS,
        **overrides,
    }
    return client.post(f"{API}/platform/provider-accounts", json=body, headers=headers)


ROUTES = [
    ("GET", "/platform/insurer-profiles"),
    ("GET", "/platform/insurer-profiles/1"),
    ("POST", "/platform/insurer-profiles"),
    ("PUT", "/platform/insurer-profiles/1"),
    ("GET", "/platform/provider-accounts"),
    ("GET", "/platform/provider-accounts/1"),
    ("POST", "/platform/provider-accounts"),
    ("PUT", "/platform/provider-accounts/1"),
    ("POST", "/platform/provider-accounts/1/verify"),
    ("POST", "/platform/provider-accounts/1/rotate-webhook-secret"),
    ("GET", "/platform/insurer-channels"),
    ("PUT", "/platform/insurer-channels"),
    ("PUT", "/platform/insurer-channels/1/webpay"),
    ("GET", "/platform/events"),
    ("GET", "/platform/events/1"),
    ("POST", "/platform/events/1/reprocess"),
    ("GET", "/platform/jobs"),
]


def test_broker_users_are_403_everywhere_and_anonymous_401(client, world, headers_a, headers_a_exec):
    for method, path in ROUTES:
        for headers in (headers_a, headers_a_exec):
            res = client.request(method, f"{API}{path}", headers=headers, json={})
            assert res.status_code == 403, (method, path, res.status_code)
        assert client.request(method, f"{API}{path}", json={}).status_code == 401, (method, path)


def test_secrets_are_write_only(client, db, world, headers_p):
    res = _create(client, headers_p)
    assert res.status_code == 201, res.text
    body = res.json()
    for value in SECRETS.values():
        assert value not in res.text
    assert body["has_secret"] is True
    assert body["secret_keys"] == sorted(SECRETS)
    assert body["status"] == "draft"
    assert body["base_url"] == "http://127.0.0.1:9/no-forpay-in-tests"  # FORPAY_DEFAULT_BASE_URL (hermetic)

    acct = db.get(ProviderAccount, body["id"])
    assert decrypt_secret(acct.secret_ciphertext) == SECRETS
    for value in SECRETS.values():
        assert value not in acct.secret_ciphertext

    listed = client.get(f"{API}/platform/provider-accounts", headers=headers_p)
    one = client.get(f"{API}/platform/provider-accounts/{body['id']}", headers=headers_p)
    for response in (listed, one):
        for value in SECRETS.values():
            assert value not in response.text
        assert "ciphertext" not in response.text and '"webhook_secret"' not in response.text

    # partial update keeps the rest; "" clears one key
    upd = client.put(
        f"{API}/platform/provider-accounts/{body['id']}",
        json={"api_key": "ROTATED-KEY", "public_key_pem": "", "label": "Mall"},
        headers=headers_p,
    )
    assert upd.status_code == 200 and "ROTATED-KEY" not in upd.text
    assert upd.json()["secret_keys"] == ["api_key", "hmac_key", "password"]
    db.expire_all()
    bundle = decrypt_secret(db.get(ProviderAccount, body["id"]).secret_ciphertext)
    assert bundle == {"api_key": "ROTATED-KEY", "password": SECRETS["password"], "hmac_key": SECRETS["hmac_key"]}


def test_webhook_url_is_a_working_url(client, db, world, headers_p, fake_provider):
    body = _create(client, headers_p, status="active").json()
    url = urlparse(body["webhook_url"])
    assert url.path == "/api/v1/webhooks/forpay"
    query = parse_qs(url.query)
    assert query["account"] == [body["webhook_key"]] and query["kind"] == ["payment"]
    assert set(body["webhook_urls"]) == {"payment", "activation"}
    # the sig in the URL authenticates: an empty body is 400 (past the 401 gate)
    res = client.post(url.path, params={k: v[0] for k, v in query.items()}, content=b"")
    assert res.status_code == 400
    res = client.post(url.path, params={**{k: v[0] for k, v in query.items()}, "sig": "bad"}, content=b"")
    assert res.status_code == 401


def test_rotation_keeps_the_old_sig_for_new_and_old_urls(client, db, world, headers_p, fake_provider):
    body = _create(client, headers_p).json()
    old = parse_qs(urlparse(body["webhook_url"]).query)
    rotated = client.post(f"{API}/platform/provider-accounts/{body['id']}/rotate-webhook-secret", headers=headers_p)
    assert rotated.status_code == 200
    new_body = rotated.json()
    new = parse_qs(urlparse(new_body["webhook_url"]).query)
    assert new["sig"] != old["sig"] and new["account"] == old["account"]
    assert new_body["previous_webhook_secret_valid_until"] is not None
    for query in (old, new):
        res = client.post("/api/v1/webhooks/forpay", params={k: v[0] for k, v in query.items()}, content=b"")
        assert res.status_code == 400
    assert "previous_webhook_secret" not in rotated.text.replace("previous_webhook_secret_valid_until", "")


def test_account_validation(client, db, world, headers_p):
    res = _create(client, headers_p, owner="insurer")
    assert res.status_code == 422 and "insurer_id" in res.json()["detail"]
    res = client.post(
        f"{API}/platform/provider-accounts",
        json={"label": "x", "owner": "radal_mall", "status": "active"},
        headers=headers_p,
    )
    assert res.status_code == 422 and res.json()["detail"].startswith("Para activar la cuenta faltan")
    res = _create(client, headers_p, base_url="ftp://forpay")
    assert res.status_code == 422
    res = _create(client, headers_p, environment="production", base_url="http://forpay.cl")
    assert res.status_code == 422
    res = _create(client, headers_p, owner="insurer", insurer_id=world.insurer.id, status="active")
    assert res.status_code == 201 and res.json()["insurer_name"] == "HDI Seguros S.A."
    assert client.get(f"{API}/platform/provider-accounts/99999", headers=headers_p).status_code == 404


def test_verify_records_success_and_failure(client, db, world, headers_p, fake_provider):
    body = _create(client, headers_p).json()
    ok = client.post(f"{API}/platform/provider-accounts/{body['id']}/verify", headers=headers_p)
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert ok.json()["account"]["last_verified_at"] is not None
    assert fake_provider.calls_to("search_receivables")  # the fake has no login probe

    fake_provider.fail_next = PaymentProviderError("Forpay respondió 403 en /login")
    bad = client.post(f"{API}/platform/provider-accounts/{body['id']}/verify", headers=headers_p)
    assert bad.json()["ok"] is False and "403" in bad.json()["error"]
    assert bad.json()["account"]["last_error"] == bad.json()["error"]
    for value in SECRETS.values():
        assert value not in bad.text


def test_verify_uses_the_login_probe_when_available(client, db, world, headers_p, monkeypatch):
    from app.services.payments import providers as registry

    class Probe(FakePaymentProvider):
        def __init__(self):
            super().__init__()
            self.logins = 0

        def verify_credentials(self, acct):  # noqa: ANN001
            self.logins += 1
            return True

    probe = Probe()
    monkeypatch.setattr(registry, "_OVERRIDE", probe)
    body = _create(client, headers_p).json()
    assert client.post(f"{API}/platform/provider-accounts/{body['id']}/verify", headers=headers_p).json()["ok"]
    assert probe.logins == 1 and probe.calls_to("search_receivables") == []


def test_insurer_profiles(client, db, world, headers_p, headers_a):
    rows = client.get(f"{API}/platform/insurer-profiles", headers=headers_p).json()
    assert {r["insurer_id"] for r in rows} == {world.insurer.id, world.insurer_no_cmf.id, world.insurer_no_rut.id}
    assert all(r["onboarding_status"] == "not_contacted" and r["has_profile"] is False for r in rows)

    res = client.put(
        f"{API}/platform/insurer-profiles/{world.insurer.id}",
        json={
            "onboarding_status": "sandbox",
            "status_note": "reunión 20-10",
            "expected_live_on": "2026-11-30",
            "collections_email": "cobranza@hdi.cl",
            "sender_domains": ["@HDI.cl", "hdi.cl", "hdiseguros.cl"],
            "payment_instructions": {"bank_name": "Banco HDI", "unknown": "x"},
        },
        headers=headers_p,
    )
    assert res.status_code == 200, res.text
    row = res.json()
    assert row["has_profile"] and row["onboarding_status"] == "sandbox"
    assert row["sender_domains"] == ["hdi.cl", "hdiseguros.cl"]
    assert row["payment_instructions"] == {"bank_name": "Banco HDI"}
    assert db.get(InsurerCollectionProfile, world.insurer.id).updated_by_user_id is not None

    assert client.post(
        f"{API}/platform/insurer-profiles", json={"insurer_id": world.insurer.id}, headers=headers_p
    ).status_code == 409
    assert client.post(f"{API}/platform/insurer-profiles", json={"insurer_id": 99999}, headers=headers_p).status_code == 404
    created = client.post(
        f"{API}/platform/insurer-profiles",
        json={"insurer_id": world.insurer_no_cmf.id, "onboarding_status": "in_conversation"},
        headers=headers_p,
    )
    assert created.status_code == 201
    assert client.put(
        f"{API}/platform/insurer-profiles/{world.insurer.id}", json={"sender_domains": ["no valido"]}, headers=headers_p
    ).status_code == 422

    filtered = client.get(f"{API}/platform/insurer-profiles", params={"onboarding_status": "not_contacted"}, headers=headers_p)
    assert [r["insurer_id"] for r in filtered.json()] == [world.insurer_no_rut.id]
    assert client.get(f"{API}/platform/insurer-profiles", params={"q": "HDI"}, headers=headers_p).json()[0]["insurer_id"] == world.insurer.id

    # the broker never sees it
    assert "sandbox" not in client.get(f"{API}/payments/insurers", params={"scope": "all"}, headers=headers_a).text
    assert "reunión" not in client.get(f"{API}/payments/insurers", params={"scope": "all"}, headers=headers_a).text


def test_channels_drive_the_broker_level(client, db, world, headers_p, headers_a, fake_provider):
    native_policy(db, world.a, world.insurer, channel=False)
    acct = _create(client, headers_p, status="active").json()
    url = f"{API}/platform/insurer-channels/{world.insurer.id}/webpay"

    assert client.put(url, json={"status": "active"}, headers=headers_p).status_code == 422
    assert client.put(url, json={"provider_account_id": 99999}, headers=headers_p).status_code == 422
    assert client.get(f"{API}/payments/insurers", headers=headers_a).json()[0]["level_available"] == "assisted"

    res = client.put(
        url, json={"provider_account_id": acct["id"], "store_commerce_code": "597055555536", "status": "active"},
        headers=headers_p,
    )
    assert res.status_code == 200, res.text
    channel = res.json()
    assert channel["status"] == "active" and channel["activated_at"] is not None
    assert channel["provider_account_label"] == "Radal Mall sandbox"
    # Review v12 #13/#17: an ACTIVE account serves native collection only once
    # it has passed *Verificar*.
    level = client.get(f"{API}/payments/insurers", headers=headers_a).json()[0]
    assert level["level_available"] == "assisted"
    verified = client.post(f"{API}/platform/provider-accounts/{acct['id']}/verify", headers=headers_p)
    assert verified.status_code == 200 and verified.json()["ok"] is True, verified.text
    level = client.get(f"{API}/payments/insurers", headers=headers_a).json()[0]
    assert level["level_available"] == "native" and level["channels"] == ["webpay"]

    bulk = client.put(
        f"{API}/platform/insurer-channels",
        json=[
            {"insurer_id": world.insurer.id, "channel": "webpay", "status": "suspended"},
            {"insurer_id": world.insurer_no_cmf.id, "channel": "bank_transfer", "provider_account_id": acct["id"]},
        ],
        headers=headers_p,
    )
    assert bulk.status_code == 200 and [c["status"] for c in bulk.json()] == ["suspended", "pending"]
    assert client.get(f"{API}/payments/insurers", headers=headers_a).json()[0]["level_available"] == "assisted"
    listed = client.get(f"{API}/platform/insurer-channels", params={"insurer_id": world.insurer.id}, headers=headers_p)
    assert len(listed.json()) == 1
    bad = client.put(
        f"{API}/platform/insurer-channels", json=[{"insurer_id": 99999, "channel": "webpay"}], headers=headers_p
    )
    assert bad.status_code == 404
    assert client.put(f"{API}/platform/insurer-channels/{world.insurer.id}/carrier-pigeon", json={}, headers=headers_p).status_code == 422


def test_event_inbox_and_reprocess(client, db, world, headers_p, fake_provider):
    _, plan, acct = native_policy(db, world.a, world.insurer)
    intent, _ = create_intent(db, world.a, [installments(plan)[0].id])
    item = intent.items[0]
    post_hook(client, acct, paid_payload(item, tamper=True))
    post_hook(client, acct, paid_payload(item))

    page = client.get(f"{API}/platform/events", headers=headers_p).json()
    assert page["total"] == 2
    assert "payload" not in page["items"][0]
    invalid = client.get(f"{API}/platform/events", params={"signature_valid": False}, headers=headers_p).json()
    assert invalid["total"] == 1
    processed = client.get(f"{API}/platform/events", params={"status": "processed"}, headers=headers_p).json()
    assert processed["total"] == 1
    event_id = processed["items"][0]["id"]
    detail = client.get(f"{API}/platform/events/{event_id}", headers=headers_p).json()
    assert detail["payload"]["installment"]["id"] == item.external_installment_id

    again = client.post(f"{API}/platform/events/{event_id}/reprocess", headers=headers_p)
    assert again.status_code == 200 and again.json()["attempts"] == 2
    assert again.json()["processing_status"] == "processed"
    assert len(db.scalars(select(Payment)).all()) == 1  # idempotent

    assert client.post(f"{API}/platform/events/{invalid['items'][0]['id']}/reprocess", headers=headers_p).status_code == 409
    assert client.get(f"{API}/platform/events/99999", headers=headers_p).status_code == 404
    db.expire_all()
    assert db.get(ProviderEvent, event_id).processing_status == EventProcessing.PROCESSED


def test_job_runs(client, db, world, headers_p, fake_provider):
    from datetime import datetime, timezone

    run_job(db, "noop", datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc))
    run_job(db, "payments.sync", datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc))
    page = client.get(f"{API}/platform/jobs", headers=headers_p).json()
    assert page["total"] == 2
    assert {"noop", "payments.sync", "payments.payouts"} <= set(page["registered"])
    only = client.get(f"{API}/platform/jobs", params={"job": "payments.sync"}, headers=headers_p).json()
    assert only["total"] == 1 and only["items"][0]["status"] == JobStatus.SKIPPED.value
    assert client.get(f"{API}/platform/jobs", params={"status": "failed"}, headers=headers_p).json()["total"] == 0


def test_profile_fixture_rows_show_up(client, db, world, headers_p):
    make_insurer_profile(db, world.insurer_no_rut, expected_live_on=date(2026, 12, 1))
    db.commit()
    row = client.get(f"{API}/platform/insurer-profiles/{world.insurer_no_rut.id}", headers=headers_p).json()
    assert row["has_profile"] and row["expected_live_on"] == "2026-12-01"
    assert client.get(f"{API}/platform/insurer-profiles/99999", headers=headers_p).status_code == 404


def test_make_provider_account_fixture_is_listed_without_secrets(client, db, world, headers_p):
    make_provider_account(db, hmac_key=TEST_HMAC_KEY)
    db.commit()
    text = client.get(f"{API}/platform/provider-accounts", headers=headers_p).text
    assert TEST_HMAC_KEY not in text and "test-api-key" not in text and "test-password" not in text
