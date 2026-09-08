"""Import the ``EXPEDIENTES DEMO`` corpus as 7 staged case files (expedientes).

    python -m app.db.import_fixtures --reset --no-upload            # the world
    python -m app.db.import_expedientes --source "<...>/EXPEDIENTES DEMO" --no-upload

The two importers COMPOSE. ``import_fixtures`` owns the tenants, the users, the
25-company CMF catalog and the 6 insurance lines; this one refuses to run until
that has happened, then hangs seven expedientes off the existing brokers. Re-run
``import_fixtures --reset`` and every case goes with it (``broker_id`` cascades),
so the safe sequence after a wipe is simply to run both again in order. To
rebuild only the cases, use ``--reset-cases``, which deletes what this importer
created and nothing else.

What it writes
--------------

* 4 ``account_group`` rows — the broker-private folder ABOVE the expediente,
  get-or-created by ``(broker_id, slug)`` from ``InsuredIdentity.account``:
  JO PASTELERÍA, COCCOLINO, GRUPO VIÑA INDOMITA (one group over TWO RUTs) and
  LA FAVORITA. With the three ``import_fixtures`` writes, seven in all;
* 6 ``case_file(kind=account)`` rows plus one ``kind=renewal``, one per line
  expediente, each parked at a different journey stage so the pipeline board
  has something to show. Every one of them carries its group, its FULL period
  (``period_start``/``period_end`` — the label ``2026-2027`` is a grouping
  label only: Coccolino runs Vehículos ago-ago and Incendio abr-abr under it)
  and an ``account_client`` row for the contratante;
* their post-sale children — endorsement, collection, claim and renewal cases —
  with ``sequence_no`` and real dates, so the policy sub-funnel renders;
* every corpus file as a ``document`` with ``category`` + ``section`` +
  ``document_code``, filed under ``documents/case_file/{id}/{category}-{n}.{ext}``;
* the operating rows the stages imply: leads, placements, quote requests,
  proposals, inspections, policies, endorsements, collection plans and
  instalments, warranties, claims and claim items;
* back-dated ``case_file_stage_event`` rows (from the document dates) and 3-6
  Spanish internal notes per case;
* two CONFIRMED ``extraction`` rows — the LA FAVORITA 07/08 pair, hand-
  transcribed from the corpus — so ``GET /policies/{id}/mirror-diff`` has
  sources and the mirror-validation demo shows the documents' real
  discrepancies without needing ``--extract`` or an ``AI_API_KEY``.

Three things worth knowing before reading the code
--------------------------------------------------

**A renewal is a sibling in time, not a post-sale child.** Three axes never
mix on ``case_file``: ``origin``/``origin_case_file_id`` is TIME (new /
renewal / period_change), ``supersedes_case_file_id`` is VERSION (rework), and
``parent_case_file_id``+``policy_id`` is POST-SALE (endoso, cobranza,
siniestro under a policy). The LA FAVORITA renewal folder therefore has
``policy_id IS NULL`` and no parent — it points back at the account it renews
through ``origin_case_file_id`` and opens its own draft placement in the next
vigencia.

**Brokers are matched by NAME, never by the corpus RUT.** The letterheads print
Ossa Covarrubias as ``77.245.318-9`` and Fuenzalida SR as ``76.114.982-3`` while
the fixtures carry ``78.069.390-8`` and ``77.290.425-8``. Those are facts about
a document, not tenant identity — and both fail mod-11, which is the tell.
Matching on them would fork every tenant in two. See
``expediente_mappings.BROKER_KEY_BY_NAME``.

**Ids are allocated before anything is written.** ``policy.source_document_id``
points at a ``document`` whose ``entity_id`` points back at a ``case_file`` whose
``policy_id`` points at the policy — a three-way cycle no insert order resolves.
``allocate_ids()`` reads ``MAX(id)`` for all fourteen affected tables and hands
out explicit primary keys from there, exactly as ``import_fixtures`` does. Both
SQLite and MySQL advance their auto-increment past an explicit PK.

**Money is transcribed, never computed.** Every premium below is read off the
corpus document and then *checked* against the Chilean invariants
(``net = taxable + exempt``, ``vat = 0.19 x taxable``, ``total = net + vat``)
with the same tolerance ``import_fixtures`` uses. A mismatch is a warning naming
the document, never a silent correction.
"""
from __future__ import annotations

import argparse
import mimetypes
import re
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.expediente_mappings import (
    BROKER_KEY_BY_NAME,
    INSURED_BY_KEY,
    INSURER_BY_LABEL,
    LINE_BY_KEY,
    PRIOR_BROKER_NAMES,
    UnknownCategory,
    UnknownSection,
    classify,
    group_slug,
    loose_rut,
    period_label,
    section_for,
)
from app.db.fixture_mappings import norm_token
from app.db.import_fixtures import FixtureError, Ids, Log, Uploader
from app.db.session import SessionLocal, engine
from app.models import (
    AccountClient,
    AccountClientRole,
    AccountGroup,
    AccountGroupStatus,
    Activity,
    Asset,
    Broker,
    CaseFile,
    CaseFileStageEvent,
    CaseFileStatus,
    CaseFileKind,
    CaseOrigin,
    CasePack,
    CaseSection,
    CaseStage,
    Claim,
    ClaimItem,
    ClaimItemKind,
    ClaimRuling,
    Client,
    CollectionInstallment,
    CollectionPlan,
    CollectionPlanStatus,
    Document,
    Endorsement,
    EndorsementKind,
    EndorsementStatus,
    Extraction,
    ExtractionKind,
    ExtractionStatus,
    Inspection,
    InstallmentStatus,
    InsuranceLine,
    Insured,
    Insurer,
    LeadStatus,
    Note,
    PackKind,
    PackStatus,
    PaymentMode,
    Placement,
    Policy,
    Proposal,
    ProposalOutcome,
    QuoteLineItem,
    QuoteRequest,
    SalesLead,
    User,
    Warranty,
    WarrantySource,
    WarrantyStatus,
)
from app.models.asset import AssetStatus
from app.models.base_class import Base
from app.models.client import ClientStatus
from app.models.document import DocumentCategory
from app.models.enums import EntityType, PersonType, Priority
from app.models.inspection import InspectionStatus
from app.models.placement import PlacementStatus
from app.models.policy import ClaimStatus, PolicyLocation, PolicyStatus
from app.models.proposal import ProposalOrigin, ProposalStatus
from app.models.quote import QuoteRequestStatus
from app.services.identifiers import normalize_codigo_cmf, validate_rut

DEFAULT_SOURCE = Path.home() / "Downloads" / "EXPEDIENTES DEMO"

# A sub-expediente folder starts with its ordinal ("1.", "4."); the nested
# ones (Cobranza / Endoso / Siniestro) start with the word instead.
_ORDINAL_HEAD = re.compile(r"^\s*\d+\s*\.")

# Same tolerance as import_fixtures: the corpus rounds VAT to the cent.
MONEY_TOLERANCE = Decimal("0.011")
VAT_RATE = Decimal("0.19")

# Files that are inventory, not evidence.
IGNORED_FILE_NAMES = {"INVENTARIO.md", ".DS_Store"}

# Every table whose primary keys are handed out up front (spec §9.4).
ALLOCATED_TABLES: tuple[str, ...] = (
    "case_file",
    "sales_lead",
    "document",
    "policy",
    "endorsement",
    "collection_plan",
    "collection_installment",
    "warranty",
    "claim",
    "claim_item",
    "case_pack",
    "case_file_stage_event",
    "note",
    "activity",
)

_MODEL_BY_TABLE: dict[str, Any] = {
    "case_file": CaseFile,
    "sales_lead": SalesLead,
    "document": Document,
    "policy": Policy,
    "endorsement": Endorsement,
    "collection_plan": CollectionPlan,
    "collection_installment": CollectionInstallment,
    "warranty": Warranty,
    "claim": Claim,
    "claim_item": ClaimItem,
    "case_pack": CasePack,
    "case_file_stage_event": CaseFileStageEvent,
    "note": Note,
    "activity": Activity,
}


# =============================================================================
# Support
# =============================================================================


def uf(value: str | int | float | Decimal | None) -> Decimal | None:
    """Exact UF amount. Goes through ``str`` so a float never bleeds in."""
    if value is None:
        return None
    return Decimal(str(value))


def noon(day: date) -> datetime:
    """The 12:00 convention: policy periods and endorsement effect are contractual."""
    return datetime.combine(day, time(12, 0), tzinfo=timezone.utc)


def at(day: date, hour: int = 9, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=timezone.utc)


def d(iso: str) -> date:
    return date.fromisoformat(iso)


def _round2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _ext_of(name: str) -> str:
    return (Path(name).suffix or "").lstrip(".").lower() or "bin"


class Sequences(Ids):
    """``Ids`` with a per-table starting offset and an anonymous ``next_id``.

    ``import_fixtures`` allocates from 1 because it owns an empty database. This
    importer runs on top of it, so every table starts at ``MAX(id) + 1``. Keys
    that have a natural source id (a document's relative path) go through
    ``allocate``; rows built on the fly take ``next_id``.
    """

    def __init__(self) -> None:
        super().__init__()
        self._bases: dict[str, int] = {}
        self._counters: Counter = Counter()

    def set_base(self, table: str, base: int) -> None:
        self._bases[table] = base

    def allocate(self, table: str, source_ids: Any) -> None:
        base = self._bases.get(table, 1)
        table_map = self._maps[table]
        for source_id in source_ids:
            if source_id not in table_map:
                table_map[source_id] = base + self._counters[table]
                self._counters[table] += 1

    def next_id(self, table: str) -> int:
        base = self._bases.get(table, 1)
        value = base + self._counters[table]
        self._counters[table] += 1
        return value


@dataclass
class CorpusFile:
    """One file on disk, already classified."""

    path: Path
    relative: str
    section: CaseSection
    category: DocumentCategory
    code: str | None

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class CaseSpec:
    """The generic half of one expediente. The bespoke half lives in a builder."""

    key: str
    folder: tuple[str, ...]
    broker_key: str
    insured_key: str
    line_key: str
    reference: str
    title: str
    stage: CaseStage
    status: CaseFileStatus
    # Which sub-expedientes to import. Empty tuple = none (the lead case);
    # None = every section the folder has.
    sections: tuple[CaseSection, ...] | None
    asset_type: str
    asset_name: str
    asset_address: str
    asset_commune: str
    asset_region: str
    placement_status: PlacementStatus
    period_start: date
    period_end: date
    opened_on: date
    summary: str
    # (stage, date) in journey order; the first event has from_stage=None.
    timeline: tuple[tuple[CaseStage, str], ...]
    # (body, follow_up_on|None)
    notes: tuple[tuple[str, str | None], ...]
    # Corpus codes to leave out even though their section is imported. The
    # intake case has its antecedentes but not yet its 01 bases técnicas or its
    # 02 carta de remisión — that is what makes it `intake` and not `technical_basis`.
    exclude_codes: tuple[str, ...] = ()
    # Set on the lead case: it has a client but no placement and no files.
    with_placement: bool = True
    meta: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# The seven expedientes (spec §9.3) — 7 cases parked at 7 journey points
# =============================================================================
#
# Dates, folios, quotation numbers, policy numbers and every UF figure below are
# transcribed from the corpus documents named in the comments. Nothing is
# invented; where the corpus is silent the field is left NULL.

CASES: tuple[CaseSpec, ...] = (
    # -- 1 -- JO PASTELERIA · ACCIDENTES PERSONALES -> `lead` -----------------
    CaseSpec(
        key="jo_ap",
        folder=("JO PASTELERIA", "ACCIDENTES PERSONALES"),
        broker_key="ossa_covarrubias",
        insured_key="pacto_food",
        line_key="personal_accident",
        reference="EXP-2026-0001",
        title="JO Pastelería · Accidentes Personales colectivo",
        stage=CaseStage.LEAD,
        status=CaseFileStatus.OPEN,
        sections=(),  # no documents at this stage (spec §9.3)
        asset_type="production_kitchen",
        asset_name="Cocina de producción y dos locales de atención",
        asset_address="Av. Borgoño 25400",
        asset_commune="Concón",
        asset_region="Región de Valparaíso",
        placement_status=PlacementStatus.DRAFT,
        period_start=d("2026-07-01"),
        period_end=d("2027-07-01"),
        opened_on=d("2026-04-08"),
        summary=(
            "La asegurada pide revisar la póliza de accidentes personales: declara 6 "
            "trabajadores cuando tiene 11 con contrato, el capital individual de UF 850 "
            "convive con un cúmulo por evento de UF 500, y la clasificación de riesgo "
            "es de restaurant y no de cocina de producción. Aún es un dato comercial: "
            "no hay antecedentes cargados ni bases técnicas."
        ),
        timeline=((CaseStage.LEAD, "2026-04-08"),),
        notes=(
            (
                "Josefina llama a raíz de la carta de RC: quiere que revisemos también "
                "accidentes personales. Dos eventos no denunciados en 2024 (corte con "
                "rebanadora, quemadura con bandeja). No hay carpeta todavía.",
                None,
            ),
            (
                "Pendiente pedir nómina de los 11 trabajadores con función y renta para "
                "dimensionar capitales, y revisar el cúmulo por evento. Sin eso no se "
                "puede armar la ficha 00B.",
                "2026-05-15",
            ),
        ),
        with_placement=False,
    ),
    # -- 2 -- COCCOLINO · VEHICULOS -> `intake` -------------------------------
    CaseSpec(
        key="coccolino_fleet",
        folder=("COCCOLINO", "VEHICULOS"),
        broker_key="ossa_covarrubias",
        insured_key="coccolino",
        line_key="fleet",
        reference="EXP-2026-0002",
        title="Coccolino · Vehículos Motorizados · flota de 3 furgones",
        stage=CaseStage.INTAKE,
        status=CaseFileStatus.OPEN,
        sections=(CaseSection.ROOT_PROSPECT, CaseSection.SUBMISSION),
        # 00A + 00B/00C/00D/00E only: the bases técnicas and the carta de
        # remisión are not written yet, which is what keeps this case at `intake`.
        exclude_codes=("01", "02"),
        asset_type="fleet",
        asset_name="Flota de reparto — 3 furgones",
        asset_address="Av. Blanca Estela 1927, Local 2",
        asset_commune="Concón",
        asset_region="Región de Valparaíso",
        placement_status=PlacementStatus.INSPECTION,
        period_start=d("2026-08-01"),
        period_end=d("2027-08-01"),
        opened_on=d("2026-07-02"),
        summary=(
            "Regularización de la flota: dos de los tres furgones circulan con póliza "
            "vencida desde mediados de junio de 2026. Antecedentes recibidos y ficha, "
            "montos, siniestralidad e inspección de flota ya cargados; faltan las bases "
            "técnicas y la carta de remisión."
        ),
        timeline=(
            (CaseStage.LEAD, "2026-07-02"),
            (CaseStage.INTAKE, "2026-07-03"),
        ),
        notes=(
            (
                "Dos furgones sin cobertura desde el 15 y el 16 de junio. Prioridad "
                "operativa: cotizar y colocar antes de que ocurra un siniestro sin póliza.",
                "2026-07-20",
            ),
            (
                "Inspección de flota folio 2026000512339 recibida: nota 71/100, "
                "clasificación insatisfactoria. El puntaje lo arrastra el perfil de "
                "conductores, no los vehículos.",
                None,
            ),
            (
                "Falta escribir las bases técnicas. Confirmar con la asegurada si el "
                "Jumper 2024 se traslada a la nueva póliza al vencimiento del 25-03-2027 "
                "o se endosa antes.",
                "2026-07-25",
            ),
        ),
    ),
    # -- 3 -- JO PASTELERIA · RESPONSABILIDAD CIVIL -> `market_submission` ----
    CaseSpec(
        key="jo_rc",
        folder=("JO PASTELERIA", "RESPONSABILIDAD CIVIL"),
        broker_key="ossa_covarrubias",
        insured_key="pacto_food",
        line_key="liability",
        reference="EXP-2026-0003",
        title="JO Pastelería · Responsabilidad Civil por ocurrencia",
        stage=CaseStage.MARKET_SUBMISSION,
        status=CaseFileStatus.OPEN,
        sections=(CaseSection.ROOT_PROSPECT, CaseSection.SUBMISSION),
        asset_type="production_kitchen",
        asset_name="Cocina de producción y dos locales de atención",
        asset_address="Av. Borgoño 25400",
        asset_commune="Concón",
        asset_region="Región de Valparaíso",
        placement_status=PlacementStatus.QUOTING,
        period_start=d("2026-06-12"),
        period_end=d("2027-06-12"),
        opened_on=d("2026-04-06"),
        summary=(
            "Colocación de RC por ocurrencia. La carpeta se juega en dos puntos: ampliar "
            "la ventana de manifestación de 7 a 60 días (listeria y hepatitis A quedan "
            "fuera por el solo transcurso del plazo) y obtener el carve-back expreso de "
            "la exclusión LMA 5394 para la intoxicación alimentaria bacteriana. Carpeta "
            "remitida a Unnio, Chubb y HDI el 04-05-2026; sin respuestas todavía."
        ),
        timeline=(
            (CaseStage.LEAD, "2026-04-06"),
            (CaseStage.INTAKE, "2026-04-10"),
            (CaseStage.PRE_UNDERWRITING, "2026-04-20"),
            (CaseStage.TECHNICAL_BASIS, "2026-04-28"),
            (CaseStage.MARKET_SUBMISSION, "2026-05-04"),
        ),
        notes=(
            (
                "La póliza vigente cubre daños por alimentos solo si se manifiestan "
                "dentro de 7 días. Listeria tiene mediana de incubación de 21 días y "
                "hepatitis A media de 28. La cobertura no existe para los dos patógenos "
                "de mayor severidad del giro.",
                None,
            ),
            (
                "LMA 5394 leída en sus términos comprende la intoxicación alimentaria "
                "bacteriana: hay una cobertura y una exclusión posterior que la vacía. "
                "Se pide carve-back expreso con salvedad de epidemia declarada.",
                None,
            ),
            (
                "Carpeta remitida simultáneamente a Unnio, Chubb y HDI. Plazo de oferta "
                "solicitado: 20 días corridos.",
                "2026-05-25",
            ),
            (
                "Si ninguna compañía acepta la ventana de 60 días, evaluar sublímite "
                "específico de intoxicación alimentaria con ventana ampliada en lugar de "
                "la ampliación general.",
                "2026-05-28",
            ),
        ),
    ),
    # -- 4 -- GRUPO VIÑA INDOMITA · VIÑA SANTA ALICIA -> `comparison` ---------
    CaseSpec(
        key="santa_alicia",
        folder=("GRUPO VIÑA INDOMITA", "VIÑA SANTA ALICIA"),
        broker_key="fuenzalida_sr",
        insured_key="vina_santa_alicia",
        line_key="property",
        reference="EXP-2026-0001",
        title="Viña Santa Alicia · Todo Riesgo Bienes Físicos con PxP",
        stage=CaseStage.COMPARISON,
        status=CaseFileStatus.OPEN,
        sections=(
            CaseSection.ROOT_PROSPECT,
            CaseSection.SUBMISSION,
            CaseSection.INSURER_QUOTES,
        ),
        asset_type="industrial_plant",
        asset_name="Planta y bodegas Santa Alicia — Pirque",
        asset_address="Camino Santa Rita 1249",
        asset_commune="Pirque",
        asset_region="Región Metropolitana",
        placement_status=PlacementStatus.NEGOTIATING,
        period_start=d("2026-10-15"),
        period_end=d("2027-10-15"),
        opened_on=d("2026-08-04"),
        summary=(
            "Segunda razón social de la cuenta GRUPO VIÑA INDOMITA. Tres ofertas TRBF "
            "recibidas y comparadas ítem por ítem. Southbridge acepta las 46 coberturas "
            "solicitadas; HDI es la más barata recortando límite de indemnización a "
            "UF 250.000; Mapfre condiciona a inspección previa y obras de anclaje."
        ),
        timeline=(
            (CaseStage.LEAD, "2026-08-04"),
            (CaseStage.INTAKE, "2026-08-08"),
            (CaseStage.PRE_UNDERWRITING, "2026-08-18"),
            (CaseStage.TECHNICAL_BASIS, "2026-08-26"),
            (CaseStage.MARKET_SUBMISSION, "2026-08-31"),
            (CaseStage.QUOTES_RECEIVED, "2026-09-18"),
            (CaseStage.COMPARISON, "2026-09-24"),
        ),
        notes=(
            (
                "Misma cuenta comercial que Viña Indómita, distinta razón social y "
                "distinto RUT. Se mantienen expedientes separados: la materia asegurada, "
                "la ubicación y el programa no son los mismos.",
                None,
            ),
            (
                "HDI aparece como la más barata por tasa media (2,357‰) recortando el "
                "límite de indemnización a UF 250.000, es decir al 68% de los bienes "
                "declarados. No es comparable en esos términos.",
                None,
            ),
            (
                "Mapfre exige obras de anclaje ejecutadas antes de la emisión y mantiene "
                "coaseguro sísmico. Se desaconseja.",
                None,
            ),
            (
                "Comparativo enviado a la asegurada. Esperando orden de colocación para "
                "pasar a propuesta de emisión.",
                "2026-10-01",
            ),
        ),
    ),
    # -- 5 -- COCCOLINO · MULTIRRIESGO -> `proposal_issued` -------------------
    CaseSpec(
        key="coccolino_multi",
        folder=("COCCOLINO", "MULTIRRIESGO"),
        broker_key="ossa_covarrubias",
        insured_key="coccolino",
        line_key="property",
        reference="EXP-2026-0004",
        title="Coccolino · Incendio y Riesgos Adicionales · Riesgos Nominados",
        stage=CaseStage.PROPOSAL_ISSUED,
        status=CaseFileStatus.OPEN,
        sections=(
            CaseSection.ROOT_PROSPECT,
            CaseSection.SUBMISSION,
            CaseSection.INSURER_QUOTES,
            CaseSection.BROKER_PROPOSAL,
        ),
        asset_type="production_kitchen",
        asset_name="Obrador, horno y local de atención — Concón",
        asset_address="Av. Blanca Estela 1927, Local 2",
        asset_commune="Concón",
        asset_region="Región de Valparaíso",
        placement_status=PlacementStatus.AWARDED,
        period_start=d("2026-04-01"),
        period_end=d("2027-04-01"),
        opened_on=d("2026-02-06"),
        summary=(
            "Cuatro ofertas sobre la misma carpeta: BCI, Consorcio y dos alternativas de "
            "HDI (Riesgos Nominados y Todo Riesgo). Se adjudica HDI Riesgos Nominados, "
            "cotización HDI-INC-2026-0338, prima bruta UF 129,91: misma tasa media que "
            "BCI (3,78‰) con 47 coberturas contra 31 y sin límite de indemnización. "
            "Propuesta de emisión enviada; la póliza aún no llega."
        ),
        timeline=(
            (CaseStage.LEAD, "2026-02-06"),
            (CaseStage.INTAKE, "2026-02-10"),
            (CaseStage.PRE_UNDERWRITING, "2026-02-16"),
            (CaseStage.TECHNICAL_BASIS, "2026-02-24"),
            (CaseStage.MARKET_SUBMISSION, "2026-02-27"),
            (CaseStage.QUOTES_RECEIVED, "2026-03-12"),
            (CaseStage.COMPARISON, "2026-03-16"),
            (CaseStage.INSURED_DECISION, "2026-03-20"),
            (CaseStage.PROPOSAL_ISSUED, "2026-03-24"),
        ),
        notes=(
            (
                "El hallazgo del comparativo: BCI y HDI Nominados tienen exactamente la "
                "misma tasa media, 3,78‰. Por el mismo precio unitario de riesgo HDI "
                "expone UF 30.720 y otorga 47 coberturas; BCI expone UF 24.320 con LMI "
                "de UF 12.000 y otorga 31.",
                None,
            ),
            (
                "La alternativa Todo Riesgo de HDI (05B) se presenta como decisión de la "
                "asegurada, no como recomendación del corredor: UF 11,73 más de prima "
                "bruta por eliminar la discusión probatoria en siniestro.",
                None,
            ),
            (
                "Rotura de maquinaria frigorífica con franquicia de 4 horas: bajo esa "
                "condición el siniestro rechazado en noviembre de 2025 habría sido "
                "indemnizado. Es la razón técnica de la adjudicación.",
                None,
            ),
            (
                "Propuesta de emisión 07 enviada a HDI el 24-03-2026 con vigencia "
                "solicitada desde el 01-04-2026 a las 12:00.",
                "2026-03-30",
            ),
            (
                "Al recibir la póliza: validación espejo campo a campo contra la "
                "propuesta. Toda diferencia se corrige por endoso sin costo.",
                "2026-04-10",
            ),
        ),
    ),
    # -- 6 -- GRUPO VIÑA INDOMITA · VIÑA INDOMITA -> `active` -----------------
    CaseSpec(
        key="vina_indomita",
        folder=("GRUPO VIÑA INDOMITA", "VIÑA INDOMITA"),
        broker_key="fuenzalida_sr",
        insured_key="vina_indomita",
        line_key="property",
        reference="EXP-2025-0001",
        title="Viña Indómita · Todo Riesgo Bienes Físicos con Terremoto",
        stage=CaseStage.ACTIVE,
        status=CaseFileStatus.WON,
        sections=None,  # everything
        asset_type="industrial_plant",
        asset_name="Planta de vinificación, guarda y embotellado — Casablanca",
        asset_address="Lote B, Parcela 12B2",
        asset_commune="Casablanca",
        asset_region="Región de Valparaíso",
        placement_status=PlacementStatus.ACTIVE,
        period_start=d("2025-10-15"),
        period_end=d("2026-10-15"),
        opened_on=d("2025-09-05"),
        summary=(
            "Póliza 0020119904 de Southbridge, TRBF con terremoto sobre UF 475.256, "
            "vigente desde el 15-10-2025. Dos endosos emitidos (inclusión de línea de "
            "embotellado y barricas por UF 22.000; exclusión de bodega de apoyo por "
            "UF 2.892), un siniestro liquidado en UF 2.399,40 y una cuota vencida que "
            "el corredor está gestionando."
        ),
        meta={
            # Rule §3.2/6: the prior brokers named on the real carrier policies
            # filed in this expediente are TEXT. They never create a broker row.
            "prior_brokers_named_in_documents": list(PRIOR_BROKER_NAMES),
        },
        timeline=(
            (CaseStage.LEAD, "2025-09-05"),
            (CaseStage.INTAKE, "2025-09-09"),
            (CaseStage.PRE_UNDERWRITING, "2025-09-15"),
            (CaseStage.TECHNICAL_BASIS, "2025-09-19"),
            (CaseStage.MARKET_SUBMISSION, "2025-09-22"),
            (CaseStage.QUOTES_RECEIVED, "2025-10-01"),
            (CaseStage.COMPARISON, "2025-10-06"),
            (CaseStage.INSURED_DECISION, "2025-10-07"),
            (CaseStage.PROPOSAL_ISSUED, "2025-10-08"),
            (CaseStage.RATIFIED, "2025-10-10"),
            (CaseStage.POLICY_ISSUED, "2025-10-13"),
            (CaseStage.MIRROR_VALIDATION, "2025-10-16"),
            (CaseStage.ACTIVE, "2025-10-20"),
        ),
        notes=(
            (
                "Se adjudica Southbridge pese a que Mapfre es UF 103,60 más barata: "
                "Mapfre trae LMI de UF 300.000 sobre bienes de UF 475.256, es decir un "
                "63% del valor. Southbridge gana 41 de 41 extensiones y 15 de 16 "
                "deducibles.",
                None,
            ),
            (
                "Brecha B-1 declarada por escrito: no se contrata perjuicio por "
                "paralización. El liquidador de riesgos la cuantificó en UF 88.000 para "
                "el escenario PML. Se recomienda incorporarlo por endoso durante la "
                "vigencia, a la tasa de la póliza.",
                "2026-02-01",
            ),
            (
                "Endoso E1 emitido el 18-03-2026: línea de embotellado Bertolaso y 180 "
                "barricas nuevas, UF 22.000, al amparo de la cláusula de incorporación "
                "automática (tope 10% del monto asegurado, máximo UF 25.000).",
                None,
            ),
            (
                "Siniestro SIN-2026-0417: rotura de la cuba 14 con derrame de 58.400 "
                "litros. Liquidado en UF 2.399,40 sobre pérdida acreditada de UF 2.666 "
                "con deducible de 10%. La sala estuvo 11 días fuera de servicio y esa "
                "pérdida de explotación no tuvo cobertura.",
                None,
            ),
            (
                "Cupón CUP-008 vencido el 05-06-2026, 67 días de mora al 11-08-2026. "
                "Riesgo de término por artículo 528. Contactar finanzas del asegurado "
                "antes de completar 90 días.",
                "2026-08-20",
            ),
            (
                "Para la renovación de octubre: llevar la recomendación 3 del liquidador "
                "(detección de nivel con alarma remota en la sala de guarda 2, coincide "
                "con la R-12 de la inspección) y la cotización del PxP.",
                "2026-09-01",
            ),
        ),
    ),
    # -- 7 -- LA FAVORITA -> `active` (the richest chain) ---------------------
    CaseSpec(
        key="la_favorita",
        folder=("LA FAVORITA",),
        broker_key="ossa_covarrubias",
        insured_key="la_favorita",
        line_key="property",
        reference="EXP-2026-0005",
        title="La Favorita · Incendio y Riesgos Adicionales con PxP",
        stage=CaseStage.ACTIVE,
        status=CaseFileStatus.WON,
        sections=None,  # everything
        asset_type="cold_storage",
        asset_name="Centro de distribución frigorífico — Lo Blanco",
        asset_address="Lo Blanco 2011",
        asset_commune="La Pintana",
        asset_region="Región Metropolitana",
        placement_status=PlacementStatus.ACTIVE,
        period_start=d("2027-01-05"),
        period_end=d("2028-01-05"),
        opened_on=d("2026-09-22"),
        summary=(
            "Riesgo declinado y recolocado. Tres compañías declinaron por escrito y una "
            "cuarta condicionó su cotización, todas por los mismos tres frentes: "
            "envolvente de poliuretano, ausencia de detección y ausencia de agua. El "
            "plan de ingeniería de 12 medidas por UF 9.110 abrió la segunda ronda y HDI "
            "cotizó. Póliza 15-04-0091883 emitida, plan ejecutado íntegro, deducibles "
            "reducidos por endoso y un siniestro de frío pagado en UF 4.397."
        ),
        meta={
            "prior_brokers_named_in_documents": list(PRIOR_BROKER_NAMES),
            "outgoing_programme": "Aseguradora Porvenir S.A. · póliza 01-42-005194",
            "outgoing_broker_commission_pct": "10.91",
        },
        timeline=(
            (CaseStage.LEAD, "2026-09-22"),
            (CaseStage.INTAKE, "2026-09-28"),
            (CaseStage.PRE_UNDERWRITING, "2026-10-08"),
            (CaseStage.TECHNICAL_BASIS, "2026-10-16"),
            (CaseStage.MARKET_SUBMISSION, "2026-10-21"),
            (CaseStage.QUOTES_RECEIVED, "2026-11-12"),
            (CaseStage.TECHNICAL_BASIS, "2026-11-24"),
            (CaseStage.MARKET_SUBMISSION, "2026-11-25"),
            (CaseStage.QUOTES_RECEIVED, "2026-12-11"),
            (CaseStage.COMPARISON, "2026-12-15"),
            (CaseStage.INSURED_DECISION, "2026-12-17"),
            (CaseStage.PROPOSAL_ISSUED, "2026-12-18"),
            (CaseStage.RATIFIED, "2026-12-20"),
            (CaseStage.POLICY_ISSUED, "2026-12-22"),
            (CaseStage.MIRROR_VALIDATION, "2026-12-29"),
            (CaseStage.ACTIVE, "2027-01-05"),
        ),
        notes=(
            (
                "Primera ronda: Porvenir, Mapfre y Consorcio declinan; HDI condiciona. "
                "Ninguna lo hace por la actividad, la administración ni la "
                "siniestralidad. Las cuatro señalan los mismos tres frentes.",
                None,
            ),
            (
                "El plan de ingeniería 03E convierte el dimensionamiento de la "
                "inspección en compromiso de ejecución: 12 medidas, UF 9.110, 270 días, "
                "cada una con proveedor cotizado e hito verificable. No es una carta de "
                "intenciones.",
                None,
            ),
            (
                "Segunda ronda con el plan adjunto: HDI, Chubb y Consorcio cotizan. Se "
                "recomienda HDI, la más cara de las tres, por ser la única que otorga la "
                "franquicia horaria de 4 horas y el mecanismo de reducción de deducible.",
                None,
            ),
            (
                "Plan ejecutado íntegro: 12 de 12 medidas dentro de plazo, inversión real "
                "UF 9.284,20, un 1,9% sobre lo presupuestado. Aviso de cumplimiento "
                "enviado el 23-09-2027.",
                None,
            ),
            (
                "Cuota 8: PAC rechazado por saldo insuficiente el 05-08, reintento "
                "rechazado el 12-08, pago por transferencia el 18-08. Mora efectiva de 13 "
                "días sobre el plazo de 30 del artículo 528. La cobertura nunca se vio "
                "afectada porque la compañía no alcanzó a emitir aviso.",
                None,
            ),
            (
                "Siniestro S-2027-58814 pagado el 19-12-2027, 41 días después del "
                "denuncio. Siniestralidad del año 1146,8% sobre prima neta: HDI la va a "
                "poner sobre la mesa en la renovación del 05-01-2028.",
                "2027-12-28",
            ),
        ),
    ),
)


