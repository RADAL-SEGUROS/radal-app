"""Import the team's real fixture package into the Radal v2 schema.

    python -m app.db.import_fixtures --no-upload          # local SQLite, no AWS
    python -m app.db.import_fixtures --reset              # wipe + reimport
    python -m app.db.import_fixtures --profile radal      # + upload the 66 files

What it does, in order:

1. ``create_all`` and seed the GLOBAL catalogs: the 44-row CMF/FECU line
   taxonomy and all 25 CMF general insurers (``is_native=False`` by default).
2. Mark the NATIVE partner set and give each one a ``native_insurer_profile``
   and ``insurer_contact`` rows.
3. Import the broker workspace: 3 brokers, 15 users, 3 insureds, 3 clients,
   3 assets, 6 insurance lines (+ CMF junction), 3 placements, 3 quote requests
   (+ line items), 9 proposals (+ coverages/exclusions), 3 inspection requests,
   3 inspections (+ boundaries), 66 documents, activity and notes.
4. Upload the 66 files to S3 under the v2 layout and point each ``document`` row
   at its new key (``--no-upload`` / ``--dry-run`` skip the AWS calls).

Two things worth knowing before reading the code:

**Ids are allocated up front.** ``proposal.source_document_id`` is NOT NULL while
``document.entity_id`` points back at the proposal — a cycle that cannot be
resolved by insert order alone. So every source id (``of-001``) is mapped to its
integer primary key before anything is written, and rows are inserted with
explicit PKs. Both SQLite and MySQL advance their auto-increment past an explicit
PK, so the app keeps inserting normally afterwards.

**The native/external split is deliberate, not incidental.** The corpus has all
nine proposals coming from catalog companies, which would leave the
external-insurer tracking path untested. Per the product owner we therefore
concentrate exactly two external proposals in ONE insurance line — see
``EXTERNAL_PROPOSAL_REASSIGNMENTS`` below.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.db.fixture_mappings import (
    ACTIVITY_VERB,
    ASSET_TYPE,
    BOUNDARY_ORIENTATION,
    CLIENT_STATUS,
    CMF_LINE_KIND,
    DOCUMENT_CATEGORY,
    ENTITY_TYPE,
    INSPECTION_REQUEST_STATUS,
    INSPECTION_STATUS,
    PLACEMENT_STATUS,
    PRIORITY_BY_URGENCY,
    PROPOSAL_STATUS,
    ROLE_BY_SUBROLE,
    is_aggravating,
    norm_token,
    normalize_checklist,
    parse_deductibles,
    split_address,
    split_asset_attributes,
    to_date,
    to_datetime,
    to_decimal,
)
from app.db.session import SessionLocal, engine
from app.models import (
    Activity,
    Asset,
    Broker,
    Client,
    CmfLine,
    CmfLineKind,
    Document,
    Inspection,
    InspectionBoundary,
    InspectionRequest,
    InsuranceLine,
    InsuranceLineCmfCode,
    Insured,
    Insurer,
    InsurerContact,
    NativeInsurerProfile,
    Note,
    Placement,
    Proposal,
    ProposalCoverage,
    QuoteLineItem,
    QuoteRequest,
    User,
)
from app.models.asset import AssetStatus
from app.models.base_class import Base
from app.models.broker import BrokerStatus
from app.models.document import DocumentCategory
from app.models.enums import CoverageKind, EntityType, PersonType, Priority, UserType
from app.models.insurer import InsurerStatus
from app.models.proposal import ProposalOrigin
from app.models.quote import QuoteRequestStatus
from app.services.identifiers import normalize_codigo_cmf, validate_rut

# Every demo account shares this password. Fixtures only — never production.
DEMO_PASSWORD = "radal1234"

DEFAULT_PACKAGE = Path.home() / "Downloads" / "radal-data-mvp 2"

# Money invariants are checked to the cent; the source rounds VAT to 2 decimals.
MONEY_TOLERANCE = Decimal("0.011")


# =============================================================================
# The native / external partner design
# =============================================================================

# Radal's commercial partners. Everything else in the 25-company CMF list stays
# is_native=False: a real, registered company the platform simply has no
# agreement with. HDI and Chubb are kept native because the corpus gives them
# rich profiles and contacts; Mapfre joins them because it is the third insurer
# quoted on every request, and four more are added so the catalog looks like a
# real partner roster rather than a trio.
NATIVE_INSURER_KEYS: dict[str, dict[str, Any]] = {
    "asr-001": {  # HDI Seguros — source profile + contact
        "priority": 10,
        "sla_hours": 48,
        "commercial_agreement": "Acuerdo marco de colaboración Radal — property corporativo, comisión estándar 15%.",
        "notes": "Partner fundador. Mesa de suscripción property dedicada.",
    },
    "asr-002": {  # Chubb Seguros — source profile + contact
        "priority": 20,
        "sla_hours": 72,
        "commercial_agreement": "Acuerdo marco de colaboración Radal — riesgos de empresa, comisión estándar 12%.",
        "notes": "Partner fundador. Fuerte en riesgos de alta complejidad.",
    },
    "asr-003": {  # Mapfre — source profile + contact
        "priority": 30,
        "sla_hours": 72,
        "commercial_agreement": "Acuerdo marco de colaboración Radal — riesgos de empresa e ingeniería.",
        "notes": "Partner fundador. Cobertura nacional con oficinas en regiones.",
    },
    "asr-006": {  # BCI Seguros Generales
        "priority": 40,
        "sla_hours": 96,
        "commercial_agreement": "Acuerdo comercial Radal — pymes y riesgos de empresa.",
        "notes": "Buen apetito en pyme industrial y comercio.",
    },
    "asr-019": {  # Seguros Generales Suramericana (SURA)
        "priority": 50,
        "sla_hours": 96,
        "commercial_agreement": "Acuerdo comercial Radal — property y responsabilidad civil.",
        "notes": "Respuesta rápida en cotizaciones bajo UF 50.000.",
    },
    "asr-020": {  # Southbridge
        "priority": 60,
        "sla_hours": 120,
        "commercial_agreement": "Acuerdo comercial Radal — riesgos industriales.",
        "notes": "Apetito selectivo; requiere inspección previa en todo riesgo.",
    },
    "asr-024": {  # Zurich Chile
        "priority": 70,
        "sla_hours": 120,
        "commercial_agreement": "Acuerdo comercial Radal — grandes riesgos e ingeniería.",
        "notes": "Capacidad para riesgos sobre UF 200.000 y coaseguro.",
    },
}

# Plausible desk contacts for the natives the corpus does not describe. The
# "-ficticio.cl" domain follows the source package's own convention for marking
# fixture data, so nobody mistakes these for real mailboxes.
EXTRA_NATIVE_CONTACTS: dict[str, dict[str, str]] = {
    "asr-006": {
        "name": "Mesa de Suscripción Empresas",
        "email": "suscripcion.empresas@bciseguros-ficticio.cl",
        "phone": "+56 2 2300 0000",
        "role": "Suscripción Riesgos de Empresa",
    },
    "asr-019": {
        "name": "Mesa Property Corporativo",
        "email": "property.corporativo@sura-ficticio.cl",
        "phone": "+56 2 2400 0000",
        "role": "Suscripción Property",
    },
    "asr-020": {
        "name": "Underwriting Riesgos Industriales",
        "email": "underwriting.industrial@southbridge-ficticio.cl",
        "phone": "+56 2 2500 0000",
        "role": "Suscripción Industrial",
    },
    "asr-024": {
        "name": "Mesa de Grandes Riesgos",
        "email": "grandes.riesgos@zurich-ficticio.cl",
        "phone": "+56 2 2600 0000",
        "role": "Suscripción Grandes Riesgos",
    },
}

# THE EXTERNAL-INSURER SCENARIO.
#
# Quote cot-003 (Clínica del Valle, Incendio y Sismo) originally received three
# proposals from HDI / Chubb / Mapfre — all partners. We move two of them to real
# CMF companies that are NOT partners, so one insurance line ends up with 2 of 3
# proposals external. Orion and Reale are genuine registered general insurers
# from the official 25-company list; nothing is invented.
#
# The winning proposal (of-009, "aceptada") is one of the two external ones on
# purpose: the broker beat their partner panel with a company Radal has no
# agreement with, which is precisely the signal the tracking path exists to
# capture.
EXTERNAL_PROPOSAL_REASSIGNMENTS: dict[str, str] = {
    "of-008": "asr-016",  # Chubb -> Orion Seguros Generales S.A.
    "of-009": "asr-017",  # Mapfre -> Reale Chile Seguros Generales S.A.
}

# Which insurance line the comparator surfaces by default. Line-agnostic on
# purpose: these are the money columns every proposal has.
DEFAULT_COMPARATOR_FIELDS: dict[str, Any] = {
    "columns": [
        "total_premium_uf",
        "net_premium_uf",
        "comprehensive_rate_permille",
        "commission_pct",
        "validity_business_days",
        "coverage_start",
        "coverage_end",
    ],
    "deductible_perils": ["fire", "earthquake", "other"],
}


class FixtureError(RuntimeError):
    """Raised when the source package violates an invariant we rely on."""


# =============================================================================
# Support: logging, id allocation, S3
# =============================================================================


class Log:
    """Plain stdout reporter. No logging config to fight with in a CLI script."""

    def __init__(self, verbose: bool = True) -> None:
        self.verbose = verbose
        self.warnings: list[str] = []

    def section(self, title: str) -> None:
        print(f"\n\033[1m{title}\033[0m", flush=True)

    def info(self, msg: str) -> None:
        if self.verbose:
            print(f"  {msg}", flush=True)

    def ok(self, msg: str) -> None:
        print(f"  \033[32m✓\033[0m {msg}", flush=True)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"  \033[33m!\033[0m {msg}", flush=True)


class Ids:
    """Source id (``of-001``) -> integer primary key, allocated before insert.

    Needed because ``proposal.source_document_id`` (NOT NULL) and
    ``document.entity_id`` point at each other; no insert order resolves that
    without knowing both keys in advance.
    """

    def __init__(self) -> None:
        self._maps: dict[str, dict[str, int]] = defaultdict(dict)

    def allocate(self, table: str, source_ids: Iterable[str]) -> None:
        table_map = self._maps[table]
        for source_id in source_ids:
            if source_id not in table_map:
                table_map[source_id] = len(table_map) + 1

    def get(self, table: str, source_id: str) -> int:
        try:
            return self._maps[table][source_id]
        except KeyError as exc:  # pragma: no cover - fixture integrity
            raise FixtureError(f"Unknown {table} id in fixtures: {source_id!r}") from exc

    def maybe(self, table: str, source_id: str | None) -> int | None:
        if not source_id:
            return None
        return self._maps[table].get(source_id)


@dataclass
class Uploader:
    """Copies the package's files to S3 under the v2 layout.

    ``enabled=False`` (``--no-upload`` / ``--dry-run``) turns every call into a
    no-op so the whole import runs on a laptop with no AWS credentials.
    """

    bucket: str
    region: str
    profile: str | None
    enabled: bool
    log: Log
    uploaded: int = 0
    skipped: int = 0
    _client: Any = field(default=None, init=False, repr=False)

    def client(self) -> Any:
        if self._client is None:
            import boto3  # noqa: PLC0415  (lazy: not needed for --no-upload)

            session = boto3.Session(profile_name=self.profile, region_name=self.region)
            self._client = session.client("s3")
        return self._client

    def put(self, local_path: Path, key: str, content_type: str | None) -> None:
        if not self.enabled:
            self.skipped += 1
            return
        extra: dict[str, str] = {}
        if content_type:
            extra["ContentType"] = content_type
        self.client().upload_file(str(local_path), self.bucket, key, ExtraArgs=extra or None)
        self.uploaded += 1
        self.log.info(f"uploaded s3://{self.bucket}/{key}")


def _end_of_day(value: Any) -> datetime | None:
    """A due *date* means the end of that day, not midnight before it."""
    parsed = to_date(value)
    if parsed is None:
        return None
    return datetime.combine(parsed, time(23, 59), tzinfo=timezone.utc)


def _ext_of(name: str) -> str:
    return (Path(name).suffix or "").lstrip(".").lower() or "bin"


def _mime_of(name: str, declared: str | None) -> str | None:
    return declared or mimetypes.guess_type(name)[0]


# =============================================================================
# The importer
# =============================================================================


class FixtureImporter:
    """Reads the package once, writes the whole graph in dependency order."""

    def __init__(self, db: Session, package: Path, uploader: Uploader, log: Log) -> None:
        self.db = db
        self.package = package
        self.seeds = package / "seeds"
        self.files = package / "s3"
        self.uploader = uploader
        self.log = log
        self.ids = Ids()
        self.raw: dict[str, list[dict[str, Any]]] = {}

        # Cross-reference tables built while importing.
        self.broker_of_client: dict[str, str] = {}
        self.broker_of_insured: dict[str, str] = {}
        self.broker_of_insurer_entity: dict[str, str] = {}
        self.client_of_asset: dict[str, str] = {}
        self.asset_of_inspection: dict[str, str] = {}
        self.quote_of_proposal: dict[str, str] = {}
        self.client_of_placement: dict[str, str] = {}
        self.insurer_of_entity: dict[str, str] = {}
        self.doc_by_source_key: dict[str, int] = {}
        self.proposal_doc: dict[str, int] = {}
        self.external_proposals: list[str] = []

    # --- loading -----------------------------------------------------------

    def load(self, name: str) -> list[dict[str, Any]]:
        if name not in self.raw:
            path = self.seeds / f"{name}.json"
            if not path.exists():
                raise FixtureError(f"Missing fixture file: {path}")
            self.raw[name] = json.loads(path.read_text(encoding="utf-8"))
        return self.raw[name]

    def allocate_ids(self) -> None:
        """Assign every primary key before a single row is written."""
        plan = [
            ("cmf_line", "ramo_cmf"),
            ("insurance_line", "ramo"),
            ("broker", "corredora"),
            ("user", "usuario"),
            ("insurer", "aseguradora"),
            ("insured", "asegurado"),
            ("client", "cliente"),
            ("asset", "activo"),
            ("placement", "proceso_ramo"),
            ("quote_request", "cotizacion"),
            ("proposal", "oferta"),
            ("inspection_request", "solicitud_inspeccion"),
            ("inspection", "inspeccion"),
            ("document", "documento"),
        ]
        for table, seed in plan:
            self.ids.allocate(table, [row["id"] for row in self.load(seed)])

        # aseguradora_entidad rows are the rich half of an insurer already
        # allocated above; index them onto the same integer key.
        for entity in self.load("aseguradora_entidad"):
            match = next(
                (a for a in self.load("aseguradora") if a.get("entidad_id") == entity["id"]),
                None,
            )
            if match is None:
                raise FixtureError(f"aseguradora_entidad {entity['id']} has no insurer")
            self.insurer_of_entity[entity["id"]] = match["id"]

    # --- ownership resolution ---------------------------------------------

    def build_cross_references(self) -> None:
        """Work out which broker owns each canonical/child record."""
        for client in self.load("cliente"):
            self.broker_of_client[client["id"]] = client["corredora_id"]
            if client.get("asegurado_id"):
                self.broker_of_insured[client["asegurado_id"]] = client["corredora_id"]
        for asset in self.load("activo"):
            self.client_of_asset[asset["id"]] = asset["cliente_id"]
        for placement in self.load("proceso_ramo"):
            self.client_of_placement[placement["id"]] = placement["cliente_id"]
        for inspection in self.load("inspeccion"):
            self.asset_of_inspection[inspection["id"]] = inspection["activo_id"]
        for proposal in self.load("oferta"):
            self.quote_of_proposal[proposal["id"]] = proposal["cotizacion_id"]
        # An insurer is canonical; the broker that introduced it comes from the
        # broker<->insurer link table.
        for link in self.load("vinculo_aseguradora"):
            self.broker_of_insurer_entity[link["aseguradora_entidad_id"]] = link["corredora_id"]

    def broker_src_for(self, entity: str, entity_id: str) -> str:
        """Which broker's workspace a source record belongs to."""
        if entity == "corredora":
            return entity_id
        if entity == "cliente":
            return self.broker_of_client[entity_id]
        if entity == "asegurado":
            return self.broker_of_insured[entity_id]
        if entity == "aseguradora_entidad":
            return self.broker_of_insurer_entity[entity_id]
        if entity == "activo":
            return self.broker_of_client[self.client_of_asset[entity_id]]
        if entity == "proceso_ramo":
            return self.broker_of_client[self.client_of_placement[entity_id]]
        if entity == "inspeccion":
            asset = self.asset_of_inspection[entity_id]
            return self.broker_of_client[self.client_of_asset[asset]]
        if entity == "oferta":
            quote = next(
                c for c in self.load("cotizacion") if c["id"] == self.quote_of_proposal[entity_id]
            )
            return self.broker_of_client[quote["cliente_id"]]
        if entity == "cotizacion":
            quote = next(c for c in self.load("cotizacion") if c["id"] == entity_id)
            return self.broker_of_client[quote["cliente_id"]]
        if entity == "usuario":
            user = next(u for u in self.load("usuario") if u["id"] == entity_id)
            return user["corredora_id"]
        raise FixtureError(f"Cannot resolve owning broker for {entity}:{entity_id}")

    def broker_id_for(self, entity: str, entity_id: str) -> int:
        return self.ids.get("broker", self.broker_src_for(entity, entity_id))

    # =========================================================================
    # 1. Global catalogs
    # =========================================================================

    def import_cmf_lines(self) -> None:
        self.log.section("CMF line taxonomy (global)")
        for row in self.load("ramo_cmf"):
            kind = CMF_LINE_KIND.get(norm_token(row["tipo"]))
            if kind is None:
                raise FixtureError(f"Unknown cmf line kind: {row['tipo']!r}")
            self.db.add(
                CmfLine(
                    id=self.ids.get("cmf_line", row["id"]),
                    code=str(row["codigo"]),
                    name=row["nombre"],
                    kind=kind,
                    group_code=row.get("grupo_codigo"),
                    group_name=row.get("grupo_nombre"),
                )
            )
        self.db.flush()
        self.log.ok(f"{len(self.load('ramo_cmf'))} cmf_line rows")

    def import_insurance_lines(self) -> None:
        """The 6 lines are GLOBAL (broker_id NULL) — Radal seeds them for everyone."""
        self.log.section("Insurance lines + CMF junction")
        # ``ramo.codigos_cmf`` references the LINE namespace only — subdivisions
        # reuse the same numbers (see CmfLine's docstring), so filter by kind.
        by_code = {
            str(row["codigo"]): row["id"]
            for row in self.load("ramo_cmf")
            if CMF_LINE_KIND.get(norm_token(row["tipo"])) is CmfLineKind.LINE
        }
        junction = 0
        for row in self.load("ramo"):
            line_id = self.ids.get("insurance_line", row["id"])
            min_fields = None
            if row["id"] == "ramo-001":
                # The only line with real asset evidence: shape asset.attributes
                # from the 10 promoted underwriting attributes.
                min_fields = {
                    "required": [
                        "built_area_m2",
                        "construction_year",
                        "structure",
                        "activity",
                        "fire_protection",
                        "seismic_zone",
                    ],
                    "optional": [
                        "land_area_m2",
                        "floors",
                        "power_supply",
                        "fire_station_distance_km",
                    ],
                }
            self.db.add(
                InsuranceLine(
                    id=line_id,
                    broker_id=None,
                    name=row["nombre"],
                    requires_inspection=bool(row.get("requiere_inspeccion")),
                    is_active=True,
                    cmf_subdivision=row.get("subdivision_cmf"),
                    min_fields=min_fields,
                    comparator_fields=dict(DEFAULT_COMPARATOR_FIELDS),
                )
            )
            composites = row.get("codigos_compuestos") or []
            for index, code in enumerate(row.get("codigos_cmf") or []):
                source_cmf = by_code.get(str(code))
                if source_cmf is None:
                    self.log.warn(f"{row['id']}: no cmf_line for code {code}")
                    continue
                self.db.add(
                    InsuranceLineCmfCode(
                        insurance_line_id=line_id,
                        cmf_line_id=self.ids.get("cmf_line", source_cmf),
                        composite_code=composites[index] if index < len(composites) else None,
                    )
                )
                junction += 1
        self.db.flush()
        self.log.ok(f"{len(self.load('ramo'))} insurance_line rows, {junction} junction rows")

    def import_insurers(self) -> None:
        """All 25 CMF companies; is_native=False unless in the partner set."""
        self.log.section("Insurer catalog (25 CMF companies)")
        entities = {e["id"]: e for e in self.load("aseguradora_entidad")}
        entity_by_insurer = {v: k for k, v in self.insurer_of_entity.items()}

        native_names: list[str] = []
        for row in self.load("aseguradora"):
            insurer_id = self.ids.get("insurer", row["id"])
            entity = entities.get(entity_by_insurer.get(row["id"], ""), {})
            rut = validate_rut(row["rut"])
            # Identity is rut + cmf_code. The CMF registry is RUT-keyed, so the
            # código IS the normalized RUT — which is exactly what the three
            # detailed records carry, so derived and given values agree.
            cmf_code = normalize_codigo_cmf(entity.get("codigo_cmf") or rut)
            is_native = row["id"] in NATIVE_INSURER_KEYS
            if is_native:
                native_names.append(row["nombre"])
            self.db.add(
                Insurer(
                    id=insurer_id,
                    rut=rut,
                    cmf_code=cmf_code,
                    legal_name=row["nombre"],
                    trade_name=None,
                    is_native=is_native,
                    status=(
                        InsurerStatus.ACTIVE
                        if norm_token(row.get("estado_cmf")) == "vigente"
                        else InsurerStatus.INACTIVE
                    ),
                    cmf_status=entity.get("cmf_estado"),
                    payment_url=entity.get("sitio_pago_url"),
                    logo_key=(
                        f"media/insurer/{insurer_id}/logo.webp" if entity.get("foto_key") else None
                    ),
                    created_by_broker_id=None,  # seeded by Radal from the CMF list
                )
            )
        self.db.flush()
        self.log.ok(f"25 insurer rows — {len(native_names)} native, {25 - len(native_names)} external")
        for name in native_names:
            self.log.info(f"native: {name}")

    def import_native_profiles(self) -> None:
        """1:1 profile + routing contacts, only for the partner set."""
        self.log.section("Native partner profiles + contacts")
        entity_by_insurer = {v: k for k, v in self.insurer_of_entity.items()}
        entities = {e["id"]: e for e in self.load("aseguradora_entidad")}
        property_line_id = self.ids.get("insurance_line", "ramo-001")

        contacts = 0
        for source_id, profile in NATIVE_INSURER_KEYS.items():
            insurer_id = self.ids.get("insurer", source_id)
            self.db.add(
                NativeInsurerProfile(
                    insurer_id=insurer_id,
                    commercial_agreement=profile["commercial_agreement"],
                    onboarded_at=datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc),
                    priority=profile["priority"],
                    sla_hours=profile["sla_hours"],
                    notes=profile["notes"],
                )
            )

            entity = entities.get(entity_by_insurer.get(source_id, ""))
            if entity:
                # Global fallback contact straight from the corpus.
                self.db.add(
                    InsurerContact(
                        insurer_id=insurer_id,
                        broker_id=None,
                        insurance_line_id=None,
                        name=entity["contacto_nombre"],
                        email=entity.get("contacto_email"),
                        phone=entity.get("contacto_telefono"),
                        role=entity["contacto_nombre"],
                        is_primary=True,
                    )
                )
                contacts += 1
                # And the broker-specific, line-specific desk from the link table.
                broker_src = self.broker_of_insurer_entity.get(entity["id"])
                if broker_src:
                    self.db.add(
                        InsurerContact(
                            insurer_id=insurer_id,
                            broker_id=self.ids.get("broker", broker_src),
                            insurance_line_id=property_line_id,
                            name=entity["contacto_nombre"],
                            email=entity.get("contacto_email"),
                            phone=entity.get("contacto_telefono"),
                            role="Ejecutivo asignado a la corredora — Incendio y Sismo",
                            is_primary=True,
                        )
                    )
                    contacts += 1
            elif source_id in EXTRA_NATIVE_CONTACTS:
                extra = EXTRA_NATIVE_CONTACTS[source_id]
                self.db.add(
                    InsurerContact(
                        insurer_id=insurer_id,
                        broker_id=None,
                        insurance_line_id=property_line_id,
                        name=extra["name"],
                        email=extra["email"],
                        phone=extra["phone"],
                        role=extra["role"],
                        is_primary=True,
                    )
                )
                contacts += 1
        self.db.flush()
        self.log.ok(
            f"{len(NATIVE_INSURER_KEYS)} native_insurer_profile rows, {contacts} insurer_contact rows"
        )

    # =========================================================================
    # 2. Brokers, users
    # =========================================================================

    def import_brokers(self) -> None:
        self.log.section("Brokers (tenants)")
        for row in self.load("corredora"):
            broker_id = self.ids.get("broker", row["id"])
            street, commune, region = split_address(row.get("direccion"))
            self.db.add(
                Broker(
                    id=broker_id,
                    rut=validate_rut(row["rut"]),
                    legal_name=row["razon_social"],
                    trade_name=row.get("nombre_fantasia"),
                    cmf_code=normalize_codigo_cmf(row.get("codigo_cmf") or ""),
                    cmf_status=row.get("cmf_estado"),
                    cmf_registration_date=to_date(row.get("cmf_fecha_inscripcion")),
                    registry_note=row.get("nota_registro"),
                    appointment_doc_type=row.get("tipo_doc_nombramiento"),
                    appointment_doc_date=to_date(row.get("fecha_doc_nombramiento")),
                    address=street,
                    commune=commune,
                    region=region,
                    phone=row.get("telefono"),
                    email=row.get("email"),
                    logo_key=f"media/broker/{broker_id}/logo.webp",
                    status=(
                        BrokerStatus.ACTIVE
                        if norm_token(row.get("vigencia")) == "vigente"
                        else BrokerStatus.INACTIVE
                    ),
                )
            )
            if row.get("nota_registro"):
                self.log.warn(
                    f"{row['razon_social']}: CMF registry note kept on the record "
                    "(operations can be gated on it later)"
                )
        self.db.flush()
        self.log.ok(f"{len(self.load('corredora'))} broker rows")

    def import_users(self) -> None:
        self.log.section("Users")
        password = hash_password(DEMO_PASSWORD)
        roles = Counter()
        for row in self.load("usuario"):
            role = ROLE_BY_SUBROLE.get(norm_token(row["subrol"]))
            if role is None:
                raise FixtureError(f"Unmapped subrol: {row['subrol']!r}")
            roles[role] += 1
            self.db.add(
                User(
                    id=self.ids.get("user", row["id"]),
                    broker_id=self.ids.get("broker", row["corredora_id"]),
                    email=row["email"].lower(),
                    hashed_password=password,
                    full_name=row["nombre"],
                    job_title=row.get("cargo"),
                    user_type=UserType.BROKER,
                    role=role,
                    is_active=norm_token(row.get("estado")) == "activo",
                )
            )
        self.db.flush()
        self.log.ok(f"{len(self.load('usuario'))} user rows — " + ", ".join(
            f"{count}× {role}" for role, count in sorted(roles.items())
        ))

    # =========================================================================
    # 3. Documents (before proposals: source_document_id is NOT NULL)
    # =========================================================================

    def import_documents(self) -> None:
        """66 rows, each re-keyed onto the v2 S3 layout, each file uploaded.

        Logos are a special case. The architecture reserves ``media/`` for
        derived 512×512 WEBP avatars referenced by ``entity.logo_key``, but the
        corpus also tracks each logo as an uploaded file. We keep both: the
        object goes to its ``media/`` route (which is what the UI renders) and
        the ``document`` row is retained as the upload's provenance — preserving
        the exact 66-file ↔ 66-row correspondence the package guarantees.
        """
        self.log.section("Documents (66 files -> v2 S3 layout)")
        ordinals: Counter = Counter()
        table_by_entity = {
            EntityType.BROKER: "broker",
            EntityType.CLIENT: "client",
            EntityType.INSURED: "insured",
            EntityType.INSURER: "insurer",
            EntityType.ASSET: "asset",
            EntityType.PLACEMENT: "placement",
            EntityType.PROPOSAL: "proposal",
            EntityType.INSPECTION: "inspection",
        }
        source_table = {
            "corredora": "broker",
            "cliente": "client",
            "asegurado": "insured",
            "aseguradora_entidad": "insurer",
            "activo": "asset",
            "proceso_ramo": "placement",
            "oferta": "proposal",
            "inspeccion": "inspection",
        }

        for row in self.load("documento"):
            entity_key = row["entidad"]
            entity_type = ENTITY_TYPE.get(entity_key)
            if entity_type is None:
                raise FixtureError(f"Unmapped document entity: {entity_key!r}")
            category = DOCUMENT_CATEGORY.get(norm_token(row.get("categoria")))
            if category is None:
                self.log.warn(f"{row['id']}: unmapped category {row.get('categoria')!r} -> other")
                category = DocumentCategory.OTHER

            # aseguradora_entidad ids resolve onto the insurer they describe.
            source_entity_id = row["entidad_id"]
            if entity_key == "aseguradora_entidad":
                source_entity_id = self.insurer_of_entity[source_entity_id]
            table = table_by_entity[entity_type]
            entity_id = self.ids.get(source_table.get(entity_key, table), source_entity_id)

            name = row["nombre_original"]
            extension = _ext_of(name)
            if category is DocumentCategory.LOGO:
                key = f"media/{entity_type.value}/{entity_id}/logo.{extension}"
            else:
                ordinals[(entity_type, entity_id, category)] += 1
                n = ordinals[(entity_type, entity_id, category)]
                key = f"documents/{entity_type.value}/{entity_id}/{category.value}-{n}.{extension}"

            document_id = self.ids.get("document", row["id"])
            self.db.add(
                Document(
                    id=document_id,
                    broker_id=self.broker_id_for(entity_key, row["entidad_id"]),
                    entity_type=entity_type,
                    entity_id=entity_id,
                    s3_key=key,
                    bucket=self.uploader.bucket,
                    original_name=name,
                    mime_type=_mime_of(name, row.get("mime_type")),
                    size_bytes=self._size_of(row["s3_key"]),
                    category=category,
                    phase=row.get("fase"),
                    uploaded_by_id=self.ids.maybe("user", row.get("subido_por")),
                )
            )
            self.doc_by_source_key[row["s3_key"]] = document_id
            if category is DocumentCategory.PROPOSAL:
                self.proposal_doc[row["entidad_id"]] = document_id

            local = self.files / row["s3_key"]
            if not local.exists():
                raise FixtureError(f"Fixture file missing on disk: {local}")
            self.uploader.put(local, key, _mime_of(name, row.get("mime_type")))

        self.db.flush()
        verb = "uploaded" if self.uploader.enabled else "skipped (--no-upload)"
        self.log.ok(f"{len(self.load('documento'))} document rows, {len(self.load('documento'))} files {verb}")

    def _size_of(self, relative_key: str) -> int | None:
        path = self.files / relative_key
        return path.stat().st_size if path.exists() else None

    # =========================================================================
    # 4. Insureds, clients, assets
    # =========================================================================

    def import_insureds(self) -> None:
        self.log.section("Insureds (canonical) + clients + assets")
        sector_by_insured = {
            c["asegurado_id"]: c.get("sector") for c in self.load("cliente") if c.get("asegurado_id")
        }
        for row in self.load("asegurado"):
            insured_id = self.ids.get("insured", row["id"])
            self.db.add(
                Insured(
                    id=insured_id,
                    rut=validate_rut(row["rut"]),
                    person_type=(
                        PersonType.LEGAL
                        if norm_token(row.get("tipo_persona")) == "juridica"
                        else PersonType.NATURAL
                    ),
                    legal_name=row["razon_social"],
                    trade_name=row.get("nombre_fantasia"),
                    # The SII activity description is only carried on the broker's
                    # client record in the source; it is a property of the company.
                    tax_activity=sector_by_insured.get(row["id"]),
                    contact_name=row.get("contacto_nombre"),
                    email=row.get("correo"),
                    phone=row.get("telefono"),
                    address=row.get("domicilio"),
                    commune=row.get("comuna"),
                    region=row.get("region"),
                    logo_key=f"media/insured/{insured_id}/logo.webp",
                )
            )
        self.db.flush()
        self.log.ok(f"{len(self.load('asegurado'))} insured rows")

    def import_clients(self) -> None:
        for row in self.load("cliente"):
            if not row.get("asegurado_id"):
                raise FixtureError(f"cliente {row['id']} has no asegurado_id")
            self.db.add(
                Client(
                    id=self.ids.get("client", row["id"]),
                    broker_id=self.ids.get("broker", row["corredora_id"]),
                    insured_id=self.ids.get("insured", row["asegurado_id"]),
                    status=CLIENT_STATUS.get(norm_token(row.get("estado")), CLIENT_STATUS["prospecto"]),
                    account_manager_id=self.ids.maybe("user", row.get("ejecutivo_id")),
                    sector=row.get("sector"),
                    since=to_date(row.get("created_at")),
                    contact_name=row.get("contacto_principal"),
                    contact_email=row.get("email"),
                    contact_phone=row.get("telefono"),
                )
            )
        self.db.flush()
        self.log.ok(f"{len(self.load('cliente'))} client rows")

    def import_assets(self) -> None:
        for row in self.load("activo"):
            attributes = split_asset_attributes(row.get("atributos"))
            if attributes.unmapped:
                self.log.warn(
                    f"{row['id']}: attribute keys kept verbatim in JSON tail "
                    f"(no English name yet): {', '.join(attributes.unmapped)}"
                )
            street, commune, region = split_address(row.get("direccion"))
            columns = dict(attributes.columns)
            # Numerics through Decimal; counts through int.
            for key in ("built_area_m2", "land_area_m2", "fire_station_distance_km"):
                if key in columns:
                    columns[key] = to_decimal(columns[key])
            for key in ("construction_year", "floors"):
                if key in columns and columns[key] is not None:
                    columns[key] = int(columns[key])
            self.db.add(
                Asset(
                    id=self.ids.get("asset", row["id"]),
                    broker_id=self.broker_id_for("activo", row["id"]),
                    client_id=self.ids.get("client", row["cliente_id"]),
                    asset_type=ASSET_TYPE.get(row["tipo_activo"], row["tipo_activo"]),
                    name=row["nombre"],
                    address=street,
                    commune=commune,
                    region=region,
                    status=AssetStatus.ACTIVE,
                    attributes=attributes.tail or None,
                    **columns,
                )
            )
        self.db.flush()
        self.log.ok(f"{len(self.load('activo'))} asset rows (10 attributes promoted to columns)")

    # =========================================================================
    # 5. Placements, quotes, proposals
    # =========================================================================

    def import_placements(self) -> None:
        self.log.section("Placements")
        quote_by_placement = {
            q["proceso_ramo_id"]: q for q in self.load("cotizacion") if q.get("proceso_ramo_id")
        }
        for row in self.load("proceso_ramo"):
            quote = quote_by_placement.get(row["id"], {})
            validity = quote.get("vigencia_deseada") or {}
            start, end = to_date(validity.get("desde")), to_date(validity.get("hasta"))
            self.db.add(
                Placement(
                    id=self.ids.get("placement", row["id"]),
                    broker_id=self.broker_id_for("proceso_ramo", row["id"]),
                    client_id=self.ids.get("client", row["cliente_id"]),
                    asset_id=self.ids.get("asset", row["activo_id"]),
                    insurance_line_id=self.ids.get("insurance_line", row["ramo_id"]),
                    period=f"{start.year}-{end.year}" if start and end else None,
                    period_start=start,
                    period_end=end,
                    status=PLACEMENT_STATUS.get(
                        norm_token(row.get("estado")), PLACEMENT_STATUS["borrador"]
                    ),
                    brief_document_id=self.doc_by_source_key.get(row.get("bases_tecnicas_s3", "")),
                )
            )
        self.db.flush()
        self.log.ok(f"{len(self.load('proceso_ramo'))} placement rows")

    def import_quotes(self) -> None:
        self.log.section("Quote requests + line items")
        items = 0

        # `recipient_insurer_ids` is a send-time snapshot of who the request went
        # to, so it has to agree with who actually answered: when a proposal is
        # moved to an external insurer, the recipient it replaces moves with it.
        recipient_swaps: dict[str, dict[str, str]] = defaultdict(dict)
        for proposal in self.load("oferta"):
            replacement = EXTERNAL_PROPOSAL_REASSIGNMENTS.get(proposal["id"])
            if replacement:
                recipient_swaps[proposal["cotizacion_id"]][proposal["aseguradora_id"]] = replacement

        for row in self.load("cotizacion"):
            quote_id = self.ids.get("quote_request", row["id"])
            declared = to_decimal(row.get("valor_declarado"))

            # docs §5: declared_value_uf MUST equal the sum of the line items.
            total = sum(
                (to_decimal(p.get("valor_uf")) or Decimal(0) for p in row.get("partidas") or []),
                Decimal(0),
            )
            if declared is not None and abs(total - declared) > MONEY_TOLERANCE:
                raise FixtureError(
                    f"{row['id']}: declared value {declared} != sum of line items {total}"
                )

            validity = row.get("vigencia_deseada") or {}
            placement_src = row["proceso_ramo_id"]
            placement_status = norm_token(
                next(p for p in self.load("proceso_ramo") if p["id"] == placement_src).get("estado")
            )
            self.db.add(
                QuoteRequest(
                    id=quote_id,
                    broker_id=self.broker_id_for("cotizacion", row["id"]),
                    placement_id=self.ids.get("placement", placement_src),
                    insured_object=row.get("bien_asegurar"),
                    declared_value_uf=declared,
                    currency=row.get("moneda") or "UF",
                    requested_coverages=row.get("coberturas_solicitadas"),
                    desired_start=to_date(validity.get("desde")),
                    desired_end=to_date(validity.get("hasta")),
                    sent_at=to_datetime(row.get("fecha_envio")),
                    due_at=_end_of_day(row.get("fecha_vence")),
                    priority=PRIORITY_BY_URGENCY.get(norm_token(row.get("prioridad")), Priority.NORMAL),
                    status=(
                        QuoteRequestStatus.CLOSED
                        if placement_status == "adjudicado"
                        else QuoteRequestStatus.SENT
                    ),
                    recipient_insurer_ids=[
                        self.ids.get("insurer", recipient_swaps[row["id"]].get(a, a))
                        for a in row.get("aseguradoras_destinatarias") or []
                    ],
                    created_by_id=self.ids.maybe("user", row.get("creado_por")),
                )
            )
            for index, item in enumerate(row.get("partidas") or []):
                self.db.add(
                    QuoteLineItem(
                        quote_request_id=quote_id,
                        name=item["partida"],
                        value_uf=to_decimal(item["valor_uf"]),
                        detail=item.get("detalle"),
                        sort_order=index,
                    )
                )
                items += 1
        self.db.flush()
        self.log.ok(
            f"{len(self.load('cotizacion'))} quote_request rows, {items} quote_line_item rows "
            "(declared value == Σ line items verified)"
        )

    def _check_money(self, source_id: str, premium: dict[str, Any], rates: dict[str, Any]) -> None:
        """Chilean market invariants — VAT is on the TAXABLE part only."""
        taxable = to_decimal(premium.get("afecta_uf")) or Decimal(0)
        exempt = to_decimal(premium.get("exenta_uf")) or Decimal(0)
        net = to_decimal(premium.get("neta_uf")) or Decimal(0)
        vat = to_decimal(premium.get("iva_uf")) or Decimal(0)
        total = to_decimal(premium.get("total_uf")) or Decimal(0)
        if abs((taxable + exempt) - net) > MONEY_TOLERANCE:
            self.log.warn(f"{source_id}: net {net} != taxable {taxable} + exempt {exempt}")
        if abs((Decimal("0.19") * taxable) - vat) > MONEY_TOLERANCE:
            self.log.warn(f"{source_id}: vat {vat} != 0.19 × taxable {taxable}")
        if abs((net + vat) - total) > MONEY_TOLERANCE:
            self.log.warn(f"{source_id}: total {total} != net {net} + vat {vat}")
        taxable_rate = to_decimal(rates.get("afecta")) or Decimal(0)
        exempt_rate = to_decimal(rates.get("exenta")) or Decimal(0)
        comprehensive = to_decimal(rates.get("comprensiva")) or Decimal(0)
        if abs((taxable_rate + exempt_rate) - comprehensive) > MONEY_TOLERANCE:
            self.log.warn(f"{source_id}: comprehensive rate != taxable + exempt")

    def import_proposals(self) -> None:
        self.log.section("Proposals + coverages/exclusions")
        natives = {self.ids.get("insurer", k) for k in NATIVE_INSURER_KEYS}
        insurer_names = {a["id"]: a["nombre"] for a in self.load("aseguradora")}
        confirmed_by = {q["id"]: q.get("creado_por") for q in self.load("cotizacion")}

        coverages = 0
        exclusions = 0
        review: list[str] = []
        for row in self.load("oferta"):
            proposal_id = self.ids.get("proposal", row["id"])
            source_insurer = row["aseguradora_id"]
            reassigned = EXTERNAL_PROPOSAL_REASSIGNMENTS.get(row["id"])
            if reassigned:
                self.log.info(
                    f"{row['id']}: reassigned {insurer_names[source_insurer]} -> "
                    f"{insurer_names[reassigned]} (external-insurer scenario)"
                )
                source_insurer = reassigned
            insurer_id = self.ids.get("insurer", source_insurer)
            origin = ProposalOrigin.NATIVE if insurer_id in natives else ProposalOrigin.EXTERNAL
            if origin is ProposalOrigin.EXTERNAL:
                self.external_proposals.append(row["id"])

            document_id = self.proposal_doc.get(row["id"])
            if document_id is None:
                # Rule 3: no source document, no proposal. Never silently skip.
                raise FixtureError(f"{row['id']}: no 'propuesta' document — proposal cannot exist")

            premium = row.get("prima") or {}
            rates = row.get("tasas_pormil") or {}
            self._check_money(row["id"], premium, rates)

            deductibles, needs_review = parse_deductibles(row.get("deducible"))
            for peril in needs_review:
                review.append(f"{row['id']}:{peril}")

            validity = row.get("vigencia") or {}
            received = to_date(row.get("fecha_recepcion"))
            self.db.add(
                Proposal(
                    id=proposal_id,
                    broker_id=self.broker_id_for("oferta", row["id"]),
                    quote_request_id=self.ids.get("quote_request", row["cotizacion_id"]),
                    insurer_id=insurer_id,
                    origin=origin,
                    source_document_id=document_id,
                    modality=row.get("modalidad"),
                    activity_classification=row.get("clasificacion_actividad"),
                    taxable_premium_uf=to_decimal(premium.get("afecta_uf")),
                    exempt_premium_uf=to_decimal(premium.get("exenta_uf")),
                    net_premium_uf=to_decimal(premium.get("neta_uf")),
                    vat_uf=to_decimal(premium.get("iva_uf")),
                    total_premium_uf=to_decimal(premium.get("total_uf")),
                    taxable_rate_permille=to_decimal(rates.get("afecta")),
                    exempt_rate_permille=to_decimal(rates.get("exenta")),
                    comprehensive_rate_permille=to_decimal(rates.get("comprensiva")),
                    commission_pct=to_decimal(row.get("comision_corredor_pct")),
                    validity_business_days=row.get("validez_dias_habiles"),
                    coverage_start=to_date(validity.get("desde")),
                    coverage_end=to_date(validity.get("hasta")),
                    received_at=received,
                    deductibles=deductibles,
                    warranties=row.get("garantias"),
                    status=PROPOSAL_STATUS.get(norm_token(row.get("estado")), PROPOSAL_STATUS["borrador"]),
                    # These are historical records the broker already worked
                    # through by hand, so they arrive confirmed — no AI
                    # extraction stands behind them.
                    is_confirmed=True,
                    confirmed_by_id=self.ids.maybe("user", confirmed_by.get(row["cotizacion_id"])),
                    confirmed_at=to_datetime(received),
                )
            )
            for index, text in enumerate(row.get("coberturas") or []):
                self.db.add(
                    ProposalCoverage(
                        proposal_id=proposal_id,
                        kind=CoverageKind.COVERAGE,
                        text=text,
                        sort_order=index,
                    )
                )
                coverages += 1
            for index, text in enumerate(row.get("exclusiones") or []):
                self.db.add(
                    ProposalCoverage(
                        proposal_id=proposal_id,
                        kind=CoverageKind.EXCLUSION,
                        text=text,
                        sort_order=index,
                    )
                )
                exclusions += 1
        self.db.flush()
        self.log.ok(
            f"{len(self.load('oferta'))} proposal rows, "
            f"{coverages + exclusions} proposal_coverage rows ({coverages} coverages, {exclusions} exclusions)"
        )
        if review:
            self.log.warn(
                "deductible prose parsed only partially (flagged confidence=partial "
                f"for AI/human review): {', '.join(review)}"
            )

    def verify_external_scenario(self) -> None:
        """Assert the product-owner requirement actually holds in the data."""
        self.log.section("Native / external proposal split")
        rows = self.db.execute(
            select(
                QuoteRequest.id,
                InsuranceLine.name,
                Proposal.origin,
                Insurer.legal_name,
                Proposal.status,
            )
            .join(Proposal, Proposal.quote_request_id == QuoteRequest.id)
            .join(Insurer, Insurer.id == Proposal.insurer_id)
            .join(Placement, Placement.id == QuoteRequest.placement_id)
            .join(InsuranceLine, InsuranceLine.id == Placement.insurance_line_id)
            .order_by(QuoteRequest.id, Proposal.id)
        ).all()

        per_quote: dict[int, list[tuple[str, str, str, str]]] = defaultdict(list)
        for quote_id, line_name, origin, insurer_name, status in rows:
            per_quote[quote_id].append((str(origin), insurer_name, line_name, str(status)))

        external_quotes = []
        for quote_id, entries in sorted(per_quote.items()):
            external = [e for e in entries if e[0] == ProposalOrigin.EXTERNAL.value]
            line = entries[0][2]
            self.log.info(
                f"quote #{quote_id} ({line}): {len(entries) - len(external)} native / "
                f"{len(external)} external"
            )
            for origin, insurer_name, _, status in entries:
                self.log.info(f"    {origin:<8} {insurer_name}  [{status}]")
            if external:
                external_quotes.append((quote_id, len(external), len(entries)))

        if len(external_quotes) != 1:
            raise FixtureError(
                f"expected exactly ONE quote with external proposals, found {len(external_quotes)}"
            )
        quote_id, external_count, total = external_quotes[0]
        if external_count != 2 or total != 3:
            raise FixtureError(
                f"quote #{quote_id}: expected 2 of 3 proposals external, got {external_count} of {total}"
            )
        self.log.ok(
            f"quote #{quote_id} has exactly 2 of 3 proposals from NON-native insurers "
            "— external-insurer tracking path exercised"
        )

    # =========================================================================
    # 6. Inspections
    # =========================================================================

    def import_inspections(self) -> None:
        self.log.section("Inspection requests + inspections + boundaries")
        placement_by_asset_line = {
            (p["activo_id"], p["ramo_id"]): p["id"] for p in self.load("proceso_ramo")
        }
        for row in self.load("solicitud_inspeccion"):
            self.db.add(
                InspectionRequest(
                    id=self.ids.get("inspection_request", row["id"]),
                    broker_id=self.broker_id_for("activo", row["activo_id"]),
                    asset_id=self.ids.get("asset", row["activo_id"]),
                    placement_id=self.ids.maybe(
                        "placement", placement_by_asset_line.get((row["activo_id"], row["ramo_id"]))
                    ),
                    insurance_line_id=self.ids.maybe("insurance_line", row.get("ramo_id")),
                    reason=row.get("motivo"),
                    urgency=PRIORITY_BY_URGENCY.get(norm_token(row.get("urgencia")), Priority.NORMAL),
                    status=INSPECTION_REQUEST_STATUS.get(
                        norm_token(row.get("estado")), INSPECTION_REQUEST_STATUS["pendiente"]
                    ),
                    requested_by_id=self.ids.maybe("user", row.get("solicitado_por")),
                    requested_at=to_datetime(row.get("fecha_solicitud")),
                )
            )
        self.db.flush()
        self.log.ok(f"{len(self.load('solicitud_inspeccion'))} inspection_request rows")

        boundaries = 0
        for row in self.load("inspeccion"):
            inspection_id = self.ids.get("inspection", row["id"])
            scores = row.get("puntajes") or {}
            checklist = row.get("checklist") or {}
            self.db.add(
                Inspection(
                    id=inspection_id,
                    broker_id=self.broker_id_for("inspeccion", row["id"]),
                    asset_id=self.ids.get("asset", row["activo_id"]),
                    inspection_request_id=self.ids.maybe("inspection_request", row.get("solicitud_id")),
                    inspector_id=self.ids.maybe("user", row.get("inspector_id")),
                    version=row.get("version") or 1,
                    status=INSPECTION_STATUS.get(
                        norm_token(row.get("estado")), INSPECTION_STATUS["borrador"]
                    ),
                    visit_date=to_date(row.get("fecha_visita")),
                    report_date=to_date(row.get("fecha_informe")),
                    folio=row.get("folio"),
                    findings_summary=row.get("hallazgos_resumen"),
                    technical_score=to_decimal(scores.get("factores_tecnicos")),
                    commercial_score=to_decimal(scores.get("factor_comercial")),
                    location_score=to_decimal(scores.get("modulo_ubicacion")),
                    loss_estimate_score=to_decimal(scores.get("estimacion_perdida")),
                    overall_score=to_decimal(scores.get("global")),
                    pml_pct=to_decimal(scores.get("pml_pct")),
                    eml_pct=to_decimal(scores.get("eml_pct")),
                    risk_classification=scores.get("clasificacion"),
                    checklist=normalize_checklist(checklist),
                    checklist_version=checklist.get("version"),
                    report_document_id=self.doc_by_source_key.get(row.get("informe_s3", "")),
                )
            )
            for index, boundary in enumerate(row.get("colindancias") or []):
                orientation = norm_token(boundary.get("orientacion"))
                self.db.add(
                    InspectionBoundary(
                        inspection_id=inspection_id,
                        orientation=BOUNDARY_ORIENTATION.get(orientation, orientation or None),
                        description=boundary.get("colindancia"),
                        distance=boundary.get("distancia"),
                        aggravating=boundary.get("agravante"),
                        is_aggravating=is_aggravating(boundary.get("agravante")),
                        sort_order=index,
                    )
                )
                boundaries += 1
        self.db.flush()
        self.log.ok(
            f"{len(self.load('inspeccion'))} inspection rows, {boundaries} inspection_boundary rows"
        )

    # =========================================================================
    # 7. Audit trail
    # =========================================================================

    def import_activity(self) -> None:
        self.log.section("Activity + notes")
        source_table = {
            "corredora": "broker",
            "cliente": "client",
            "asegurado": "insured",
            "aseguradora_entidad": "insurer",
            "activo": "asset",
            "proceso_ramo": "placement",
            "cotizacion": "quote_request",
            "oferta": "proposal",
            "inspeccion": "inspection",
        }
        users = {u["id"]: u for u in self.load("usuario")}

        for row in self.load("actividad"):
            entity_key = row["entidad"]
            entity_type = ENTITY_TYPE.get(entity_key)
            if entity_type is None:
                self.log.warn(f"{row['id']}: unmapped activity entity {entity_key!r}, skipped")
                continue
            source_entity_id = row["entidad_id"]
            if entity_key == "aseguradora_entidad":
                source_entity_id = self.insurer_of_entity[source_entity_id]
            author = users.get(row.get("autor_id") or "")
            if author is None:
                self.log.warn(f"{row['id']}: unknown author, skipped")
                continue
            verb = ACTIVITY_VERB.get(norm_token(row.get("tipo")), norm_token(row.get("tipo")))
            self.db.add(
                Activity(
                    broker_id=self.ids.get("broker", author["corredora_id"]),
                    user_id=self.ids.get("user", author["id"]),
                    action=f"{entity_type.value}.{verb}",
                    entity_type=entity_type,
                    entity_id=self.ids.get(source_table[entity_key], source_entity_id),
                    description=row.get("detalle"),
                    meta={"source_id": row["id"], "source_type": row.get("tipo")},
                    occurred_at=to_datetime(row.get("fecha")),
                )
            )

        for row in self.load("observacion"):
            entity_key = row["entidad"]
            entity_type = ENTITY_TYPE.get(entity_key)
            author = users.get(row.get("autor_id") or "")
            if entity_type is None or author is None:
                self.log.warn(f"{row['id']}: unmapped note target/author, skipped")
                continue
            self.db.add(
                Note(
                    broker_id=self.ids.get("broker", author["corredora_id"]),
                    entity_type=entity_type,
                    entity_id=self.ids.get(source_table[entity_key], row["entidad_id"]),
                    author_id=self.ids.get("user", author["id"]),
                    body=row["texto"],
                    is_internal=True,
                    phase=row.get("fase"),
                )
            )
        self.db.flush()
        self.log.ok(
            f"{len(self.load('actividad'))} activity rows, {len(self.load('observacion'))} note rows"
        )

    # --- orchestration -----------------------------------------------------

    def run(self) -> None:
        self.allocate_ids()
        self.build_cross_references()

        self.import_cmf_lines()
        self.import_insurance_lines()
        self.import_brokers()
        self.import_users()
        self.import_documents()
        self.import_insurers()
        self.import_native_profiles()
        self.import_insureds()
        self.import_clients()
        self.import_assets()
        self.import_placements()
        self.import_quotes()
        self.import_proposals()
        self.import_inspections()
        self.import_activity()
        self.verify_external_scenario()


