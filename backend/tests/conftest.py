"""Shared pytest fixtures: an isolated SQLite database and a two-broker world.

The database URL is pinned to a temp file BEFORE any ``app.*`` module is
imported, because ``app.db.session`` builds its engine at import time. Every
test then runs against a schema created by ``Base.metadata.create_all`` on that
same engine, and ``get_db`` is overridden so the FastAPI app and the test share
one session factory.

The world is deliberately built as TWO brokers holding mirror-image records
(``world.a`` / ``world.b``). Tenant isolation is only meaningful when broker B
actually owns something for broker A to fail to reach.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

# --- Pin the DB before app import -------------------------------------------
_TMP_DB = Path(tempfile.mkdtemp(prefix="radal-tests-")) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("S3_BUCKET", "radal-test-bucket")
# Hermetic AI: a developer's backend/.env may carry a real AI_API_KEY, and a
# best-effort summarize (packs) would then call the live provider from inside
# the test suite — slow at best, a socket that never returns at worst. Blank
# the key (assignment, not setdefault: it must shadow .env) and point the base
# URL at an unroutable port so even a test that fakes a key fails fast instead
# of reaching DeepInfra. Tests that exercise AI paths fake the chat model.
os.environ["AI_API_KEY"] = ""
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9/no-provider-in-tests"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.db.base  # noqa: F401,E402  (registers every model on the metadata)
from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal, engine, get_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models.asset import Asset  # noqa: E402
from app.models.base_class import Base  # noqa: E402
from app.models.broker import Broker  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.document import Document, DocumentCategory  # noqa: E402
from app.models.enums import EntityType, UserType  # noqa: E402
from app.models.insurance_line import InsuranceLine  # noqa: E402
from app.models.insured import Insured  # noqa: E402
from app.models.insurer import Insurer  # noqa: E402
from app.models.placement import Placement, PlacementStatus  # noqa: E402
from app.models.quote import QuoteLineItem, QuoteRequest, QuoteRequestStatus  # noqa: E402
from app.models.user import User  # noqa: E402

API = "/api/v1"


# --- Schema lifecycle --------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db() -> Session:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncate every table between tests so each one starts from a known world."""
    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.exec_driver_sql(f'DELETE FROM "{table.name}"')


@pytest.fixture()
def client() -> TestClient:
    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()


# --- The two-broker world ----------------------------------------------------

@dataclass
class Tenant:
    """One broker workspace plus the records the tests reach for."""

    broker: Broker
    admin: User
    executive: User
    client: Client
    asset: Asset
    line: InsuranceLine
    placement: Placement
    quote: QuoteRequest
    document: Document
    # Added with the case-file pass: the two roles whose grants differ on the
    # new modules (technician approves, inspector only sees inspected cases).
    technician: User | None = None
    inspector: User | None = None

    @property
    def broker_id(self) -> int:
        return self.broker.id


@dataclass
class World:
    a: Tenant
    b: Tenant
    insurer: Insurer          # complete: rut + cmf_code
    insurer_no_cmf: Insurer   # missing cmf_code -> proposals must be refused
    insurer_no_rut: Insurer   # missing rut      -> proposals must be refused


PASSWORD = "radal1234"

# bcrypt is deliberately slow. The suite rebuilds the world for every test, so
# hash the single test password once instead of 4x per test.
_PASSWORD_HASH: str | None = None


def _password_hash() -> str:
    global _PASSWORD_HASH
    if _PASSWORD_HASH is None:
        _PASSWORD_HASH = hash_password(PASSWORD)
    return _PASSWORD_HASH