# =============================================================================
# LA FAVORITA mirror sources — hand-transcribed 07 / 08 payloads (spec §7.6)
# =============================================================================
#
# A plain import writes structure only (no AI runs), so the mirror-diff would
# report ``missing_sources`` and the `mirror_validation` feature would demo
# empty. These payloads are HUMAN TRANSCRIPTIONS of the two corpus documents:
#
#   3. Subexpediente de Propuesta/07 Propuesta de Emision de Poliza - La Favorita.docx
#   4. Subexpediente de Póliza/08 Poliza N 15-04-0091883 - HDI - La Favorita.pdf
#
# Values are copied in the documents' own Chilean notation ("77.240,00",
# "05-01-2027", "12:00 horas del 05 de enero de 2027") and parsed by the
# registry schemas' validators, exactly as an AI extraction would be. The
# DISCREPANCIES between the two payloads are the documents' own and are
# preserved, never reconciled (§3.2 rule 4): the issued policy's condiciones
# particulares omit four declarations that the proposal's §5 states —
# modalidad de aseguramiento, límite de indemnización, base de indemnización
# and territorio — and its carátula dates the originating proposal 18-12-2026
# while the 07 itself is signed 19-12-2026. Sections identical on both sides
# (exclusiones, cláusulas particulares, sublímites, declaraciones) are omitted
# from BOTH payloads symmetrically and flagged in ``extraction_notes``.

#: §9 of the 07 / §4 of the 08 — the 46 nominated coverages, verbatim and
#: IDENTICAL on both documents: (number, name, limit text).
_LA_FAVORITA_COVERAGES: tuple[tuple[str, str, str], ...] = (
    ("1", "Incendio, rayo y sus consecuencias directas",
     "Al 100% del monto asegurado de cada ubicación"),
    ("2", "Explosión de cualquier origen",
     "Al 100% del monto asegurado de la ubicación afectada"),
    ("3", "Daños por humo, hollín y gases de combustión",
     "Al 100% del monto asegurado. Cobertura esencial atendida la naturaleza "
     "del panel de poliuretano"),
    ("4", "Sismo, temblor y terremoto", "Al 100% del monto asegurado de cada ubicación"),
    ("5", "Salida de mar, maremoto y tsunami",
     "Al 100% del monto asegurado de cada ubicación"),
    ("6", "Erupción volcánica y lluvia de cenizas",
     "Al 100% del monto asegurado de cada ubicación"),
    ("7", "Viento, ventarrón y granizo", "Al 100% del monto asegurado de cada ubicación"),
    ("8", "Inundación, anegamiento y desborde de cauces",
     "Al 100% del monto asegurado de cada ubicación"),
    ("9", "Aluvión, alud y deslizamiento de tierra",
     "Al 100% del monto asegurado de cada ubicación"),
    ("10", "Peso de nieve o hielo", "Al 100% del monto asegurado de cada ubicación"),
    ("11", "Colapso o derrumbe de edificios y estructuras",
     "Al 100% del monto asegurado de la ubicación afectada"),
    ("12", "Impacto de vehículos terrestres y de sus cargas", "UF 3.000 por evento"),
    ("13", "Rotura de cañerías, daños por agua y filtraciones", "UF 2.500 por evento"),
    ("14", "Huelga, conmoción civil, actos vandálicos y daño malicioso",
     "UF 12.000 por evento y en agregado"),
    ("15", "Terrorismo y sabotaje", "UF 12.000 por evento y en agregado"),
    ("16", "Combustión espontánea y calor propio", "UF 3.000 por evento"),
    ("17", "ROTURA DE MAQUINARIA FRIGORÍFICA",
     "UF 12.000 por evento y UF 18.000 en agregado anual. Comprende compresores, "
     "condensadores, evaporadores, tableros de control y cañerías de refrigerante "
     "de las cámaras y túneles de congelado"),
    ("18", "DETERIORO DE MERCADERÍA POR VARIACIÓN DE TEMPERATURA",
     "UF 15.000 por evento y UF 22.000 en agregado anual, con franquicia de 4 "
     "horas continuas de desviación fuera del rango declarado. Comprende el "
     "deterioro derivado de falla de equipo, corte de suministro eléctrico y "
     "error de operación"),
    ("19", "Avería de maquinaria de proceso", "UF 4.000 por evento y UF 6.000 en agregado"),
    ("20", "Daño eléctrico a maquinaria, tableros e instalaciones",
     "UF 5.000 por evento y UF 8.000 en agregado"),
    ("21", "Fuga o derrame de amoníaco de la instalación frigorífica",
     "UF 6.000 por evento, incluidos costos de neutralización, ventilación y "
     "disposición del producto contaminado"),
    ("22", "Robo y hurto con fuerza en las cosas, incluidos daños a la propiedad",
     "UF 4.000 por evento y por ubicación"),
    ("23", "Robo de valores en caja y remesas en tránsito",
     "UF 400 en caja y UF 400 en tránsito, por evento"),
    ("24", "Infidelidad y apropiación indebida de empleados",
     "UF 1.200 por evento y en agregado"),
    ("25", "Rotura de cristales, espejos y letreros", "UF 300 por evento"),
    ("26", "Equipos computacionales y electrónicos, todo riesgo",
     "UF 640, incluye daño por sobretensión"),
    ("27", "Bienes de terceros bajo custodia del asegurado",
     "UF 3.000 por evento. Comprende producto de clientes en consignación"),
    ("28", "Bienes propios en recinto de terceros",
     "UF 8.000 por evento. Corresponde a la ubicación U-2, cámara arrendada a "
     "operador logístico"),
    ("29", "Bienes en tránsito entre ubicaciones y hacia clientes",
     "UF 3.000 por despacho, INCLUYENDO EXPRESAMENTE mercaderías y productos "
     "elaborados, con cobertura de deterioro por falla del equipo de frío del vehículo"),
    ("30", "Gastos de remoción de escombros y desmontaje",
     "10% de la pérdida indemnizable, tope UF 6.000"),
    ("31", "Gastos de descontaminación y disposición sanitaria de producto",
     "UF 4.000 por evento. Comprende el retiro y destrucción certificada de "
     "producto no apto"),
    ("32", "Honorarios de arquitectos, ingenieros, peritos y contadores",
     "UF 4.000 por evento"),
    ("33", "Gastos extraordinarios de combate del incendio y salvamento",
     "UF 2.500 por evento"),
    ("34", "Gastos de arriendo de cámara de emergencia",
     "UF 2.000 por evento, como gasto de mitigación del deterioro de mercadería"),
    ("35", "Cláusula de leeway o margen de tolerancia sobre montos declarados",
     "10% sobre el monto asegurado de cada partida"),
    ("36", "Declaración flotante de existencias",
     "Se solicita mecanismo de declaración mensual de existencias con ajuste de "
     "prima al cierre de la vigencia, atendida la estacionalidad del rubro cárnico"),
    ("37", "Nuevos bienes adquiridos, incorporación automática",
     "10% del monto asegurado total, tope UF 6.000, con aviso dentro de 60 días"),
    ("38", "Cláusula de reposición a nuevo, sin depreciación",
     "Aplicable a la totalidad de las partidas de bienes físicos"),
    ("39", "Perjuicio por paralización — margen de contribución y gastos fijos",
     "UF 17.200, período indemnizable 12 meses"),
    ("40", "PxP por interrupción de suministro de energía eléctrica",
     "UF 6.000, con franquicia de 12 horas continuas"),
    ("41", "PxP por acceso impedido o cierre por acto de autoridad sanitaria",
     "UF 5.000, máximo 45 días"),
    ("42", "Responsabilidad civil de explotación", "UF 8.000 por evento y en agregado"),
    ("43", "Responsabilidad civil de productos alimenticios y después de entrega",
     "UF 8.000 por evento y en agregado, con ventana de manifestación de 60 días "
     "desde la fecha de la ingesta"),
    ("44", "Responsabilidad civil patronal",
     "UF 4.000 por persona · UF 8.000 por evento y en agregado, en exceso de la "
     "Ley 16.744"),
    ("45", "Accidentes personales de trabajadores, 24 horas",
     "UF 400 por trabajador en muerte e invalidez, UF 120 en gastos médicos"),
    ("46", "Cláusula de reducción de deducible por cumplimiento de ingeniería",
     "Se solicita mecanismo contractual de reducción de deducible al acreditarse "
     "el cumplimiento verificado del plan de ingeniería de riesgo, mediante "
     "re-inspección de la compañía"),
)

#: §10 of the 07 / §6 of the 08 — identical: (peril, deducible inicial, reducido).
_LA_FAVORITA_DEDUCTIBLES: tuple[tuple[str, str, str], ...] = (
    ("Incendio, rayo, explosión y humo",
     "15% de la pérdida, mínimo UF 120", "10% de la pérdida, mínimo UF 72"),
    ("Sismo y riesgos de la naturaleza",
     "2,5% del monto asegurado de la ubicación afectada, mínimo UF 180",
     "1,5% del monto asegurado, mínimo UF 110"),
    ("Rotura de maquinaria frigorífica",
     "15% de la pérdida, mínimo UF 50", "10% de la pérdida, mínimo UF 30"),
    ("Deterioro de mercadería por variación de temperatura",
     "15% de la pérdida, mínimo UF 50, más franquicia de 4 horas continuas",
     "10% de la pérdida, mínimo UF 30, más franquicia de 4 horas continuas"),
    ("Avería de maquinaria de proceso y daño eléctrico",
     "15% de la pérdida, mínimo UF 40", "10% de la pérdida, mínimo UF 24"),
    ("Fuga o derrame de amoníaco",
     "15% de la pérdida, mínimo UF 60", "10% de la pérdida, mínimo UF 36"),
    ("Robo y hurto con fuerza", "10% de la pérdida, mínimo UF 30", "Sin variación"),
    ("Daños por agua e impacto de vehículos",
     "10% de la pérdida, mínimo UF 40", "Sin variación"),
    ("Perjuicio por paralización",
     "7 días de la indemnización diaria", "5 días de la indemnización diaria"),
    ("Responsabilidad civil", "10% de la indemnización, mínimo UF 40", "Sin variación"),
    ("Accidentes personales", "Sin deducible", "Sin deducible"),
)

#: §13 of the 07 / §9 of the 08 — identical: (code, title, fecha límite).
_LA_FAVORITA_WARRANTIES: tuple[tuple[str, str, str], ...] = (
    ("G-1", "M-1 · Zona de acopio a no menos de 5 metros del panel, demarcada y mantenida",
     "Cumplida al 19-11-2026. Obligación permanente"),
    ("G-2", "M-2 · Sellado de 14 juntas degradadas, 4 puntos de núcleo expuesto y "
            "9 penetraciones", "15-01-2027"),
    ("G-3", "M-3 · Procedimiento de permiso de trabajo en caliente con vigilancia "
            "posterior de 60 minutos", "Cumplida al 21-11-2026. Obligación permanente"),
    ("G-4", "M-6 · Registro continuo de temperatura con alarma de desviación y "
            "respaldo a 12 meses",
     "28-02-2027 · condición suspensiva de las coberturas de frío"),
    ("G-5", "M-4 · Detección de humo por aspiración en cámaras y túnel, con "
            "monitoreo 24/7", "31-03-2027"),
    ("G-6", "M-8 · Detección de amoníaco con alarma, corte automático y ventilación "
            "forzada", "30-04-2027 · condición suspensiva de la cobertura de amoníaco"),
    ("G-7", "M-5 · Red húmeda con reserva dedicada de 25 m³ y bocas en los cuatro "
            "frentes", "30-06-2027 · gatilla la reducción de deducible"),
    ("G-8", "M-7 · Generador de respaldo con transferencia automática",
     "30-06-2027 · condición suspensiva del PxP por interrupción de suministro · "
     "gatilla la reducción"),
    ("G-9", "M-9 · Sectorización de la nave en tres sectores independientes hasta "
            "cubierta", "30-09-2027 · gatilla la reducción de deducible"),
    ("G-10", "M-10 · Termografía anual de tableros con cierre documentado de "
             "hallazgos", "28-02-2027 y anual"),
    ("G-11", "M-11 · Canalización definitiva en sala de máquinas, con declaración "
             "SEC TE-1", "15-02-2027"),
    ("G-12", "M-12 · Plan de emergencia actualizado y dos simulacros anuales "
             "documentados", "31-03-2027 y anual"),
    ("G-13", "Mantención preventiva trimestral de equipos de frío con servicio "
             "técnico calificado", "Permanente"),
    ("G-14", "Prueba semestral de red húmeda y prueba mensual de generador, "
             "registradas", "Permanente desde su recepción"),
    ("G-15", "Declaración mensual de existencias a costo de reposición",
     "Día 10 de cada mes"),
    ("G-16", "Resolución sanitaria y habilitación del Servicio Agrícola y Ganadero "
             "vigentes", "Permanente"),
)

#: §7 of the 07 / §2 of the 08 — identical: (ubicación, partida, monto UF verbatim).
_LA_FAVORITA_AMOUNTS: tuple[tuple[str, str, str], ...] = (
    ("U-1", "Edificio y obras civiles", "19.400,00"),
    ("U-1", "Instalaciones frigoríficas y equipos de frío", "8.600,00"),
    ("U-1", "Maquinaria y equipos de proceso", "3.200,00"),
    ("U-1", "Muebles, útiles y equipos computacionales", "640,00"),
    ("U-1", "Existencias de carnes, cecinas, huevos y pescados", "21.800,00"),
    ("U-2", "Existencias de carnes, cecinas, huevos y pescados", "6.400,00"),
    ("", "Perjuicio por paralización — margen de contribución y gastos fijos, "
         "período indemnizable 12 meses", "17.200,00"),
)

#: §14 of the 07 / §10 of the 08 — identical: (partida, monto expuesto, tasa ‰, prima).
_LA_FAVORITA_PREMIUM_LINES: tuple[tuple[str, str, str, str], ...] = (
    ("Bienes físicos — porción afecta a IVA", "60.040,00", "1,685", "101,18"),
    ("Bienes físicos — porción exenta de IVA, riesgos de la naturaleza",
     "60.040,00", "2,765", "166,00"),
    ("Perjuicio por paralización 12 meses — afecta a IVA", "17.200,00", "6,675", "114,81"),
)

#: Both documents print the same premium summary. net = taxable + exempt ·
#: vat = 0.19 x taxable · total = net + vat — the transcription satisfies the
#: invariants because the documents do; nothing is rebalanced here.
_LA_FAVORITA_PREMIUM: dict[str, str] = {
    "taxable_premium_uf": "215,99",
    "exempt_premium_uf": "166,00",
    "net_premium_uf": "381,99",
    "vat_uf": "41,04",
    "total_premium_uf": "423,03",
}

#: §16 of the 07 / §11 of the 08 — identical cuponera: (cuota, vencimiento, monto).
_LA_FAVORITA_INSTALMENTS: tuple[tuple[int, str, str], ...] = tuple(
    [(n, f"05-{month:02d}-2027", "42,30") for n, month in enumerate(range(1, 10), start=1)]
    + [(10, "05-10-2027", "42,33")]
)

_LA_FAVORITA_LOCATIONS: tuple[dict[str, str], ...] = (
    {
        "name": "U-1 · Centro de distribución frigorífico",
        "address": "Lo Blanco 2011",
        "commune": "La Pintana",
        "region": "Región Metropolitana",
        "use": "Recepción, despiece, envasado, congelado y despacho mayorista de "
               "carnes, cecinas, huevos y pescados",
        "note": "Tenencia: Propio",
    },
    {
        "name": "U-2 · Cámara de frío en recinto de tercero",
        "address": "Camino a Melipilla 14.820, Bodega 4",
        "commune": "Maipú",
        "region": "Región Metropolitana",
        "use": "Sobrestock de producto congelado en cámara arrendada a operador "
               "logístico. Sin operación propia",
        "note": "Tenencia: Arrendada a tercero",
    },
)

_LA_FAVORITA_TRANSCRIPTION_NOTE = (
    "Transcripción humana parcial del documento: exclusiones (24), cláusulas "
    "particulares (11), sublímites y declaraciones del asegurado no transcritos "
    "— idénticos en propuesta y póliza, sin efecto en la validación espejo."
)


