"""v12 DEMO WORLD — a rich, consistent demo broker on a throwaway database.

    python -m scripts.seed_v12_demo                                   # DATABASE_URL must be sqlite under /tmp
    DATABASE_URL=sqlite:////tmp/radal-demo.db python -m scripts.seed_v12_demo
    python -m scripts.seed_v12_demo --url mysql+pymysql://u:pw@host/demo --i-know-this-is-a-demo-db

It REFUSES to run unless the target is a SQLite file under ``/tmp`` or
``--i-know-this-is-a-demo-db`` is passed: it writes a whole fictional broker,
platform payment rows (Forpay account, HDI channel) and a collection-clock job
run, none of which belong on a real database.

What it builds (every write goes through the same router/service functions the
API uses — no TestClient, no HTTP; the handlers are called as plain functions):

* schema (``create_all``), the closed ramo catalogue (``seed_catalog``), the
  cobranza defaults (``seed_collection_defaults``) and the platform admin
  ``admin@radal.cl / radal1234``;
* broker **Corredora Demo SpA** with ``admin@demo.cl`` (broker_admin) and
  ``ejecutivo@demo.cl`` (broker_executive), password ``radal1234``; payer
  notices enabled and the notice templates approved;
* insurers (canonical, valid RUT + CMF code): **HDI** (profile ``active`` +
  a ``radal_mall`` sandbox provider account with ENCRYPTED dummy secrets + an
  active ``webpay`` channel with a store code → *native*), **Sura** (payment
  instructions + collections e-mail → *assisted*), **Mapfre** (nothing →
  *assisted*, ``missing=insurer_contact``) and **Chubb** (a broker override
  carries its collections e-mail);
* three groups (asegurados) with their RUTs and five policies across ramos:
  one FULL-JOURNEY account (placement → quote request → two confirmed
  cotizaciones → accepted → policy from the proposal, every stage walked
  through the guarded machine), two policies by **vía Manual**
  (``POST /policies``) and two through the **policy registry** door
  (``origin=reader`` + colocación on demand), the on-demand folders carrying
  *omitida* stage events;
* collection plans whose cuotas sit at fixed offsets from TODAY: paid, overdue
  +4, termination risk +12, due today, due in 3 days and a partial cuota paid
  in CLP; 61 days of ``currency_rate`` (UF ≈ 39 000–39 500 CLP);
* a status inquiry to Sura (outbound mail recorded ``skipped``), an insured
  share link (HDI policy) and an insurer share link (Sura policy) through which
  Sura reports a cuota paid → a ``suggested`` payment evidence; one portal
  search queued and one failed with ``layout_changed``; HDI portal credentials;
  and the bell, filled by running the ``collection.clock`` job for today.

Atomic: the router functions' ``db.commit()`` calls are turned into
flush + expire on a dedicated session, and the world is committed ONCE at the
end — a failure leaves the database untouched.

Idempotent: a second run detects the demo broker and only re-prints the
summary (catalogue/admin/platform rows are upserts; nothing is duplicated).

Hermetic: mail sending, RPA and the AI key are switched off for the run and
media is forced to the LOCAL backend (``--media-dir``, default
``settings.MEDIA_LOCAL_DIR``) — nothing reaches S3, Zoho, Forpay, HDI or
DeepInfra. The provider account points at an unroutable base URL on purpose.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import create_engine, func, or_, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

sys.path.insert(0, ".")  # allow `python -m scripts.seed_v12_demo` from backend/

import app.db.base  # noqa: E402,F401  (registers every model)
from app.core.config import settings  # noqa: E402
from app.core.crypto import encrypt_secret  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.seed_dev import ensure_platform_admin  # noqa: E402
from app.db.seed_v12 import seed_catalog, seed_collection_defaults  # noqa: E402
from app.models.base_class import Base, utcnow  # noqa: E402
from app.models.broker import Broker  # noqa: E402
from app.models.catalog import InsuranceSubtype  # noqa: E402
from app.models.collection import CollectionInstallment, CollectionPlan  # noqa: E402
from app.models.collection_settings import CollectionSettings  # noqa: E402
from app.models.enums import (  # noqa: E402
    CaseFileKind,
    CaseSection,
    CaseStage,
    ChannelStatus,
    CollectionLevelPreference,
    CollectionPlanOrigin,
    CollectionPlanSource,
    CredentialStatus,
    EntityType,
    MailSender,
    OnboardingStatus,
    PaymentChannel,
    PaymentCurrency,
    PaymentMode,
    PaymentProviderName,
    PolicyOrigin,
    PortalRunStatus,
    ProviderAccountStatus,
    ProviderEnvironment,
    ProviderOwner,
    ShareAudience,
    ShareEntity,
    UserType,
)
from app.models.insurance_line import InsuranceLine  # noqa: E402
from app.models.insurer import Insurer, InsurerContact, InsurerStatus  # noqa: E402
from app.models.insurer_collection import (  # noqa: E402
    BrokerInsurerCollection,
    InsurerCollectionProfile,
    InsurerPaymentChannel,
)
from app.models.document import Document, DocumentCategory  # noqa: E402
from app.models.provider import ProviderAccount  # noqa: E402
from app.models.rpa import PortalSearchRun  # noqa: E402
from app.models.share_link import ShareLink  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.identifiers import compute_dv  # noqa: E402

#: Refuse-guard override flag (spelled out so nobody passes it by accident).
DEMO_FLAG = "--i-know-this-is-a-demo-db"
TMP_ROOTS = ("/tmp", "/private/tmp")

PASSWORD = "radal1234"
DEMO_BROKER_NAME = "Corredora Demo SpA"
ADMIN_EMAIL = "admin@demo.cl"
EXECUTIVE_EMAIL = "ejecutivo@demo.cl"
PROVIDER_LABEL = "Radal mall · Forpay sandbox (demo)"
#: Unroutable on purpose: a demo click on "Pagar" fails closed instead of
#: reaching the real Forpay sandbox with dummy credentials.
PROVIDER_BASE_URL = "http://127.0.0.1:9/forpay-demo"
HDI_STORE_CODE = "597055555532"

UF4 = Decimal("0.0001")


def rut(body: int) -> str:
    """A valid ``BODY-DV`` RUT for a numeric body (mod-11 DV computed)."""
    text = str(body)
    return f"{text}-{compute_dv(text)}"


DEMO_BROKER_RUT = rut(77123456)

#: Canonical insurers. ``cmf_code`` follows the convention of the CMF-seeded
#: rows already in Radal (the RUT doubles as the registry code).
INSURERS: dict[str, dict[str, str]] = {
    "hdi": {"rut": "99061000-2", "legal_name": "HDI Seguros S.A.", "trade_name": "HDI Seguros"},
    "sura": {
        "rut": "99017000-2",
        "legal_name": "Seguros Generales Suramericana S.A.",
        "trade_name": "Sura",
    },
    "mapfre": {
        "rut": "96508210-7",
        "legal_name": "Mapfre Compañía de Seguros Generales de Chile S.A.",
        "trade_name": "Mapfre",
    },
    "chubb": {"rut": "99225000-3", "legal_name": "Chubb Seguros Chile S.A.", "trade_name": "Chubb"},
}

#: Demo insureds. Every e-mail is under ``example.com`` (RFC 2606): never deliverable.
CLIENTS: dict[str, dict[str, Any]] = {
    "los_andes": {
        "rut": rut(76123456),
        "legal_name": "Inmobiliaria Los Andes SpA",
        "sector": "Inmobiliario",
        "contact_name": "Paula Herrera",
        "contact_email": "finanzas@losandes.example.com",
        "contact_phone": "+56 2 2345 6789",
        "address": "Av. Apoquindo 4501",
        "commune": "Las Condes",
        "region": "Metropolitana",
    },
    "rentas_andes": {
        "rut": rut(76234567),
        "legal_name": "Rentas Los Andes Ltda.",
        "sector": "Inmobiliario",
        "contact_name": "Jorge Soto",
        "contact_email": "pagos@rentaslosandes.example.com",
        "contact_phone": "+56 2 2345 6790",
        "address": "Av. Apoquindo 4501, of. 1201",
        "commune": "Las Condes",
        "region": "Metropolitana",
    },
    "transportes_sur": {
        "rut": rut(76345678),
        "legal_name": "Transportes del Sur SpA",
        "sector": "Transporte y logística",
        "contact_name": "Andrea Muñoz",
        "contact_email": "tesoreria@transportesdelsur.example.com",
        "contact_phone": "+56 65 223 4455",
        "address": "Ruta 5 Sur km 1.020",
        "commune": "Puerto Montt",
        "region": "Los Lagos",
    },
    "valle_verde": {
        "rut": rut(76456789),
        "legal_name": "Agrícola Valle Verde Ltda.",
        "sector": "Agrícola",
        "contact_name": "Rodrigo Pérez",
        "contact_email": "administracion@valleverde.example.com",
        "contact_phone": "+56 72 221 9988",
        "address": "Fundo El Peumo s/n",
        "commune": "San Fernando",
        "region": "O'Higgins",
    },
}


class DemoSeedError(RuntimeError):
    """The demo world could not be built (nothing was committed)."""


# =============================================================================
# Session: router commits become flush + expire, the seed commits once
# =============================================================================


class DemoSession(Session):
    """A session whose ``commit()`` only flushes (and expires, like a commit).

    The router/service functions the seed calls commit after each step; here
    that becomes a flush so the whole world lands in ONE transaction that
    :meth:`commit_for_real` commits at the end (or nothing, on any failure).
    """

    def commit(self) -> None:  # noqa: D401 - see class docstring
        self.flush()
        self.expire_all()

    def commit_for_real(self) -> None:
        super().commit()


# =============================================================================
# Refuse guard + hermetic runtime
# =============================================================================


def _resolve_url(cli_url: str | None) -> str:
    if cli_url:
        return cli_url
    if os.environ.get("DATABASE_URL"):
        from app.db.session import _normalized_sqlite_url

        return _normalized_sqlite_url(os.environ["DATABASE_URL"])
    from app.db.session import DATABASE_URL

    return DATABASE_URL


def _sqlite_path(url: str) -> Path | None:
    parsed = make_url(url)
    if not parsed.drivername.startswith("sqlite"):
        return None
    if not parsed.database or parsed.database == ":memory:":
        return None
    return Path(parsed.database)


def is_throwaway_url(url: str) -> bool:
    """True for a SQLite FILE under ``/tmp`` (symlinks resolved)."""
    path = _sqlite_path(url)
    if path is None:
        return False
    resolved = os.path.realpath(str(path))
    return any(resolved == root or resolved.startswith(root + os.sep) for root in TMP_ROOTS)


def refusal_reason(url: str, *, forced: bool) -> str | None:
    """Why the seed refuses this target (``None`` = go ahead)."""
    if forced or is_throwaway_url(url):
        return None
    shown = make_url(url).render_as_string(hide_password=True)
    return (
        f"refusing to seed a demo world into {shown}: only a SQLite file under /tmp is "
        f"accepted without {DEMO_FLAG}."
    )


@contextlib.contextmanager
def hermetic_runtime(media_dir: str) -> Iterator[None]:
    """No mail, RPA, AI or S3 during the run; restored afterwards."""
    overrides = {
        "MAIL_ENABLED": False,
        "RPA_ENABLED": False,
        "RPA_INLINE": False,
        "AI_API_KEY": "",
        "MEDIA_BACKEND": "local",
        "MEDIA_LOCAL_DIR": media_dir,
    }
    previous = {key: getattr(settings, key) for key in overrides}
    try:
        for key, value in overrides.items():
            setattr(settings, key, value)
        yield
    finally:
        for key, value in previous.items():
            setattr(settings, key, value)


# =============================================================================
# Small helpers
# =============================================================================


def split_amount(total: Decimal, n: int) -> list[Decimal]:
    """``n`` UF amounts summing EXACTLY to ``total``; the last absorbs rounding."""
    each = (total / n).quantize(UF4, rounding=ROUND_HALF_UP)
    return [each] * (n - 1) + [total - each * (n - 1)]


def money(taxable: str, exempt: str = "0") -> dict[str, Decimal]:
    """The Chilean premium arithmetic: VAT on the taxable part only."""
    t = Decimal(taxable).quantize(UF4)
    e = Decimal(exempt).quantize(UF4)
    net = t + e
    vat = (t * Decimal("0.19")).quantize(UF4, rounding=ROUND_HALF_UP)
    return {
        "taxable_premium_uf": t,
        "exempt_premium_uf": e,
        "net_premium_uf": net,
        "vat_uf": vat,
        "total_premium_uf": net + vat,
    }


def _pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def demo_pdf(title: str, lines: list[str]) -> bytes:
    """A tiny valid one-page PDF (Helvetica, WinAnsi) — enough for a viewer."""
    rows = [f"BT /F1 16 Tf 56 780 Td ({_pdf_text(title)}) Tj ET"]
    y = 750
    for line in ["Documento de demostración — Radal (datos ficticios)", *lines]:
        rows.append(f"BT /F1 10 Tf 56 {y} Td ({_pdf_text(line)}) Tj ET")
        y -= 16
    stream = "\n".join(rows).encode("cp1252", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


@dataclass
class World:
    """Handles the builders pass around. ``today`` is the Santiago business date."""

    db: DemoSession
    today: date
    now: datetime
    broker: Broker | None = None
    admin: User | None = None
    executive: User | None = None
    insurers: dict[str, Insurer] = field(default_factory=dict)
    clients: dict[str, int] = field(default_factory=dict)
    groups: dict[str, int] = field(default_factory=dict)
    policies: dict[str, int] = field(default_factory=dict)
    plans: dict[str, int] = field(default_factory=dict)
    cases: dict[str, int] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)
    doc_seq: int = 0

    @property
    def broker_id(self) -> int:
        assert self.broker is not None
        return self.broker.id

    def day(self, offset: int) -> date:
        return self.today + timedelta(days=offset)

    def subtype(self, code: str) -> InsuranceSubtype:
        row = self.db.scalar(select(InsuranceSubtype).where(InsuranceSubtype.code == code))
        if row is None:
            raise DemoSeedError(f"catalogue subtipo {code} missing")
        return row

    def line(self, code: str) -> InsuranceLine:
        row = self.db.scalar(
            select(InsuranceLine).where(InsuranceLine.broker_id.is_(None), InsuranceLine.code == code)
        )
        if row is None:
            raise DemoSeedError(f"catalogue ramo {code} missing")
        return row


def store_demo_document(
    world: World,
    *,
    entity_type: EntityType,
    entity_id: int,
    category: DocumentCategory,
    name: str,
    title: str,
    lines: list[str],
    case_file_id: int | None = None,
    section: CaseSection | None = None,
    user: User | None = None,
) -> Document:
    """A generated PDF registered through ``store_generated_document``.

    The key lives under ``documents/demo-<broker rut>/`` so a demo database
    never overwrites the files of another local database sharing the media dir.
    """
    from app.api.routers.documents import store_generated_document

    world.doc_seq += 1
    key = (
        f"{(settings.DOCUMENT_S3_PREFIX or 'documents/').rstrip('/')}/demo-{world.broker.rut}/"
        f"{entity_type.value}/{entity_id}/{category.value}-{world.doc_seq}.pdf"
    )
    doc = store_generated_document(
        world.db,
        broker_id=world.broker_id,
        entity_type=entity_type,
        entity_id=entity_id,
        category=category,
        data=demo_pdf(title, lines),
        original_name=name,
        mime_type="application/pdf",
        key=key,
        uploaded_by_id=user.id if user else None,
    )
    doc.case_file_id = case_file_id
    doc.section = section
    world.db.flush()
    return doc


# =============================================================================
# Platform rows (idempotent upserts — safe on every run)
# =============================================================================


def ensure_insurer(db: Session, *, rut: str, legal_name: str, trade_name: str) -> Insurer:
    """Find by RUT or CMF code (never by name); create as native when unknown."""
    row = db.scalar(select(Insurer).where(or_(Insurer.rut == rut, Insurer.cmf_code == rut)))
    if row is None:
        row = Insurer(
            rut=rut,
            cmf_code=rut,
            legal_name=legal_name,
            trade_name=trade_name,
            is_native=True,
            status=InsurerStatus.ACTIVE,
            cmf_status="Compañía de Seguros Generales con Registro Vigente",
        )
        db.add(row)
        db.flush()
    elif not row.trade_name:
        row.trade_name = trade_name
    return row


def _profile(db: Session, insurer: Insurer) -> InsurerCollectionProfile:
    row = db.get(InsurerCollectionProfile, insurer.id)
    if row is None:
        row = InsurerCollectionProfile(insurer_id=insurer.id)
        db.add(row)
        db.flush()
    return row


def seed_platform(db: Session, *, platform_user: User, now: datetime) -> dict[str, Insurer]:
    """Insurers, their cobranza profiles, the Forpay account and HDI's channel."""
    insurers = {key: ensure_insurer(db, **data) for key, data in INSURERS.items()}

    hdi = _profile(db, insurers["hdi"])
    hdi.onboarding_status = OnboardingStatus.ACTIVE
    hdi.status_note = "Demo: Forpay + Radal activo (sandbox)."
    hdi.collections_email = hdi.collections_email or "cobranza@hdi.example.com"
    hdi.sender_domains = hdi.sender_domains or ["hdi.example.com"]
    hdi.payment_instructions = hdi.payment_instructions or {
        "portal_url": "https://pagos.hdi.example.com",
        "notes": "Pago en línea con Webpay a través de Radal.",
    }
    hdi.updated_by_user_id = platform_user.id

    sura = _profile(db, insurers["sura"])
    if sura.onboarding_status == OnboardingStatus.NOT_CONTACTED:
        sura.onboarding_status = OnboardingStatus.IN_CONVERSATION
    sura.collections_email = sura.collections_email or "cobranzas@sura.example.com"
    sura.sender_domains = sura.sender_domains or ["sura.example.com"]
    sura.payment_instructions = sura.payment_instructions or {
        "portal_url": "https://pagos.sura.example.com",
        "bank_name": "Banco de Chile",
        "account_type": "Cuenta corriente",
        "account_number": "00-123-45678-09",
        "account_holder": INSURERS["sura"]["legal_name"],
        "account_rut": INSURERS["sura"]["rut"],
        "transfer_email": "recaudacion@sura.example.com",
        "receipt_email": "comprobantes@sura.example.com",
        "notes": "Indicar el número de póliza en el detalle de la transferencia.",
    }
    sura.updated_by_user_id = platform_user.id

    _profile(db, insurers["mapfre"])  # nothing: assisted, missing insurer_contact
    _profile(db, insurers["chubb"])

    account = db.scalar(select(ProviderAccount).where(ProviderAccount.label == PROVIDER_LABEL))
    if account is None:
        from app.services.payments.accounts import new_webhook_key, new_webhook_secret

        account = ProviderAccount(
            provider=PaymentProviderName.FORPAY,
            label=PROVIDER_LABEL,
            owner=ProviderOwner.RADAL_MALL,
            environment=ProviderEnvironment.SANDBOX,
            base_url=PROVIDER_BASE_URL,
            commerce="RADAL-DEMO",
            channel_header="RADAL-DEMO",
            process_id="WHITE_LABEL",
            username="radal-demo",
            secret_ciphertext=encrypt_secret(
                {
                    "api_key": "demo-api-key-not-real",
                    "password": "demo-password-not-real",
                    "hmac_key": "demo-hmac-key-not-real",
                    "public_key_pem": "",
                }
            ),
            webhook_key=new_webhook_key(),
            webhook_secret_ciphertext=encrypt_secret(new_webhook_secret()),
            status=ProviderAccountStatus.ACTIVE,
            last_verified_at=now,
        )
        db.add(account)
        db.flush()

    channel = db.scalar(
        select(InsurerPaymentChannel).where(
            InsurerPaymentChannel.insurer_id == insurers["hdi"].id,
            InsurerPaymentChannel.channel == PaymentChannel.WEBPAY,
        )
    )
    if channel is None:
        channel = InsurerPaymentChannel(insurer_id=insurers["hdi"].id, channel=PaymentChannel.WEBPAY)
        db.add(channel)
    channel.provider_account_id = account.id
    channel.store_commerce_code = channel.store_commerce_code or HDI_STORE_CODE
    channel.status = ChannelStatus.ACTIVE
    channel.activated_at = channel.activated_at or now
    channel.fee_model = channel.fee_model or {"kind": "pct", "value": "1.9"}
    db.flush()
    return insurers


