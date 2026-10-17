"""v12 share-link skeleton (app.services.share_links): mint, resolve, revoke, URLs.

The token is the credential: unknown, revoked and expired all resolve to None
(the public router's 404), and a link can only ever point at the creating
broker's own row.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.models.enums import ShareAudience, ShareEntity
from app.services.share_links import (
    DEFAULT_EXPIRES_IN_DAYS,
    ShareTargetNotFound,
    create_share_link,
    link_permissions,
    normalize_permissions,
    record_share_view,
    resolve_share_token,
    revoke_share_link,
    share_url,
    whatsapp_url,
)
from tests.fakes_v12 import make_policy_with_plan

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def policy_a(db, world):
    policy, plan = make_policy_with_plan(db, world.a, world.insurer, n_installments=2)
    db.commit()
    return policy, plan


def _mint(db, world, policy, **kw):
    fields = {
        "broker_id": world.a.broker_id,
        "user": world.a.admin,
        "entity_type": ShareEntity.POLICY,
        "entity_id": policy.id,
        "audience": ShareAudience.INSURED,
        "now": NOW,
    }
    fields.update(kw)
    link = create_share_link(db, **fields)
    db.commit()
    return link


def test_create_and_resolve(db, world, policy_a):
    policy, _plan = policy_a
    link = _mint(db, world, policy, label="Cuotas 2026")
    assert len(link.token) >= 32
    assert link.broker_id == world.a.broker_id
    assert link.created_by_user_id == world.a.admin.id
    assert link.view_count == 0 and link.label == "Cuotas 2026"
    expires = link.expires_at.replace(tzinfo=timezone.utc) if link.expires_at.tzinfo is None else link.expires_at
    assert expires == NOW + timedelta(days=DEFAULT_EXPIRES_IN_DAYS)
    assert link.permissions == {
        "can_pay": True, "can_upload_receipt": True, "can_report_status": False, "show_documents": False,
    }
    found = resolve_share_token(db, link.token, now=NOW + timedelta(days=1))
    assert found is not None and found.id == link.id


def test_tokens_are_unique(db, world, policy_a):
    policy, _plan = policy_a
    tokens = {_mint(db, world, policy).token for _ in range(5)}
    assert len(tokens) == 5


def test_every_entity_type_resolves_inside_the_broker(db, world, policy_a):
    policy, plan = policy_a
    for entity_type, entity_id in (
        (ShareEntity.POLICY, policy.id),
        (ShareEntity.COLLECTION_PLAN, plan.id),
        (ShareEntity.INSTALLMENT, plan.installments[0].id),
    ):
        assert _mint(db, world, policy, entity_type=entity_type, entity_id=entity_id).id


def test_another_brokers_row_cannot_be_shared(db, world, policy_a):
    policy, _plan = policy_a
    with pytest.raises(ShareTargetNotFound):
        create_share_link(
            db, broker_id=world.b.broker_id, user=world.b.admin, entity_type=ShareEntity.POLICY,
            entity_id=policy.id, audience=ShareAudience.INSURED,
        )
    with pytest.raises(ShareTargetNotFound):
        create_share_link(
            db, broker_id=world.a.broker_id, user=None, entity_type=ShareEntity.POLICY,
            entity_id=999_999, audience=ShareAudience.INSURED,
        )
    with pytest.raises(ValueError):
        create_share_link(
            db, broker_id=world.a.broker_id, user=None, entity_type="nope",
            entity_id=policy.id, audience=ShareAudience.INSURED,
        )


@pytest.mark.parametrize("days", [0, -1, 366])
def test_expiry_is_bounded(db, world, policy_a, days):
    policy, _plan = policy_a
    with pytest.raises(ValueError):
        _mint(db, world, policy, expires_in_days=days)


def test_unknown_revoked_and_expired_are_all_none(db, world, policy_a):
    policy, _plan = policy_a
    assert resolve_share_token(db, "no-such-token", now=NOW) is None
    assert resolve_share_token(db, "", now=NOW) is None
    assert resolve_share_token(db, "x" * 65, now=NOW) is None

    short = _mint(db, world, policy, expires_in_days=1)
    assert resolve_share_token(db, short.token, now=NOW + timedelta(hours=23)) is not None
    assert resolve_share_token(db, short.token, now=NOW + timedelta(days=1)) is None

    revoked = _mint(db, world, policy)
    revoke_share_link(revoked, now=NOW)
    first = revoked.revoked_at
    revoke_share_link(revoked, now=NOW + timedelta(days=3))
    assert revoked.revoked_at == first
    db.commit()
    assert resolve_share_token(db, revoked.token, now=NOW) is None


def test_naive_now_is_taken_as_utc(db, world, policy_a):
    policy, _plan = policy_a
    link = _mint(db, world, policy, expires_in_days=1)
    assert resolve_share_token(db, link.token, now=NOW.replace(tzinfo=None)) is not None


def test_view_counter(db, world, policy_a):
    policy, _plan = policy_a
    link = _mint(db, world, policy)
    record_share_view(link, now=NOW)
    record_share_view(link, now=NOW + timedelta(minutes=5))
    db.commit()
    db.refresh(link)
    assert link.view_count == 2
    assert link.last_viewed_at is not None


def test_permissions_are_normalised_per_audience(db, world, policy_a):
    assert normalize_permissions(ShareAudience.INSURER, {"can_pay": True, "evil": True}) == {
        "can_pay": False, "can_upload_receipt": False, "can_report_status": True, "show_documents": False,
    }
    assert normalize_permissions("insured", {"can_report_status": True, "show_documents": True}) == {
        "can_pay": True, "can_upload_receipt": True, "can_report_status": False, "show_documents": True,
    }
    assert normalize_permissions("insured", {"can_pay": False})["can_pay"] is False
    with pytest.raises(ValueError):
        normalize_permissions("hacker", None)
    policy, _plan = policy_a
    link = _mint(db, world, policy, audience=ShareAudience.INSURER, permissions={"can_pay": True})
    assert link_permissions(link)["can_pay"] is False
    assert link_permissions(link)["can_report_status"] is True


def test_share_url_uses_the_public_app_url(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_APP_URL", "https://app.radal.test/")
    assert share_url("abc") == "https://app.radal.test/s/abc"
    monkeypatch.setattr(settings, "PUBLIC_APP_URL", "")
    monkeypatch.setattr(settings, "CORS_ORIGINS", "http://localhost:4000,https://other")
    assert share_url("abc") == "http://localhost:4000/s/abc"


def test_whatsapp_url_encodes_the_text():
    url = whatsapp_url("Hola, paga aquí: https://app/s/abc?x=1&y=2")
    assert url.startswith("https://wa.me/?text=")
    assert " " not in url and "&y" not in url
    assert "https%3A%2F%2Fapp%2Fs%2Fabc" in url