def _la_favorita_issuance_proposal_payload() -> dict[str, Any]:
    """The 07 as a registry ``issuance_proposal`` payload, values verbatim."""
    return {
        "document_date": "19 de diciembre de 2026",
        "document_city": "Santiago",
        "addressee_insurer": {"legal_name": "HDI SEGUROS S.A."},
        "accepted_quotation_number": "HDI-2026-118447",
        "accepted_quotation_date": "09 de diciembre de 2026",
        "mirror_validation_clause": (
            "Este corredor practicará validación espejo entre el presente documento "
            "y la póliza que la compañía emita, comparando individualizaciones, "
            "vigencia, montos asegurados, coberturas, sublímites, deducibles, "
            "exclusiones, cláusulas particulares, garantías, apertura de prima y "
            "comisión. Toda diferencia será observada por escrito y deberá "
            "corregirse mediante endoso sin costo antes de la entrega de la póliza "
            "al asegurado."
        ),
        "policyholder": {
            "legal_name": "COMERCIALIZADORA LA FAVORITA LTDA",
            "rut": "76.789.935-1",
            "activity": (
                "Almacenamiento, despiece, envasado, congelado y comercio mayorista "
                "de carnes, cecinas, huevos y pescados"
            ),
            "address": "Lo Blanco 2011",
            "commune": "La Pintana",
            "contact_name": "Cristián Cabezas Varela",
            "contact_email": "gerencia@lafavorita.cl",
        },
        "broker": {
            "legal_name": "OSSA COVARRUBIAS CORREDORES DE SEGUROS LTDA.",
            "rut": "77.245.318-9",
            "contact_name": "José Francisco Sousa",
        },
        "insurance_line": "Incendio y Riesgos Adicionales con Perjuicio por Paralización",
        # §5 declarations — the four the issued policy OMITS (the diff's rows).
        "cover_mode": "Riesgos nominados, con las coberturas designadas en la sección 9",
        "indemnity_limit": "Full value. Sin límite máximo de indemnización por evento",
        "indemnity_basis": (
            "Reposición a nuevo, sin depreciación, en todas las partidas de bienes "
            "físicos"
        ),
        "territory": "República de Chile, en las ubicaciones declaradas en la sección 6",
        "replaced_policy": (
            "Aseguradora Porvenir S.A. N° 01-42-005194, que vence el 05-01-2027 y "
            "que la compañía comunicó no renovar"
        ),
        "period_start_at": "12:00 horas del 05 de enero de 2027",
        "period_end_at": "12:00 horas del 05 de enero de 2028",
        "locations": list(_LA_FAVORITA_LOCATIONS),
        "insured_values_by_partida": [
            {"item": f"{location} · {item}".lstrip("· ").strip(), "amount_uf": amount}
            for location, item, amount in _LA_FAVORITA_AMOUNTS
        ],
        "total_insured_amount_uf": "77.240,00",
        "coverages_to_issue": [
            {"number": number, "name": name, "text": limit}
            for number, name, limit in _LA_FAVORITA_COVERAGES
        ],
        "deductibles_to_issue": [
            {"peril": peril, "offered": initial, "reduced": reduced}
            for peril, initial, reduced in _LA_FAVORITA_DEDUCTIBLES
        ],
        "warranties": [
            {"code": code, "title": title, "requirement": deadline}
            for code, title, deadline in _LA_FAVORITA_WARRANTIES
        ],
        "premium_by_item": [
            {"item": item, "basis": f"Monto expuesto UF {base}", "pct": rate,
             "amount_uf": amount}
            for item, base, rate, amount in _LA_FAVORITA_PREMIUM_LINES
        ],
        "premium": dict(_LA_FAVORITA_PREMIUM),
        "commission": {
            "pct": "12",
            "amount_uf": "45,84",
            "note": (
                "12% sobre la prima neta, conforme a lo ofertado. Base: prima neta "
                "total de UF 381,99, afecta y exenta. La póliza saliente contemplaba "
                "10,91% de comisión para el corredor anterior."
            ),
        },
        # The mirror aligns commission on the top-level key the policy schema
        # uses; the registry allows extra keys, so the same stated 12% is also
        # recorded under that name (§15 of the document).
        "commission_pct": "12",
        "payment_plan": {
            "payment_mode": "Cargo automático en cuenta corriente (PAC), mandato "
                            "firmado que se acompaña",
            "installment_count": 10,
            "first_due_date": "05-01-2027",
            "text": (
                "10 cuotas mensuales iguales y sucesivas en Unidad de Fomento. Valor "
                "de cada cuota UF 42,30 brutas, salvo la última de UF 42,33."
            ),
        },
        "instalments": [
            {"number": number, "due_date": due, "gross_amount_uf": amount}
            for number, due, amount in _LA_FAVORITA_INSTALMENTS
        ],
        "signer": {
            "name": "José Francisco Sousa",
            "role": "Gerente Técnico",
            "company": "OSSA COVARRUBIAS CORREDORES DE SEGUROS LTDA.",
            "signed_on": "19 de diciembre de 2026",
        },
        "extraction_notes": [_LA_FAVORITA_TRANSCRIPTION_NOTE],
    }


def _la_favorita_policy_payload() -> dict[str, Any]:
    """The 08 as a registry ``policy`` payload, values verbatim.

    Deliberately ABSENT, because the issued document nowhere states them:
    ``coverage_modality``, ``indemnity_limit``, ``valuation_basis`` and any
    territory declaration — the proposal's §5 block that HDI's condiciones
    particulares dropped. Those four omissions are the mirror-diff.
    """
    return {
        "policy_number": "15-04-0091883",
        "insurer": {"legal_name": "HDI Seguros S.A."},
        "line_of_business": "Incendio y Riesgos Adicionales con Perjuicio por Paralización",
        "policyholder": {
            "legal_name": "COMERCIALIZADORA LA FAVORITA LTDA",
            "rut": "76.789.935-1",
        },
        "insured": {
            "legal_name": "COMERCIALIZADORA LA FAVORITA LTDA",
            "rut": "76.789.935-1",
        },
        "broker": {
            "legal_name": "OSSA COVARRUBIAS CORREDORES DE SEGUROS LTDA.",
            "rut": "77.245.318-9",
        },
        "cmf_policy_code": "POL 1 2013 0221",
        "cmf_additional_clause_codes": [
            "CAD 1 2013 0355", "CAD 1 2013 0361", "CAD 1 2013 0388", "CAD 1 2013 0402",
        ],
        "period_start_at": "12:00 horas del 05 de enero de 2027",
        "period_end_at": "12:00 horas del 05 de enero de 2028",
        "currency": "Unidad de Fomento",
        "issue_date": "22 de diciembre de 2026",
        "issue_place": "Santiago",
        # The carátula's own words — 18-12-2026, although the 07 is signed
        # 19-12-2026. A genuine discrepancy, transcribed as printed.
        "source_proposal_date": "18 de diciembre de 2026",
        "source_quote_reference": "HDI-2026-118447 de 09 de diciembre de 2026",
        "mirror_issuance_statement": (
            "Esta póliza se emite en los términos íntegros de la Propuesta de "
            "Emisión de Póliza remitida por el corredor. Las secciones siguientes "
            "reproducen dicha propuesta sin modificación. Toda diferencia que el "
            "corredor detecte en su validación espejo será corregida mediante "
            "endoso sin costo."
        ),
        "locations": list(_LA_FAVORITA_LOCATIONS),
        "insured_amounts": [
            {"item": f"{location} · {item}".lstrip("· ").strip(), "amount_uf": amount}
            for location, item, amount in _LA_FAVORITA_AMOUNTS
        ],
        "total_insured_amount_uf": "77.240,00",
        "coverages": [
            {"number": number, "name": name, "text": limit}
            for number, name, limit in _LA_FAVORITA_COVERAGES
        ],
        "deductibles": [
            {"peril": peril, "offered": initial, "reduced": reduced}
            for peril, initial, reduced in _LA_FAVORITA_DEDUCTIBLES
        ],
        "warranties": [
            {"code": code, "title": title, "requirement": deadline}
            for code, title, deadline in _LA_FAVORITA_WARRANTIES
        ],
        "premium_lines": [
            {"item": item, "basis": f"Monto expuesto UF {base}", "pct": rate,
             "amount_uf": amount}
            for item, base, rate, amount in _LA_FAVORITA_PREMIUM_LINES
        ],
        "premium": dict(_LA_FAVORITA_PREMIUM),
        "commission_pct": "12",
        "commission_uf": "45,84",
        # Printed "4.945‰"; in the document's Chilean notation the dot is a
        # typo for the decimal comma (423,03 / 77.240 ≈ 5,5‰ gross, 4,945‰ net).
        # Passed as a number so the parser cannot read it as four thousand.
        "average_rate_permille": Decimal("4.945"),
        "payment_mode": (
            "Cargo automático en cuenta corriente (PAC), mandato firmado que se "
            "acompaña"
        ),
        "installments": [
            {"number": number, "due_date": due, "gross_amount_uf": amount}
            for number, due, amount in _LA_FAVORITA_INSTALMENTS
        ],
        "extraction_notes": [
            _LA_FAVORITA_TRANSCRIPTION_NOTE,
            "Las condiciones particulares emitidas no consignan modalidad de "
            "aseguramiento, límite de indemnización, base de indemnización ni "
            "territorio (declarados en la §5 de la propuesta), y la carátula "
            "fecha la propuesta de origen el 18-12-2026 cuando el documento 07 "
            "está firmado el 19-12-2026.",
        ],
    }


# =============================================================================
# The importer
# =============================================================================


