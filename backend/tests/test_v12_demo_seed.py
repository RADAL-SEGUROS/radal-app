"""``scripts/seed_v12_demo.py`` — the v12 demo world (built once per module).

The seed runs against its OWN temp SQLite file under ``/tmp`` (the guard's
no-flag door), never the suite's database, and every assertion reads that file
through a separate engine. Hermetic like the rest of the suite: conftest has
already blanked the AI key and pointed mail/FX/Forpay at the discard port, and
the seed itself switches mail/RPA off and forces local media for its run.
"""
from __future__ import annotations

import shutil
import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret
from app.models.broker import Broker
from app.models.case_file import CaseFile, CaseFileStageEvent
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.currency import CurrencyRate
from app.models.document import Document
from app.models.enums import CaseFileKind, CaseStage
from app.models.insurer import Insurer
from app.models.insurer_collection import InsurerCollectionProfile, InsurerPaymentChannel
from app.models.jobs import JobRun
from app.models.mail import MailMessage, PaymentEvidence, StatusInquiry
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.policy import Policy
from app.models.provider import ProviderAccount
from app.models.rpa import PortalCredential, PortalSearchRun
from app.models.share_link import ShareLink
from app.models.user import User
from app.services.collection.resolver import resolve_collection_mode
from app.services.collection.status import compute_installment_state
from app.services.policy_registry import today_santiago
from scripts import seed_v12_demo as demo


def _table_counts(engine) -> dict[str, int]:  # noqa: ANN001
    with engine.connect() as conn:
        return {
            name: conn.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
            for name in inspect(conn).get_table_names()
        }


@pytest.fixture(scope="module")
def demo_db():
    """Seed once into /tmp (no flag needed there), then seed AGAIN."""
    root = Path(tempfile.mkdtemp(prefix="radal-demo-", dir="/tmp"))
    url = f"sqlite:///{root / 'demo.db'}"
    media = root / "media"
    argv = ["--url", url, "--media-dir", str(media)]
    first_exit = demo.main(argv)
    engine = create_engine(url, future=True)
    first_counts = _table_counts(engine)
    second_summary, second_created = demo.seed_demo(url, media_dir=str(media))
    second_counts = _table_counts(engine)
    third_exit = demo.main(argv)
    try:
        yield {
            "engine": engine,
            "media": media,
            "first_exit": first_exit,
            "third_exit": third_exit,
            "first_counts": first_counts,
            "second_counts": second_counts,
            "second_created": second_created,
            "second_summary": second_summary,
        }
    finally:
        engine.dispose()
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def world(demo_db):
    with Session(demo_db["engine"]) as session:
        broker = session.scalar(select(Broker).where(Broker.legal_name == "Corredora Demo SpA"))
        assert broker is not None
        yield session, broker


# --- the guard -----------------------------------------------------------------------


def test_refuses_anything_but_a_tmp_sqlite_file_without_the_flag(tmp_path):
    assert demo.refusal_reason("sqlite:////tmp/radal-demo/demo.db", forced=False) is None
    assert demo.refusal_reason("sqlite:////private/tmp/x/demo.db", forced=False) is None
    for url in (
        "sqlite:////var/lib/radal/radal.db",
        "sqlite:///./radal.db",
        "sqlite:///:memory:",
        "mysql+pymysql://user:secret@db.example.com/radal",
    ):
        reason = demo.refusal_reason(url, forced=False)
        assert reason is not None and demo.DEMO_FLAG in reason
        assert "secret" not in reason  # the password is never echoed
        assert demo.refusal_reason(url, forced=True) is None

    target = Path("/nonexistent-radal-demo-dir") / "demo.db"
    assert demo.main(["--url", f"sqlite:///{target}"]) == 2
    assert not target.exists()


def test_a_tmp_symlink_escape_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link_root = Path(tempfile.mkdtemp(prefix="radal-demo-link-", dir="/tmp"))
    try:
        link = link_root / "escape"
        link.symlink_to(outside, target_is_directory=True)
        url = f"sqlite:///{link / 'demo.db'}"
        if str(outside.resolve()).startswith(("/tmp/", "/private/tmp/")):
            pytest.skip("pytest tmp_path lives under /tmp on this platform")
        assert demo.refusal_reason(url, forced=False) is not None
    finally:
        shutil.rmtree(link_root, ignore_errors=True)


# --- idempotency -----------------------------------------------------------------------


