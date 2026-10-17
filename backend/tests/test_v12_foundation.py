"""v12 phase-1 foundation: crypto, settings, the closed catalogue, seed assets,
RBAC modules and the new models.

These lock the SHARED pieces every v12 lane builds on (docs/v12/00-orchestration.md
§4): if one of them moves, a lane breaks far from the cause. Everything here is
hermetic — no network, no real keys (``tests/conftest.py`` pins a test-only
``CREDENTIALS_KEY`` and points every mail/FX/Forpay host at port 9).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.core import crypto
from app.core.config import Settings, settings
from app.core.permissions import has_permission
from app.core.roles_config import ACTIONS, MODULES, ROLES, grant_for
from app.db.seed_v12 import (
    CATALOG_CSV,
    NOTICE_TEMPLATES_JSON,
    cmf_codes_for,
    load_catalog_rows,
    seed_catalog,
    seed_collection_defaults,
)
from app.models import (
    CollectionInstallment,
    CollectionPlan,
    CollectionSettings,
    InsuranceLine,
    InsuranceSubtype,
    InsurerCollectionProfile,
    Payment,
    PaymentEvidence,
    PaymentMandate,
    PolicyParty,
)
from app.models.enums import (
    CaseOrigin,
    EvidenceLevel,
    NoticeKind,
    PartyRole,
    PaymentCurrency,
    PaymentSource,
    PlacementRoute,
    PolicyCurrency,
    SuggestedEntry,
)
from tests.conftest import make_policy


# ---------------------------------------------------------------------------
# app.core.crypto
# ---------------------------------------------------------------------------
class TestCrypto:
    def test_string_round_trip(self) -> None:
        token = crypto.encrypt_secret("zoho-app-password")
        assert token != "zoho-app-password"
        assert "zoho-app-password" not in token
        assert crypto.decrypt_secret(token) == "zoho-app-password"

    def test_dict_round_trip_keeps_the_type(self) -> None:
        bundle = {"api_key": "k", "password": "p", "hmac_key": "h", "nested": {"a": 1}}
        token = crypto.encrypt_secret(bundle)
        assert crypto.decrypt_secret(token) == bundle

    def test_a_string_that_looks_like_json_stays_a_string(self) -> None:
        assert crypto.decrypt_secret(crypto.encrypt_secret('{"a": 1}')) == '{"a": 1}'

    def test_empty_string_round_trips(self) -> None:
        assert crypto.decrypt_secret(crypto.encrypt_secret("")) == ""

    def test_tokens_are_not_deterministic(self) -> None:
        assert crypto.encrypt_secret("x") != crypto.encrypt_secret("x")

    def test_uses_the_configured_credentials_key(self) -> None:
        token = crypto.encrypt_secret("secret")
        raw = Fernet(settings.CREDENTIALS_KEY.encode()).decrypt(token.encode()).decode()
        assert raw.endswith("secret")

    def test_blank_key_falls_back_to_a_key_derived_from_secret_key(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "CREDENTIALS_KEY", "")
        token = crypto.encrypt_secret("secret")
        derived = Fernet(crypto.derive_key(settings.SECRET_KEY))
        assert derived.decrypt(token.encode()).decode().endswith("secret")
        assert crypto.decrypt_secret(token) == "secret"

    def test_wrong_key_or_tampering_raises_without_echoing_the_value(self, monkeypatch) -> None:
        token = crypto.encrypt_secret("hunter2")
        monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
        with pytest.raises(crypto.SecretDecryptionError) as excinfo:
            crypto.decrypt_secret(token)
        assert "hunter2" not in str(excinfo.value)
        with pytest.raises(crypto.SecretDecryptionError):
            crypto.decrypt_secret("not-a-token")
        with pytest.raises(crypto.SecretDecryptionError):
            crypto.decrypt_secret("")

    def test_malformed_credentials_key_fails_loudly(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "CREDENTIALS_KEY", "too-short")
        with pytest.raises(ValueError, match="CREDENTIALS_KEY"):
            crypto.encrypt_secret("x")

    def test_rejects_other_types(self) -> None:
        with pytest.raises(TypeError):
            crypto.encrypt_secret(123)  # type: ignore[arg-type]

    def test_never_logs_the_value(self, caplog) -> None:
        with caplog.at_level(logging.DEBUG):
            crypto.decrypt_secret(crypto.encrypt_secret("never-in-a-log"))
        assert "never-in-a-log" not in caplog.text

    def test_has_secret(self) -> None:
        assert crypto.has_secret(crypto.encrypt_secret("x")) is True
        assert crypto.has_secret(None) is False
        assert crypto.has_secret("") is False


# ---------------------------------------------------------------------------
# app.core.config — v12 settings
# ---------------------------------------------------------------------------
class TestSettings:
    def test_the_suite_is_hermetic(self) -> None:
        assert settings.MAIL_ENABLED is False
        assert settings.RADAL_MAIL_PASSWORD == ""
        assert settings.RADAL_MAIL_SMTP_HOST == "127.0.0.1"
        assert settings.RADAL_MAIL_SMTP_PORT == 9
        assert settings.RADAL_MAIL_IMAP_PORT == 9
        assert settings.FX_SOURCE_URL.startswith("http://127.0.0.1:9/")
        assert settings.FORPAY_DEFAULT_BASE_URL.startswith("http://127.0.0.1:9/")
        assert settings.RPA_ENABLED is False and settings.RPA_INLINE is False
        assert settings.JOBS_TOKEN == "test-jobs-token"

    def test_defaults(self, monkeypatch) -> None:
        for name in ("FX_SOURCE_URL", "FORPAY_DEFAULT_BASE_URL", "RADAL_MAIL_SMTP_HOST",
                     "RADAL_MAIL_SMTP_PORT", "RADAL_MAIL_IMAP_HOST", "RADAL_MAIL_IMAP_PORT",
                     "MAIL_ENABLED", "RPA_ENABLED", "RPA_INLINE", "JOBS_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        fresh = Settings(_env_file=None)
        assert fresh.FX_SOURCE_URL == "https://mindicador.cl/api"
        assert fresh.FORPAY_DEFAULT_BASE_URL == "https://devapi.forpayservices.cl/white/label"
        assert fresh.RADAL_MAIL_ADDRESS == "contacto@radalseguros.cl"
        assert (fresh.RADAL_MAIL_SMTP_HOST, fresh.RADAL_MAIL_SMTP_PORT) == ("smtp.zoho.com", 465)
        assert (fresh.RADAL_MAIL_IMAP_HOST, fresh.RADAL_MAIL_IMAP_PORT) == ("imap.zoho.com", 993)
        assert fresh.MAIL_TIMEOUT_SECONDS == 20
        assert fresh.MAIL_ENABLED is False and fresh.RPA_HEADLESS is True
        assert fresh.JOBS_TOKEN == ""

    def test_public_urls_fall_back(self) -> None:
        cfg = Settings(_env_file=None, CORS_ORIGINS="https://a.cl/, https://b.cl",
                       PUBLIC_APP_URL="", PUBLIC_API_URL="")
        assert cfg.public_app_url == "https://a.cl"
        assert cfg.public_api_url == "https://a.cl"
        cfg = Settings(_env_file=None, PUBLIC_APP_URL="https://app.cl/",
                       PUBLIC_API_URL="https://hooks.radalseguros.cl/")
        assert cfg.public_app_url == "https://app.cl"
        assert cfg.public_api_url == "https://hooks.radalseguros.cl"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("true", True), ("1", True), ("YES", True), ("false", False), ("0", False), ("off", False)],
    )
    def test_booleans_are_coerced(self, raw, expected) -> None:
        assert Settings(_env_file=None, MAIL_ENABLED=raw).MAIL_ENABLED is expected

    def test_blank_boolean_keeps_the_field_default(self) -> None:
        cfg = Settings(_env_file=None, MAIL_ENABLED="", RPA_HEADLESS="")
        assert cfg.MAIL_ENABLED is False
        assert cfg.RPA_HEADLESS is True

    def test_ints_and_mail_security_are_validated(self) -> None:
        assert Settings(_env_file=None, RADAL_MAIL_SMTP_PORT=" 587 ").RADAL_MAIL_SMTP_PORT == 587
        assert Settings(_env_file=None, RADAL_MAIL_SMTP_SECURITY="STARTTLS").RADAL_MAIL_SMTP_SECURITY == "starttls"
        with pytest.raises(ValueError):
            Settings(_env_file=None, RADAL_MAIL_IMAP_SECURITY="tls")


# ---------------------------------------------------------------------------
# The closed catalogue + seed step
# ---------------------------------------------------------------------------
class TestCatalogSeed:
    def test_the_csv_is_the_team_catalogue_plus_pro_05(self) -> None:
        rows = load_catalog_rows()
        assert len(rows) == 54
        assert len({r.line_code for r in rows}) == 18
        assert len({r.subtype_code for r in rows}) == 54
        robo = next(r for r in rows if r.subtype_code == "PRO-05")
        assert (robo.line_code, robo.subtype_name, robo.entry) == ("PRO", "Robo", "mercado")
        # The team file is the source: every row but PRO-05 comes from it verbatim.
        team = (CATALOG_CSV.parents[3].parent / "docs/v12/catalogo_ramos.csv")
        if team.exists():
            team_codes = {r.subtype_code for r in load_catalog_rows(team)}
            assert {r.subtype_code for r in rows} - team_codes == {"PRO-05"}

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("PRO-02", ["1", "3", "4", "6"]),
            ("PRO-05", ["8"]),
            ("VEH-03", ["10", "16"]),
            ("RC-07", ["14"]),
            ("RC-08", ["15"]),
            ("TRA-02", ["18", "19"]),
            ("HOG-01", ["1", "3", "4", "8", "9", "13"]),
            ("CIB-01", ["50"]),
            ("VID-02", []),
            ("ASV-02", ["36"]),
        ],
    )
    def test_cmf_mapping(self, code, expected) -> None:
        assert cmf_codes_for(code) == expected

    def test_an_unmapped_subtipo_fails_loudly(self) -> None:
        with pytest.raises(KeyError):
            cmf_codes_for("XYZ-01")

    def test_seed_creates_18_global_ramos_and_54_subtipos(self, db, catalog) -> None:
        assert catalog["lines_created"] == 18
        assert catalog["subtypes_created"] == 54
        lines = db.scalars(
            select(InsuranceLine)
            .where(InsuranceLine.broker_id.is_(None), InsuranceLine.code.is_not(None))
            .order_by(InsuranceLine.sort_order)
        ).all()
        assert [line.code for line in lines][:5] == ["VEH", "HOG", "CON", "PYM", "PRO"]
        assert [line.sort_order for line in lines] == list(range(1, 19))
        pro = next(line for line in lines if line.code == "PRO")
        assert [s.code for s in pro.subtypes] == ["PRO-01", "PRO-02", "PRO-03", "PRO-04", "PRO-05"]
        todo_riesgo = db.scalar(select(InsuranceSubtype).where(InsuranceSubtype.code == "PRO-02"))
        assert todo_riesgo.suggested_entry == SuggestedEntry.MARKET
        assert todo_riesgo.suggested_route == PlacementRoute.TRADITIONAL
        assert todo_riesgo.cmf_codes == ["1", "3", "4", "6"]
        livianos = db.scalar(select(InsuranceSubtype).where(InsuranceSubtype.code == "VEH-01"))
        assert livianos.suggested_entry == SuggestedEntry.PORTAL
        assert livianos.suggested_route == PlacementRoute.DIGITAL

    def test_seed_is_idempotent(self, db, catalog) -> None:
        again = seed_catalog(db)
        db.commit()
        assert sum(again.values()) == 0
        assert db.scalar(select(func.count()).select_from(InsuranceSubtype)) == 54

    def test_seed_reuses_a_global_row_with_the_same_name(self, db) -> None:
        legacy = InsuranceLine(broker_id=None, name="Transporte")
        db.add(legacy)
        db.commit()
        stats = seed_catalog(db)
        db.commit()
        assert stats["lines_created"] == 17 and stats["lines_updated"] == 1
        db.refresh(legacy)
        assert legacy.code == "TRA"

    def test_seed_never_touches_broker_rows(self, db, world, catalog) -> None:
        db.refresh(world.a.line)
        assert world.a.line.code is None
        assert world.a.line.broker_id == world.a.broker_id

    def test_seed_keeps_a_retired_subtipo_retired(self, db, catalog) -> None:
        subtype = db.scalar(select(InsuranceSubtype).where(InsuranceSubtype.code == "OTR-04"))
        subtype.is_active = False
        db.commit()
        seed_catalog(db)
        db.commit()
        db.refresh(subtype)
        assert subtype.is_active is False

    def test_collection_defaults_fill_only_what_is_missing(self, db, world) -> None:
        first = seed_collection_defaults(db)
        db.commit()
        assert first["profiles_created"] == 3  # the world's three insurers
        assert first["settings_created"] == 2  # brokers A and B
        assert sum(seed_collection_defaults(db).values()) == 0
        profile = db.get(InsurerCollectionProfile, world.insurer.id)
        assert profile.onboarding_status.value == "not_contacted"
        settings_a = db.scalar(
            select(CollectionSettings).where(CollectionSettings.broker_id == world.a.broker_id)
        )
        assert settings_a.cadence_days == [-15, -5, 0, 3, 10]
        assert settings_a.tolerance_uf == Decimal("0.0090")
        assert settings_a.notices_enabled is False
        assert settings_a.native_collection_enabled is True
        assert settings_a.inquiry_after_days == 3


# ---------------------------------------------------------------------------
# Default notice templates (data)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def seed() -> dict:
    return json.loads(NOTICE_TEMPLATES_JSON.read_text(encoding="utf-8"))


class TestNoticeTemplates:
    def test_kinds_and_levels(self, seed) -> None:
        pairs = {(t["kind"], t["cadence_level"]) for t in seed["templates"]}
        assert pairs == {
            ("payer_reminder", -5),
            ("payer_reminder", 0),
            ("payer_overdue", 3),
            ("payer_overdue", 10),
            ("payment_link", None),
            ("insurer_status_inquiry", None),
        }
        for template in seed["templates"]:
            assert NoticeKind(template["kind"])
            assert template["channel"] == "email"
            assert template["subject"].strip() and template["body"].strip()

    def test_only_known_variables_are_used(self, seed) -> None:
        allowed = set(seed["variables"])
        for template in seed["templates"]:
            used = set(re.findall(r"\{\{(\w+)\}\}", template["subject"] + template["body"]))
            assert used <= allowed, (template["kind"], used - allowed)

    def test_the_inquiry_subject_carries_the_token(self, seed) -> None:
        inquiry = next(t for t in seed["templates"] if t["kind"] == "insurer_status_inquiry")
        assert "[RADAL-{{inquiry_token}}]" in inquiry["subject"]
        assert "{{share_url}}" in inquiry["body"]


# ---------------------------------------------------------------------------
# RBAC — Mail / Integrations / Platform
# ---------------------------------------------------------------------------
class TestRbacV12:
    def test_the_three_modules_exist_and_every_role_has_a_full_row(self) -> None:
        for module in ("Mail", "Integrations", "Platform"):
            assert module in MODULES
        for role, entry in ROLES.items():
            for module in MODULES:
                assert set(entry[module]) == set(ACTIONS), f"{role}.{module}"

    def test_platform_is_platform_admin_only(self) -> None:
        for role in ROLES:
            for action in ACTIONS:
                expected = role == "platform_admin"
                assert has_permission(role, "Platform", action) is expected, (role, action)

    def test_broker_admin_owns_mail_and_integrations(self) -> None:
        for action in ACTIONS:
            assert has_permission("broker_admin", "Mail", action) is True
            assert has_permission("broker_admin", "Integrations", action) is True

    def test_executive_grants(self) -> None:
        granted = {a for a in ACTIONS if has_permission("broker_executive", "Collections", a)}
        assert granted == {"View", "Create", "Edit", "Comment", "Upload", "Submit"}
        assert {a for a in ACTIONS if has_permission("broker_executive", "Mail", a)} == {
            "View", "Create"
        }
        assert {a for a in ACTIONS if has_permission("broker_executive", "Integrations", a)} == {
            "View", "Submit"
        }

    def test_collections_desk_grants(self) -> None:
        assert {a for a in ACTIONS if has_permission("broker_collections", "Mail", a)} == {
            "View", "Create"
        }
        for action in ACTIONS:
            assert has_permission("broker_collections", "Integrations", action) is False
            assert has_permission("broker_collections", "Collections", action) is True

    @pytest.mark.parametrize(
        "role",
        ["broker_inspector", "broker_technician", "broker_claims", "insured_admin",
         "insured_user", "insurer_admin", "insurer_underwriter", "insurer_executive"],
    )
    def test_every_other_role_gets_nothing(self, role) -> None:
        for module in ("Mail", "Integrations", "Platform"):
            for action in ACTIONS:
                assert grant_for(role, module, action) == "no", (role, module, action)


# ---------------------------------------------------------------------------
# Models: the shapes the lanes rely on
# ---------------------------------------------------------------------------
class TestModels:
    def test_case_origin_axis_is_untouched(self) -> None:
        """The team ``origen`` lives in ``case_file.entry_origin`` — v3's axis stays."""
        assert [m.value for m in CaseOrigin] == ["new", "renewal", "period_change"]

    def test_policy_defaults_and_parties(self, db, world) -> None:
        policy = make_policy(db, world.a, world.insurer)
        policy.parties.append(
            PolicyParty(broker_id=world.a.broker_id, role=PartyRole.POLICYHOLDER,
                        name="Asegurado Alfa S.A.", rut="77111111-4", is_primary=True)
        )
        db.commit()
        db.refresh(policy)
        assert policy.currency == PolicyCurrency.UF
        assert policy.has_source_document is False
        assert policy.is_provisional is False
        assert policy.origin is None
        assert policy.parties[0].notify_collections is True

    def test_installment_payments_and_the_two_fk_cycles_flush(self, db, world) -> None:
        policy = make_policy(db, world.a, world.insurer)
        plan = CollectionPlan(broker_id=world.a.broker_id, policy_id=policy.id)
        inst = CollectionInstallment(
            broker_id=world.a.broker_id, number=1, due_date=date(2026, 2, 1),
            gross_amount_uf=Decimal("139.0000"),
        )
        plan.installments.append(inst)
        db.add(plan)
        db.flush()
        assert plan.level_preference.value == "auto"
        assert plan.notices_enabled is False
        assert inst.currency == PolicyCurrency.UF

        # collection_plan <-> payment_mandate in ONE flush (post_update side).
        mandate = PaymentMandate(broker_id=world.a.broker_id, plan=plan)
        plan.mandate = mandate
        # payment <-> payment_evidence in ONE flush (post_update side).
        payment = Payment(
            broker_id=world.a.broker_id, amount=Decimal("139"), currency=PaymentCurrency.UF,
            amount_uf=Decimal("139.0000"), paid_on=date(2026, 2, 1),
            source=PaymentSource.INSURER_REPLY, evidence_level=EvidenceLevel.REPORTED,
        )
        inst.payments.append(payment)
        evidence = PaymentEvidence(broker_id=world.a.broker_id, installment_id=inst.id,
                                   payment=payment)
        payment.evidence = evidence
        db.add_all([mandate, evidence])
        db.commit()
        db.refresh(plan)
        db.refresh(payment)
        assert plan.mandate_id == mandate.id
        assert payment.evidence_id == evidence.id
        assert evidence.payment_id == payment.id
        assert payment.status.value == "active"
        assert payment.confirmed_by_insurer is False
        assert [p.id for p in inst.payments] == [payment.id]