class ExpedienteImporter:
    """Reads the corpus once, writes seven expedientes in dependency order."""

    def __init__(
        self,
        db: Session,
        source: Path,
        uploader: Uploader,
        log: Log,
        *,
        full: bool = False,
        extract: bool = False,
    ) -> None:
        self.db = db
        self.source = source
        self.uploader = uploader
        self.log = log
        self.full = full
        self.extract = extract
        self.ids = Sequences()

        # Caches, all keyed by the mapping-module keys.
        self._brokers: dict[str, Broker] = {}
        self._users: dict[tuple[int, str], User | None] = {}
        self._insureds: dict[str, Insured] = {}
        self._clients: dict[tuple[str, int], Client] = {}
        self._lines: dict[str, InsuranceLine] = {}
        self._insurers: dict[str, Insurer] = {}
        # (broker_id, slug) -> the broker-private commercial group.
        self._groups: dict[tuple[int, str], AccountGroup] = {}

        # account case_file_id -> {account_group_id, period_start, period_end,
        # period_label}. Post-sale children inherit this from their parent
        # account so the tree never has to join through `policy` for counts.
        self.account_ctx: dict[int, dict[str, Any]] = {}

        # Per-case scratch, rebuilt for each expediente.
        self.files: dict[str, list[CorpusFile]] = {}
        self.docs: dict[str, dict[str, int]] = {}  # case key -> relative path -> doc id
        self.doc_by_code: dict[str, dict[str, int]] = {}  # case key -> code -> doc id
        self.case_id: dict[str, int] = {}
        self.imported: list[str] = []
        self.skipped: list[str] = []
        self.counts: Counter = Counter()

    # -- preconditions ------------------------------------------------------

    def require_fixture_world(self) -> None:
        brokers = int(self.db.scalar(select(func.count()).select_from(Broker)) or 0)
        if brokers == 0:
            raise FixtureError(
                "no broker rows found — run `python -m app.db.import_fixtures` first; "
                "this importer hangs expedientes off the tenants that one creates"
            )
        insurers = int(self.db.scalar(select(func.count()).select_from(Insurer)) or 0)
        if insurers == 0:
            raise FixtureError(
                "no insurer catalog found — run `python -m app.db.import_fixtures` first"
            )
        self.log.ok(f"precondition met: {brokers} broker(s), {insurers} insurer(s) present")

    # -- id allocation ------------------------------------------------------

    def allocate_ids(self) -> None:
        """Every primary key comes from here, before a single row is written."""
        for table in ALLOCATED_TABLES:
            model = _MODEL_BY_TABLE[table]
            top = int(self.db.scalar(select(func.max(model.id))) or 0)
            self.ids.set_base(table, top + 1)

        # Documents get their key from the relative path, so they can be looked
        # up later by the rows that reference them.
        for spec in CASES:
            files = self.scan(spec)
            self.files[spec.key] = files
            self.ids.allocate("document", [f"{spec.key}::{f.relative}" for f in files])

        total = sum(len(v) for v in self.files.values())
        self.log.ok(
            f"ids allocated for {len(ALLOCATED_TABLES)} tables; "
            f"{total} corpus file(s) classified"
        )

    # -- corpus scan --------------------------------------------------------

    def scan(self, spec: CaseSpec) -> list[CorpusFile]:
        """Walk one expediente folder, classify every file, in filing order."""
        root = self.source.joinpath(*spec.folder)
        if not root.is_dir():
            raise FixtureError(f"expediente folder missing: {root}")

        if spec.sections is None:
            wanted: set[CaseSection] | None = None      # every section the folder has
        elif spec.sections == () and self.full:
            wanted = None                               # --full un-skips the lead case
        else:
            wanted = set(spec.sections)

        out: list[CorpusFile] = []
        for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
            if not path.is_file() or path.name in IGNORED_FILE_NAMES:
                continue
            relative = path.relative_to(root).as_posix()
            folder_parts = path.relative_to(root).parts[:-1]
            try:
                section = section_for(folder_parts)
                category, code = classify(section, path.name)
            except (UnknownSection, UnknownCategory) as exc:
                raise FixtureError(f"{spec.key}: {exc}") from exc
            if wanted is not None and section not in wanted:
                continue
            if code is not None and code in spec.exclude_codes:
                continue
            out.append(
                CorpusFile(
                    path=path, relative=relative, section=section, category=category, code=code
                )
            )
        return out

    # -- identity -----------------------------------------------------------

    def broker(self, key: str) -> Broker:
        """Match an EXISTING tenant by normalized trade/legal name. Never by RUT."""
        if key in self._brokers:
            return self._brokers[key]
        identity = BROKER_KEY_BY_NAME[key]
        for row in self.db.scalars(select(Broker)).all():
            haystack = norm_token(f"{row.legal_name} {row.trade_name or ''}")
            if any(token in haystack for token in identity.name_tokens):
                self._brokers[key] = row
                self.log.info(
                    f"broker {key}: reusing tenant #{row.id} {row.legal_name!r} "
                    f"(rut {row.rut}); the corpus prints {identity.corpus_rut} — "
                    "document fact, not tenant identity"
                )
                return row
        # No match: create rather than mis-assign. The corpus RUT is the only one
        # we have and it fails mod-11, so it is stored loosely with a warning.
        rut = loose_rut(identity.corpus_rut)
        self.log.warn(
            f"broker {key}: no existing tenant matched {identity.name_tokens} — creating "
            f"one with the corpus RUT {rut} (which does NOT validate mod-11)"
        )
        row = Broker(
            rut=rut,
            legal_name=identity.fallback_legal_name,
            trade_name=identity.fallback_trade_name,
            cmf_code=normalize_codigo_cmf(rut),
        )
        self.db.add(row)
        self.db.flush()
        self._brokers[key] = row
        return row

    def user(self, broker_id: int, role: str) -> User | None:
        cache_key = (broker_id, role)
        if cache_key not in self._users:
            self._users[cache_key] = self.db.scalars(
                select(User)
                .where(User.broker_id == broker_id, User.role == role)
                .order_by(User.id)
            ).first()
        return self._users[cache_key]

    def insured(self, key: str) -> Insured:
        """Canonical, matched and created BY RUT."""
        if key in self._insureds:
            return self._insureds[key]
        identity = INSURED_BY_KEY[key]
        rut = validate_rut(identity.rut)
        row = self.db.scalars(select(Insured).where(Insured.rut == rut)).first()
        if row is None:
            row = Insured(
                rut=rut,
                person_type=PersonType.LEGAL,
                legal_name=identity.legal_name,
                trade_name=identity.trade_name,
                tax_activity=identity.tax_activity,
                contact_name=identity.contact_name,
                email=identity.email,
                address=identity.address,
                commune=identity.commune,
                region=identity.region,
            )
            self.db.add(row)
            self.db.flush()
            self.counts["insured"] += 1
        self._insureds[key] = row
        return row

    def group(self, broker: Broker, name: str) -> AccountGroup:
        """Get-or-create the broker-private commercial group by ``(broker, slug)``.

        The group is the folder ABOVE the expediente: the label the broker uses
        for one commercial relationship. ``InsuredIdentity.account`` is that
        label, and it is deliberately NOT the legal name — "JO PASTELERÍA" is
        the trade name of Pacto Food SpA, and "GRUPO VIÑA INDOMITA" spans TWO
        RUTs, which is exactly why the group exists.

        The key is the SLUG, never the name: ``import_fixtures``,
        ``scripts/backfill_groups.py`` and the API all use the same rule
        (``expediente_mappings.group_slug``), so a re-import cannot fork a group
        that a backfill already created.
        """
        slug = group_slug(name)
        cache_key = (broker.id, slug)
        if cache_key in self._groups:
            return self._groups[cache_key]
        row = self.db.scalars(
            select(AccountGroup).where(
                AccountGroup.broker_id == broker.id, AccountGroup.slug == slug
            )
        ).first()
        if row is None:
            row = AccountGroup(
                broker_id=broker.id,
                name=name,
                slug=slug,
                status=AccountGroupStatus.ACTIVE,
            )
            self.db.add(row)
            self.db.flush()
            self.log.info(f"account_group created: #{row.id} {name!r} (slug {slug})")
            self.counts["account_group"] += 1
        self._groups[cache_key] = row
        return row

    def client(self, key: str, broker: Broker, executive_id: int | None) -> Client:
        """One `client` row per (broker, insured), attached to its group.

        GRUPO VIÑA INDOMITA is ONE commercial account across TWO RUTs, so it
        yields TWO client rows that share the account name — never one client.
        Both point at the SAME ``account_group``: that is the whole reason the
        group table exists (spec v3 §3.3).
        """
        cache_key = (key, broker.id)
        if cache_key in self._clients:
            return self._clients[cache_key]
        identity = INSURED_BY_KEY[key]
        insured = self.insured(key)
        group = self.group(broker, identity.account)
        row = self.db.scalars(
            select(Client).where(Client.broker_id == broker.id, Client.insured_id == insured.id)
        ).first()
        if row is None:
            row = Client(
                broker_id=broker.id,
                insured_id=insured.id,
                account_group_id=group.id,
                status=ClientStatus.ACTIVE,
                account_manager_id=executive_id,
                # The commercial account the broker files this RUT under.
                source=identity.account,
                sector=identity.tax_activity,
                contact_name=identity.contact_name,
                contact_email=identity.email,
            )
            self.db.add(row)
            self.db.flush()
            self.counts["client"] += 1
        elif row.account_group_id is None:
            # Only ever FILLS a hole — a client someone regrouped by hand keeps
            # the group they were moved to.
            row.account_group_id = group.id
            self.db.flush()
        self._clients[cache_key] = row
        return row

    def add_member(
        self,
        *,
        case_file_id: int,
        broker_id: int,
        client_id: int,
        role: AccountClientRole = AccountClientRole.INSURED,
        is_primary: bool = False,
    ) -> None:
        """One RUT on one account folder (`account_client`), idempotently.

        Membership of an account is ``account_client`` UNION the placements
        pointing at the folder; ``case_file.client_id`` stays the contratante
        and gets the ``is_primary`` row.
        """
        existing = self.db.scalars(
            select(AccountClient).where(
                AccountClient.case_file_id == case_file_id,
                AccountClient.client_id == client_id,
            )
        ).first()
        if existing is not None:
            return
        self.db.add(
            AccountClient(
                broker_id=broker_id,
                case_file_id=case_file_id,
                client_id=client_id,
                role=role,
                is_primary=is_primary,
            )
        )
        self.counts["account_client"] += 1

    def line(self, key: str) -> InsuranceLine:
        if key in self._lines:
            return self._lines[key]
        identity = LINE_BY_KEY[key]
        for row in self.db.scalars(select(InsuranceLine)).all():
            if any(token in norm_token(row.name) for token in identity.name_tokens):
                self._lines[key] = row
                return row
        row = InsuranceLine(
            broker_id=None,  # global, exactly like the seeded six
            name=identity.create_name,
            requires_inspection=identity.requires_inspection,
            is_active=True,
        )
        self.db.add(row)
        self.db.flush()
        self.log.info(f"insurance_line created (global): {identity.create_name}")
        self.counts["insurance_line"] += 1
        self._lines[key] = row
        return row

    def insurer(self, label: str, broker_id: int) -> Insurer:
        """Resolve by normalized cmf_code then rut. NEVER by name."""
        if label in self._insurers:
            return self._insurers[label]
        identity = INSURER_BY_LABEL[label]
        rut = validate_rut(identity.rut)
        cmf_code = normalize_codigo_cmf(rut)
        row = self.db.scalars(select(Insurer).where(Insurer.cmf_code == cmf_code)).first()
        if row is None:
            row = self.db.scalars(select(Insurer).where(Insurer.rut == rut)).first()
        if row is None:
            row = Insurer(
                rut=rut,
                cmf_code=cmf_code,
                legal_name=identity.legal_name,
                is_native=False,
                created_by_broker_id=broker_id,
            )
            self.db.add(row)
            self.db.flush()
            self.log.warn(
                f"insurer {identity.legal_name!r} not in the catalog — created "
                f"is_native=False, created_by_broker_id={broker_id}"
            )
            self.counts["insurer"] += 1
        self._insurers[label] = row
        return row

    # -- money --------------------------------------------------------------

    def check_money(
        self,
        label: str,
        taxable: Decimal | None,
        exempt: Decimal | None,
        net: Decimal | None,
        vat: Decimal | None,
        total: Decimal | None,
    ) -> None:
        """Chilean invariants. VAT is on the TAXABLE part only, never on net."""
        taxable = taxable or Decimal(0)
        exempt = exempt or Decimal(0)
        net = net or Decimal(0)
        vat = vat or Decimal(0)
        total = total or Decimal(0)
        if abs((taxable + exempt) - net) > MONEY_TOLERANCE:
            self.log.warn(f"{label}: net {net} != taxable {taxable} + exempt {exempt}")
        if abs(_round2(VAT_RATE * taxable) - vat) > MONEY_TOLERANCE:
            self.log.warn(f"{label}: vat {vat} != 0.19 x taxable {taxable}")
        if abs((net + vat) - total) > MONEY_TOLERANCE:
            self.log.warn(f"{label}: total {total} != net {net} + vat {vat}")

    # -- row factories ------------------------------------------------------

    def new_case_file(
        self,
        *,
        case_file_id: int,
        broker_id: int,
        client_id: int,
        kind: CaseFileKind,
        stage: CaseStage,
        status: CaseFileStatus,
        reference: str,
        title: str,
        opened_on: date,
        placement_id: int | None = None,
        policy_id: int | None = None,
        parent_case_file_id: int | None = None,
        insurance_line_id: int | None = None,
        sequence_no: int = 1,
        owner_user_id: int | None = None,
        closed_on: date | None = None,
        summary: str | None = None,
        meta: dict[str, Any] | None = None,
        account_group_id: int | None = None,
        period_start: date | None = None,
        period_end: date | None = None,
        origin: CaseOrigin = CaseOrigin.NEW,
        origin_case_file_id: int | None = None,
    ) -> CaseFile:
        row = CaseFile(
            id=case_file_id,
            broker_id=broker_id,
            client_id=client_id,
            placement_id=placement_id,
            policy_id=policy_id,
            parent_case_file_id=parent_case_file_id,
            insurance_line_id=insurance_line_id,
            account_group_id=account_group_id,
            period_start=period_start,
            period_end=period_end,
            # A LABEL over full dates, never a shared range: Coccolino runs
            # Vehículos ago-ago and Incendio abr-abr, both "2026-2027".
            period_label=period_label(period_start, period_end),
            origin=origin,
            origin_case_file_id=origin_case_file_id,
            kind=kind,
            stage=stage,
            status=status,
            reference=reference,
            title=title,
            sequence_no=sequence_no,
            version=1,
            owner_user_id=owner_user_id,
            opened_at=at(opened_on),
            closed_at=at(closed_on, 18) if closed_on else None,
            summary=summary,
            meta=meta,
        )
        self.db.add(row)
        self.counts["case_file"] += 1
        return row

    def timeline(
        self,
        *,
        case_file_id: int,
        broker_id: int,
        user_id: int | None,
        steps: tuple[tuple[CaseStage, str], ...],
    ) -> None:
        """Back-dated stage events + the matching activity row for each."""
        previous: CaseStage | None = None
        for stage, day in steps:
            occurred = at(d(day), 10)
            self.db.add(
                CaseFileStageEvent(
                    id=self.ids.next_id("case_file_stage_event"),
                    broker_id=broker_id,
                    case_file_id=case_file_id,
                    from_stage=previous,
                    to_stage=stage,
                    occurred_at=occurred,
                    user_id=user_id,
                )
            )
            self.db.add(
                Activity(
                    id=self.ids.next_id("activity"),
                    broker_id=broker_id,
                    user_id=user_id,
                    action="case_file.transitioned",
                    entity_type=EntityType.CASE_FILE,
                    entity_id=case_file_id,
                    description=(
                        f"Expediente movido a la etapa {stage.value}"
                        if previous is None
                        else f"Expediente movido de {previous.value} a {stage.value}"
                    ),
                    meta={"from_stage": previous.value if previous else None,
                          "to_stage": stage.value},
                    occurred_at=occurred,
                )
            )
            self.counts["case_file_stage_event"] += 1
            self.counts["activity"] += 1
            previous = stage

    def add_notes(
        self,
        *,
        case_file_id: int,
        broker_id: int,
        authors: list[int | None],
        notes: tuple[tuple[str, str | None], ...],
        entity_type: EntityType = EntityType.CASE_FILE,
    ) -> None:
        for index, (body, follow_up) in enumerate(notes):
            self.db.add(
                Note(
                    id=self.ids.next_id("note"),
                    broker_id=broker_id,
                    entity_type=entity_type,
                    entity_id=case_file_id,
                    author_id=authors[index % len(authors)] if authors else None,
                    body=body,
                    is_internal=True,
                    follow_up_on=d(follow_up) if follow_up else None,
                )
            )
            self.counts["note"] += 1

    # -- documents ----------------------------------------------------------

    def import_documents(self, spec: CaseSpec, case_file_id: int, broker_id: int,
                         uploader_id: int | None) -> None:
        """Every corpus file becomes one `document` filed under the case.

        Key: ``documents/case_file/{id}/{category}-{n}.{ext}`` — the existing
        derivation, unchanged. Binaries are read from ``--source`` and are never
        committed to the repo.
        """
        ordinals: Counter = Counter()
        by_path: dict[str, int] = {}
        by_code: dict[str, int] = {}
        for corpus_file in self.files[spec.key]:
            document_id = self.ids.get("document", f"{spec.key}::{corpus_file.relative}")
            extension = _ext_of(corpus_file.name)
            ordinals[corpus_file.category] += 1
            key = (
                f"documents/{EntityType.CASE_FILE.value}/{case_file_id}/"
                f"{corpus_file.category.value}-{ordinals[corpus_file.category]}.{extension}"
            )
            mime = mimetypes.guess_type(corpus_file.name)[0]
            self.db.add(
                Document(
                    id=document_id,
                    broker_id=broker_id,
                    entity_type=EntityType.CASE_FILE,
                    entity_id=case_file_id,
                    s3_key=key,
                    bucket=self.uploader.bucket,
                    original_name=corpus_file.name,
                    mime_type=mime,
                    size_bytes=corpus_file.path.stat().st_size,
                    category=corpus_file.category,
                    case_file_id=case_file_id,
                    section=corpus_file.section,
                    document_code=corpus_file.code,
                    uploaded_by_id=uploader_id,
                )
            )
            self.uploader.put(corpus_file.path, key, mime)
            by_path[corpus_file.relative] = document_id
            if corpus_file.code:
                by_code.setdefault(corpus_file.code, document_id)
            self.counts["document"] += 1
            self.counts[f"section:{corpus_file.section.value}"] += 1
        self.docs[spec.key] = by_path
        self.doc_by_code[spec.key] = by_code

    def doc(self, case_key: str, code: str) -> int | None:
        """The document id of a corpus code within one expediente (``"08"``)."""
        return self.doc_by_code.get(case_key, {}).get(code)

    # -- the generic half of an expediente ----------------------------------

    def build_case(self, spec: CaseSpec) -> None:
        broker = self.broker(spec.broker_key)
        existing = self.db.scalars(
            select(CaseFile).where(
                CaseFile.broker_id == broker.id, CaseFile.reference == spec.reference
            )
        ).first()
        if existing is not None:
            self.skipped.append(f"{spec.reference} ({spec.key})")
            self.log.info(
                f"{spec.reference}: already present as case #{existing.id} — skipped "
                "(use --reset-cases to rebuild)"
            )
            return

        admin = self.user(broker.id, "broker_admin")
        executive = self.user(broker.id, "broker_executive")
        technician = self.user(broker.id, "broker_technician")
        authors = [u.id if u else None for u in (executive, technician, admin)]
        owner_id = executive.id if executive else (admin.id if admin else None)

        client = self.client(spec.insured_key, broker, executive.id if executive else None)
        line = self.line(spec.line_key)
        identity = INSURED_BY_KEY[spec.insured_key]

        case_file_id = self.ids.next_id("case_file")
        self.case_id[spec.key] = case_file_id

        placement: Placement | None = None
        if spec.with_placement:
            asset = self.db.scalars(
                select(Asset).where(
                    Asset.broker_id == broker.id,
                    Asset.client_id == client.id,
                    Asset.name == spec.asset_name,
                )
            ).first()
            if asset is None:
                asset = Asset(
                    broker_id=broker.id,
                    client_id=client.id,
                    asset_type=spec.asset_type,
                    name=spec.asset_name,
                    address=spec.asset_address,
                    commune=spec.asset_commune,
                    region=spec.asset_region,
                    status=AssetStatus.ACTIVE,
                    activity=identity.tax_activity,
                )
                self.db.add(asset)
                self.db.flush()
                self.counts["asset"] += 1
            placement = Placement(
                broker_id=broker.id,
                client_id=client.id,
                asset_id=asset.id,
                insurance_line_id=line.id,
                period=f"{spec.period_start.year}-{spec.period_end.year}",
                period_start=spec.period_start,
                period_end=spec.period_end,
                status=spec.placement_status,
                case_file_id=case_file_id,
            )
            self.db.add(placement)
            self.db.flush()
            self.counts["placement"] += 1

        meta: dict[str, Any] = {
            "commercial_account": identity.account,
            "corpus_folder": "/".join(spec.folder),
            # Letterhead facts, kept as TEXT. They never create identity rows.
            "broker_letterhead": BROKER_KEY_BY_NAME[spec.broker_key].corpus_legal_name,
            "broker_letterhead_rut": BROKER_KEY_BY_NAME[spec.broker_key].corpus_rut,
        }
        meta.update(spec.meta)

        self.new_case_file(
            case_file_id=case_file_id,
            broker_id=broker.id,
            client_id=client.id,
            kind=CaseFileKind.ACCOUNT,
            stage=spec.stage,
            status=spec.status,
            reference=spec.reference,
            title=spec.title,
            opened_on=spec.opened_on,
            placement_id=placement.id if placement else None,
            insurance_line_id=line.id,
            owner_user_id=owner_id,
            summary=spec.summary,
            meta=meta,
            account_group_id=client.account_group_id,
            # The folder IS the vigencia. Case 1 has no placement, so the
            # CaseSpec is the only place its period can come from.
            period_start=spec.period_start,
            period_end=spec.period_end,
            origin=CaseOrigin.NEW,
        )
        self.db.flush()

        # The contratante's membership row. Every post-sale child of this
        # account inherits the group and the period from here.
        self.add_member(
            case_file_id=case_file_id,
            broker_id=broker.id,
            client_id=client.id,
            role=AccountClientRole.POLICYHOLDER,
            is_primary=True,
        )
        self.account_ctx[case_file_id] = {
            "account_group_id": client.account_group_id,
            "period_start": spec.period_start,
            "period_end": spec.period_end,
        }
        self.db.flush()

        self.import_documents(spec, case_file_id, broker.id, technician.id if technician else None)
        self.db.flush()

        # The technical brief is the placement's brief_document_id (rule 8: a FK,
        # never a raw key).
        if placement is not None:
            brief = self.doc(spec.key, "01")
            if brief is not None:
                placement.brief_document_id = brief

        self.timeline(
            case_file_id=case_file_id,
            broker_id=broker.id,
            user_id=owner_id,
            steps=spec.timeline,
        )
        self.add_notes(
            case_file_id=case_file_id,
            broker_id=broker.id,
            authors=authors,
            notes=spec.notes,
        )

        builder = getattr(self, f"stage_{spec.key}", None)
        if builder is not None:
            builder(
                spec=spec,
                broker=broker,
                client=client,
                line=line,
                placement=placement,
                case_file_id=case_file_id,
                authors=authors,
                owner_id=owner_id,
                technician_id=technician.id if technician else None,
            )
        self.db.flush()
        self.imported.append(f"{spec.reference} {spec.key} -> {spec.stage.value}")

    # -- shared post-sale helpers -------------------------------------------

    def child_case(
        self,
        *,
        spec: CaseSpec,
        broker: Broker,
        client: Client,
        line: InsuranceLine,
        parent_id: int | None,
        policy_id: int | None,
        kind: CaseFileKind,
        stage: CaseStage,
        status: CaseFileStatus,
        suffix: str,
        title: str,
        sequence_no: int,
        opened_on: str,
        closed_on: str | None,
        owner_id: int | None,
        summary: str,
        steps: tuple[tuple[CaseStage, str], ...],
        meta: dict[str, Any] | None = None,
        origin: CaseOrigin = CaseOrigin.NEW,
        origin_case_file_id: int | None = None,
        period_start: date | None = None,
        period_end: date | None = None,
    ) -> int:
        """One child case: post-sale on a policy, or a renewal sibling in time.

        Three axes never mix (spec v3 §1). ``parent_case_file_id`` + ``policy_id``
        is the POST-SALE axis (endoso / cobranza / siniestro under a policy);
        ``origin`` + ``origin_case_file_id`` is the TIME axis (a renewal folder
        is a SIBLING of the prior vigencia, never its child). A renewal
        therefore passes ``parent_id=None, policy_id=None`` and its own period.

        Post-sale children with a parent INHERIT the group and the period of the
        account, so the tree can count them without joining through ``policy``.
        """
        source_id = origin_case_file_id if parent_id is None else parent_id
        inherited = self.account_ctx.get(source_id or 0, {})
        child_id = self.ids.next_id("case_file")
        self.new_case_file(
            case_file_id=child_id,
            broker_id=broker.id,
            client_id=client.id,
            kind=kind,
            stage=stage,
            status=status,
            reference=f"{spec.reference}-{suffix}",
            title=title,
            opened_on=d(opened_on),
            policy_id=policy_id,
            parent_case_file_id=parent_id,
            insurance_line_id=line.id,
            sequence_no=sequence_no,
            owner_user_id=owner_id,
            closed_on=d(closed_on) if closed_on else None,
            summary=summary,
            meta=meta,
            account_group_id=inherited.get("account_group_id"),
            period_start=period_start if period_start is not None
            else inherited.get("period_start"),
            period_end=period_end if period_end is not None
            else inherited.get("period_end"),
            origin=origin,
            origin_case_file_id=origin_case_file_id,
        )
        self.db.flush()
        self.timeline(
            case_file_id=child_id, broker_id=broker.id, user_id=owner_id, steps=steps
        )
        return child_id

    def add_proposal(
        self,
        *,
        label: str,
        broker: Broker,
        quote_request_id: int,
        case_file_id: int,
        insurer_label: str,
        document_id: int | None,
        received_on: str,
        status: ProposalStatus,
        outcome: ProposalOutcome,
        confirmed_by: int | None,
        coverage_start: date,
        coverage_end: date,
        quotation_number: str | None = None,
        cover_mode: str | None = None,
        modality: str | None = None,
        taxable: str | None = None,
        exempt: str | None = None,
        net: str | None = None,
        vat: str | None = None,
        total: str | None = None,
        commission_pct: str | None = None,
        validity_days: int | None = None,
        comprehensive_rate: str | None = None,
        insured_amount_uf: str | None = None,
        deductibles: dict[str, Any] | None = None,
        warranties: str | None = None,
        notes: str | None = None,
    ) -> Proposal:
        """One insurer answer. A declination is a proposal too — with no money."""
        if document_id is None:
            raise FixtureError(f"{label}: no source document — a proposal cannot exist")
        insurer = self.insurer(insurer_label, broker.id)
        taxable_d, exempt_d = uf(taxable), uf(exempt)
        net_d, vat_d, total_d = uf(net), uf(vat), uf(total)
        if total_d is not None:
            self.check_money(label, taxable_d, exempt_d, net_d, vat_d, total_d)

        taxable_rate = exempt_rate = None
        amount = uf(insured_amount_uf)
        if amount and amount > 0 and taxable_d is not None and exempt_d is not None:
            taxable_rate = _round2(taxable_d / amount * 1000)
            exempt_rate = _round2(exempt_d / amount * 1000)

        row = Proposal(
            broker_id=broker.id,
            quote_request_id=quote_request_id,
            case_file_id=case_file_id,
            insurer_id=insurer.id,
            origin=ProposalOrigin.NATIVE if insurer.is_native else ProposalOrigin.EXTERNAL,
            source_document_id=document_id,
            quotation_number=quotation_number,
            cover_mode=cover_mode,
            modality=modality,
            taxable_premium_uf=taxable_d,
            exempt_premium_uf=exempt_d,
            net_premium_uf=net_d,
            vat_uf=vat_d,
            total_premium_uf=total_d,
            taxable_rate_permille=taxable_rate,
            exempt_rate_permille=exempt_rate,
            comprehensive_rate_permille=uf(comprehensive_rate),
            commission_pct=uf(commission_pct),
            validity_business_days=validity_days,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            received_at=d(received_on),
            deductibles=deductibles,
            warranties=warranties,
            notes=notes,
            status=status,
            outcome=outcome,
            # Historical records the broker already worked through by hand: they
            # arrive confirmed, with no AI extraction standing behind them.
            is_confirmed=True,
            confirmed_by_id=confirmed_by,
            confirmed_at=at(d(received_on), 16),
        )
        self.db.add(row)
        self.db.flush()
        self.counts["proposal"] += 1
        return row

    def add_quote_request(
        self,
        *,
        broker: Broker,
        placement: Placement,
        case_file_id: int,
        insured_object: str,
        declared_value_uf: str | None,
        desired_start: date,
        desired_end: date,
        sent_on: str,
        due_on: str,
        recipients: tuple[str, ...],
        status: QuoteRequestStatus,
        created_by: int | None,
        round_no: int = 1,
        requested_coverages: str | None = None,
        line_items: tuple[tuple[str, str, str | None], ...] = (),
    ) -> QuoteRequest:
        row = QuoteRequest(
            broker_id=broker.id,
            placement_id=placement.id,
            case_file_id=case_file_id,
            round_no=round_no,
            insured_object=insured_object,
            declared_value_uf=uf(declared_value_uf),
            currency="UF",
            requested_coverages=requested_coverages,
            desired_start=desired_start,
            desired_end=desired_end,
            sent_at=at(d(sent_on), 11),
            due_at=datetime.combine(d(due_on), time(23, 59), tzinfo=timezone.utc),
            priority=Priority.HIGH,
            status=status,
            # Send-time SNAPSHOT of who the carpeta actually went to.
            recipient_insurer_ids=[
                self.insurer(label, broker.id).id for label in recipients
            ],
            created_by_id=created_by,
        )
        self.db.add(row)
        self.db.flush()
        for index, (name, value, detail) in enumerate(line_items):
            self.db.add(
                QuoteLineItem(
                    quote_request_id=row.id,
                    name=name,
                    value_uf=uf(value),
                    detail=detail,
                    sort_order=index,
                )
            )
            self.counts["quote_line_item"] += 1
        self.counts["quote_request"] += 1
        return row

    def add_pack(
        self,
        *,
        broker_id: int,
        case_file_id: int,
        kind: PackKind,
        section: CaseSection,
        pdf_document_id: int | None,
        generated_on: str,
        summary: str,
        generated_by: int | None,
        recipients: list[dict[str, Any]] | None = None,
    ) -> None:
        self.db.add(
            CasePack(
                id=self.ids.next_id("case_pack"),
                broker_id=broker_id,
                case_file_id=case_file_id,
                kind=kind,
                section=section,
                status=PackStatus.GENERATED,
                pdf_document_id=pdf_document_id,
                summary=summary,
                summary_model=None,
                summary_prompt_version=None,
                # Written by the broker, not the model: already confirmed.
                is_summary_confirmed=True,
                recipients=recipients,
                generated_at=at(d(generated_on), 15),
                generated_by_id=generated_by,
            )
        )
        self.counts["case_pack"] += 1

    # =========================================================================
    # 1. JO PASTELERIA · ACCIDENTES PERSONALES -> `lead`
    # =========================================================================

    def stage_jo_ap(self, *, spec, broker, client, line, placement, case_file_id,
                    authors, owner_id, technician_id) -> None:
        """A data point, not a carpeta: one `sales_lead`, no files, one follow-up."""
        lead_id = self.ids.next_id("sales_lead")
        self.db.add(
            SalesLead(
                id=lead_id,
                broker_id=broker.id,
                name="JO Pastelería — Accidentes Personales colectivo",
                rut=validate_rut(INSURED_BY_KEY["pacto_food"].rut),
                contact_name="Josefina Ortúzar Bulnes",
                contact_email=INSURED_BY_KEY["pacto_food"].email,
                contact_phone="+56 9 8871 4402",
                source="referral",
                insurance_line_id=line.id,
                # "crear grupo -> crear cuenta" starts at the lead: convert
                # forwards both the group and the period into the folder.
                account_group_id=client.account_group_id,
                period_start=spec.period_start,
                period_end=spec.period_end,
                # 11 trabajadores x UF 850 de capital, tasa de mercado del ramo.
                estimated_premium_uf=uf("38"),
                status=LeadStatus.QUALIFIED,
                follow_up_on=d("2026-05-15"),
                owner_user_id=owner_id,
                summary=(
                    "Cuenta ya existente por RC. Pide revisar accidentes personales: "
                    "nómina desactualizada (6 declarados contra 11 con contrato), "
                    "cúmulo por evento de UF 500 que vacía el capital individual de "
                    "UF 850, y clasificación de riesgo de restaurant en una cocina de "
                    "producción con hornos, amasadoras y freidora industrial."
                ),
            )
        )
        self.counts["sales_lead"] += 1
        row = self.db.get(CaseFile, case_file_id)
        if row is not None:
            meta = dict(row.meta or {})
            meta["sales_lead_id"] = lead_id
            row.meta = meta

    # =========================================================================
    # 2. COCCOLINO · VEHICULOS -> `intake`
    # =========================================================================

    def stage_coccolino_fleet(self, *, spec, broker, client, line, placement,
                              case_file_id, authors, owner_id, technician_id) -> None:
        """Antecedentes arriving. The 00E becomes a real `inspection` row."""
        inspector = self.user(broker.id, "broker_inspector")
        self.db.add(
            Inspection(
                broker_id=broker.id,
                asset_id=placement.asset_id,
                case_file_id=case_file_id,
                inspector_id=inspector.id if inspector else None,
                version=1,
                status=InspectionStatus.ISSUED,
                visit_date=d("2026-07-08"),
                report_date=d("2026-07-10"),
                folio="2026000512339",
                findings_summary=(
                    "Flota de 3 furgones de reparto. Nota global 71/100, clasificación "
                    "insatisfactoria: el puntaje lo arrastra el perfil de conductores y "
                    "la ausencia de un procedimiento de conducción, no el estado de los "
                    "vehículos, que tienen revisión técnica y permisos al día."
                ),
                overall_score=uf("71"),
                risk_classification="Insatisfactorio — mejorable con medidas de gestión",
                report_document_id=self.doc(spec.key, "00E"),
            )
        )
        self.counts["inspection"] += 1

    # =========================================================================
    # 3. JO PASTELERIA · RESPONSABILIDAD CIVIL -> `market_submission`
    # =========================================================================

    def stage_jo_rc(self, *, spec, broker, client, line, placement, case_file_id,
                    authors, owner_id, technician_id) -> None:
        """Carpeta sent, nothing back yet: a quote request with a real addressee list."""
        self.add_quote_request(
            broker=broker,
            placement=placement,
            case_file_id=case_file_id,
            insured_object=(
                "Responsabilidad civil por ocurrencia de PACTO FOOD SPA (JO PASTELERÍA): "
                "cocina de producción, dos locales de atención de público y reparto propio."
            ),
            declared_value_uf=None,
            desired_start=spec.period_start,
            desired_end=spec.period_end,
            # Addressees read off the 02 carta de remisión of 04-05-2026.
            sent_on="2026-05-04",
            due_on="2026-05-24",
            recipients=("unnio", "chubb", "hdi"),
            status=QuoteRequestStatus.SENT,
            created_by=owner_id,
            requested_coverages=(
                "RC general y de explotación; RC por productos elaborados con ventana de "
                "manifestación de 60 días; carve-back expreso de la exclusión LMA 5394 "
                "para la intoxicación o infección alimentaria de origen bacteriano, "
                "parasitario o toxínico; RC patronal; RC cruzada; gastos de defensa."
            ),
        )

    # =========================================================================
    # 4. GRUPO VIÑA INDOMITA · VIÑA SANTA ALICIA -> `comparison`
    # =========================================================================

    def stage_santa_alicia(self, *, spec, broker, client, line, placement,
                           case_file_id, authors, owner_id, technician_id) -> None:
        """Three TRBF offers on the table and the comparativo built."""
        quote = self.add_quote_request(
            broker=broker,
            placement=placement,
            case_file_id=case_file_id,
            insured_object=(
                "Todo Riesgo Bienes Físicos con Perjuicio por Paralización — planta y "
                "bodegas de VIÑA SANTA ALICIA SPA, Pirque."
            ),
            declared_value_uf=None,
            desired_start=spec.period_start,
            desired_end=spec.period_end,
            sent_on="2026-08-31",
            due_on="2026-09-20",
            recipients=("southbridge", "mapfre", "hdi"),
            status=QuoteRequestStatus.RECEIVING,
            created_by=owner_id,
            requested_coverages=(
                "46 coberturas nominadas bajo modalidad Todo Riesgo Bienes Físicos, a "
                "full value y sin límite máximo de indemnización, con perjuicio por "
                "paralización a 12 meses y sin coaseguro sísmico."
            ),
        )
        common = dict(
            broker=broker,
            quote_request_id=quote.id,
            case_file_id=case_file_id,
            confirmed_by=technician_id,
            coverage_start=spec.period_start,
            coverage_end=spec.period_end,
            outcome=ProposalOutcome.QUOTED,
            cover_mode="Todo Riesgo Bienes Físicos",
            modality="TRBF — todo daño material súbito e imprevisto salvo lo expresamente excluido",
        )
        # Figures transcribed from 03/04/05 and cross-checked against the 06.
        self.add_proposal(
            label="santa_alicia/southbridge",
            document_id=self.doc(spec.key, "03"),
            insurer_label="southbridge",
            quotation_number="SB-TRBF-2026-04417",
            received_on="2026-09-14",
            status=ProposalStatus.SUBMITTED,
            taxable="510.16", exempt="709.79", net="1219.95", vat="96.93", total="1316.88",
            commission_pct="10", validity_days=30, comprehensive_rate="2.619",
            notes=(
                "Acepta las 46 coberturas solicitadas. Full value sin límite máximo de "
                "indemnización. Reposición a nuevo en edificios hasta 10 años."
            ),
            **common,
        )
        self.add_proposal(
            label="santa_alicia/mapfre",
            document_id=self.doc(spec.key, "04"),
            insurer_label="mapfre",
            quotation_number="MAP-TR-2026-11208",
            received_on="2026-09-16",
            status=ProposalStatus.SUBMITTED,
            taxable="509.32", exempt="690.04", net="1199.36", vat="96.77", total="1296.13",
            commission_pct="10", validity_days=20, comprehensive_rate="2.765",
            notes=(
                "Condicionada a inspección previa de la compañía y a la ejecución de las "
                "obras de anclaje antes de la emisión. Mantiene coaseguro sísmico y "
                "reduce el período de paralización a 12 meses."
            ),
            **common,
        )
        self.add_proposal(
            label="santa_alicia/hdi",
            document_id=self.doc(spec.key, "05"),
            insurer_label="hdi",
            quotation_number="HDI-TRBF-2026-0771",
            received_on="2026-09-17",
            status=ProposalStatus.SUBMITTED,
            taxable="416.40", exempt="624.61", net="1041.01", vat="79.12", total="1120.13",
            commission_pct="10", validity_days=20, comprehensive_rate="2.357",
            notes=(
                "La más barata en prima bruta y en tasa media, con límite máximo de "
                "indemnización de UF 250.000 por evento — el 68% de los bienes físicos "
                "declarados. Sujeta a inspección previa."
            ),
            **common,
        )
        self.add_pack(
            broker_id=broker.id,
            case_file_id=case_file_id,
            kind=PackKind.COMPARISON,
            section=CaseSection.INSURER_QUOTES,
            pdf_document_id=self.doc(spec.key, "06"),
            generated_on="2026-09-24",
            generated_by=owner_id,
            summary=(
                "Tres ofertas TRBF comparadas ítem por ítem: 41 extensiones y 16 "
                "deducibles. Se recomienda Southbridge, cotización SB-TRBF-2026-04417, "
                "prima bruta UF 1.316,88. Es la única que acepta las 46 coberturas "
                "solicitadas a full value y sin límite máximo de indemnización. HDI "
                "aparece más barata (2,357‰) recortando el límite a UF 250.000; Mapfre "
                "tiene la tasa más alta y aun así mantiene coaseguro sísmico."
            ),
        )

    # =========================================================================
    # 5. COCCOLINO · MULTIRRIESGO -> `proposal_issued`
    # =========================================================================

    def stage_coccolino_multi(self, *, spec, broker, client, line, placement,
                              case_file_id, authors, owner_id, technician_id) -> None:
        """Winner accepted, siblings rejected, quote closed, placement awarded."""
        quote = self.add_quote_request(
            broker=broker,
            placement=placement,
            case_file_id=case_file_id,
            insured_object=(
                "Incendio y Riesgos Adicionales bajo Riesgos Nominados — obrador, horno "
                "y local de atención de COCCOLINO PASTELERÍA SPA, Concón."
            ),
            declared_value_uf="30720",
            desired_start=spec.period_start,
            desired_end=spec.period_end,
            sent_on="2026-02-27",
            due_on="2026-03-18",
            recipients=("bci", "consorcio", "hdi"),
            status=QuoteRequestStatus.CLOSED,
            created_by=owner_id,
            requested_coverages=(
                "47 coberturas nominadas, a full value y sin límite máximo de "
                "indemnización, con perjuicio por paralización a 12 meses y rotura de "
                "maquinaria frigorífica con franquicia de 4 horas."
            ),
            line_items=(
                ("Bienes físicos — edificio, instalaciones, maquinaria y existencias",
                 "17920", "Valor de reposición determinado"),
                ("Perjuicio por paralización — 12 meses",
                 "12800", "Margen de contribución y gastos fijos"),
            ),
        )
        common = dict(
            broker=broker,
            quote_request_id=quote.id,
            case_file_id=case_file_id,
            confirmed_by=technician_id,
            coverage_start=spec.period_start,
            coverage_end=spec.period_end,
            outcome=ProposalOutcome.QUOTED,
            commission_pct="12",
        )
        # Figures from the 06 comparativo, which reconciles the four offers.
        self.add_proposal(
            label="coccolino_multi/bci",
            document_id=self.doc(spec.key, "03"),
            insurer_label="bci",
            quotation_number="IN-2026-0071845",
            received_on="2026-03-09",
            status=ProposalStatus.REJECTED,
            cover_mode="Riesgos Nominados",
            modality="RIESGOS NOMINADOS — Póliza de Incendio con coberturas adicionales",
            taxable="58.65", exempt="33.28", net="91.93", vat="11.14", total="103.07",
            validity_days=20, comprehensive_rate="3.78", insured_amount_uf="24320",
            notes=(
                "Otorga 31 de las 47 coberturas y limita la indemnización a UF 12.000 "
                "por evento para el conjunto de ubicaciones. Paralización a 6 meses."
            ),
            **common,
        )
        self.add_proposal(
            label="coccolino_multi/consorcio",
            document_id=self.doc(spec.key, "04"),
            insurer_label="consorcio",
            quotation_number="CN-INC-2026-08841",
            received_on="2026-03-10",
            status=ProposalStatus.REJECTED,
            cover_mode="Riesgos Nominados",
            modality="RIESGOS NOMINADOS — Póliza de Incendio con coberturas adicionales",
            taxable="65.50", exempt="37.66", net="103.16", vat="12.45", total="115.60",
            validity_days=25, comprehensive_rate="3.75", insured_amount_uf="27520",
            notes="Otorga 44 de las 47 coberturas, a full value y sin inspección previa. "
                  "Paralización a 9 meses.",
            **common,
        )
        winner = self.add_proposal(
            label="coccolino_multi/hdi-nominados",
            document_id=self.doc(spec.key, "05"),
            insurer_label="hdi",
            quotation_number="HDI-INC-2026-0338",
            received_on="2026-03-11",
            status=ProposalStatus.ACCEPTED,
            cover_mode="Riesgos Nominados",
            modality="RIESGOS NOMINADOS — Póliza de Incendio con coberturas adicionales",
            taxable="72.81", exempt="43.27", net="116.08", vat="13.83", total="129.91",
            validity_days=30, comprehensive_rate="3.78", insured_amount_uf="30720",
            warranties=(
                "Mantención anual certificada de instalaciones eléctricas con "
                "termografía; permiso escrito de trabajo en caliente; extintores y "
                "detección operativos durante toda la vigencia."
            ),
            notes=(
                "Acepta las 47 coberturas sin desviación, a full value y sin límite "
                "máximo de indemnización, con paralización a 12 meses. Única oferta con "
                "rotura de maquinaria frigorífica con franquicia de 4 horas."
            ),
            **common,
        )
        self.add_proposal(
            label="coccolino_multi/hdi-trbf",
            document_id=self.doc(spec.key, "05B"),
            insurer_label="hdi",
            quotation_number="HDI-TRBF-2026-0339",
            received_on="2026-03-11",
            status=ProposalStatus.REJECTED,
            cover_mode="Todo Riesgo Bienes Físicos",
            modality="TRBF — todo daño material súbito e imprevisto salvo lo excluido",
            taxable="82.66", exempt="43.27", net="125.93", vat="15.71", total="141.64",
            validity_days=30, comprehensive_rate="4.10", insured_amount_uf="30720",
            notes=(
                "Alternativa Todo Riesgo sobre la misma carpeta. UF 11,73 más de prima "
                "bruta por eliminar la discusión probatoria en siniestro. Se presenta "
                "como decisión de la asegurada, no como recomendación del corredor."
            ),
            **common,
        )
        self.add_pack(
            broker_id=broker.id,
            case_file_id=case_file_id,
            kind=PackKind.COMPARISON,
            section=CaseSection.INSURER_QUOTES,
            pdf_document_id=self.doc(spec.key, "06"),
            generated_on="2026-03-16",
            generated_by=owner_id,
            summary=(
                "Cuatro alternativas comparadas. BCI y HDI Nominados tienen exactamente "
                "la misma tasa media, 3,78‰: por el mismo precio unitario de riesgo HDI "
                "expone UF 30.720 y otorga 47 coberturas, BCI expone UF 24.320 con "
                "límite de UF 12.000 y otorga 31. Se recomienda HDI Riesgos Nominados, "
                "cotización HDI-INC-2026-0338, prima bruta UF 129,91."
            ),
        )
        self.add_pack(
            broker_id=broker.id,
            case_file_id=case_file_id,
            kind=PackKind.PROPOSAL,
            section=CaseSection.BROKER_PROPOSAL,
            pdf_document_id=self.doc(spec.key, "07"),
            generated_on="2026-03-24",
            generated_by=owner_id,
            summary=(
                "Propuesta de emisión de póliza remitida a HDI Seguros S.A. sobre la "
                "cotización HDI-INC-2026-0338, con vigencia solicitada desde las 12:00 "
                "del 01-04-2026. Cláusula espejo: toda diferencia detectada en la "
                "validación se corrige por endoso sin costo."
            ),
            recipients=[
                {
                    "insurer_id": self.insurer("hdi", broker.id).id,
                    "legal_name": INSURER_BY_LABEL["hdi"].legal_name,
                    "is_native": self.insurer("hdi", broker.id).is_native,
                    "resolution_level": "line",
                }
            ],
        )
        _ = winner

    def move_docs(self, case_key: str, codes: tuple[str, ...], case_file_id: int) -> None:
        """Re-file specific corpus codes onto a post-sale child case."""
        for code in codes:
            document_id = self.doc(case_key, code)
            if document_id is None:
                continue
            row = self.db.get(Document, document_id)
            if row is not None:
                row.case_file_id = case_file_id
                row.entity_id = case_file_id

    def add_installments(
        self,
        *,
        broker_id: int,
        plan_id: int,
        rows: tuple[tuple[int, str, str, str, str | None, str, str | None, int | None], ...],
        endorsement_id_by_coupon: dict[str, int] | None = None,
    ) -> Decimal:
        """``(number, coupon, due, gross, paid_on, status, note, days_late)`` -> rows.

        Returns the summed gross so the caller can assert the corpus invariant:
        Σ instalments == policy gross premium + Σ endorsement premium deltas.
        """
        total = Decimal(0)
        for number, coupon, due, gross, paid_on, status, note, days_late in rows:
            amount = uf(gross) or Decimal(0)
            total += amount
            self.db.add(
                CollectionInstallment(
                    id=self.ids.next_id("collection_installment"),
                    broker_id=broker_id,
                    collection_plan_id=plan_id,
                    number=number,
                    coupon_number=coupon,
                    due_date=d(due),
                    gross_amount_uf=amount,
                    paid_on=d(paid_on) if paid_on else None,
                    days_late=days_late,
                    status=InstallmentStatus(status),
                    endorsement_id=(endorsement_id_by_coupon or {}).get(coupon),
                    note=note,
                )
            )
            self.counts["collection_installment"] += 1
        return total

    def check_collection_sum(
        self, label: str, instalment_total: Decimal, gross: Decimal, deltas: Decimal
    ) -> None:
        expected = gross + deltas
        if abs(instalment_total - expected) > MONEY_TOLERANCE:
            self.log.warn(
                f"{label}: Σ instalments {instalment_total} != policy gross {gross} "
                f"+ Σ endorsement deltas {deltas} (= {expected})"
            )
        else:
            self.log.info(
                f"{label}: Σ instalments {instalment_total} == gross {gross} + deltas {deltas}"
            )

    # =========================================================================
    # 6. GRUPO VIÑA INDOMITA · VIÑA INDOMITA -> `active`
    # =========================================================================

    def stage_vina_indomita(self, *, spec, broker, client, line, placement,
                            case_file_id, authors, owner_id, technician_id) -> None:
        """Full chain: policy 0020119904 + collection + E1/E2 + claim SIN-2026-0417."""
        policy_id = self.ids.next_id("policy")

        quote = self.add_quote_request(
            broker=broker,
            placement=placement,
            case_file_id=case_file_id,
            insured_object=(
                "Todo Riesgo Bienes Físicos con Terremoto — planta de vinificación, "
                "guarda y embotellado de VIÑA INDÓMITA SPA, Casablanca, y bodega de "
                "apoyo en Cabrero."
            ),
            declared_value_uf="475256",
            desired_start=spec.period_start,
            desired_end=spec.period_end,
            sent_on="2025-09-22",
            due_on="2025-10-03",
            recipients=("southbridge", "hdi", "mapfre"),
            status=QuoteRequestStatus.CLOSED,
            created_by=owner_id,
            requested_coverages=(
                "41 extensiones y 16 deducibles bajo modalidad Todo Riesgo Bienes "
                "Físicos con cláusula adicional de terremoto, a full value, con "
                "rehabilitación automática de montos y cláusula de inalterabilidad a "
                "favor del acreedor prendario."
            ),
            line_items=(
                ("Edificios e instalaciones fijas", "96473", "Ubicaciones 1 y 2"),
                ("Maquinaria y equipos", "35483", "Ubicaciones 1 y 2"),
                ("Muebles, útiles y equipo computacional", "1644", "Ubicación 1"),
                ("Cubas y barricas", "95223", "Ubicación 1"),
                ("Existencias", "246433", "Ubicación 1"),
            ),
        )
        common = dict(
            broker=broker,
            quote_request_id=quote.id,
            case_file_id=case_file_id,
            confirmed_by=technician_id,
            coverage_start=spec.period_start,
            coverage_end=spec.period_end,
            outcome=ProposalOutcome.QUOTED,
            insured_amount_uf="475256",
        )
        self.add_proposal(
            label="vina_indomita/southbridge",
            document_id=self.doc(spec.key, "03"),
            insurer_label="southbridge",
            quotation_number="SB-2025-14872",
            received_on="2025-09-30",
            status=ProposalStatus.ACCEPTED,
            cover_mode="Todo Riesgo Bienes Físicos con Terremoto",
            modality="Todo riesgo — cobertura comprensiva, delimitada por exclusiones",
            taxable="439.14", exempt="658.70", net="1097.84", vat="83.44", total="1181.28",
            commission_pct="10", validity_days=30, comprehensive_rate="2.310",
            warranties=(
                "G-1 robo · G-2 medidas de protección declaradas · G-3 trabajos en "
                "caliente · G-4 paneles compuestos combustibles · G-5 mantención "
                "eléctrica · G-6 no variación del riesgo · G-7 inspección."
            ),
            notes=(
                "Gana 41 de 41 extensiones y 15 de 16 deducibles. Full value sin límite "
                "máximo. Coaseguro sísmico del 25% con 90 días de plazo para acreditar "
                "el diseño asísmico, contra 25% desde el inicio de las otras dos."
            ),
            **common,
        )
        self.add_proposal(
            label="vina_indomita/hdi",
            document_id=self.doc(spec.key, "04"),
            insurer_label="hdi",
            quotation_number="HDI-CO-2025-3391",
            received_on="2025-10-01",
            status=ProposalStatus.REJECTED,
            cover_mode="Todo Riesgo Bienes Físicos con Terremoto y PxP",
            modality="Todo riesgo — cobertura comprensiva, delimitada por exclusiones",
            taxable="522.78", exempt="919.13", net="1441.91", vat="99.33", total="1541.24",
            commission_pct="10", validity_days=30,
            notes=(
                "Única oferta que resuelve la brecha B-1 incorporando perjuicio por "
                "paralización (monto expuesto UF 565.256). UF 359,96 más cara, de los "
                "cuales solo UF 220,50 corresponden al PxP."
            ),
            **common,
        )
        self.add_proposal(
            label="vina_indomita/mapfre",
            document_id=self.doc(spec.key, "05"),
            insurer_label="mapfre",
            quotation_number="1287553",
            received_on="2025-10-01",
            status=ProposalStatus.REJECTED,
            cover_mode="Todo Riesgo Bienes Físicos",
            modality="Todo riesgo con límite máximo de indemnización",
            taxable="419.18", exempt="578.86", net="998.04", vat="79.64", total="1077.68",
            commission_pct="8", validity_days=20,
            notes=(
                "La más barata en prima bruta, con límite de indemnización de UF 300.000 "
                "sobre bienes de UF 475.256 — el 63% del valor. Gana 0 de 41 extensiones."
            ),
            **common,
        )
        self.add_pack(
            broker_id=broker.id, case_file_id=case_file_id, kind=PackKind.COMPARISON,
            section=CaseSection.INSURER_QUOTES, pdf_document_id=self.doc(spec.key, "06"),
            generated_on="2025-10-06", generated_by=owner_id,
            summary=(
                "41 extensiones y 16 deducibles comparados ítem por ítem. Score técnico "
                "RADAL: Southbridge 84, HDI 79, Mapfre 48. Se recomienda adjudicar a "
                "Southbridge por UF 1.181,28 e incorporar el perjuicio por paralización "
                "mediante endoso durante la vigencia, a la tasa de la póliza."
            ),
        )
        self.add_pack(
            broker_id=broker.id, case_file_id=case_file_id, kind=PackKind.PROPOSAL,
            section=CaseSection.BROKER_PROPOSAL, pdf_document_id=self.doc(spec.key, "07"),
            generated_on="2025-10-08", generated_by=owner_id,
            summary=(
                "Propuesta de emisión remitida a Southbridge sobre la cotización "
                "SB-2025-14872, vigencia desde las 12:00 del 15-10-2025."
            ),
        )

        # --- the policy -----------------------------------------------------
        self.db.add(
            Policy(
                id=policy_id,
                broker_id=broker.id,
                client_id=client.id,
                asset_id=placement.asset_id,
                placement_id=placement.id,
                insurer_id=self.insurer("southbridge", broker.id).id,
                insurance_line_id=line.id,
                case_file_id=case_file_id,
                source_document_id=self.doc(spec.key, "08"),
                policy_number="0020119904",
                start_date=spec.period_start,
                end_date=spec.period_end,
                period_start_at=noon(spec.period_start),
                period_end_at=noon(spec.period_end),
                issued_at=d("2025-10-13"),
                status=PolicyStatus.ACTIVE,
                cover_mode="Todo Riesgo Bienes Físicos con Terremoto",
                insured_amount_semantics=(
                    "Full value sobre el valor de reposición a nuevo declarado por "
                    "materia asegurada y ubicación; no es suma de límites de cobertura."
                ),
                average_rate_permille=uf("2.21"),
                indemnity_limit=(
                    "Full value UF 497.256 — sin límite máximo de indemnización. "
                    "Cláusula de inalterabilidad a favor del acreedor prendario por "
                    "UF 203.544 sobre edificios, maquinaria de proceso y cubas de la "
                    "ubicación 1."
                ),
                insured_amount_uf=uf("475256"),
                taxable_premium_uf=uf("439.14"),
                exempt_premium_uf=uf("658.70"),
                net_premium_uf=uf("1097.84"),
                vat_uf=uf("83.44"),
                total_premium_uf=uf("1181.28"),
                commission_pct=uf("10"),
                notes=(
                    "Emitida en espejo de la propuesta del corredor de 08-10-2025 sobre "
                    "la cotización SB-2025-14872 del 30-09-2025."
                ),
            )
        )
        self.counts["policy"] += 1
        self.check_money("vina_indomita/policy", uf("439.14"), uf("658.70"),
                         uf("1097.84"), uf("83.44"), uf("1181.28"))
        for name, address, commune, region, amount in (
            ("Ubicación 1 — Planta de vinificación, guarda y embotellado",
             "Lote B, Parcela 12B2", "Casablanca", "Región de Valparaíso", "474004"),
            ("Ubicación 2 — Bodega de apoyo y almacenamiento",
             "Fundo Santa Carla, Bodega Quinel, Ruta Q 50-O", "Cabrero", "Región del Biobío",
             "1252"),
        ):
            self.db.add(
                PolicyLocation(
                    policy_id=policy_id, name=name, address=address, commune=commune,
                    region=region, insured_amount_uf=uf(amount),
                )
            )
            self.counts["policy_location"] += 1

        # --- warranties G-1..G-7, verbatim from section 9 of the policy ------
        warranties = (
            ("G-1", "Garantía de robo",
             "Es condición para la indemnización por robo que cada recinto asegurado "
             "mantenga plenamente operativas al menos DOS de las siguientes medidas: "
             "rejas en ventanas y claraboyas con chapas de seguridad; cuidador o "
             "vigilante 24 horas; cortinas metálicas hacia la vía pública con candados "
             "protegidos; alarma conectada a central de monitoreo."),
            ("G-2", "Medidas de protección declaradas",
             "Mantener plenamente operativos durante toda la vigencia los sistemas de "
             "protección y elementos de combate de incendio informados en el informe de "
             "inspección, y comunicar toda modificación, retiro o falla dentro de 48 horas."),
            ("G-3", "Trabajos en caliente",
             "Todo trabajo de soldadura, oxicorte o esmerilado requiere permiso escrito, "
             "retiro o protección de material combustible en 10 metros, extintor operativo "
             "y vigilancia posterior del área durante al menos 60 minutos."),
            ("G-4", "Paneles compuestos combustibles",
             "Programa de inspección periódica con reemplazo de paneles dañados dentro de "
             "30 días; prohibición absoluta de soldar, cortar o esmerilar sobre ellos; "
             "sellado de bordes expuestos; detección de humo con transmisión de alarma."),
            ("G-5", "Mantención de instalaciones eléctricas",
             "Mantención anual certificada de tableros e instalaciones eléctricas y "
             "termografía anual con informe escrito disponible para la compañía."),
            ("G-6", "No variación del riesgo",
             "Los términos ofrecidos quedan sujetos a la no ocurrencia de nuevos "
             "siniestros y a la no alteración de la información proporcionada. Toda "
             "modificación material se comunica dentro de 5 días hábiles."),
            ("G-7", "Inspección",
             "La compañía podrá inspeccionar las ubicaciones en cualquier momento y se "
             "reserva el derecho de modificar los términos o cancelar con aviso de 30 días."),
        )
        for order, (code, title, requirement) in enumerate(warranties):
            self.db.add(
                Warranty(
                    id=self.ids.next_id("warranty"),
                    broker_id=broker.id,
                    policy_id=policy_id,
                    case_file_id=case_file_id,
                    code=code,
                    title=title,
                    requirement=requirement,
                    source=WarrantySource.UNDERWRITING_WARRANTY,
                    is_permanent=True,
                    is_suspensive=code in {"G-1", "G-2"},
                    status=WarrantyStatus.IN_PROGRESS,
                    sort_order=order,
                )
            )
            self.counts["warranty"] += 1

        # --- E1 / E2 --------------------------------------------------------
        e1_case = self.child_case(
            spec=spec, broker=broker, client=client, line=line, parent_id=case_file_id,
            policy_id=policy_id, kind=CaseFileKind.ENDORSEMENT,
            stage=CaseStage.ENDORSEMENT_APPLIED, status=CaseFileStatus.CLOSED,
            suffix="E1", sequence_no=1,
            title="Endoso E1 · Inclusión de bienes y aumento de suma asegurada",
            opened_on="2026-03-12", closed_on="2026-03-20", owner_id=owner_id,
            summary=(
                "Línea de embotellado Bertolaso Monobloc 24-24-6 (UF 9.400) y 180 "
                "barricas de roble francés Radoux (UF 12.600), UF 22.000 en total, al "
                "amparo de la cláusula de incorporación automática de nuevos bienes."
            ),
            steps=(
                (CaseStage.ENDORSEMENT_REQUESTED, "2026-03-12"),
                (CaseStage.ENDORSEMENT_PROPOSED, "2026-03-12"),
                (CaseStage.ENDORSEMENT_ISSUED, "2026-03-18"),
                (CaseStage.ENDORSEMENT_APPLIED, "2026-03-20"),
            ),
        )
        e2_case = self.child_case(
            spec=spec, broker=broker, client=client, line=line, parent_id=case_file_id,
            policy_id=policy_id, kind=CaseFileKind.ENDORSEMENT,
            stage=CaseStage.ENDORSEMENT_APPLIED, status=CaseFileStatus.CLOSED,
            suffix="E2", sequence_no=2,
            title="Endoso E2 · Exclusión de bienes y disminución de suma asegurada",
            opened_on="2026-07-01", closed_on="2026-07-08", owner_id=owner_id,
            summary=(
                "Se excluye la totalidad de la maquinaria de la ubicación 2 (UF 1.252), "
                "trasladada a un tercero, y la cuba de acero N° 18 dada de baja "
                "(UF 1.640). La ubicación 2 queda sin materia asegurada y se elimina."
            ),
            steps=(
                (CaseStage.ENDORSEMENT_REQUESTED, "2026-07-01"),
                (CaseStage.ENDORSEMENT_PROPOSED, "2026-07-01"),
                (CaseStage.ENDORSEMENT_ISSUED, "2026-07-06"),
                (CaseStage.ENDORSEMENT_APPLIED, "2026-07-08"),
            ),
        )
        self.move_docs(spec.key, ("07A", "08A"), e1_case)
        self.move_docs(spec.key, ("07B", "08B"), e2_case)

        e1_id = self.ids.next_id("endorsement")
        self.db.add(
            Endorsement(
                id=e1_id, broker_id=broker.id, policy_id=policy_id, case_file_id=e1_case,
                endorsement_number="0020119904-E1", sequence_no=1,
                kind=EndorsementKind.SUM_INSURED_INCREASE,
                # ISSUED, not APPLIED: the carrier's folio exists and the delta
                # rides on top of the policy's as-issued premium (which stays
                # faithful to the 08 document). APPLIED means Radal already
                # folded the delta into policy.total_premium_uf, which the
                # corpus arithmetic — plan == gross + Σ deltas — says it has not.
                status=EndorsementStatus.ISSUED,
                effective_at=noon(d("2026-03-20")), ends_at=noon(spec.period_end),
                issued_at=d("2026-03-18"),
                proposal_document_id=self.doc(spec.key, "07A"),
                issued_document_id=self.doc(spec.key, "08A"),
                motive=(
                    "Se INCLUYEN en la póliza la línea de embotellado Bertolaso "
                    "(factura 88421 de 14-01-2026) y 180 barricas de roble francés "
                    "Radoux (factura 88976 de 22-01-2026), ambas en la ubicación 1."
                ),
                contractual_basis=(
                    "Cláusula de incorporación automática de nuevos bienes — hasta el "
                    "10% del monto asegurado con máximo de UF 25.000"
                ),
                insured_amount_delta_uf=uf("22000"),
                taxable_premium_delta_uf=uf("11.14"),
                exempt_premium_delta_uf=uf("16.70"),
                net_premium_delta_uf=uf("27.84"),
                vat_delta_uf=uf("2.12"),
                total_premium_delta_uf=uf("29.96"),
                commission_delta_uf=uf("2.78"),
                prorata_days=209, unexpired_days=365,
                effect={
                    "rate_permille": "2.21",
                    "annual_equivalent_uf": "48.62",
                    "items": [
                        {"location": 1, "matter": "Maquinaria y equipos",
                         "before_uf": "34231", "delta_uf": "9400", "after_uf": "43631"},
                        {"location": 1, "matter": "Cubas y barricas",
                         "before_uf": "95223", "delta_uf": "12600", "after_uf": "107823"},
                    ],
                    "pledge_note": (
                        "Comunicado al acreedor el 18-03-2026. No altera el monto "
                        "garantizado de UF 203.544."
                    ),
                },
                is_confirmed=True, confirmed_by_id=technician_id,
                confirmed_at=at(d("2026-03-18"), 16),
            )
        )
        self.counts["endorsement"] += 1
        self.check_money("vina_indomita/E1", uf("11.14"), uf("16.70"), uf("27.84"),
                         uf("2.12"), uf("29.96"))

        e2_id = self.ids.next_id("endorsement")
        self.db.add(
            Endorsement(
                id=e2_id, broker_id=broker.id, policy_id=policy_id, case_file_id=e2_case,
                endorsement_number="0020119904-E2", sequence_no=2,
                kind=EndorsementKind.SUM_INSURED_DECREASE,
                # ISSUED, not APPLIED: the carrier's folio exists and the delta
                # rides on top of the policy's as-issued premium (which stays
                # faithful to the 08 document). APPLIED means Radal already
                # folded the delta into policy.total_premium_uf, which the
                # corpus arithmetic — plan == gross + Σ deltas — says it has not.
                status=EndorsementStatus.ISSUED,
                effective_at=noon(d("2026-07-08")), ends_at=noon(spec.period_end),
                issued_at=d("2026-07-06"),
                proposal_document_id=self.doc(spec.key, "07B"),
                issued_document_id=self.doc(spec.key, "08B"),
                motive=(
                    "Se EXCLUYEN la totalidad de la maquinaria y equipos de la bodega de "
                    "apoyo (ubicación 2), retirada el 30-06-2026, y la cuba de acero "
                    "inoxidable N° 18 dada de baja el 27-06-2026 según acta N° 214."
                ),
                contractual_basis="Modificación de la materia asegurada — devolución de prima a prorrata",
                insured_amount_delta_uf=uf("-2892"),
                taxable_premium_delta_uf=uf("-0.69"),
                exempt_premium_delta_uf=uf("-1.04"),
                net_premium_delta_uf=uf("-1.73"),
                vat_delta_uf=uf("-0.13"),
                total_premium_delta_uf=uf("-1.86"),
                commission_delta_uf=uf("-0.17"),
                prorata_days=99, unexpired_days=365,
                effect={
                    "rate_permille": "2.21",
                    "annual_equivalent_uf": "6.39",
                    "items": [
                        {"location": 2, "matter": "Maquinaria y equipos",
                         "before_uf": "1252", "delta_uf": "-1252", "after_uf": "0"},
                        {"location": 1, "matter": "Cubas y barricas",
                         "before_uf": "107823", "delta_uf": "-1640", "after_uf": "106183"},
                    ],
                    "location_removed": 2,
                },
                is_confirmed=True, confirmed_by_id=technician_id,
                confirmed_at=at(d("2026-07-06"), 16),
            )
        )
        self.counts["endorsement"] += 1
        self.check_money("vina_indomita/E2", uf("-0.69"), uf("-1.04"), uf("-1.73"),
                         uf("-0.13"), uf("-1.86"))

        # --- collection -----------------------------------------------------
        collection_case = self.child_case(
            spec=spec, broker=broker, client=client, line=line, parent_id=case_file_id,
            policy_id=policy_id, kind=CaseFileKind.COLLECTION,
            stage=CaseStage.COLLECTION_OVERDUE, status=CaseFileStatus.OPEN,
            suffix="CB1", sequence_no=1,
            title="Cobranza · cuponera de 10 cuotas + endoso E1",
            opened_on="2025-10-15", closed_on=None, owner_id=owner_id,
            summary=(
                "Cuponera de 10 cuotas mensuales de UF 118,13 más 3 cuotas del endoso "
                "E1. El cupón CUP-008 lleva 67 días de mora al 11-08-2026: riesgo de "
                "término del contrato por el artículo 528 del Código de Comercio."
            ),
            steps=(
                (CaseStage.COLLECTION_SCHEDULED, "2025-10-15"),
                (CaseStage.COLLECTION_IN_PROGRESS, "2025-11-05"),
                (CaseStage.COLLECTION_OVERDUE, "2026-06-06"),
            ),
        )
        self.move_docs(spec.key, ("09",), collection_case)
        plan_id = self.ids.next_id("collection_plan")
        self.db.add(
            CollectionPlan(
                id=plan_id, broker_id=broker.id, policy_id=policy_id,
                case_file_id=collection_case,
                plan_number="0020119904-PP", payment_mode=PaymentMode.COUPON_BOOK,
                installment_count=14, total_premium_uf=uf("1209.38"),
                status=CollectionPlanStatus.OVERDUE, as_of_date=d("2026-08-11"),
                art528_events=[
                    {"date": "2026-06-05", "event": "Vencimiento del cupón CUP-008 sin pago"},
                    {"date": "2026-08-11", "event": "67 días de mora — sin aviso de la compañía"},
                ],
                management_note=(
                    "Alerta de cobranza: el cupón CUP-008 con vencimiento 05-06-2026 está "
                    "vencido. La compañía puede poner término al contrato por no pago de "
                    "prima conforme al artículo 528. Un evento ocurrido durante la mora "
                    "daría lugar a discusión sobre la vigencia de la cobertura. Acción: "
                    "contactar finanzas del asegurado y regularizar antes de 90 días."
                ),
            )
        )
        self.counts["collection_plan"] += 1
        instalment_total = self.add_installments(
            broker_id=broker.id, plan_id=plan_id,
            endorsement_id_by_coupon={
                "CUP-E1-01": e1_id, "CUP-E1-02": e1_id, "CUP-E1-03": e1_id,
                "DEV-E2": e2_id,
            },
            rows=(
                (1, "CUP-001", "2025-11-05", "118.13", "2025-11-05", "paid", None, None),
                (2, "CUP-002", "2025-12-05", "118.13", "2025-12-04", "paid", None, None),
                (3, "CUP-003", "2026-01-05", "118.13", "2026-01-07", "paid_late",
                 "Pago con 2 días de desfase, sin efecto contractual", 2),
                (4, "CUP-004", "2026-02-05", "118.13", "2026-02-05", "paid", None, None),
                (5, "CUP-005", "2026-03-05", "118.13", "2026-03-05", "paid", None, None),
                (6, "CUP-006", "2026-04-05", "118.13", "2026-04-06", "paid_late",
                 "Pago con 1 día de desfase", 1),
                (7, "CUP-007", "2026-05-05", "118.13", "2026-05-05", "paid", None, None),
                (8, "CUP-008", "2026-06-05", "118.13", None, "overdue",
                 "VENCIDA. 67 días de mora al 11-08-2026. Artículo 528 en curso", 67),
                (9, "CUP-009", "2026-07-05", "118.13", None, "pending", None, None),
                (10, "CUP-010", "2026-08-05", "118.11", None, "pending",
                 "Última cuota: absorbe el redondeo de la cuponera", None),
                (11, "CUP-E1-01", "2026-04-05", "9.99", "2026-04-06", "paid_late",
                 "Prima adicional del endoso E1", 1),
                (12, "CUP-E1-02", "2026-05-05", "9.99", "2026-05-05", "paid",
                 "Prima adicional del endoso E1", None),
                (13, "CUP-E1-03", "2026-06-05", "9.98", None, "overdue",
                 "Prima adicional del endoso E1", 67),
                (14, "DEV-E2", "2026-07-08", "-1.86", None, "credited",
                 "Devolución de prima del endoso E2, imputada al plan conforme a su "
                 "sección 4", None),
            ),
        )
        self.check_collection_sum(
            "vina_indomita/collection", instalment_total, uf("1181.28"),
            uf("29.96") + uf("-1.86"),
        )

        # --- claim SIN-2026-0417 --------------------------------------------
        claim_case = self.child_case(
            spec=spec, broker=broker, client=client, line=line, parent_id=case_file_id,
            policy_id=policy_id, kind=CaseFileKind.CLAIM,
            stage=CaseStage.CLAIM_SETTLED, status=CaseFileStatus.CLOSED,
            suffix="SN1", sequence_no=1,
            title="Siniestro SIN-2026-0417 · rotura de cuba con derrame",
            opened_on="2026-05-23", closed_on="2026-06-30", owner_id=owner_id,
            summary=(
                "Rotura de la soldadura perimetral de la cuba N° 14 con derrame de "
                "58.400 litros de tinto 2025 en guarda. Liquidado por Graham Miller en "
                "UF 2.399,40 sobre pérdida acreditada de UF 2.666, con deducible del 10%."
            ),
            steps=(
                (CaseStage.CLAIM_REPORTED, "2026-05-23"),
                (CaseStage.CLAIM_ADJUSTING, "2026-05-26"),
                (CaseStage.CLAIM_PRELIMINARY, "2026-06-05"),
                (CaseStage.CLAIM_FINAL, "2026-06-30"),
                (CaseStage.CLAIM_SETTLED, "2026-07-10"),
            ),
        )
        self.move_docs(spec.key, ("10", "11", "12"), claim_case)
        claim_id = self.ids.next_id("claim")
        self.db.add(
            Claim(
                id=claim_id, broker_id=broker.id, policy_id=policy_id, client_id=client.id,
                asset_id=placement.asset_id, case_file_id=claim_case,
                claim_number="SIN-2026-0417",
                kind="Todo Riesgo Bienes Físicos — rotura de cuba con derrame",
                event_date=d("2026-05-23"), reported_date=d("2026-05-23"),
                occurred_at=at(d("2026-05-23"), 4, 40),
                reported_at=at(d("2026-05-23"), 9, 15),
                description=(
                    "Rotura de la soldadura perimetral inferior de la cuba de acero "
                    "inoxidable N° 14, de 60.000 litros, con derrame íntegro del vino "
                    "tinto cosecha 2025 en guarda sobre el radier de la sala 2, "
                    "alcanzando la base de las cubas 12, 13 y 15 y 48 barricas de roble."
                ),
                status=ClaimStatus.SETTLED,
                adjuster_name="Graham Miller Chile S.A. — Ing. Andrés Villalobos Peña",
                adjuster_registry="0327",
                coverage_ruling=ClaimRuling.COVERED,
                deductible_uf=uf("266.60"),
                estimated_amount_uf=uf("2980"),
                settled_amount_uf=uf("2399.40"),
                paid_amount_uf=uf("2399.40"),
                cost_uf=uf("2399.40"),
            )
        )
        self.counts["claim"] += 1
        for order, (kind, item, basis, notified, determined) in enumerate((
            (ClaimItemKind.MATERIAL_DAMAGE, "Vino tinto cosecha 2025 — existencias",
             "58.400 litros en guarda, pérdida total por derrame y contaminación",
             "1980", "1842"),
            (ClaimItemKind.MATERIAL_DAMAGE, "Cuba N° 14 — reposición",
             "Cotización de proveedor autorizado por cuba equivalente de 60.000 litros, "
             "menos UF 12 del costo de la soldadura defectuosa, excluido por la "
             "cláusula LEG 2", "640", "628"),
            (ClaimItemKind.MATERIAL_DAMAGE, "48 barricas de roble",
             "9 barricas irrecuperables de 48 evaluadas, a valor de reposición con "
             "depreciación por uso", "210", "39"),
            (ClaimItemKind.EXPENSE, "Lavado técnico y ozonización de 39 barricas",
             "Gasto de alivio de pérdida acreditado con factura, amparado por la "
             "extensión N° 19", None, "24"),
            (ClaimItemKind.MATERIAL_DAMAGE, "Radier y canaleta",
             "Reparación de junta de dilatación y limpieza industrial. Se excluye el "
             "repintado general no atribuible al evento", "95", "78"),
            (ClaimItemKind.EXPENSE, "Extracción y disposición autorizada",
             "Acreditado con guía de disposición y factura", "55", "55"),
        )):
            self.db.add(
                ClaimItem(
                    id=self.ids.next_id("claim_item"), broker_id=broker.id, claim_id=claim_id,
                    kind=kind, item=item, basis=basis,
                    notified_uf=uf(notified), determined_uf=uf(determined),
                    damage_uf=uf(determined), sort_order=order,
                )
            )
            self.counts["claim_item"] += 1
        self.add_notes(
            case_file_id=claim_case, broker_id=broker.id, authors=authors,
            notes=(
                ("Sublímite de la extensión N° 40 (filtración, contaminación del vino y "
                 "rotura de cubas): UF 5.000. La pérdida acreditada de UF 2.666 queda "
                 "dentro, no opera reducción.", None),
                ("La sala de guarda 2 estuvo 11 días fuera de servicio. El margen no "
                 "percibido, unas UF 46, no tiene cobertura por no haberse contratado "
                 "perjuicio por paralización. La brecha fue advertida por escrito en las "
                 "bases técnicas de 15-09-2025.", None),
                ("Recomendación 5 del liquidador: ejercer la garantía del fabricante de "
                 "la cuba por el defecto de soldadura, en beneficio del recupero.",
                 "2026-08-30"),
            ),
        )

    # =========================================================================
    # 7. LA FAVORITA -> `active` (declined, re-placed, and the richest chain)
    # =========================================================================

    # (code, title, scope, deadline_days, due_date, budget, actual, completed, verification)
    LA_FAVORITA_MEASURES: tuple[tuple[str, str, str, int, str, str, str, str, str], ...] = (
        ("M-1", "Retiro del acopio adosado al panel",
         "Retiro del acopio adosado al panel y demarcación de zona libre de 5 metros.",
         0, "2026-12-22", "0", "0", "2026-11-19",
         "Acta de verificación con registro fotográfico de los 12 metros de frente liberados"),
        ("M-2", "Sellado de juntas, núcleo expuesto y penetraciones",
         "Sellado de 14 juntas degradadas, 4 puntos de núcleo expuesto y 9 penetraciones.",
         45, "2027-01-15", "180", "196.4", "2027-01-12",
         "Certificado de sellado por punto, con planta de ubicación y ensayo de continuidad. "
         "Mayor metraje que el presupuestado"),
        ("M-3", "Procedimiento de permiso de trabajo en caliente",
         "Procedimiento escrito de permiso de trabajo en caliente.",
         30, "2026-12-22", "20", "20", "2026-11-21",
         "Procedimiento firmado por gerencia, registro de difusión y talonario foliado"),
        ("M-4", "Detección de humo por aspiración con monitoreo 24/7",
         "Detección de humo por aspiración en cámaras y túnel, y puntual en proceso y "
         "sala de máquinas.",
         120, "2027-03-31", "1240", "1240", "2027-03-27",
         "Certificado de puesta en marcha Tyco, protocolo de prueba por detector y "
         "contrato de monitoreo 24/7 vigente"),
        ("M-5", "Red húmeda con reserva de 25 m³ y 4 bocas",
         "Red húmeda con reserva dedicada de 25 m³, bomba de presión y bocas en cuatro frentes.",
         180, "2027-06-30", "2180", "2244", "2027-06-26",
         "Protocolo de prueba hidráulica conforme a NCh 2111. Gatilla la reducción de deducible"),
        ("M-6", "Registro continuo de temperatura con alarma",
         "Registro continuo de temperatura con alarma de desviación y aviso a turno.",
         90, "2027-02-28", "320", "320", "2027-02-19",
         "CONDICIÓN SUSPENSIVA de las coberturas de frío. Reporte de 30 días de registro "
         "continuo y prueba de alarma documentada"),
        ("M-7", "Generador de 150 kVA con transferencia automática",
         "Generador de respaldo con transferencia automática para cámaras y túnel.",
         180, "2027-06-30", "1640", "1663.2", "2027-06-26",
         "Protocolo de partida en carga y prueba de transferencia automática. CONDICIÓN "
         "SUSPENSIVA del PxP por interrupción de suministro. Gatilla la reducción"),
        ("M-8", "Detección de amoníaco con corte automático",
         "Detección de amoníaco con alarma, corte automático y ventilación forzada de emergencia.",
         150, "2027-04-30", "540", "540", "2027-04-24",
         "CONDICIÓN SUSPENSIVA de la cobertura de amoníaco. Certificado de calibración, "
         "prueba de corte automático y prueba de ventilación"),
        ("M-9", "Sectorización en tres sectores hasta cubierta",
         "Sectorización de la nave en tres sectores independientes con cierre hasta cubierta.",
         270, "2027-09-30", "2860", "2946", "2027-09-22",
         "Planos as-built aprobados y certificados de resistencia al fuego F-60. Gatilla "
         "la reducción de deducible"),
        ("M-10", "Termografía anual de tableros",
         "Termografía de tableros con periodicidad anual.",
         60, "2027-02-28", "40", "40", "2027-02-18",
         "Informe termográfico con 3 hallazgos, todos cerrados al 05-03-2027"),
        ("M-11", "Canalización definitiva en sala de máquinas",
         "Canalización definitiva en sala de máquinas.",
         45, "2027-02-15", "60", "44.6", "2027-01-08",
         "Declaración SEC TE-1 actualizada"),
        ("M-12", "Plan de emergencia y dos simulacros anuales",
         "Plan de emergencia actualizado y dos simulacros anuales documentados.",
         90, "2027-03-31", "30", "30", "2027-03-03",
         "Plan firmado y actas de simulacro. Simulacros el 12-05 y el 08-09-2027, uno de "
         "ellos de fuga de amoníaco"),
    )

    def stage_la_favorita(self, *, spec, broker, client, line, placement, case_file_id,
                          authors, owner_id, technician_id) -> None:
        policy_id = self.ids.next_id("policy")

        quote = self.add_quote_request(
            broker=broker,
            placement=placement,
            case_file_id=case_file_id,
            insured_object=(
                "Incendio y Riesgos Adicionales con Perjuicio por Paralización — centro "
                "de distribución frigorífico de COMERCIALIZADORA LA FAVORITA LTDA en Lo "
                "Blanco 2011, La Pintana, más cámara arrendada en Maipú."
            ),
            declared_value_uf="77240",
            desired_start=spec.period_start,
            desired_end=spec.period_end,
            # Round 1 went out on 21-10-2026 (02); round 2 on 25-11-2026 (03F),
            # with the plan de ingeniería attached. round_no records the second.
            sent_on="2026-11-25",
            due_on="2026-12-12",
            recipients=("porvenir", "mapfre", "consorcio", "hdi", "chubb"),
            status=QuoteRequestStatus.CLOSED,
            created_by=owner_id,
            round_no=2,
            requested_coverages=(
                "45 coberturas nominadas con perjuicio por paralización a 12 meses; "
                "rotura de maquinaria frigorífica y deterioro de mercadería por variación "
                "de temperatura con franquicia horaria de 4 horas; mecanismo contractual "
                "de reducción de deducible por cumplimiento verificado del plan de "
                "ingeniería de riesgo."
            ),
            line_items=(
                ("Edificio y obras civiles — U-1", "19400", None),
                ("Instalaciones frigoríficas y equipos de frío — U-1", "8600", None),
                ("Maquinaria y equipos de proceso — U-1", "3200", None),
                ("Muebles, útiles y equipos computacionales — U-1", "640", None),
                ("Existencias de carnes, cecinas, huevos y pescados — U-1", "21800", None),
                ("Existencias en cámara arrendada — U-2", "6400", None),
                ("Perjuicio por paralización — 12 meses", "17200",
                 "Margen de contribución y gastos fijos"),
            ),
        )
        common = dict(
            broker=broker,
            quote_request_id=quote.id,
            case_file_id=case_file_id,
            confirmed_by=technician_id,
            coverage_start=spec.period_start,
            coverage_end=spec.period_end,
        )
        # --- round 1: three declinations and one conditional pronouncement ---
        for label, insurer_label, code, received, reason in (
            ("porvenir", "porvenir", "03A", "2026-11-03",
             "Declina. Envolvente de poliuretano en el 55% de la construcción, ausencia "
             "total de detección y ausencia total de agua. Mantiene la póliza vigente "
             "N° 01-42-005194 hasta el 05-01-2027 sin renovación."),
            ("mapfre", "mapfre", "03B", "2026-11-06",
             "Declina con disposición a reevaluar el riesgo en su configuración actual. "
             "Mismos tres frentes técnicos."),
            ("consorcio", "consorcio", "03C", "2026-11-10",
             "Declina la suscripción del riesgo presentado. Mismos tres frentes técnicos."),
        ):
            self.add_proposal(
                label=f"la_favorita/{label}-declinacion",
                document_id=self.doc(spec.key, code),
                insurer_label=insurer_label,
                received_on=received,
                status=ProposalStatus.REJECTED,
                outcome=ProposalOutcome.DECLINED,
                notes=reason,
                **common,
            )
        self.add_proposal(
            label="la_favorita/hdi-condicionado",
            document_id=self.doc(spec.key, "03D"),
            insurer_label="hdi",
            received_on="2026-11-12",
            status=ProposalStatus.REJECTED,
            outcome=ProposalOutcome.CONDITIONAL,
            notes=(
                "No declina, pero no cotiza en la configuración actual: condiciona la "
                "cotización a la ejecución previa de medidas del plan de ingeniería. "
                "El fundamento es el propio informe de inspección acompañado."
            ),
            **common,
        )
        # --- round 2: three real offers --------------------------------------
        self.add_proposal(
            label="la_favorita/hdi",
            document_id=self.doc(spec.key, "04"),
            insurer_label="hdi",
            quotation_number="HDI-2026-118447",
            received_on="2026-12-09",
            status=ProposalStatus.ACCEPTED,
            outcome=ProposalOutcome.QUOTED,
            cover_mode="Riesgos Nominados con Perjuicio por Paralización",
            modality="Incendio y Riesgos Adicionales con PxP · POL 1 2013 0221",
            taxable="215.99", exempt="166.00", net="381.99", vat="41.04", total="423.03",
            commission_pct="12", validity_days=30, comprehensive_rate="4.945",
            insured_amount_uf="77240",
            deductibles={
                "fire": {"text": "15% de la pérdida, mínimo UF 120",
                         "reduced": "10% de la pérdida, mínimo UF 72"},
                "earthquake": {"text": "2,5% del monto asegurado de la ubicación, "
                                       "sobre la ubicación afectada",
                               "reduced": "1,5% del monto asegurado"},
                "machinery_breakdown": {"text": "15% de la pérdida, mínimo UF 50",
                                        "reduced": "10% de la pérdida, mínimo UF 30"},
                "stock_deterioration": {
                    "text": "15% de la pérdida, mínimo UF 50, más franquicia de 4 horas",
                    "reduced": "10% de la pérdida, mínimo UF 30, más franquicia de 4 horas"},
                "business_interruption": {"text": "7 días de la indemnización diaria",
                                          "reduced": "5 días de la indemnización diaria"},
            },
            warranties=(
                "G-1 a G-12: las doce medidas del plan de ingeniería incorporadas como "
                "garantías de la póliza, tres de ellas como condición suspensiva."
            ),
            notes=(
                "La oferta más cara de las tres y la única que otorga íntegras las dos "
                "coberturas de frío con franquicia horaria de 4 horas, y el mecanismo de "
                "reducción de deducible que remunera la inversión de UF 9.110."
            ),
            **common,
        )
        self.add_proposal(
            label="la_favorita/chubb",
            document_id=self.doc(spec.key, "05"),
            insurer_label="chubb",
            quotation_number="CH-26-40911",
            received_on="2026-12-10",
            status=ProposalStatus.REJECTED,
            outcome=ProposalOutcome.QUOTED,
            cover_mode="Riesgos Nominados con Perjuicio por Paralización",
            modality="Incendio y Riesgos Adicionales con PxP · POL 1 2013 0221",
            taxable="166.84", exempt="162.13", net="328.97", vat="31.70", total="360.67",
            commission_pct="12", validity_days=30, comprehensive_rate="4.566",
            insured_amount_uf="72040",
            notes=(
                "La de menor tasa media (4,566‰) a costa de dejar 6 coberturas fuera y "
                "restringir otras 4. Franquicia horaria de 8 horas: la desviación de "
                "6 h 05 min del siniestro de noviembre no habría estado cubierta."
            ),
            **common,
        )
        self.add_proposal(
            label="la_favorita/consorcio",
            document_id=self.doc(spec.key, "05B"),
            insurer_label="consorcio",
            quotation_number="CNS-26-77320",
            received_on="2026-12-11",
            status=ProposalStatus.REJECTED,
            outcome=ProposalOutcome.QUOTED,
            cover_mode="Riesgos Nominados",
            modality="Incendio y Riesgos Adicionales · POL 1 2016 0181",
            taxable="115.32", exempt="158.24", net="273.56", vat="21.91", total="295.47",
            commission_pct="12", validity_days=30, comprehensive_rate="5.100",
            insured_amount_uf="53640",
            notes=(
                "La más barata en prima bruta y la más cara por unidad de riesgo: "
                "5,100‰. No otorga perjuicio por paralización y expone UF 23.600 menos. "
                "Franquicia horaria de 12 horas."
            ),
            **common,
        )
        self.add_pack(
            broker_id=broker.id, case_file_id=case_file_id, kind=PackKind.COMPARISON,
            section=CaseSection.INSURER_QUOTES, pdf_document_id=self.doc(spec.key, "06"),
            generated_on="2026-12-15", generated_by=owner_id,
            summary=(
                "Tres ofertas más la póliza vigente de Porvenir como línea base. Se "
                "recomienda HDI por UF 423,03: es la más cara, en UF 62,36 sobre Chubb y "
                "UF 127,56 sobre Consorcio, y la única que resuelve el problema por el "
                "cual el asegurado llegó a esta corredora. En la simulación de siniestro "
                "paga UF 4.607,27 contra UF 3.776,36 de Chubb y UF 496,00 de Consorcio."
            ),
        )
        self.add_pack(
            broker_id=broker.id, case_file_id=case_file_id, kind=PackKind.PROPOSAL,
            section=CaseSection.BROKER_PROPOSAL, pdf_document_id=self.doc(spec.key, "07"),
            generated_on="2026-12-18", generated_by=owner_id,
            summary=(
                "Propuesta de emisión remitida a HDI sobre la cotización HDI-2026-118447 "
                "del 09-12-2026, con vigencia desde las 12:00 del 05-01-2027 y cláusula "
                "de validación espejo."
            ),
        )

        # --- the policy -----------------------------------------------------
        self.db.add(
            Policy(
                id=policy_id,
                broker_id=broker.id,
                client_id=client.id,
                asset_id=placement.asset_id,
                placement_id=placement.id,
                insurer_id=self.insurer("hdi", broker.id).id,
                insurance_line_id=line.id,
                case_file_id=case_file_id,
                source_document_id=self.doc(spec.key, "08"),
                policy_number="15-04-0091883",
                start_date=spec.period_start,
                end_date=spec.period_end,
                period_start_at=noon(spec.period_start),
                period_end_at=noon(spec.period_end),
                issued_at=d("2026-12-22"),
                status=PolicyStatus.ACTIVE,
                cover_mode="Riesgos Nominados con Perjuicio por Paralización",
                cmf_policy_code="POL 1 2013 0221",
                insured_amount_semantics=(
                    "UF 77.240,00 al valor de reposición determinado, no la suma "
                    "aritmética de los límites de las coberturas. Las existencias de U-1 "
                    "quedan sujetas a declaración flotante de la cláusula particular 5."
                ),
                average_rate_permille=uf("4.945"),
                indemnity_limit=(
                    "Al 100% del monto asegurado de cada ubicación en las coberturas "
                    "básicas; sublímites por evento y en agregado en las coberturas "
                    "nominadas de las secciones 4 y 5, que operan dentro del monto "
                    "asegurado y no en adición a él."
                ),
                insured_amount_uf=uf("77240"),
                taxable_premium_uf=uf("215.99"),
                exempt_premium_uf=uf("166.00"),
                net_premium_uf=uf("381.99"),
                vat_uf=uf("41.04"),
                total_premium_uf=uf("423.03"),
                commission_pct=uf("12"),
                deductibles={
                    "fire": {"initial": "15% de la pérdida, mínimo UF 120",
                             "reduced_from_2027_10_13": "10% de la pérdida, mínimo UF 72"},
                    "earthquake": {"initial": "2,5% del monto asegurado de la ubicación",
                                   "reduced_from_2027_10_13": "1,5% del monto asegurado, "
                                                              "mínimo UF 110"},
                    "machinery_breakdown": {"initial": "15% de la pérdida, mínimo UF 50",
                                            "reduced_from_2027_10_13": "10% de la pérdida, "
                                                                       "mínimo UF 30"},
                    "stock_deterioration": {
                        "initial": "15% de la pérdida, mínimo UF 50, más franquicia de 4 horas",
                        "reduced_from_2027_10_13": "10% de la pérdida, mínimo UF 30, más "
                                                   "franquicia de 4 horas"},
                    "business_interruption": {"initial": "7 días de la indemnización diaria",
                                              "reduced_from_2027_10_13": "5 días de la "
                                                                         "indemnización diaria"},
                },
                notes=(
                    "Emitida en espejo de la propuesta del corredor de 18-12-2026 sobre "
                    "la cotización HDI-2026-118447 del 09-12-2026. El programa saliente "
                    "de Porvenir aseguraba UF 40.000 sobre bienes de UF 60.040,00, con "
                    "un infraseguro de UF 20.040,00; el corredor anterior tenía 10,91% "
                    "de comisión."
                ),
            )
        )
        self.counts["policy"] += 1
        self.check_money("la_favorita/policy", uf("215.99"), uf("166.00"), uf("381.99"),
                         uf("41.04"), uf("423.03"))
        self.seed_mirror_sources(
            spec=spec, broker=broker, case_file_id=case_file_id,
            confirmed_by=technician_id or owner_id,
        )
        for name, address, commune, region, amount in (
            ("U-1 · Centro de distribución frigorífico", "Lo Blanco 2011", "La Pintana",
             "Región Metropolitana", "70840"),
            ("U-2 · Cámara de frío en recinto de tercero",
             "Camino a Melipilla 14.820, Bodega 4", "Maipú", "Región Metropolitana", "6400"),
        ):
            self.db.add(
                PolicyLocation(policy_id=policy_id, name=name, address=address,
                               commune=commune, region=region, insured_amount_uf=uf(amount))
            )
            self.counts["policy_location"] += 1

        # --- the 12 engineering measures, as warranties ----------------------
        for order, row in enumerate(self.LA_FAVORITA_MEASURES):
            code, title, scope, deadline_days, due, budget, actual, completed, verify = row
            self.db.add(
                Warranty(
                    id=self.ids.next_id("warranty"),
                    broker_id=broker.id,
                    policy_id=policy_id,
                    case_file_id=case_file_id,
                    code=code,
                    title=title,
                    requirement=scope,
                    source=WarrantySource.ENGINEERING_MEASURE,
                    deadline_days=deadline_days,
                    due_date=d(due),
                    is_permanent=code in {"M-10", "M-12"},
                    is_suspensive=code in {"M-6", "M-7", "M-8"},
                    status=WarrantyStatus.MET_ON_TIME,
                    completed_on=d(completed),
                    verification=verify,
                    budget_uf=uf(budget),
                    actual_cost_uf=uf(actual),
                    evidence_document_id=self.doc(spec.key, "09A"),
                    sort_order=order,
                )
            )
            self.counts["warranty"] += 1

        # --- E1 (deductible reduction) and E2 (sum insured increase) ---------
        e1_case = self.child_case(
            spec=spec, broker=broker, client=client, line=line, parent_id=case_file_id,
            policy_id=policy_id, kind=CaseFileKind.ENDORSEMENT,
            stage=CaseStage.ENDORSEMENT_APPLIED, status=CaseFileStatus.CLOSED,
            suffix="E1", sequence_no=1,
            title="Endoso E1 · Reducción de deducible por ingeniería",
            opened_on="2027-09-23", closed_on="2027-10-13", owner_id=owner_id,
            summary=(
                "Aviso de cumplimiento de M-5, M-7 y M-9 el 23-09-2027; re-inspección de "
                "HDI el 06-10-2027; informe favorable folio 2027000441209 del 13-10-2027 "
                "con re-calificación de nota 35,6 clase E a 74,8 clase B y MFL de "
                "UF 70.840 a UF 34.200. Endoso sin costo de prima."
            ),
            steps=(
                (CaseStage.ENDORSEMENT_REQUESTED, "2027-09-23"),
                (CaseStage.ENDORSEMENT_PROPOSED, "2027-10-06"),
                (CaseStage.ENDORSEMENT_ISSUED, "2027-10-13"),
                (CaseStage.ENDORSEMENT_APPLIED, "2027-10-13"),
            ),
        )
        e2_case = self.child_case(
            spec=spec, broker=broker, client=client, line=line, parent_id=case_file_id,
            policy_id=policy_id, kind=CaseFileKind.ENDORSEMENT,
            stage=CaseStage.ENDORSEMENT_APPLIED, status=CaseFileStatus.CLOSED,
            suffix="E2", sequence_no=2,
            title="Endoso E2 · Aumento de monto asegurado",
            opened_on="2027-10-26", closed_on="2027-11-01", owner_id=owner_id,
            summary=(
                "Nueva cámara de congelado y mayor volumen de existencias: el monto "
                "asegurado sube de UF 77.240,00 a UF 89.640,00. Informado antes de la "
                "puesta en servicio, se mantiene la tasa de 4,450‰ sin recargo."
            ),
            steps=(
                (CaseStage.ENDORSEMENT_REQUESTED, "2027-10-26"),
                (CaseStage.ENDORSEMENT_PROPOSED, "2027-10-26"),
                (CaseStage.ENDORSEMENT_ISSUED, "2027-11-01"),
                (CaseStage.ENDORSEMENT_APPLIED, "2027-11-01"),
            ),
        )
        self.move_docs(spec.key, ("09A", "09B"), e1_case)
        self.move_docs(spec.key, ("09C", "09D"), e2_case)

        e1_id = self.ids.next_id("endorsement")
        self.db.add(
            Endorsement(
                id=e1_id, broker_id=broker.id, policy_id=policy_id, case_file_id=e1_case,
                endorsement_number="1", sequence_no=1,
                kind=EndorsementKind.DEDUCTIBLE_REDUCTION,
                # ISSUED, not APPLIED: the carrier's folio exists and the delta
                # rides on top of the policy's as-issued premium (which stays
                # faithful to the 08 document). APPLIED means Radal already
                # folded the delta into policy.total_premium_uf, which the
                # corpus arithmetic — plan == gross + Σ deltas — says it has not.
                status=EndorsementStatus.ISSUED,
                effective_at=noon(d("2027-10-13")), ends_at=noon(spec.period_end),
                issued_at=d("2027-10-13"),
                issued_document_id=self.doc(spec.key, "09B"),
                motive=(
                    "REDUCCIÓN DE DEDUCIBLE por cumplimiento verificado del plan de "
                    "ingeniería. Re-inspección del 06-10-2027, informe favorable folio "
                    "2027000441209 del 13-10-2027: nota 74,8 clase B y MFL de UF 34.200 "
                    "contra las UF 70.840 originales."
                ),
                contractual_basis=(
                    "Cláusula particular 4 — reducción de deducible por ingeniería, "
                    "acreditada la ejecución de M-5, M-7 y M-9"
                ),
                # Administrative in money terms: all-zero is VALID and must not
                # trip the validator. The insured's consideration was the works.
                insured_amount_delta_uf=uf("0"),
                taxable_premium_delta_uf=uf("0"),
                exempt_premium_delta_uf=uf("0"),
                net_premium_delta_uf=uf("0"),
                vat_delta_uf=uf("0"),
                total_premium_delta_uf=uf("0"),
                commission_delta_uf=uf("0"),
                deductibles={
                    "fire": "10% de la pérdida, mínimo UF 72",
                    "earthquake": "1,5% del monto asegurado, mínimo UF 110",
                    "machinery_breakdown": "10% de la pérdida, mínimo UF 30",
                    "stock_deterioration": "10% de la pérdida, mínimo UF 30, más "
                                           "franquicia de 4 horas",
                    "water_damage": "10% de la pérdida, mínimo UF 24",
                    "theft": "10% de la pérdida, mínimo UF 36",
                    "business_interruption": "5 días de la indemnización diaria",
                },
                effect={
                    "score_before": "35.6", "score_after": "74.8",
                    "class_before": "E", "class_after": "B",
                    "mfl_before_uf": "70840", "mfl_after_uf": "34200",
                    "reinspection_folio": "2027000441209",
                    "reversible": "La compañía podrá revertir la reducción, mediante "
                                  "endoso y con aviso de 30 días, si una inspección "
                                  "posterior acredita que las instalaciones dejaron de "
                                  "operar o de mantenerse.",
                },
                is_confirmed=True, confirmed_by_id=technician_id,
                confirmed_at=at(d("2027-10-13"), 16),
            )
        )
        self.counts["endorsement"] += 1
        self.check_money("la_favorita/E1", uf("0"), uf("0"), uf("0"), uf("0"), uf("0"))

        e2_id = self.ids.next_id("endorsement")
        self.db.add(
            Endorsement(
                id=e2_id, broker_id=broker.id, policy_id=policy_id, case_file_id=e2_case,
                endorsement_number="2", sequence_no=2,
                kind=EndorsementKind.SUM_INSURED_INCREASE,
                # ISSUED, not APPLIED: the carrier's folio exists and the delta
                # rides on top of the policy's as-issued premium (which stays
                # faithful to the 08 document). APPLIED means Radal already
                # folded the delta into policy.total_premium_uf, which the
                # corpus arithmetic — plan == gross + Σ deltas — says it has not.
                status=EndorsementStatus.ISSUED,
                effective_at=noon(d("2027-11-01")), ends_at=noon(spec.period_end),
                issued_at=d("2027-11-01"),
                proposal_document_id=self.doc(spec.key, "09C"),
                issued_document_id=self.doc(spec.key, "09D"),
                motive=(
                    "AUMENTA el monto asegurado por nueva cámara de congelado y mayor "
                    "volumen de existencias: de UF 77.240,00 a UF 89.640,00. Informado "
                    "antes de la puesta en servicio."
                ),
                contractual_basis="Cláusula particular 5 — declaración flotante de existencias",
                insured_amount_delta_uf=uf("12400"),
                taxable_premium_delta_uf=uf("3.72"),
                exempt_premium_delta_uf=uf("6.11"),
                net_premium_delta_uf=uf("9.83"),
                vat_delta_uf=uf("0.71"),
                total_premium_delta_uf=uf("10.54"),
                commission_delta_uf=uf("1.18"),
                prorata_days=65, unexpired_days=365,
                effect={
                    "rate_permille": "4.450",
                    "annual_equivalent_uf": "55.18",
                    "insured_amount_before_uf": "77240",
                    "insured_amount_after_uf": "89640",
                    "floating_declaration_base_before_uf": "21800",
                    "floating_declaration_base_after_uf": "29400",
                    "charging_instruction": "Pago único el 05-11-2027 con cargo al mismo "
                                            "mandato PAC.",
                },
                is_confirmed=True, confirmed_by_id=technician_id,
                confirmed_at=at(d("2027-11-01"), 16),
            )
        )
        self.counts["endorsement"] += 1
        self.check_money("la_favorita/E2", uf("3.72"), uf("6.11"), uf("9.83"),
                         uf("0.71"), uf("10.54"))

        # --- collection: the PAC-rejection ledger ----------------------------
        collection_case = self.child_case(
            spec=spec, broker=broker, client=client, line=line, parent_id=case_file_id,
            policy_id=policy_id, kind=CaseFileKind.COLLECTION,
            stage=CaseStage.COLLECTION_SETTLED, status=CaseFileStatus.CLOSED,
            suffix="CB1", sequence_no=1,
            title="Cobranza · 10 cuotas PAC + prima adicional del endoso E2",
            opened_on="2027-01-05", closed_on="2027-11-05", owner_id=owner_id,
            summary=(
                "Diez cuotas mensuales por cargo automático en cuenta corriente más el "
                "cargo único del endoso E2. La cuota 8 tuvo dos rechazos PAC por saldo "
                "insuficiente y se pagó por transferencia el 18-08 con 13 días de mora, "
                "dentro del plazo de 30 días del artículo 528. La cobertura nunca se vio "
                "afectada: la compañía no alcanzó a emitir aviso."
            ),
            steps=(
                (CaseStage.COLLECTION_SCHEDULED, "2027-01-05"),
                (CaseStage.COLLECTION_IN_PROGRESS, "2027-01-05"),
                (CaseStage.COLLECTION_OVERDUE, "2027-08-06"),
                (CaseStage.COLLECTION_IN_PROGRESS, "2027-08-18"),
                (CaseStage.COLLECTION_SETTLED, "2027-11-05"),
            ),
        )
        self.move_docs(spec.key, ("10",), collection_case)
        plan_id = self.ids.next_id("collection_plan")
        self.db.add(
            CollectionPlan(
                id=plan_id, broker_id=broker.id, policy_id=policy_id,
                case_file_id=collection_case,
                plan_number="15-04-0091883-PAC", payment_mode=PaymentMode.DIRECT_DEBIT,
                installment_count=11, total_premium_uf=uf("433.57"),
                status=CollectionPlanStatus.SETTLED, as_of_date=d("2027-12-31"),
                art528_events=[
                    {"date": "2027-08-05", "event": "PAC rechazado por saldo insuficiente"},
                    {"date": "2027-08-06", "event": "Detectado en el control diario de "
                                                    "rechazos PAC del corredor"},
                    {"date": "2027-08-12", "event": "Segundo intento de cargo, también rechazado"},
                    {"date": "2027-08-13", "event": "Gestión del corredor con el asegurado"},
                    {"date": "2027-08-18", "event": "Pago por transferencia — mora efectiva "
                                                    "de 13 días sobre un plazo de 30"},
                ],
                management_note=(
                    "El rechazo del 05-08-2027 obedeció a saldo insuficiente por desfase "
                    "de un pago de cliente, no a revocación del mandato. El corredor lo "
                    "detectó en su control diario del 06-08, antes de que la compañía "
                    "emitiera aviso alguno. El plazo del artículo 528 se computa desde la "
                    "comunicación de la compañía, que no llegó a emitirse. La diferencia "
                    "con el caso de otro asegurado de esta cartera, en que un rechazo "
                    "idéntico no gestionado derivó en terminación efectiva y 14 días sin "
                    "cobertura, no está en el asegurado: está en si alguien mira los "
                    "rechazos PAC todos los días."
                ),
            )
        )
        self.counts["collection_plan"] += 1
        instalment_total = self.add_installments(
            broker_id=broker.id, plan_id=plan_id,
            endorsement_id_by_coupon={"E2-UNICA": e2_id},
            rows=(
                (1, "1 de 10", "2027-01-05", "42.30", "2027-01-05", "paid", "PAC conforme", None),
                (2, "2 de 10", "2027-02-05", "42.30", "2027-02-05", "paid", "PAC conforme", None),
                (3, "3 de 10", "2027-03-05", "42.30", "2027-03-05", "paid", "PAC conforme", None),
                (4, "4 de 10", "2027-04-05", "42.30", "2027-04-05", "paid", "PAC conforme", None),
                (5, "5 de 10", "2027-05-05", "42.30", "2027-05-05", "paid", "PAC conforme", None),
                (6, "6 de 10", "2027-06-05", "42.30", "2027-06-05", "paid", "PAC conforme", None),
                (7, "7 de 10", "2027-07-05", "42.30", "2027-07-05", "paid", "PAC conforme", None),
                (8, "8 de 10", "2027-08-05", "42.30", "2027-08-18", "paid_late",
                 "PAC RECHAZADO por saldo insuficiente el 05-08. Reintento el 12-08 "
                 "también rechazado. Gestión del corredor el 13-08. Pago por "
                 "transferencia el 18-08. Mora de 13 días", 13),
                (9, "9 de 10", "2027-09-05", "42.30", "2027-09-05", "paid", "PAC conforme", None),
                (10, "10 de 10", "2027-10-05", "42.33", "2027-10-05", "paid",
                 "Última cuota: absorbe el redondeo", None),
                (11, "E2-UNICA", "2027-11-05", "10.54", "2027-11-05", "paid",
                 "Cargo único del endoso N° 2. Aumento de monto asegurado de UF 77.240 "
                 "a UF 89.640", None),
            ),
        )
        self.check_collection_sum(
            "la_favorita/collection", instalment_total, uf("423.03"),
            uf("0") + uf("10.54"),
        )

        # --- claim S-2027-58814 ----------------------------------------------
        claim_case = self.child_case(
            spec=spec, broker=broker, client=client, line=line, parent_id=case_file_id,
            policy_id=policy_id, kind=CaseFileKind.CLAIM,
            stage=CaseStage.CLAIM_SETTLED, status=CaseFileStatus.CLOSED,
            suffix="SN1", sequence_no=1,
            title="Siniestro S-2027-58814 · falla del compresor de congelados",
            opened_on="2027-11-08", closed_on="2027-12-22", owner_id=owner_id,
            summary=(
                "Falla del motocompresor N° 2 con desviación de temperatura de 6 h 05 min "
                "sobre una franquicia de 4 horas. Daño determinado UF 4.975,00, "
                "deducibles reducidos del Endoso N° 1 por UF 578,00, indemnización "
                "UF 4.397,00 pagada el 19-12-2027. Tercer siniestro de frío en cinco "
                "años y el primero que se paga."
            ),
            steps=(
                (CaseStage.CLAIM_REPORTED, "2027-11-08"),
                (CaseStage.CLAIM_ADJUSTING, "2027-11-10"),
                (CaseStage.CLAIM_PRELIMINARY, "2027-11-24"),
                (CaseStage.CLAIM_FINAL, "2027-12-12"),
                (CaseStage.CLAIM_SETTLED, "2027-12-19"),
            ),
        )
        self.move_docs(spec.key, ("11", "12", "13", "14"), claim_case)
        claim_id = self.ids.next_id("claim")
        self.db.add(
            Claim(
                id=claim_id, broker_id=broker.id, policy_id=policy_id, client_id=client.id,
                asset_id=placement.asset_id, case_file_id=claim_case,
                claim_number="S-2027-58814",
                kind="Rotura de maquinaria frigorífica y deterioro de mercadería",
                event_date=d("2027-11-08"), reported_date=d("2027-11-08"),
                occurred_at=at(d("2027-11-08"), 2, 41),
                reported_at=at(d("2027-11-08"), 9, 15),
                notice_deadline_days=5,
                description=(
                    "A las 02:41 el registro continuo instalado en cumplimiento de la "
                    "medida M-6 emitió alarma de desviación en la cámara de congelados "
                    "N° 1: −16,4 °C contra un régimen declarado de −22 °C. El "
                    "motocompresor N° 2 estaba detenido con protección térmica activada "
                    "y desprendimiento de bobinado. La desviación duró 6 horas y 5 "
                    "minutos, de 02:41 a 08:46, sobre una franquicia contractual de 4 "
                    "horas continuas. Se trasladaron 62 pallets a cámara de emergencia."
                ),
                status=ClaimStatus.PAID,
                adjuster_name="Cristián Meza Aravena",
                adjuster_registry="Liquidador Oficial de Seguros",
                coverage_ruling=ClaimRuling.COVERED,
                deductible_uf=uf("578.00"),
                loss_ratio_pct=uf("1146.8"),
                estimated_amount_uf=uf("4975"),
                settled_amount_uf=uf("4397"),
                paid_amount_uf=uf("4397"),
                cost_uf=uf("4493.40"),
            )
        )
        self.counts["claim"] += 1
        for order, (kind, item, basis, notified, determined, deductible, indemnity) in enumerate((
            (ClaimItemKind.MATERIAL_DAMAGE, "Motocompresor de tornillo N° 2",
             "Reposición a nuevo sin depreciación, cláusula particular 1. Tres "
             "cotizaciones formales, se adopta la menor de proveedor autorizado. "
             "Deducible: 10% de la pérdida, mínimo UF 30",
             "840", "840", "84", "756"),
            (ClaimItemKind.MATERIAL_DAMAGE, "Producto congelado no apto",
             "6,2 toneladas a costo de reposición conforme a facturas de compra de los "
             "90 días anteriores: vacuno 2,4 t, cecinas 1,8 t, cerdo 2,0 t. Deducible: "
             "10% de la pérdida, mínimo UF 30",
             "2940", "2940", "294", "2646"),
            (ClaimItemKind.EXPENSE, "Arriendo de cámara de emergencia",
             "Dos camiones frigoríficos de 28 pallets por 9 días más traslados, facturas "
             "de Fricom Logística. Sin deducible: gasto de mitigación",
             "310", "310", "0", "310"),
            (ClaimItemKind.EXPENSE, "Disposición sanitaria",
             "Retiro y destrucción certificada de 6,2 toneladas con certificado de "
             "disposición final. Sin deducible",
             "165", "165", "0", "165"),
            (ClaimItemKind.BUSINESS_INTERRUPTION, "Perjuicio por paralización",
             "18 días de operación reducida por menor capacidad de congelado, sobre "
             "margen de contribución y gastos fijos de los 12 meses anteriores. "
             "Indemnización diaria determinada en UF 40,00. Deducible: 5 días",
             "720", "720", "200", "520"),
        )):
            self.db.add(
                ClaimItem(
                    id=self.ids.next_id("claim_item"), broker_id=broker.id, claim_id=claim_id,
                    kind=kind, item=item, basis=basis,
                    notified_uf=uf(notified), determined_uf=uf(determined),
                    damage_uf=uf(determined), deductible_uf=uf(deductible),
                    indemnity_uf=uf(indemnity), sort_order=order,
                )
            )
            self.counts["claim_item"] += 1
        self.add_notes(
            case_file_id=claim_case, broker_id=broker.id, authors=authors,
            notes=(
                ("Los deducibles reducidos del Endoso N° 1 rigen desde el 13-10-2027, "
                 "esto es 26 días antes del siniestro. Le devolvieron UF 269,00 en este "
                 "solo evento.", None),
                ("Con la franquicia de 8 horas de Chubb la mercadería no habría estado "
                 "cubierta: UF 2.646,00 menos. Con la de 12 horas de Consorcio tampoco, "
                 "y además no habría habido arriendo de cámara, disposición sanitaria ni "
                 "paralización: UF 3.725,00 menos.", None),
                ("Sin subrogación: la falla obedeció a defecto de aislamiento del "
                 "devanado de un equipo de 6 años, fuera de garantía del fabricante y sin "
                 "tercero responsable identificable.", None),
            ),
        )

        # --- the renewal case, seeded from the nota de cierre ----------------
        #
        # A renewal is a SIBLING IN TIME of the account it renews, not a child
        # of its policy (spec v3 §1, §3.3 row 16): `origin=renewal` +
        # `origin_case_file_id` carry that link, while `policy_id` and
        # `parent_case_file_id` stay NULL — the outgoing policy is prior
        # context (`meta.prior_policy_ids`), not the folder's anchor. It opens
        # in the NEXT vigencia (05-01-2028 -> 05-01-2029) with its own draft
        # placement over the same asset, exactly as `POST /case-files/{id}/renew`
        # would build it.
        renewal_period_start = d("2028-01-05")
        renewal_period_end = d("2029-01-05")
        renewal_case = self.child_case(
            spec=spec, broker=broker, client=client, line=line, parent_id=None,
            policy_id=None, kind=CaseFileKind.RENEWAL,
            stage=CaseStage.RENEWAL_REVIEW, status=CaseFileStatus.OPEN,
            suffix="RN1", sequence_no=1,
            origin=CaseOrigin.RENEWAL,
            origin_case_file_id=case_file_id,
            period_start=renewal_period_start,
            period_end=renewal_period_end,
            title="Renovación 05-01-2028 · Incendio y Riesgos Adicionales con PxP",
            opened_on="2027-12-22", closed_on=None, owner_id=owner_id,
            summary=(
                "La siniestralidad del año es 1146,8% sobre prima neta y HDI la va a "
                "poner sobre la mesa. La posición de la corredora: el riesgo que HDI "
                "suscribió en enero de 2027 estaba en nota 35,6 clase E y el que renueva "
                "está en 74,8 clase B por su propia re-inspección; el MFL bajó de "
                "UF 70.840 a UF 34.200; las doce medidas se ejecutaron íntegras y dentro "
                "de plazo; ninguna garantía fue incumplida. Un recargo de tarifa sobre "
                "un solo año de siniestralidad, en una cuenta que acaba de invertir "
                "UF 9.284 en reducir su propio riesgo, es una señal equivocada."
            ),
            steps=((CaseStage.RENEWAL_REVIEW, "2027-12-22"),),
            meta={
                "renewal_target_date": "2028-01-05",
                "prior_policy_number": "15-04-0091883",
                # `policy_id` is NULL on a renewal folder: the outgoing policy
                # is context, not the anchor. Same shape `/renew` records.
                "prior_policy_ids": [policy_id],
                "origin_case_file_id": case_file_id,
                "loss_ratio_pct": "1146.8",
                "paid_uf": "4397.00",
                "paid_on": "2027-12-19",
                "days_from_notice_to_payment": 41,
                "deductibles_borne_uf": "578.00",
                "score_before": "35.6",
                "score_after": "74.8",
                "engineering_investment_uf": "9284.20",
                "claim_history": [
                    {"date": "2024-02-06", "event": "Falla del compresor de refrigerados",
                     "programme": "Porvenir · sin cobertura de frío", "outcome": "RECHAZADO",
                     "paid_uf": "0"},
                    {"date": "2025-09-14", "event": "Corte de suministro de 11 horas",
                     "programme": "Porvenir · sin cobertura de frío", "outcome": "RECHAZADO",
                     "paid_uf": "0"},
                    {"date": "2027-11-08", "event": "Falla del compresor de congelados",
                     "programme": "HDI · con cobertura de frío y plan ejecutado",
                     "outcome": "PAGADO", "paid_uf": "4397.00"},
                ],
                "seeded_from": "14 Nota de Cierre del Corredor",
            },
        )
        # The renewal folder's own draft placement: same asset, same line, the
        # NEXT period. This is what makes the renewal a real, workable account
        # instead of a marker — and it is why `--reset-cases` has to purge
        # placements by `Placement.case_file_id`, not only by
        # `CaseFile.placement_id`.
        renewal_placement = Placement(
            broker_id=broker.id,
            client_id=client.id,
            asset_id=placement.asset_id,
            insurance_line_id=line.id,
            period=period_label(renewal_period_start, renewal_period_end),
            period_start=renewal_period_start,
            period_end=renewal_period_end,
            status=PlacementStatus.DRAFT,
            case_file_id=renewal_case,
        )
        self.db.add(renewal_placement)
        self.db.flush()
        self.counts["placement"] += 1
        renewal_row = self.db.get(CaseFile, renewal_case)
        if renewal_row is not None:
            renewal_row.placement_id = renewal_placement.id
        self.add_member(
            case_file_id=renewal_case,
            broker_id=broker.id,
            client_id=client.id,
            role=AccountClientRole.POLICYHOLDER,
            is_primary=True,
        )
        self.account_ctx[renewal_case] = {
            "account_group_id": client.account_group_id,
            "period_start": renewal_period_start,
            "period_end": renewal_period_end,
        }
        self.db.flush()

        self.add_notes(
            case_file_id=renewal_case, broker_id=broker.id, authors=authors,
            notes=(
                ("Preparar la carpeta de renovación con el informe de re-inspección de "
                 "HDI del 13-10-2027 y el control de cumplimiento de las 12 medidas: es "
                 "el activo de suscripción de la cuenta.", "2027-12-28"),
                ("Mantener las pruebas periódicas al día (red húmeda, generador, "
                 "termografía) antes del 05-01-2028: son garantías vivas de la póliza "
                 "saliente y serán condición de la entrante.", "2028-01-02"),
                ("Si HDI recarga tarifa, re-cotizar con Chubb y Consorcio dejando por "
                 "escrito la diferencia de franquicia horaria: 4 h contra 8 h y 12 h.",
                 "2028-01-03"),
            ),
        )

    # =========================================================================
    # Mirror sources (spec §7.6)
    # =========================================================================

    def seed_mirror_sources(self, *, spec: CaseSpec, broker: Broker,
                            case_file_id: int, confirmed_by: int | None) -> None:
        """Seed the CONFIRMED 07/08 extraction pair behind the mirror-diff.

        A plain import writes structure only, so ``GET /policies/{id}/mirror-diff``
        would answer ``missing_sources`` and the ``mirror_validation`` stage would
        demo empty. These two rows are human transcriptions of the corpus
        documents (see the ``_la_favorita_*_payload`` builders above), validated
        against the registry schemas and stored ``succeeded`` — the same state a
        reviewer-confirmed AI extraction reaches. No AI ran and none is claimed:
        ``model`` says so, and rule 6 (suggest -> confirm -> commit) is about the
        AI write path, which this is not.
        """
        from app.schemas.extraction.registry import spec_for  # noqa: PLC0415

        proposal_document_id = self.doc(spec.key, "07")
        policy_document_id = self.doc(spec.key, "08")
        if proposal_document_id is None or policy_document_id is None:
            self.log.warn(
                f"{spec.key}: 07/08 documents not imported — mirror sources not seeded"
            )
            return

        for category, document_id, payload in (
            (DocumentCategory.ISSUANCE_PROPOSAL, proposal_document_id,
             _la_favorita_issuance_proposal_payload()),
            (DocumentCategory.POLICY, policy_document_id,
             _la_favorita_policy_payload()),
        ):
            registry_spec = spec_for(category)
            parsed = registry_spec.schema.model_validate(payload)
            self.db.add(
                Extraction(
                    broker_id=broker.id,
                    document_id=document_id,
                    case_file_id=case_file_id,
                    kind=ExtractionKind.CASE_DOCUMENT,
                    category=category,
                    model="human/corpus-transcription",
                    prompt_version=registry_spec.prompt_version,
                    parsed=parsed.model_dump(mode="json", exclude_none=True),
                    confidence=uf("100"),
                    status=ExtractionStatus.SUCCEEDED,
                    created_by_id=confirmed_by,
                )
            )
            self.counts["extraction"] += 1
        self.log.ok(
            "mirror sources seeded: confirmed issuance_proposal (07) + policy (08) "
            "extractions for the 15-04-0091883 diff"
        )

    # =========================================================================
    # Optional AI pass
    # =========================================================================

    def run_extractions(self) -> None:
        """``--extract``: one real extraction per category, so the demo has provenance.

        Off by default — without it the import is pure structure and needs no
        ``AI_API_KEY``. Suggest -> confirm -> commit still applies: these rows are
        written UNCONFIRMED and nothing is auto-applied to a domain entity.
        """
        self.log.section("AI extractions (--extract)")
        try:
            from app.services.ai import extract_document  # noqa: PLC0415
        except Exception as exc:  # pragma: no cover - impl-ai lands separately
            self.log.warn(f"--extract requested but app.services.ai is unavailable: {exc}")
            return

        seen: set[DocumentCategory] = set()
        done = failed = 0
        for spec in CASES:
            case_file_id = self.case_id.get(spec.key)
            if case_file_id is None:
                continue
            broker = self.broker(spec.broker_key)
            user = self.user(broker.id, "broker_technician") or self.user(
                broker.id, "broker_admin"
            )
            for corpus_file in self.files.get(spec.key, []):
                if corpus_file.category in seen:
                    continue
                seen.add(corpus_file.category)
                document_id = self.ids.get("document", f"{spec.key}::{corpus_file.relative}")
                try:
                    extract_document(
                        self.db,
                        document_id=document_id,
                        broker_id=broker.id,
                        user=user,
                        category=corpus_file.category,
                    )
                    done += 1
                except Exception as exc:  # an LLM hiccup must never abort the import
                    failed += 1
                    self.log.warn(
                        f"extraction failed for {corpus_file.name!r} "
                        f"({corpus_file.category.value}): {exc}"
                    )
        self.log.ok(f"{done} extraction(s) written, {failed} failed across {len(seen)} categories")

    # =========================================================================
    # Orchestration
    # =========================================================================

    def run(self) -> None:
        self.log.section("Preconditions")
        self.require_fixture_world()

        self.log.section("Corpus scan + id allocation")
        self.allocate_ids()

        for spec in CASES:
            self.log.section(f"{spec.reference} · {spec.title}  ->  {spec.stage.value}")
            self.build_case(spec)
            self.log.ok(
                f"{len(self.files.get(spec.key, []))} document(s), "
                f"stage {spec.stage.value}, {len(spec.notes)} note(s)"
            )

        if self.extract:
            self.run_extractions()