def test_second_and_third_runs_change_nothing(demo_db):
    assert demo_db["first_exit"] == 0 and demo_db["third_exit"] == 0
    assert demo_db["second_created"] is False
    assert demo_db["second_counts"] == demo_db["first_counts"]
    assert _table_counts(demo_db["engine"]) == demo_db["first_counts"]


def test_summary_carries_logins_and_relative_share_urls(demo_db):
    summary = demo_db["second_summary"]
    emails = [login[0] for login in summary["logins"]]
    assert emails == ["admin@radal.cl", "admin@demo.cl", "ejecutivo@demo.cl"]
    urls = [link["url"] for link in summary["share_links"]]
    assert len(urls) == 3 and all(u.startswith("/s/") and len(u) > 20 for u in urls)
    assert summary["counts"]["policies"] == 5


# --- the world -------------------------------------------------------------------------


def test_logins_insurers_and_platform_rows(world):
    db, broker = world
    users = {u.email: u for u in db.scalars(select(User))}
    assert users["admin@radal.cl"].role == "platform_admin" and users["admin@radal.cl"].broker_id is None
    assert users["admin@demo.cl"].role == "broker_admin" and users["admin@demo.cl"].broker_id == broker.id
    assert users["ejecutivo@demo.cl"].role == "broker_executive"

    insurers = {i.rut: i for i in db.scalars(select(Insurer))}
    assert set(insurers) == {data["rut"] for data in demo.INSURERS.values()}
    assert all(i.cmf_code for i in insurers.values())
    hdi = insurers[demo.INSURERS["hdi"]["rut"]]
    sura = insurers[demo.INSURERS["sura"]["rut"]]
    assert db.get(InsurerCollectionProfile, hdi.id).onboarding_status.value == "active"
    sura_profile = db.get(InsurerCollectionProfile, sura.id)
    assert sura_profile.collections_email and sura_profile.payment_instructions["account_number"]

    account = db.scalars(select(ProviderAccount)).one()
    assert account.owner.value == "radal_mall" and account.environment.value == "sandbox"
    assert "demo-api-key" not in (account.secret_ciphertext or "")  # really encrypted
    assert decrypt_secret(account.secret_ciphertext)["api_key"] == "demo-api-key-not-real"
    assert account.base_url.startswith("http://127.0.0.1:9")  # never the live sandbox
    channel = db.scalars(select(InsurerPaymentChannel)).one()
    assert (channel.insurer_id, channel.channel.value, channel.status.value) == (hdi.id, "webpay", "active")
    assert channel.store_commerce_code

    from app.models.collection_settings import CollectionSettings

    settings_row = db.scalar(select(CollectionSettings).where(CollectionSettings.broker_id == broker.id))
    assert settings_row.notices_enabled and settings_row.notices_approved_at is not None


def test_policies_and_journeys(world):
    db, broker = world
    policies = db.scalars(select(Policy).where(Policy.broker_id == broker.id)).all()
    assert Counter(p.origin.value for p in policies) == {"journey": 1, "manual": 2, "reader": 2}
    assert len({p.insurance_subtype_id for p in policies}) == 5
    assert all(p.case_file_id for p in policies)
    assert all(len(p.parties) >= 3 and any(x.email for x in p.parties) for p in policies)

    accounts = db.scalars(
        select(CaseFile).where(CaseFile.broker_id == broker.id, CaseFile.kind == CaseFileKind.ACCOUNT)
    ).all()
    assert len(accounts) == 5 and all(c.stage == CaseStage.ACTIVE for c in accounts)

    def skipped(case: CaseFile) -> int:
        return int(
            db.scalar(
                select(func.count()).select_from(CaseFileStageEvent).where(
                    CaseFileStageEvent.case_file_id == case.id, CaseFileStageEvent.is_skipped.is_(True)
                )
            )
        )

    full = [c for c in accounts if c.entry_origin.value == "full_journey"]
    assert len(full) == 1 and skipped(full[0]) == 0
    assert full[0].placement_id is not None
    assert {c.entry_origin.value for c in accounts if skipped(c)} == {"manual_entry", "policy_upload"}
    # Three groups, the first with two RUTs on one account.
    assert len({c.account_group_id for c in accounts}) == 3