def _make_tenant(db: Session, tag: str, rut: str, insured_rut: str) -> Tenant:
    broker = Broker(rut=rut, legal_name=f"Corredora {tag} SpA", trade_name=tag)
    db.add(broker)
    db.flush()

    admin = User(
        broker_id=broker.id,
        email=f"admin@{tag.lower()}.cl",
        hashed_password=_password_hash(),
        full_name=f"Admin {tag}",
        user_type=UserType.BROKER,
        role="broker_admin",
        is_active=True,
    )
    executive = User(
        broker_id=broker.id,
        email=f"exec@{tag.lower()}.cl",
        hashed_password=_password_hash(),
        full_name=f"Ejecutivo {tag}",
        user_type=UserType.BROKER,
        role="broker_executive",
        is_active=True,
    )
    insured = Insured(rut=insured_rut, legal_name=f"Asegurado {tag} S.A.")
    db.add_all([admin, executive, insured])
    db.flush()

    client_row = Client(broker_id=broker.id, insured_id=insured.id, sector="Industrial")
    line = InsuranceLine(broker_id=broker.id, name=f"Incendio y Sismo {tag}")
    db.add_all([client_row, line])
    db.flush()

    asset = Asset(
        broker_id=broker.id,
        client_id=client_row.id,
        asset_type="property",
        name=f"Planta {tag}",
    )
    db.add(asset)
    db.flush()

    placement = Placement(
        broker_id=broker.id,
        client_id=client_row.id,
        asset_id=asset.id,
        insurance_line_id=line.id,
        status=PlacementStatus.NEGOTIATING,
        period_start=date(2026, 1, 1),
        period_end=date(2027, 1, 1),
    )
    db.add(placement)
    db.flush()

    quote = QuoteRequest(
        broker_id=broker.id,
        placement_id=placement.id,
        insured_object=f"Planta {tag}",
        declared_value_uf=Decimal("100000.0000"),
        status=QuoteRequestStatus.RECEIVING,
    )
    quote.line_items.append(
        QuoteLineItem(name="Edificio", value_uf=Decimal("100000.0000"), sort_order=0)
    )
    db.add(quote)
    db.flush()

    document = Document(
        broker_id=broker.id,
        entity_type=EntityType.QUOTE_REQUEST,
        entity_id=quote.id,
        s3_key=f"documents/quote_request/{quote.id}/{tag}-propuesta.pdf",
        bucket="radal-test-bucket",
        original_name=f"{tag}-propuesta.pdf",
        mime_type="application/pdf",
        category=DocumentCategory.PROPOSAL,
    )
    db.add(document)
    db.flush()

    return Tenant(
        broker=broker,
        admin=admin,
        executive=executive,
        client=client_row,
        asset=asset,
        line=line,
        placement=placement,
        quote=quote,
        document=document,
    )


@pytest.fixture()
def world(db: Session) -> World:
    tenant_a = _make_tenant(db, "Alfa", "76111111-6", "77111111-4")
    tenant_b = _make_tenant(db, "Beta", "76222222-1", "77222222-K")

    insurer = Insurer(
        legal_name="HDI Seguros S.A.",
        rut="99301000-6",
        cmf_code="CMF-HDI-001",
        is_native=True,
    )
    insurer_no_cmf = Insurer(
        legal_name="Sin CMF Seguros S.A.",
        rut="5000023-0",
        cmf_code="",
        is_native=False,
    )
    insurer_no_rut = Insurer(
        legal_name="Sin RUT Seguros S.A.",
        rut="",
        cmf_code="CMF-NORUT-9",
        is_native=False,
    )
    db.add_all([insurer, insurer_no_cmf, insurer_no_rut])
    db.commit()

    for obj in (
        tenant_a.broker, tenant_a.admin, tenant_a.executive, tenant_a.client,
        tenant_a.asset, tenant_a.line, tenant_a.placement, tenant_a.quote,
        tenant_a.document,
        tenant_b.broker, tenant_b.admin, tenant_b.executive, tenant_b.client,
        tenant_b.asset, tenant_b.line, tenant_b.placement, tenant_b.quote,
        tenant_b.document,
        insurer, insurer_no_cmf, insurer_no_rut,
    ):
        db.refresh(obj)

    return World(
        a=tenant_a,
        b=tenant_b,
        insurer=insurer,
        insurer_no_cmf=insurer_no_cmf,
        insurer_no_rut=insurer_no_rut,
    )


# --- Auth helpers ------------------------------------------------------------