# =============================================================================
# --reset-cases
# =============================================================================


def reset_cases(db: Session, log: Log) -> None:
    """Delete ONLY what this importer creates. The fixture world is untouched.

    Every case this importer writes carries a ``reference`` starting with
    ``EXP-``; everything else it writes points at one of those cases through a
    ``case_file_id``. That is the whole blast radius — brokers, users, insureds
    shared with the fixture package, the insurer catalog and the six seeded
    insurance lines all survive.

    Three things the groups pass added to the radius (spec v3 §3.3):
    ``account_client`` membership rows; placements reached through
    ``Placement.case_file_id`` and not only ``CaseFile.placement_id`` (one
    folder owns N placements, and the renewal folder's cloned draft is one);
    and ``account_group`` rows that end up with NO clients and NO cases. In
    practice the corpus clients survive a reset, so their groups do too and the
    next run reuses them by slug — which is what makes a second import produce
    identical counts.
    """
    log.section("Reset (--reset-cases)")
    case_ids = [
        row for row in db.scalars(
            select(CaseFile.id).where(CaseFile.reference.like("EXP-%"))
        ).all()
    ]
    if not case_ids:
        log.ok("no EXP-* case files present — nothing to reset")
        return

    claim_ids = list(db.scalars(select(Claim.id).where(Claim.case_file_id.in_(case_ids))).all())
    plan_ids = list(
        db.scalars(select(CollectionPlan.id).where(CollectionPlan.case_file_id.in_(case_ids))).all()
    )
    quote_ids = list(
        db.scalars(select(QuoteRequest.id).where(QuoteRequest.case_file_id.in_(case_ids))).all()
    )
    # BOTH directions: `case_file.placement_id` is only the PRIMARY placement,
    # while one folder can own N placements through `placement.case_file_id`
    # (the renewal folder's cloned draft is one). Purging only the first would
    # leave orphan placements behind and break re-import idempotency.
    placement_ids = sorted(
        {
            pid for pid in db.scalars(
                select(CaseFile.placement_id).where(CaseFile.id.in_(case_ids))
            ).all() if pid is not None
        }
        | {
            pid for pid in db.scalars(
                select(Placement.id).where(Placement.case_file_id.in_(case_ids))
            ).all() if pid is not None
        }
    )
    policy_ids = list(db.scalars(select(Policy.id).where(Policy.case_file_id.in_(case_ids))).all())

    removed: Counter = Counter()

    def run_delete(label: str, statement: Any) -> None:
        result = db.execute(statement)
        removed[label] += int(result.rowcount or 0)

    if claim_ids:
        run_delete("claim_item", delete(ClaimItem).where(ClaimItem.claim_id.in_(claim_ids)))
    if plan_ids:
        run_delete(
            "collection_installment",
            delete(CollectionInstallment).where(
                CollectionInstallment.collection_plan_id.in_(plan_ids)
            ),
        )
    run_delete("claim", delete(Claim).where(Claim.case_file_id.in_(case_ids)))
    run_delete("collection_plan", delete(CollectionPlan).where(CollectionPlan.case_file_id.in_(case_ids)))
    run_delete("warranty", delete(Warranty).where(Warranty.case_file_id.in_(case_ids)))
    run_delete("endorsement", delete(Endorsement).where(Endorsement.case_file_id.in_(case_ids)))
    run_delete("case_pack", delete(CasePack).where(CasePack.case_file_id.in_(case_ids)))
    run_delete(
        "account_client",
        delete(AccountClient).where(AccountClient.case_file_id.in_(case_ids)),
    )
    run_delete(
        "case_file_stage_event",
        delete(CaseFileStageEvent).where(CaseFileStageEvent.case_file_id.in_(case_ids)),
    )
    run_delete(
        "note",
        delete(Note).where(
            Note.entity_type == EntityType.CASE_FILE, Note.entity_id.in_(case_ids)
        ),
    )
    run_delete(
        "activity",
        delete(Activity).where(
            Activity.entity_type == EntityType.CASE_FILE, Activity.entity_id.in_(case_ids)
        ),
    )
    run_delete("inspection.case_file", Inspection.__table__.delete().where(
        Inspection.__table__.c.case_file_id.in_(case_ids)
    ))
    # Before the documents they point at (extraction.document_id is NOT NULL).
    run_delete(
        "extraction", delete(Extraction).where(Extraction.case_file_id.in_(case_ids))
    )
    run_delete("proposal", delete(Proposal).where(Proposal.case_file_id.in_(case_ids)))
    if quote_ids:
        run_delete(
            "quote_line_item",
            delete(QuoteLineItem).where(QuoteLineItem.quote_request_id.in_(quote_ids)),
        )
    run_delete("quote_request", delete(QuoteRequest).where(QuoteRequest.case_file_id.in_(case_ids)))
    if policy_ids:
        run_delete(
            "policy_location",
            PolicyLocation.__table__.delete().where(
                PolicyLocation.__table__.c.policy_id.in_(policy_ids)
            ),
        )
    # Break the document <-> policy/case cycle before deleting either side.
    db.execute(
        Policy.__table__.update()
        .where(Policy.__table__.c.case_file_id.in_(case_ids))
        .values(source_document_id=None)
    )
    db.flush()
    run_delete("document", delete(Document).where(Document.case_file_id.in_(case_ids)))
    run_delete("policy", delete(Policy).where(Policy.case_file_id.in_(case_ids)))
    if placement_ids:
        db.execute(
            Placement.__table__.update()
            .where(Placement.__table__.c.id.in_(placement_ids))
            .values(case_file_id=None, brief_document_id=None)
        )
    run_delete(
        "sales_lead",
        delete(SalesLead).where(
            SalesLead.name.in_([
                "JO Pastelería — Accidentes Personales colectivo",
            ])
        ),
    )
    # Children first: parent_case_file_id / supersedes_case_file_id are SET NULL,
    # but deleting bottom-up keeps the intent obvious.
    db.execute(
        CaseFile.__table__.update()
        .where(CaseFile.__table__.c.id.in_(case_ids))
        .values(parent_case_file_id=None, supersedes_case_file_id=None, policy_id=None,
                placement_id=None)
    )
    db.flush()
    run_delete("case_file", delete(CaseFile).where(CaseFile.id.in_(case_ids)))
    if placement_ids:
        run_delete("placement", delete(Placement).where(Placement.id.in_(placement_ids)))
    db.flush()

    # Groups are only removed once NOTHING points at them any more. The corpus
    # clients survive a --reset-cases (this importer never created the fixture
    # world), so in practice their groups survive too and the next run reuses
    # them by slug — which is exactly what keeps the counts identical.
    referenced = {
        gid for gid in db.scalars(
            select(Client.account_group_id).where(Client.account_group_id.is_not(None))
        ).all()
    } | {
        gid for gid in db.scalars(
            select(CaseFile.account_group_id).where(CaseFile.account_group_id.is_not(None))
        ).all()
    }
    orphan_groups = [
        gid for gid in db.scalars(select(AccountGroup.id)).all() if gid not in referenced
    ]
    if orphan_groups:
        run_delete(
            "account_group",
            delete(AccountGroup).where(AccountGroup.id.in_(orphan_groups)),
        )
    db.flush()

    for label, count in sorted(removed.items()):
        if count:
            log.info(f"deleted {count:>4} {label}")
    log.ok(f"{sum(removed.values())} row(s) removed across {len(case_ids)} case file(s)")