def test_cuotas_sit_in_every_state_relative_to_today(world):
    db, broker = world
    today = today_santiago()
    states = []
    for inst in db.scalars(
        select(CollectionInstallment).where(CollectionInstallment.broker_id == broker.id)
    ):
        state = compute_installment_state(
            inst, inst.payments, today=today, cadence_days=[-15, -5, 0, 3, 10]
        )
        states.append((state, (inst.due_date - today).days, inst))
    assert db.scalar(select(func.count()).select_from(CollectionPlan)) == 4
    by_name = Counter(s.state for s, _, _ in states)
    assert by_name["paid"] >= 4 and by_name["due_today"] == 1
    assert by_name["overdue"] == 1 and by_name["termination_risk"] == 1
    assert {s.days_late for s, _, _ in states if s.state in ("overdue", "termination_risk")} == {4, 12}
    assert any(s.state == "upcoming" and offset == 3 for s, offset, _ in states)
    partial = [inst for s, _, inst in states if "partial" in s.flags]
    assert len(partial) == 1
    clp = [p for p in partial[0].payments if p.currency.value == "CLP"]
    assert len(clp) == 1 and clp[0].uf_rate and clp[0].amount_uf < partial[0].gross_amount_uf

    rates = db.scalars(select(CurrencyRate)).all()
    assert len(rates) >= 60
    assert all(Decimal("39000") <= r.uf <= Decimal("39500") for r in rates)


def test_collection_levels_per_insurer(world):
    db, broker = world
    levels = {}
    for plan in db.scalars(select(CollectionPlan).where(CollectionPlan.broker_id == broker.id)):
        mode = resolve_collection_mode(db, plan.installments[0], broker_id=broker.id)
        insurer = db.get(Insurer, plan.policy.insurer_id)
        levels[insurer.trade_name] = mode
    assert levels["HDI Seguros"].level == "native" and levels["HDI Seguros"].channels == ("webpay",)
    assert levels["Sura"].level == "assisted" and "insurer_contact" not in levels["Sura"].missing
    assert levels["Mapfre"].level == "assisted" and "insurer_contact" in levels["Mapfre"].missing
    assert levels["Chubb"].level == "assisted" and "insurer_contact" not in levels["Chubb"].missing


def test_inquiry_evidence_links_and_bell(world):
    db, broker = world
    inquiry = db.scalars(select(StatusInquiry).where(StatusInquiry.broker_id == broker.id)).one()
    assert db.get(Insurer, inquiry.insurer_id).trade_name == "Sura"
    mail = db.get(MailMessage, inquiry.outbound_message_id)
    assert (mail.direction.value, mail.status.value) == ("outbound", "skipped")

    evidence = db.scalars(select(PaymentEvidence).where(PaymentEvidence.broker_id == broker.id)).one()
    assert (evidence.source.value, evidence.status.value, evidence.reported_status.value) == (
        "insurer_share_link", "suggested", "paid",
    )

    links = db.scalars(select(ShareLink).where(ShareLink.broker_id == broker.id)).all()
    pairs = Counter((link.entity_type.value, link.audience.value) for link in links)
    assert pairs == {("policy", "insured"): 1, ("policy", "insurer"): 1, ("status_inquiry", "insurer"): 1}
    by_audience = {link.audience.value: link for link in links if link.entity_type.value == "policy"}
    assert by_audience["insured"].entity_id != by_audience["insurer"].entity_id

    kinds = {n.kind.value for n in db.scalars(select(Notification).where(Notification.broker_id == broker.id))}
    assert {
        "installment_overdue", "termination_risk", "installment_due", "plan_missing",
        "evidence_pending", "portal_search_failed",
    } <= kinds
    clock = db.scalars(select(JobRun).where(JobRun.job == "collection.clock")).one()
    assert clock.status.value == "succeeded"


def test_portal_runs_credentials_and_documents(world, demo_db):
    db, broker = world
    runs = db.scalars(select(PortalSearchRun).where(PortalSearchRun.broker_id == broker.id)).all()
    assert Counter((r.status.value, r.error_kind.value if r.error_kind else None) for r in runs) == {
        ("failed", "layout_changed"): 1, ("queued", None): 1,
    }
    credential = db.scalars(select(PortalCredential).where(PortalCredential.broker_id == broker.id)).one()
    assert credential.portal == "hdi" and "not-real" not in (credential.secret_ciphertext or "")

    docs = db.scalars(select(Document).where(Document.broker_id == broker.id)).all()
    assert len(docs) >= 8
    for doc in docs:
        path = demo_db["media"] / doc.s3_key
        assert path.read_bytes().startswith(b"%PDF"), doc.s3_key
    assert db.scalar(select(func.count()).select_from(Payment)) == 6