# =============================================================================
# Reporting
# =============================================================================

COUNTED_MODELS = [
    ("broker", Broker),
    ("user", User),
    ("insured", Insured),
    ("client", Client),
    ("asset", Asset),
    ("insurer", Insurer),
    ("native_insurer_profile", NativeInsurerProfile),
    ("insurer_contact", InsurerContact),
    ("cmf_line", CmfLine),
    ("insurance_line", InsuranceLine),
    ("insurance_line_cmf_code", InsuranceLineCmfCode),
    ("placement", Placement),
    ("quote_request", QuoteRequest),
    ("quote_line_item", QuoteLineItem),
    ("proposal", Proposal),
    ("proposal_coverage", ProposalCoverage),
    ("inspection_request", InspectionRequest),
    ("inspection", Inspection),
    ("inspection_boundary", InspectionBoundary),
    ("document", Document),
    ("activity", Activity),
    ("note", Note),
]


def report_counts(db: Session, log: Log) -> dict[str, int]:
    log.section("Row counts")
    counts: dict[str, int] = {}
    width = max(len(name) for name, _ in COUNTED_MODELS)
    for name, model in COUNTED_MODELS:
        count = int(db.scalar(select(func.count()).select_from(model)) or 0)
        counts[name] = count
        print(f"  {name.ljust(width)}  {count:>4}")
    print(f"  {'TOTAL'.ljust(width)}  {sum(counts.values()):>4}")
    return counts