# =============================================================================
# Reporting
# =============================================================================


def report(db: Session, log: Log) -> dict[str, int]:
    log.section("Case files")
    rows = db.execute(
        select(CaseFile.reference, CaseFile.kind, CaseFile.stage, CaseFile.title)
        .order_by(CaseFile.broker_id, CaseFile.id)
    ).all()
    for reference, kind, stage, title in rows:
        print(f"  {str(reference):<18} {str(kind):<12} {str(stage):<22} {title}")

    log.section("Row counts")
    counted: list[tuple[str, Any]] = [
        ("account_group", AccountGroup),
        ("account_client", AccountClient),
        ("case_file", CaseFile),
        ("case_file_stage_event", CaseFileStageEvent),
        ("case_pack", CasePack),
        ("sales_lead", SalesLead),
        ("document", Document),
        ("placement", Placement),
        ("quote_request", QuoteRequest),
        ("quote_line_item", QuoteLineItem),
        ("proposal", Proposal),
        ("inspection", Inspection),
        ("policy", Policy),
        ("policy_location", PolicyLocation),
        ("endorsement", Endorsement),
        ("collection_plan", CollectionPlan),
        ("collection_installment", CollectionInstallment),
        ("warranty", Warranty),
        ("claim", Claim),
        ("claim_item", ClaimItem),
        ("extraction", Extraction),
        ("note", Note),
        ("activity", Activity),
    ]
    counts: dict[str, int] = {}
    width = max(len(name) for name, _ in counted)
    for name, model in counted:
        counts[name] = int(db.scalar(select(func.count()).select_from(model)) or 0)
        print(f"  {name.ljust(width)}  {counts[name]:>4}")

    log.section("Documents by section")
    section_rows = db.execute(
        select(Document.section, func.count())
        .where(Document.case_file_id.is_not(None))
        .group_by(Document.section)
        .order_by(Document.section)
    ).all()
    for section, count in section_rows:
        print(f"  {str(section):<16} {count:>4}")
    other = int(
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.case_file_id.is_not(None),
                Document.category == DocumentCategory.OTHER,
            )
        )
        or 0
    )
    counts["case_document"] = int(
        db.scalar(
            select(func.count()).select_from(Document).where(Document.case_file_id.is_not(None))
        )
        or 0
    )
    if other:
        log.warn(f"{other} case document(s) landed in category `other` — classification gap")
    else:
        log.ok(f"{counts['case_document']} case documents, 0 in category `other`")
    return counts