# =============================================================================
# The broker world
# =============================================================================


def _user(db: Session, broker: Broker, *, email: str, name: str, role: str, title: str) -> User:
    user = User(
        broker_id=broker.id,
        email=email,
        hashed_password=hash_password(PASSWORD),
        full_name=name,
        job_title=title,
        user_type=UserType.BROKER,
        role=role,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def seed_broker(world: World) -> None:
    db = world.db
    broker = Broker(
        rut=DEMO_BROKER_RUT,
        legal_name=DEMO_BROKER_NAME,
        trade_name="Demo Corredores",
        cmf_code=DEMO_BROKER_RUT,
        email="contacto@corredora-demo.example.com",
        phone="+56 2 2900 1000",
        address="Av. Providencia 1208, of. 401",
        commune="Providencia",
        region="Metropolitana",
    )
    db.add(broker)
    db.flush()
    world.broker = broker
    world.admin = _user(
        db, broker, email=ADMIN_EMAIL, name="Carolina Rojas", role="broker_admin",
        title="Gerenta comercial",
    )
    world.executive = _user(
        db, broker, email=EXECUTIVE_EMAIL, name="Matías Fuentes", role="broker_executive",
        title="Ejecutivo de cuentas",
    )
    seed_collection_defaults(db)  # the broker's collection_settings row

    from app.api.routers.collections import approve_notice_templates

    settings_row = db.scalar(select(CollectionSettings).where(CollectionSettings.broker_id == broker.id))
    settings_row.notices_enabled = True
    settings_row.default_sender = MailSender.RADAL
    settings_row.auto_status_inquiries = False
    approve_notice_templates(db=db, broker_id=broker.id, current_user=world.admin)

    # Broker-admin override: Chubb's collections desk (02 §5 override).
    db.add(
        BrokerInsurerCollection(
            broker_id=broker.id,
            insurer_id=world.insurers["chubb"].id,
            collections_email="cobranzas@chubb.example.com",
            contact_name="Mesa de cobranza Chubb",
            native_enabled=True,
            notes="Confirmar pagos por correo; responden en 48 h.",
        )
    )
    # Quote routing contacts (insurer_contact, broker scope) for the market.
    for key, name, email in (
        ("hdi", "Suscripción Empresas HDI", "suscripcion@hdi.example.com"),
        ("sura", "Suscripción Property Sura", "property@sura.example.com"),
        ("chubb", "Suscripción Property Chubb", "property@chubb.example.com"),
    ):
        db.add(
            InsurerContact(
                insurer_id=world.insurers[key].id,
                broker_id=broker.id,
                name=name,
                email=email,
                role="Suscripción",
                is_primary=True,
            )
        )
    db.flush()


def seed_rates(world: World) -> int:
    """61 days of UF (≈ 39 000 → 39 450 CLP) and observed dollar, ending today."""
    from app.services import fx

    rows = []
    for back in range(60, -1, -1):
        day = world.day(-back)
        step = 60 - back
        uf = Decimal("39000") + Decimal(step) * Decimal("7.5") + Decimal((step * 7) % 5) / 10
        usd = Decimal("945") + Decimal((step * 3) % 11) * Decimal("1.3")
        rows.append((day, uf.quantize(Decimal("0.01")), usd.quantize(Decimal("0.01")), "demo"))
    return fx.upsert_rates(world.db, rows)


def seed_clients_and_groups(world: World) -> None:
    from app.api.routers.account_groups import create_account_group
    from app.api.routers.clients import create_client
    from app.models.account_group import AccountGroupIconKind
    from app.models.client import ClientStatus
    from app.schemas.account_group import AccountGroupCreate, AccountGroupIconInput
    from app.schemas.client import ClientCreate

    db = world.db
    for key, data in CLIENTS.items():
        read = create_client(
            ClientCreate(
                rut=data["rut"],
                legal_name=data["legal_name"],
                insured_email=data["contact_email"],
                insured_phone=data["contact_phone"],
                insured_address=data["address"],
                insured_commune=data["commune"],
                insured_region=data["region"],
                status=ClientStatus.ACTIVE,
                account_manager_id=world.executive.id,
                source="Referido",
                sector=data["sector"],
                since=world.day(-400),
                contact_name=data["contact_name"],
                contact_email=data["contact_email"],
                contact_phone=data["contact_phone"],
            ),
            db=db,
            broker_id=world.broker_id,
            current_user=world.executive,
        )
        world.clients[key] = read.id

    for key, name, emoji, members, notes in (
        ("los_andes", "Grupo Los Andes", "🏔️", ["los_andes", "rentas_andes"],
         "Holding inmobiliario: dos RUT, renovación anual de Property."),
        ("transportes_sur", "Transportes del Sur", "🚚", ["transportes_sur"],
         "Flota liviana y RC; cotiza vehículos por portal HDI."),
        ("valle_verde", "Agrícola Valle Verde", "🌾", ["valle_verde"],
         "Exportador de fruta: transporte terrestre y PYME."),
    ):
        detail = create_account_group(
            AccountGroupCreate(
                name=name,
                notes=notes,
                icon=AccountGroupIconInput(kind=AccountGroupIconKind.EMOJI, value=emoji),
                client_ids=[world.clients[m] for m in members],
            ),
            db=db,
            broker_id=world.broker_id,
            current_user=world.admin,
        )
        world.groups[key] = detail.id


# --- the full-journey account -----------------------------------------------------


def seed_full_journey(world: World) -> None:
    """Grupo Los Andes · Property Todo Riesgo: every stage through the guards."""
    from app.api.routers.case_files import create_case_file
    from app.api.routers.policies import create_policy_from_proposal
    from app.api.routers.proposals import accept_proposal
    from app.models.asset import Asset
    from app.models.placement import Placement, PlacementStatus
    from app.models.policy import Policy, PolicyStatus
    from app.models.proposal import Proposal, ProposalOrigin, ProposalStatus
    from app.models.quote import QuoteLineItem, QuoteRequest, QuoteRequestStatus
    from app.schemas.case_file import CaseFileCreate
    from app.schemas.policy import PolicyFromProposal
    from app.services import case_files as machine
    from app.services import policy_registry as registry

    db = world.db
    user = world.admin
    subtype = world.subtype("PRO-02")
    start, end = world.day(-40), world.day(-40) + timedelta(days=365)
    client_id = world.clients["los_andes"]

    asset = Asset(
        broker_id=world.broker_id,
        client_id=client_id,
        asset_type="building",
        name="Edificio Corporativo Los Andes",
        address="Av. Apoquindo 4501",
        commune="Las Condes",
        region="Metropolitana",
        construction_year=2012,
        floors=14,
        activity="Oficinas en arriendo",
        seismic_zone="Zona 2",
    )
    db.add(asset)
    db.flush()
    placement = Placement(
        broker_id=world.broker_id,
        client_id=client_id,
        asset_id=asset.id,
        insurance_line_id=subtype.insurance_line_id,
        period=machine.period_label_for(start, end),
        period_start=start,
        period_end=end,
        status=PlacementStatus.DRAFT,
        notes="Renovación Property 2026: incendio + sismo + perjuicio por paralización.",
    )
    db.add(placement)
    db.flush()

    detail = create_case_file(
        CaseFileCreate(
            placement_id=placement.id,
            insurance_subtype_id=subtype.id,
            period_start=start,
            period_end=end,
            account_group_id=world.groups["los_andes"],
            client_ids=[world.clients["rentas_andes"]],
            title="Grupo Los Andes · Property Todo Riesgo",
            owner_user_id=world.admin.id,
        ),
        db=db,
        broker_id=world.broker_id,
        current_user=user,
    )
    from app.models.case_file import CaseFile

    case = db.get(CaseFile, detail.id)
    world.cases["full_journey"] = case.id

    def move(stage: CaseStage, note: str) -> None:
        machine.apply_transition(db, case=case, to_stage=stage, user=user, note=note)
        db.commit()

    move(CaseStage.INTAKE, "Antecedentes solicitados al cliente")
    for category, name, title in (
        (DocumentCategory.INSURED_VALUES_SCHEDULE, "00C Cuadro de valores asegurados.pdf",
         "Cuadro de valores asegurados"),
        (DocumentCategory.LOSS_HISTORY, "00D Siniestralidad 5 años.pdf", "Siniestralidad últimos 5 años"),
    ):
        store_demo_document(
            world, entity_type=EntityType.CASE_FILE, entity_id=case.id, category=category,
            name=name, title=title, lines=["Edificio Corporativo Los Andes · Las Condes"],
            case_file_id=case.id, section=CaseSection.ROOT_PROSPECT, user=user,
        )
    move(CaseStage.TECHNICAL_BASIS, "Sin inspección: el ramo no la exige")
    store_demo_document(
        world, entity_type=EntityType.CASE_FILE, entity_id=case.id,
        category=DocumentCategory.TECHNICAL_BRIEF, name="01 Bases técnicas.pdf",
        title="Bases técnicas · Property Todo Riesgo",
        lines=["Monto asegurado UF 250.000 (edificio) + UF 30.000 (contenidos)",
               "Deducible sismo 2% del monto asegurado, mínimo UF 50"],
        case_file_id=case.id, section=CaseSection.SUBMISSION, user=user,
    )
    quote = QuoteRequest(
        broker_id=world.broker_id,
        placement_id=placement.id,
        case_file_id=case.id,
        insured_object="Edificio de oficinas de 14 pisos y sus contenidos",
        declared_value_uf=Decimal("280000.0000"),
        currency="UF",
        requested_coverages="Incendio, sismo, perjuicio por paralización, RC",
        desired_start=start,
        desired_end=end,
        status=QuoteRequestStatus.SENT,
        sent_at=world.now - timedelta(days=55),
        recipient_insurer_ids=[world.insurers[k].id for k in ("hdi", "sura", "chubb")],
        created_by_id=user.id,
    )
    quote.line_items = [
        QuoteLineItem(name="Edificio", value_uf=Decimal("250000.0000"), sort_order=1),
        QuoteLineItem(name="Contenidos", value_uf=Decimal("30000.0000"), sort_order=2),
    ]
    db.add(quote)
    db.flush()
    move(CaseStage.MARKET_SUBMISSION, "Bases enviadas a HDI, Sura y Chubb")

    proposals: dict[str, Proposal] = {}
    for key, taxable, exempt, number in (
        ("sura", "120", "30", "SURA-COT-26-4417"),
        ("chubb", "131.5", "28.25", "CHUBB-Q-889102"),
    ):
        doc = store_demo_document(
            world, entity_type=EntityType.CASE_FILE, entity_id=case.id,
            category=DocumentCategory.INSURER_QUOTATION,
            name=f"03 Cotización {INSURERS[key]['trade_name']}.pdf",
            title=f"Cotización {INSURERS[key]['trade_name']} · {number}",
            lines=[f"Prima neta UF {Decimal(taxable) + Decimal(exempt)} + IVA"],
            case_file_id=case.id, section=CaseSection.INSURER_QUOTES, user=user,
        )
        proposal = Proposal(
            broker_id=world.broker_id,
            quote_request_id=quote.id,
            case_file_id=case.id,
            insurer_id=world.insurers[key].id,
            origin=ProposalOrigin.NATIVE,
            source_document_id=doc.id,
            status=ProposalStatus.SUBMITTED,
            quotation_number=number,
            coverage_start=start,
            coverage_end=end,
            received_at=world.day(-50),
            validity_business_days=30,
            commission_pct=Decimal("12.5"),
            cover_mode="Todo riesgo",
            is_confirmed=True,
            confirmed_by_id=user.id,
            confirmed_at=world.now - timedelta(days=48),
            **money(taxable, exempt),
        )
        db.add(proposal)
        db.flush()
        proposals[key] = proposal
    move(CaseStage.QUOTES_RECEIVED, "Dos cotizaciones recibidas; HDI declinó")
    move(CaseStage.COMPARISON, "Comparación armada")
    move(CaseStage.INSURED_DECISION, "Comparación enviada al cliente")
    accept_proposal(proposals["sura"].id, db=db, broker_id=world.broker_id, _user=user)
    move(CaseStage.PROPOSAL_ISSUED, "El cliente eligió Sura")

    policy_doc = store_demo_document(
        world, entity_type=EntityType.CASE_FILE, entity_id=case.id, category=DocumentCategory.POLICY,
        name="08 Póliza Sura 5520-0001.pdf", title="Póliza Property Todo Riesgo · Sura",
        lines=["Póliza N° 5520-0001", f"Vigencia {start.isoformat()} a {end.isoformat()}"],
        case_file_id=case.id, section=CaseSection.POLICY_FILE, user=user,
    )
    read = create_policy_from_proposal(
        PolicyFromProposal(
            proposal_id=proposals["sura"].id,
            policy_number="5520-0001",
            source_document_id=policy_doc.id,
            case_file_id=case.id,
        ),
        db=db,
        broker_id=world.broker_id,
        current_user=user,
    )
    policy = db.get(Policy, read.id)
    policy.status = PolicyStatus.ACTIVE
    policy.insurance_subtype_id = subtype.id
    policy.has_source_document = True
    policy.payment_method = PaymentMode.TRANSFER
    policy.issued_at = world.day(-42)
    case.policy_id = policy.id
    db.flush()
    registry.ensure_default_parties(
        db,
        policy,
        payer={
            "name": CLIENTS["rentas_andes"]["legal_name"],
            "rut": CLIENTS["rentas_andes"]["rut"],
            "email": CLIENTS["rentas_andes"]["contact_email"],
            "phone": CLIENTS["rentas_andes"]["contact_phone"],
        },
    )
    move(CaseStage.POLICY_ISSUED, "Póliza emitida por Sura")
    move(CaseStage.MIRROR_VALIDATION, "Validación espejo sin diferencias")
    move(CaseStage.ACTIVE, "Colocación vigente")
    world.policies["sura_property"] = policy.id


# --- the partial doors: vía Manual + policy registry -------------------------------


def manual_policy(
    world: World,
    *,
    key: str,
    client: str,
    insurer: str,
    subtype_code: str,
    number: str,
    start_offset: int,
    taxable: str,
    exempt: str = "0",
    payment_method: PaymentMode,
    commission: str,
    payer: dict[str, str] | None = None,
    insured_amount: str | None = None,
    notes: str | None = None,
) -> int:
    """``POST /policies`` vía Manual: colocación on demand, skipped stages."""
    from app.api.routers.policies import create_policy
    from app.schemas.policy import PartyContact, PolicyCreate

    start = world.day(start_offset)
    detail = create_policy(
        PolicyCreate(
            client_id=world.clients[client],
            insurer_id=world.insurers[insurer].id,
            insurance_subtype_id=world.subtype(subtype_code).id,
            policy_number=number,
            start_date=start,
            end_date=start + timedelta(days=365),
            issued_at=start - timedelta(days=3),
            payment_method=payment_method,
            commission_pct=Decimal(commission),
            insured_amount_uf=Decimal(insured_amount) if insured_amount else None,
            payer=PartyContact(**payer) if payer else None,
            notes=notes,
            **money(taxable, exempt),
        ),
        db=world.db,
        broker_id=world.broker_id,
        current_user=world.executive,
    )
    world.policies[key] = detail.id
    return detail.id


def registry_policy(
    world: World,
    *,
    key: str,
    client: str,
    insurer: str,
    subtype_code: str,
    number: str,
    start_offset: int,
    taxable: str,
    exempt: str = "0",
    payment_method: PaymentMode,
    commission: str,
) -> int:
    """The registry door (as ``POST /policies/upload`` commits it, minus the AI
    read): ``origin=reader``, a source PDF, colocación on demand
    (``entry_origin=policy_upload``) with *omitida* events, default parties."""
    from app.api.routers.clients import record_activity
    from app.api.routers.policies import _sync_period
    from app.models.enums import CaseEntryOrigin
    from app.models.policy import Policy, PolicyStatus
    from app.services import policy_registry as registry

    db = world.db
    subtype = world.subtype(subtype_code)
    start = world.day(start_offset)
    policy = Policy(
        broker_id=world.broker_id,
        client_id=world.clients[client],
        insurer_id=world.insurers[insurer].id,
        insurance_line_id=subtype.insurance_line_id,
        insurance_subtype_id=subtype.id,
        policy_number=number,
        start_date=start,
        end_date=start + timedelta(days=365),
        issued_at=start - timedelta(days=2),
        status=PolicyStatus.ACTIVE,
        origin=PolicyOrigin.READER,
        payment_method=payment_method,
        commission_pct=Decimal(commission),
        is_core_valid=True,
        **money(taxable, exempt),
    )
    _sync_period(policy)
    db.add(policy)
    db.flush()
    doc = store_demo_document(
        world, entity_type=EntityType.POLICY, entity_id=policy.id, category=DocumentCategory.POLICY,
        name=f"Póliza {INSURERS[insurer]['trade_name']} {number}.pdf",
        title=f"Póliza {subtype.name} · {INSURERS[insurer]['trade_name']}",
        lines=[f"Póliza N° {number}", f"Vigencia {start.isoformat()} a {policy.end_date.isoformat()}"],
        section=CaseSection.POLICY_FILE, user=world.executive,
    )
    policy.source_document_id = doc.id
    policy.has_source_document = True
    attachment = registry.attach_or_create_account(
        db, broker_id=world.broker_id, policy=policy,
        entry_origin=CaseEntryOrigin.POLICY_UPLOAD, user=world.executive,
    )
    doc.case_file_id = attachment.case.id
    registry.ensure_default_parties(db, policy)
    record_activity(
        db,
        broker_id=world.broker_id,
        user=world.executive,
        action="policy.created",
        entity_type=EntityType.POLICY,
        entity_id=policy.id,
        description=f"Póliza {policy.policy_number} registrada desde PDF",
        meta={"origin": PolicyOrigin.READER.value, "case_file_id": attachment.case.id,
              "case_created": attachment.created},
    )
    db.commit()
    world.policies[key] = policy.id
    return policy.id


def seed_policies(world: World) -> None:
    seed_full_journey(world)
    manual_policy(
        world, key="hdi_vehicle", client="transportes_sur", insurer="hdi", subtype_code="VEH-01",
        number="HDI-VL-778812", start_offset=-72, taxable="18.5", payment_method=PaymentMode.CARD_DEBIT,
        commission="10", insured_amount="1450",
        notes="Camioneta Toyota Hilux 2023 · patente KTRS21",
    )
    registry_policy(
        world, key="mapfre_rc", client="transportes_sur", insurer="mapfre", subtype_code="RC-04",
        number="MAP-RC-300214", start_offset=-27, taxable="40", payment_method=PaymentMode.COUPON_BOOK,
        commission="15",
    )
    manual_policy(
        world, key="chubb_transport", client="valle_verde", insurer="chubb", subtype_code="TRA-01",
        number="CHB-TT-55102", start_offset=-60, taxable="60", payment_method=PaymentMode.TRANSFER,
        commission="12", insured_amount="9000",
        payer={"name": "Exportadora Valle Verde S.A.", "rut": rut(76567890),
               "email": "pagos@exportadoravv.example.com"},
    )
    registry_policy(
        world, key="sura_pyme", client="valle_verde", insurer="sura", subtype_code="PYM-01",
        number="SURA-PYME-71830", start_offset=-15, taxable="25", payment_method=PaymentMode.TRANSFER,
        commission="18",
    )
    # Keep the folders' handles for the summary.
    from app.models.policy import Policy

    for key, policy_id in world.policies.items():
        world.cases.setdefault(key, world.db.get(Policy, policy_id).case_file_id)


# --- cobranza --------------------------------------------------------------------------


def create_plan(
    world: World,
    *,
    key: str,
    policy_key: str,
    offsets: list[int],
    payment_mode: PaymentMode,
    level: CollectionLevelPreference = CollectionLevelPreference.AUTO,
) -> CollectionPlan:
    from app.api.routers.collections import create_collection_plan
    from app.models.policy import Policy
    from app.schemas.collection import CollectionPlanCreate, InstallmentCreate

    db = world.db
    policy = db.get(Policy, world.policies[policy_key])
    amounts = split_amount(Decimal(str(policy.total_premium_uf)), len(offsets))
    read = create_collection_plan(
        CollectionPlanCreate(
            policy_id=policy.id,
            case_file_id=policy.case_file_id,
            plan_number=f"PP-{policy.policy_number}",
            payment_mode=payment_mode,
            installment_count=len(offsets),
            origin=CollectionPlanOrigin.POLICY,
            source=CollectionPlanSource.MANUAL,
            level_preference=level,
            installments=[
                InstallmentCreate(
                    number=index,
                    coupon_number=f"{index:03d}",
                    due_date=world.day(offset),
                    gross_amount_uf=amount,
                )
                for index, (offset, amount) in enumerate(zip(offsets, amounts), start=1)
            ],
        ),
        db=db,
        broker_id=world.broker_id,
        current_user=world.executive,
    )
    world.plans[key] = read.id
    plan = db.get(CollectionPlan, read.id)
    if read.past_installments_pending is not None:
        # Review r2 (D1): past cuotas of a manual/reader policy start "por
        # revisar". The demo's late cuotas ARE unpaid — say so, so the overdue /
        # termination-risk examples stay on the board.
        from app.services.collection import past_review
        from app.services.collection.clock import business_today

        past_review.decide(
            db, plan, decision="unpaid", user=world.executive, today=business_today()
        )
        db.commit()
    return plan


def installment(world: World, plan_key: str, number: int) -> CollectionInstallment:
    row = world.db.scalar(
        select(CollectionInstallment).where(
            CollectionInstallment.collection_plan_id == world.plans[plan_key],
            CollectionInstallment.number == number,
        )
    )
    assert row is not None
    return row


def pay(
    world: World,
    inst: CollectionInstallment,
    *,
    paid_on: date,
    amount: Decimal | int | None = None,
    currency: PaymentCurrency = PaymentCurrency.UF,
    method: PaymentMode = PaymentMode.TRANSFER,
    confirmed: bool = False,
    note: str | None = None,
) -> None:
    from app.api.routers.collections import register_installment_payment
    from app.schemas.collection_board import PaymentCreate

    register_installment_payment(
        inst.id,
        PaymentCreate(
            amount=amount if amount is not None else Decimal(str(inst.gross_amount_uf)),
            currency=currency,
            paid_on=paid_on,
            method=method,
            confirmed_by_insurer=confirmed,
            note=note,
        ),
        db=world.db,
        broker_id=world.broker_id,
        current_user=world.executive,
    )


def start_notices(world: World, plan_key: str) -> None:
    from app.api.routers.collections import start_collection

    start_collection(
        world.plans[plan_key], payload=None, db=world.db, broker_id=world.broker_id,
        current_user=world.executive,
    )


def seed_collections(world: World) -> None:
    from app.services import fx

    # Sura Property (assisted): paid · overdue +4 · four upcoming.
    create_plan(world, key="sura_property", policy_key="sura_property",
                offsets=[-34, -4, 26, 56, 86, 116], payment_mode=PaymentMode.TRANSFER)
    pay(world, installment(world, "sura_property", 1), paid_on=world.day(-35),
        note="Transferencia informada por el cliente")
    start_notices(world, "sura_property")

    # HDI vehicle (native): paid · paid late · termination risk +12 · partial CLP.
    create_plan(world, key="hdi_vehicle", policy_key="hdi_vehicle",
                offsets=[-72 + 30 * k for k in range(12)], payment_mode=PaymentMode.CARD_DEBIT)
    pay(world, installment(world, "hdi_vehicle", 1), paid_on=world.day(-72),
        method=PaymentMode.CARD_DEBIT, confirmed=True)
    pay(world, installment(world, "hdi_vehicle", 2), paid_on=world.day(-38),
        method=PaymentMode.CARD_DEBIT, confirmed=True, note="Pagó con 4 días de atraso")
    partial = installment(world, "hdi_vehicle", 4)
    paid_on = world.day(-1)
    rate = fx.get_uf_rate(world.db, paid_on)
    half_clp = fx.uf_to_clp(Decimal(str(partial.gross_amount_uf)) / 2, rate)
    pay(world, partial, paid_on=paid_on, amount=half_clp, currency=PaymentCurrency.CLP,
        note="Abono parcial por transferencia en pesos")
    start_notices(world, "hdi_vehicle")

    # Mapfre RC (assisted, no insurer contact): due in 3 days · three upcoming.
    create_plan(world, key="mapfre_rc", policy_key="mapfre_rc",
                offsets=[3, 33, 63, 93], payment_mode=PaymentMode.COUPON_BOOK)
    start_notices(world, "mapfre_rc")

    # Chubb transport: paid (confirmed) · paid · due today · upcoming.
    create_plan(world, key="chubb_transport", policy_key="chubb_transport",
                offsets=[-60, -30, 0, 30, 60, 90], payment_mode=PaymentMode.TRANSFER)
    pay(world, installment(world, "chubb_transport", 1), paid_on=world.day(-61), confirmed=True)
    pay(world, installment(world, "chubb_transport", 2), paid_on=world.day(-30))
    # The Sura PYME policy deliberately has NO plan (the clock's plan_missing).


def seed_inquiry_and_links(world: World) -> None:
    from fastapi import Response

    from app.api.routers.inquiries import create_status_inquiry
    from app.api.routers.public_share import report_status
    from app.schemas.inquiries import InquiryCreate
    from app.schemas.share import PublicReportCreate
    from app.services.share_links import create_share_link

    db = world.db
    overdue = installment(world, "sura_property", 2)
    read = create_status_inquiry(
        InquiryCreate(
            policy_id=world.policies["sura_property"],
            installment_ids=[overdue.id],
            message="El cliente indica que transfirió la cuota; ¿pueden confirmar?",
            send_via="radal",
        ),
        db=db,
        broker_id=world.broker_id,
        current_user=world.executive,
    )
    from app.models.mail import StatusInquiry

    inquiry = db.get(StatusInquiry, read.id)
    world.links["inquiry_sura"] = db.get(ShareLink, inquiry.share_link_id).token

    insured_link = create_share_link(
        db, broker_id=world.broker_id, user=world.executive, entity_type=ShareEntity.POLICY,
        entity_id=world.policies["hdi_vehicle"], audience=ShareAudience.INSURED,
        label="Transportes del Sur · cuotas HDI", expires_in_days=60, now=world.now,
    )
    insurer_link = create_share_link(
        db, broker_id=world.broker_id, user=world.executive, entity_type=ShareEntity.POLICY,
        entity_id=world.policies["sura_property"], audience=ShareAudience.INSURER,
        label="Sura · estado de cuotas póliza 5520-0001", expires_in_days=30, now=world.now,
    )
    world.links["insured_hdi"] = insured_link.token
    world.links["insurer_sura"] = insurer_link.token
    db.commit()

    # Sura answers through its link: the overdue cuota was paid yesterday.
    report_status(
        insurer_link.token,
        PublicReportCreate(
            installment_id=overdue.id,
            status="paid",
            amount=Decimal(str(overdue.gross_amount_uf)),
            currency=PaymentCurrency.UF,
            paid_on=world.day(-1),
            comment="Pago recibido por transferencia; se refleja en nuestro sistema.",
        ),
        Response(),
        db,
    )


def seed_rpa(world: World) -> None:
    from app.api.routers.portal_searches import put_credentials
    from app.schemas.portal_searches import CredentialWrite
    from app.services.rpa.base import RunContext
    from app.services.rpa.engine import _finish_failure, get_credential
    from app.services.rpa.errors import LayoutChanged
    from app.services.rpa.registry import get_connector

    db = world.db
    connector = get_connector("hdi")
    if connector is None:
        raise DemoSeedError("the HDI portal connector is not registered")
    put_credentials(
        "hdi",
        CredentialWrite(username="corredora.demo", password="demo-portal-password-not-real"),
        db=db,
        broker_id=world.broker_id,
        current_user=world.admin,
    )
    credential = get_credential(db, broker_id=world.broker_id, portal="hdi")
    credential.status = CredentialStatus.VALID
    credential.last_verified_at = world.now - timedelta(days=2)

    subtype = world.subtype("VEH-01")
    case_id = world.cases.get("hdi_vehicle")

    def queue(form: dict[str, Any]) -> PortalSearchRun:
        run = PortalSearchRun(
            broker_id=world.broker_id,
            portal=connector.key,
            case_file_id=case_id,
            insurance_subtype_id=subtype.id,
            requested_by_user_id=world.executive.id,
            input=connector.validate_input(form),
            status=PortalRunStatus.QUEUED,
            connector_version=connector.version[:16],
        )
        db.add(run)
        db.flush()
        return run

    base = {
        "brand": "Toyota", "model": "Hilux 2.4 DX 4x4", "year": 2023, "use": "commercial",
        "commune": "Puerto Montt", "insured_rut": CLIENTS["transportes_sur"]["rut"],
        "start_date": world.day(10).isoformat(),
    }
    queue({**base, "plate": "LKJP34"})  # stays queued (rpa.run picks it up)

    failed = queue({**base, "plate": "KTRS21", "model": "Hilux 2.8 SRX 4x4", "year": 2024})
    # Created → started → finished in that order, and close to "now" because the
    # step log is stamped with the real clock (QA v12: it "started" 2 h before
    # it was created).
    failed.created_at = world.now - timedelta(minutes=4)
    failed.status = PortalRunStatus.RUNNING
    failed.started_at = world.now - timedelta(minutes=3)
    ctx = RunContext(page=None, input=dict(failed.input), credentials={})
    for name, op in (("open_login", "goto"), ("fill_username", "fill"), ("fill_password", "fill"),
                     ("submit_login", "click"), ("login_result", "wait_for"),
                     ("fill_plate", "fill"), ("submit_quote", "click"),
                     ("wait_results", "wait_for")):
        ctx.log_step(name, True, op=op)
    ctx.log_step("read_results", False, "selector not found: #results-table tbody tr", op="extract_table")
    _finish_failure(
        db, failed, connector,
        LayoutChanged("selector not found: #results-table tbody tr", step="read_results"),
        ctx, None, world.now,
    )
    db.commit()


def run_clock(world: World) -> dict[str, Any]:
    """The ``collection.clock`` job for today; the broker's clock if the slot ran."""
    from app.jobs import trigger_job
    from app.models.enums import JobStatus
    from app.services.collection.clock import run_broker_clock

    outcome = trigger_job(world.db, "collection.clock", world.now)
    if outcome.executed:
        if outcome.run.status != JobStatus.SUCCEEDED:
            raise DemoSeedError(f"collection.clock failed: {outcome.run.error}")
        return dict(outcome.run.stats or {})
    # Today's slot already ran on this database: run the demo broker's clock.
    stats = run_broker_clock(world.db, world.broker_id, now=world.now)
    world.db.commit()
    return stats


def build_world(db: DemoSession, *, today: date, now: datetime, platform_user: User) -> World:
    world = World(db=db, today=today, now=now)
    world.insurers = seed_platform(db, platform_user=platform_user, now=now)
    seed_broker(world)
    seed_rates(world)
    seed_clients_and_groups(world)
    seed_policies(world)
    seed_collections(world)
    seed_inquiry_and_links(world)
    seed_rpa(world)
    run_clock(world)
    return world


# =============================================================================
# Summary
# =============================================================================


def find_demo_broker(db: Session) -> Broker | None:
    return db.scalar(
        select(Broker).where(or_(Broker.rut == DEMO_BROKER_RUT, Broker.legal_name == DEMO_BROKER_NAME))
    )


def summarize(db: Session, broker: Broker) -> dict[str, Any]:
    """Counts + the share-link URLs of the demo broker (read-only)."""
    from app.models.case_file import CaseFile
    from app.models.mail import PaymentEvidence, StatusInquiry
    from app.models.notification import Notification
    from app.models.payment import Payment
    from app.models.policy import Policy

    def count(model, *where) -> int:  # noqa: ANN001
        return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)

    bid = broker.id
    links = db.scalars(
        select(ShareLink).where(ShareLink.broker_id == bid).order_by(ShareLink.id)
    ).all()
    return {
        "broker": f"{broker.legal_name} ({broker.rut})",
        "logins": [
            ("admin@radal.cl", PASSWORD, "platform_admin"),
            (ADMIN_EMAIL, PASSWORD, "broker_admin"),
            (EXECUTIVE_EMAIL, PASSWORD, "broker_executive"),
        ],
        "counts": {
            "policies": count(Policy, Policy.broker_id == bid),
            "account_folders": count(CaseFile, CaseFile.broker_id == bid, CaseFile.kind == CaseFileKind.ACCOUNT),
            "collection_plans": count(CollectionPlan, CollectionPlan.broker_id == bid),
            "installments": count(CollectionInstallment, CollectionInstallment.broker_id == bid),
            "payments": count(Payment, Payment.broker_id == bid),
            "status_inquiries": count(StatusInquiry, StatusInquiry.broker_id == bid),
            "payment_evidence": count(PaymentEvidence, PaymentEvidence.broker_id == bid),
            "share_links": len(links),
            "notifications": count(Notification, Notification.broker_id == bid),
            "portal_search_runs": count(PortalSearchRun, PortalSearchRun.broker_id == bid),
        },
        "share_links": [
            {
                "label": link.label,
                "audience": link.audience.value,
                "entity": f"{link.entity_type.value}:{link.entity_id}",
                "url": f"/s/{link.token}",
            }
            for link in links
        ],
    }