def login(client: TestClient, email: str, password: str = PASSWORD) -> str:
    """Log in through the real endpoint and return the access token."""
    response = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth_headers(client: TestClient, email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {login(client, email)}"}


@pytest.fixture()
def headers_a(client: TestClient, world: World) -> dict[str, str]:
    """Broker A administrator — full rights inside tenant A."""
    return auth_headers(client, world.a.admin.email)


@pytest.fixture()
def headers_b(client: TestClient, world: World) -> dict[str, str]:
    """Broker B administrator — full rights inside tenant B."""
    return auth_headers(client, world.b.admin.email)


@pytest.fixture()
def headers_a_exec(client: TestClient, world: World) -> dict[str, str]:
    """Broker A executive — a low-privilege role (no Users.Manage)."""
    return auth_headers(client, world.a.executive.email)


def ensure_role_user(db: Session, tenant: Tenant, role: str, label: str) -> User:
    """Create (once) an extra broker user with ``role`` inside ``tenant``.

    Deliberately NOT part of the default world: the isolation suite asserts the
    exact user roster of a tenant, and this pass must stay additive.
    """
    existing = getattr(tenant, label, None)
    if existing is not None:
        return existing
    tag = tenant.broker.trade_name or str(tenant.broker_id)
    user = User(
        broker_id=tenant.broker_id,
        email=f"{label}@{tag.lower()}.cl",
        hashed_password=_password_hash(),
        full_name=f"{label.title()} {tag}",
        user_type=UserType.BROKER,
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    setattr(tenant, label, user)
    return user


@pytest.fixture()
def headers_a_tech(client: TestClient, db: Session, world: World) -> dict[str, str]:
    """Broker A technician — owns the technical side, approves endorsements."""
    user = ensure_role_user(db, world.a, "broker_technician", "technician")
    return auth_headers(client, user.email)


@pytest.fixture()
def headers_a_inspector(client: TestClient, db: Session, world: World) -> dict[str, str]:
    """Broker A inspector — the narrowest broker role (partial CaseFiles.View)."""
    user = ensure_role_user(db, world.a, "broker_inspector", "inspector")
    return auth_headers(client, user.email)


# --- Case-file helpers (additive; used by the post-sale + pack suites) --------

def make_case_file(db: Session, tenant: Tenant, **overrides):
    """An account case wrapping the tenant's placement, at ``intake`` by default."""
    from app.models.case_file import CaseFile
    from app.models.enums import CaseFileKind, CaseFileStatus, CaseStage

    fields = {
        "broker_id": tenant.broker_id,
        "client_id": tenant.client.id,
        "placement_id": tenant.placement.id,
        "insurance_line_id": tenant.line.id,
        "kind": CaseFileKind.ACCOUNT,
        "stage": CaseStage.INTAKE,
        "status": CaseFileStatus.OPEN,
        "reference": f"EXP-TEST-{tenant.broker_id}-{overrides.pop('n', 1)}",
        "title": f"Expediente {tenant.broker.trade_name}",
        "sequence_no": 1,
        "version": 1,
    }
    fields.update(overrides)
    case = CaseFile(**fields)
    db.add(case)
    db.flush()
    return case


@pytest.fixture()
def insurer_policy(db: Session, world: World):
    """One active policy of broker A, money already reconciled."""
    policy = make_policy(db, world.a, world.insurer)
    db.commit()
    db.refresh(policy)
    return policy


def make_policy(db: Session, tenant: Tenant, insurer, **overrides):
    """A minimal active policy for the post-sale suites."""
    from decimal import Decimal as _Decimal

    from app.models.policy import Policy, PolicyStatus

    fields = {
        "broker_id": tenant.broker_id,
        "client_id": tenant.client.id,
        "placement_id": tenant.placement.id,
        "insurer_id": insurer.id,
        "insurance_line_id": tenant.line.id,
        "policy_number": f"POL-{tenant.broker_id}-1",
        "status": PolicyStatus.ACTIVE,
        "insured_amount_uf": _Decimal("100000.0000"),
        "taxable_premium_uf": _Decimal("100.0000"),
        "exempt_premium_uf": _Decimal("20.0000"),
        "net_premium_uf": _Decimal("120.0000"),
        "vat_uf": _Decimal("19.0000"),
        "total_premium_uf": _Decimal("139.0000"),
    }
    fields.update(overrides)
    policy = Policy(**fields)
    db.add(policy)
    db.flush()
    return policy