# =============================================================================
# CLI
# =============================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.db.import_fixtures",
        description="Import the radal-data-mvp fixture package into the v2 schema.",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=Path(os.environ.get("RADAL_FIXTURES_PATH", str(DEFAULT_PACKAGE))),
        help="Path to the fixture package (default: ~/Downloads/radal-data-mvp 2)",
    )
    parser.add_argument("--bucket", default=settings.S3_BUCKET, help="Target S3 bucket")
    parser.add_argument("--region", default=settings.S3_REGION, help="AWS region")
    parser.add_argument("--profile", default="radal", help="AWS CLI profile used for uploads")
    parser.add_argument(
        "--no-upload", action="store_true", help="Import to the DB but skip every S3 call"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the whole import, skip S3, then ROLL BACK the transaction",
    )
    parser.add_argument(
        "--reset", action="store_true", help="Drop and recreate every table before importing"
    )
    parser.add_argument("--quiet", action="store_true", help="Only print section results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = Log(verbose=not args.quiet)

    package = args.path.expanduser()
    if not (package / "seeds").is_dir():
        print(f"error: no seeds/ directory under {package}", file=sys.stderr)
        return 2

    log.section("Schema")
    if args.reset:
        Base.metadata.drop_all(bind=engine)
        log.ok("dropped all tables (--reset)")
    Base.metadata.create_all(bind=engine)
    log.ok(f"create_all on {engine.url.render_as_string(hide_password=True)}")

    uploader = Uploader(
        bucket=args.bucket,
        region=args.region,
        profile=args.profile,
        enabled=not (args.no_upload or args.dry_run),
        log=log,
    )
    if uploader.enabled:
        log.info(f"uploading to s3://{uploader.bucket} with AWS profile {args.profile!r}")
    else:
        log.info("S3 uploads disabled — document rows still get their v2 keys")

    db = SessionLocal()
    try:
        existing = int(db.scalar(select(func.count()).select_from(Broker)) or 0)
        if existing and not args.reset:
            print(
                f"error: database already holds {existing} broker(s). "
                "Re-run with --reset to wipe and reimport.",
                file=sys.stderr,
            )
            return 1

        importer = FixtureImporter(db=db, package=package, uploader=uploader, log=log)
        importer.run()

        if args.dry_run:
            db.rollback()
            log.section("Dry run")
            log.ok("transaction rolled back — nothing was written")
            return 0

        db.commit()
        counts = report_counts(db, log)

        log.section("Summary")
        native = int(
            db.scalar(select(func.count()).select_from(Insurer).where(Insurer.is_native.is_(True)))
            or 0
        )
        external_proposals = int(
            db.scalar(
                select(func.count())
                .select_from(Proposal)
                .where(Proposal.origin == ProposalOrigin.EXTERNAL)
            )
            or 0
        )
        log.ok(f"{sum(counts.values())} rows across {len(counts)} tables")
        log.ok(f"insurers: {native} native / {counts['insurer'] - native} external")
        log.ok(
            f"proposals: {counts['proposal'] - external_proposals} native / "
            f"{external_proposals} external"
        )
        if uploader.enabled:
            log.ok(f"{uploader.uploaded} files uploaded to s3://{uploader.bucket}")
        else:
            log.ok(f"{uploader.skipped} file uploads skipped (--no-upload)")
        log.ok(f"every demo user password: {DEMO_PASSWORD!r}")
        if log.warnings:
            print(f"\n  {len(log.warnings)} warning(s) — see above.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