def print_summary(summary: dict[str, Any], *, created: bool) -> None:
    print(("demo world created: " if created else "demo world already present: ") + summary["broker"])
    print("logins:")
    for email, password, role in summary["logins"]:
        print(f"  {email:<22} / {password}   ({role})")
    print("counts:")
    for key, value in summary["counts"].items():
        print(f"  {key}: {value}")
    print("share links (relative to the SPA origin):")
    for link in summary["share_links"]:
        print(f"  [{link['audience']}] {link['label']}: {link['url']}")


# =============================================================================
# Entry point
# =============================================================================


def seed_demo(url: str, *, media_dir: str, today: date | None = None) -> tuple[dict[str, Any], bool]:
    """Build (or find) the demo world on ``url``. Returns ``(summary, created)``.

    The caller has already applied the refuse guard.
    """
    from app.services.policy_registry import today_santiago

    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args, future=True)
    try:
        Base.metadata.create_all(bind=engine)
        factory = sessionmaker(bind=engine, class_=DemoSession, autoflush=False, future=True)
        with hermetic_runtime(media_dir):
            db: DemoSession = factory()
            try:
                platform_user, _ = ensure_platform_admin(db)
                seed_catalog(db)
                seed_collection_defaults(db)
                broker = find_demo_broker(db)
                created = broker is None
                if created:
                    build_world(
                        db, today=today or today_santiago(), now=utcnow(), platform_user=platform_user
                    )
                    broker = find_demo_broker(db)
                else:
                    seed_platform(db, platform_user=platform_user, now=utcnow())
                db.commit_for_real()
                return summarize(db, broker), created
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.seed_v12_demo",
        description="Build the v12 demo world (refuses anything but a throwaway database).",
    )
    parser.add_argument("--url", help="database URL (default: DATABASE_URL / the app's database)")
    parser.add_argument(DEMO_FLAG, dest="forced", action="store_true",
                        help="accept a database that is not a SQLite file under /tmp")
    parser.add_argument("--media-dir", default=None,
                        help="local media dir for the generated PDFs (default: MEDIA_LOCAL_DIR)")
    args = parser.parse_args(argv)

    url = _resolve_url(args.url)
    reason = refusal_reason(url, forced=args.forced)
    if reason is not None:
        print(reason, file=sys.stderr)
        return 2
    print(f"database: {make_url(url).render_as_string(hide_password=True)}")
    media_dir = args.media_dir or settings.MEDIA_LOCAL_DIR
    summary, created = seed_demo(url, media_dir=media_dir)
    print_summary(summary, created=created)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