def self_check(db: Session, log: Log, *, full: bool = False) -> int:
    """Assert the shape spec v3 §3.3 promises. Returns the number of failures.

    These are not unit tests — they are the guarantees the demo screens are
    built on, checked against the database that was actually written. A failure
    is a WARNING, not an exit code: `--full` and a partially-skipped re-import
    are both legitimate states, and the operator needs the whole report either
    way.
    """
    log.section("Self-check (spec v3 §3.3)")
    failures = 0

    def check(label: str, ok: bool, got: Any = None) -> None:
        nonlocal failures
        if ok:
            log.ok(label if got is None else f"{label} ({got})")
        else:
            failures += 1
            log.warn(f"{label} — FAILED (got {got!r})")

    def group_by_account(account: str) -> AccountGroup | None:
        return db.scalars(
            select(AccountGroup).where(AccountGroup.slug == group_slug(account))
        ).first()

    # -- 7 groups: 3 from the fixtures + 4 from the corpus --------------------
    groups = int(db.scalar(select(func.count()).select_from(AccountGroup)) or 0)
    check("7 account_group rows (3 fixture + 4 corpus)", groups == 7, groups)

    # -- every account/renewal folder is grouped, dated and has a contratante -
    account_kinds = (CaseFileKind.ACCOUNT, CaseFileKind.RENEWAL)
    accounts = list(
        db.scalars(
            select(CaseFile)
            .where(CaseFile.kind.in_(account_kinds), CaseFile.reference.like("EXP-%"))
            .order_by(CaseFile.id)
        ).all()
    )
    ungrouped = [c.reference for c in accounts if c.account_group_id is None]
    check("every account folder carries account_group_id", not ungrouped, ungrouped)
    undated = [
        c.reference for c in accounts if c.period_start is None or c.period_end is None
    ]
    check("every account folder carries a full period", not undated, undated)
    unlabelled = [c.reference for c in accounts if not c.period_label]
    check("every account folder carries a period_label", not unlabelled, unlabelled)
    primaries = {
        cid for cid in db.scalars(
            select(AccountClient.case_file_id).where(AccountClient.is_primary.is_(True))
        ).all()
    }
    memberless = [c.reference for c in accounts if c.id not in primaries]
    check("every account folder has a primary account_client", not memberless, memberless)

    # -- post-sale children inherit the group and the period of their account -
    children = list(
        db.scalars(
            select(CaseFile)
            .where(CaseFile.parent_case_file_id.is_not(None))
            .order_by(CaseFile.id)
        ).all()
    )
    by_id = {c.id: c for c in db.scalars(select(CaseFile)).all()}
    drift = [
        c.reference
        for c in children
        if (parent := by_id.get(c.parent_case_file_id)) is not None
        and (
            c.account_group_id != parent.account_group_id
            or c.period_start != parent.period_start
            or c.period_end != parent.period_end
        )
    ]
    check(
        f"{len(children)} post-sale children inherit group + period from their account",
        not drift,
        drift,
    )

    # -- GRUPO VIÑA INDOMITA: ONE group, TWO RUTs, TWO folders ----------------
    vina = group_by_account(INSURED_BY_KEY["vina_indomita"].account)
    if vina is None:
        check("GRUPO VIÑA INDOMITA group exists", False, None)
    else:
        vina_clients = int(
            db.scalar(
                select(func.count()).select_from(Client)
                .where(Client.account_group_id == vina.id)
            ) or 0
        )
        vina_accounts = [c for c in accounts if c.account_group_id == vina.id]
        check("Viña Indómita: 1 group over 2 clients", vina_clients == 2, vina_clients)
        check(
            "Viña Indómita: 2 account folders, 2 vigencias",
            len(vina_accounts) == 2
            and len({c.period_label for c in vina_accounts}) == 2,
            [(c.reference, c.period_label) for c in vina_accounts],
        )

    # -- COCCOLINO: two lines, DIFFERENT dates, ONE label ---------------------
    coccolino = group_by_account(INSURED_BY_KEY["coccolino"].account)
    if coccolino is None:
        check("COCCOLINO group exists", False, None)
    else:
        rows = [c for c in accounts if c.account_group_id == coccolino.id]
        spans = {(c.period_start, c.period_end) for c in rows}
        labels = {c.period_label for c in rows}
        check(
            "Coccolino: 2 folders, 2 different date spans, 1 label (vigencia is a LABEL)",
            len(rows) == 2 and len(spans) == 2 and labels == {"2026-2027"},
            [(c.reference, str(c.period_start), str(c.period_end), c.period_label)
             for c in rows],
        )

    # -- the renewal folder is a SIBLING IN TIME, not a post-sale child -------
    renewal = db.scalars(
        select(CaseFile).where(CaseFile.reference == "EXP-2026-0005-RN1")
    ).first()
    if renewal is None:
        check("EXP-2026-0005-RN1 present", False, None)
    else:
        source = by_id.get(renewal.origin_case_file_id or 0)
        renewal_placements = int(
            db.scalar(
                select(func.count()).select_from(Placement)
                .where(Placement.case_file_id == renewal.id)
            ) or 0
        )
        check(
            "RN1: kind=renewal, origin=renewal, origin_case_file_id -> LA FAVORITA",
            renewal.kind == CaseFileKind.RENEWAL
            and renewal.origin == CaseOrigin.RENEWAL
            and source is not None
            and source.reference == "EXP-2026-0005",
            (str(renewal.kind), str(renewal.origin),
             source.reference if source else None),
        )
        check(
            "RN1: policy_id and parent_case_file_id are NULL (not a post-sale child)",
            renewal.policy_id is None and renewal.parent_case_file_id is None,
            (renewal.policy_id, renewal.parent_case_file_id),
        )
        check(
            "RN1: own draft placement attached, next vigencia 2028-2029",
            renewal_placements == 1
            and renewal.placement_id is not None
            and renewal.period_label == "2028-2029",
            (renewal_placements, renewal.placement_id, renewal.period_label),
        )

    # -- the headline row counts ----------------------------------------------
    expected_docs = 111 if full else 89
    for label, model, want in (
        ("case_file", CaseFile, 16),
        ("policy", Policy, 2),
        ("endorsement", Endorsement, 4),
        ("collection_plan", CollectionPlan, 2),
        ("claim", Claim, 2),
        ("warranty", Warranty, 19),
    ):
        got = int(db.scalar(select(func.count()).select_from(model)) or 0)
        check(f"{label} == {want}", got == want, got)
    case_docs = int(
        db.scalar(
            select(func.count()).select_from(Document).where(Document.case_file_id.is_not(None))
        ) or 0
    )
    check(f"case documents == {expected_docs}", case_docs == expected_docs, case_docs)

    if failures:
        log.warn(f"{failures} self-check(s) failed — the demo shape is NOT as specified")
    else:
        log.ok("every self-check passed")
    return failures


def report_classification(source: Path, log: Log) -> int:
    """``--dry-run``: classify every corpus file and print the tally, write nothing."""
    log.section("Classification (dry run — the whole corpus, no filtering)")
    by_category: Counter = Counter()
    by_section: Counter = Counter()
    unresolved: list[str] = []
    total = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.name in IGNORED_FILE_NAMES:
            continue
        relative = path.relative_to(source)
        # The expediente root is the account folder plus, when the account has
        # more than one line, the line folder (LA FAVORITA has none). Neither is
        # a sub-expediente, so drop everything before the first "N. ..." folder.
        parts = relative.parts[:-1]
        folder_parts = tuple(
            part for part in parts
            if _ORDINAL_HEAD.match(part) or "subexpediente" in norm_token(part)
        )
        try:
            section = section_for(folder_parts)
            category, _code = classify(section, path.name)
        except (UnknownSection, UnknownCategory) as exc:
            unresolved.append(f"{relative.as_posix()} — {exc}")
            continue
        total += 1
        by_category[category.value] += 1
        by_section[section.value] += 1
    width = max((len(k) for k in by_category), default=10)
    for name, count in sorted(by_category.items()):
        print(f"  {name.ljust(width)}  {count:>4}")
    print()
    for name, count in sorted(by_section.items()):
        print(f"  section {name:<16} {count:>4}")
    log.ok(f"{total} file(s) classified, 0 in `other`" if not by_category.get("other")
           else f"{total} file(s) classified, {by_category['other']} in `other`")
    for line in unresolved:
        log.warn(f"unresolved: {line}")
    return 1 if unresolved else 0


# =============================================================================
# CLI
# =============================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.db.import_expedientes",
        description=(
            "Import the EXPEDIENTES DEMO corpus as 7 staged case files. Run "
            "`python -m app.db.import_fixtures` first."
        ),
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(os.environ.get("RADAL_EXPEDIENTES_PATH", str(DEFAULT_SOURCE))),
        help='Path to the corpus root (default: ~/Downloads/"EXPEDIENTES DEMO")',
    )
    parser.add_argument("--bucket", default=settings.S3_BUCKET, help="Target S3 bucket")
    parser.add_argument("--region", default=settings.S3_REGION, help="AWS region")
    parser.add_argument("--profile", default="radal", help="AWS CLI profile used for uploads")
    parser.add_argument(
        "--no-upload", action="store_true", help="Import to the DB but skip every S3 call"
    )
    parser.add_argument(
        "--no-local-copy",
        action="store_true",
        help=(
            "Skip mirroring corpus bytes into MEDIA_LOCAL_DIR. By default the "
            "bytes are mirrored whenever uploads are off or MEDIA_BACKEND=local, "
            "so /documents/{id}/content works without S3"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify the whole corpus and print the tally; write nothing, touch no DB",
    )
    parser.add_argument(
        "--reset-cases",
        action="store_true",
        help="Delete the case files this importer created (and only those) before importing",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also import the documents of the lead-stage expediente (JO Pastelería AP)",
    )
    parser.add_argument(
        "--extract",
        action="store_true",
        help="Run extract_document over one document per category (needs AI_API_KEY)",
    )
    parser.add_argument("--quiet", action="store_true", help="Only print section results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = Log(verbose=not args.quiet)

    source = args.source.expanduser()
    if not source.is_dir():
        print(f"error: no such corpus directory: {source}", file=sys.stderr)
        return 2

    if args.dry_run:
        return report_classification(source, log)

    log.section("Schema")
    Base.metadata.create_all(bind=engine)
    log.ok(f"create_all on {engine.url.render_as_string(hide_password=True)}")

    uploader = Uploader(
        bucket=args.bucket,
        region=args.region,
        profile=args.profile,
        enabled=not args.no_upload,
        log=log,
        local_dir=Uploader.resolve_local_dir(
            upload_enabled=not args.no_upload,
            no_local_copy=args.no_local_copy,
        ),
    )
    if uploader.enabled:
        log.info(f"uploading to s3://{uploader.bucket} with AWS profile {args.profile!r}")
    else:
        log.info("S3 uploads disabled — document rows still get their v2 keys")
    if uploader.local_dir is not None:
        log.info(f"mirroring corpus bytes into {uploader.local_dir} (MEDIA_BACKEND=local layout)")

    db = SessionLocal()
    try:
        if args.reset_cases:
            reset_cases(db, log)
            db.commit()

        importer = ExpedienteImporter(
            db=db, source=source, uploader=uploader, log=log,
            full=args.full, extract=args.extract,
        )
        try:
            importer.run()
        except FixtureError as exc:
            db.rollback()
            print(f"error: {exc}", file=sys.stderr)
            return 1
        db.commit()

        counts = report(db, log)
        self_check(db, log, full=args.full)

        log.section("Summary")
        for line in importer.imported:
            log.ok(line)
        for line in importer.skipped:
            log.info(f"skipped (already present): {line}")
        stages = int(
            db.scalar(
                select(func.count(func.distinct(CaseFile.stage)))
                .select_from(CaseFile)
                .where(CaseFile.kind == CaseFileKind.ACCOUNT)
            )
            or 0
        )
        log.ok(
            f"{counts['account_group']} account group(s), "
            f"{counts['account_client']} membership(s), "
            f"{counts['case_file']} case file(s) — "
            f"{stages} distinct account stages, "
            f"{counts['case_document']} case document(s) "
            f"({counts['document']} in the document table overall), "
            f"{counts['policy']} policy(ies), "
            f"{counts['endorsement']} endorsement(s), "
            f"{counts['collection_plan']} collection plan(s), "
            f"{counts['claim']} claim(s), {counts['warranty']} warranty(ies)"
        )
        if uploader.enabled:
            log.ok(f"{uploader.uploaded} files uploaded to s3://{uploader.bucket}")
        else:
            log.ok(f"{uploader.skipped} file uploads skipped (--no-upload)")
        if uploader.local_dir is not None:
            log.ok(f"{uploader.copied} files mirrored into {uploader.local_dir}")
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
