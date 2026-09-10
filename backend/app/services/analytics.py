"""One filter vocabulary, one group-by engine, one export registry.

Why this module exists
----------------------
``/analytics`` used to be one page that fused a KPI dashboard with six data
tables. It is being split into **Datos** (the tables) and **Analítica** (the
visuals), and both halves must speak the *same* filter language: the **grupo**
(``account_group_id``), the **grupo-cuenta** (``case_file_id``) and a date
window on the entity's natural date column. Rather than hand-rolling that in
thirteen routers, every router composes the pieces here.

The three rules this module never breaks
----------------------------------------
1. **Tenant scope first.** ``broker_id`` is always applied by the caller; every
   predicate built here *also* re-asserts ``broker_id`` on the row it hops to.
   A foreign ``account_group_id`` therefore yields an **empty page**, never
   another tenant's rows and never a 403 that would confirm the id exists
   (non-negotiable 2).
2. **No denormalised column.** There is no Alembic (non-negotiable 10), so the
   group is resolved through the entity's *real* path — usually
   ``case_file.account_group_id``, sometimes ``client.account_group_id`` — with
   correlated ``EXISTS`` sub-selects. ``EXISTS`` (rather than a join) keeps the
   existing query shape intact: no row multiplication, no pagination drift.
3. **Never invent a dimension.** ``group_by`` is validated against the entity's
   own list; an unsupported one is a **422**, never a silently ignored param.

Nulls and date windows
----------------------
A date bound filters the entity's declared date column. Rows whose date column
is NULL are **excluded** while a bound is present (they cannot be placed in the
window honestly) and included when no bound is given. Each entity's chosen
column is documented in ``ENTITIES`` and echoed in every router docstring.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Sequence

from fastapi import Depends, HTTPException, Query, status
from sqlalchemy import DateTime, func, or_, select
from sqlalchemy.orm import Session

from app.models.account_client import AccountClient
from app.models.account_group import AccountGroup
from app.models.asset import Asset
from app.models.case_file import CaseFile
from app.models.client import Client
from app.models.collection import CollectionPlan
from app.models.document import Document
from app.models.endorsement import Endorsement
from app.models.inspection import Inspection
from app.models.insurance_line import InsuranceLine
from app.models.insurer import Insurer
from app.models.offering import Offering
from app.models.placement import Placement
from app.models.policy import Claim, Policy
from app.models.proposal import Proposal
from app.models.quote import QuoteRequest
from app.models.sales_lead import SalesLead
from app.schemas.analytics import (
    EntitySummary,
    GroupedSummary,
    ScopeFilterPayload,
    SummaryBucket,
    unsupported_group_by,
)

# The hard ceiling on an export. Above it the caller gets a 422 telling them to
# narrow the filters, instead of a Lambda timeout half a megabyte in.
EXPORT_ROW_CAP = 10_000

UNASSIGNED = "unassigned"

_ONE_DAY = timedelta(days=1)


# ---------------------------------------------------------------------------
# The filter dependency
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScopeFilters:
    """The uniform scope every list / summary endpoint accepts."""

    account_group_id: int | None = None
    case_file_id: int | None = None
    date_from: date | None = None
    date_to: date | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.account_group_id, self.case_file_id, self.date_from, self.date_to)
        )

    def describe(self) -> dict[str, Any]:
        return {
            "account_group_id": self.account_group_id,
            "case_file_id": self.case_file_id,
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
        }


def scope_filters(
    account_group_id: int | None = Query(
        default=None,
        ge=1,
        description=(
            "Grupo (account_group). Se resuelve por el camino real de la entidad "
            "al grupo; un grupo de otro corredor devuelve una página vacía."
        ),
    ),
    case_file_id: int | None = Query(
        default=None,
        ge=1,
        description="Grupo-cuenta (case_file / expediente).",
    ),
    date_from: date | None = Query(
        default=None, description="Desde (inclusive) sobre la fecha natural de la entidad."
    ),
    date_to: date | None = Query(
        default=None, description="Hasta (inclusive) sobre la fecha natural de la entidad."
    ),
) -> ScopeFilters:
    """FastAPI dependency: the four scope params, identical on every endpoint."""
    return ScopeFilters(
        account_group_id=account_group_id,
        case_file_id=case_file_id,
        date_from=date_from,
        date_to=date_to,
    )


ScopeFiltersDep = Depends(scope_filters)


def filters_from_payload(payload: ScopeFilterPayload | None) -> ScopeFilters:
    """Body -> :class:`ScopeFilters` (the exports take the same vocabulary)."""
    payload = payload or ScopeFilterPayload()
    return ScopeFilters(
        account_group_id=payload.account_group_id,
        case_file_id=payload.case_file_id,
        date_from=payload.date_from,
        date_to=payload.date_to,
    )


# ---------------------------------------------------------------------------
# EXISTS helpers — the entity's real path to the grupo
# ---------------------------------------------------------------------------

def _case_file_in_group(fk, broker_id: int, group_id: int):
    return (
        select(1)
        .where(
            CaseFile.id == fk,
            CaseFile.broker_id == broker_id,
            CaseFile.account_group_id == group_id,
        )
        .exists()
    )


def _client_in_group(fk, broker_id: int, group_id: int):
    return (
        select(1)
        .where(
            Client.id == fk,
            Client.broker_id == broker_id,
            Client.account_group_id == group_id,
        )
        .exists()
    )


def _policy_in_group(fk, broker_id: int, group_id: int):
    """A policy belongs to the grupo through its expediente or its contratante."""
    return (
        select(1)
        .where(
            Policy.id == fk,
            Policy.broker_id == broker_id,
            or_(
                _case_file_in_group(Policy.case_file_id, broker_id, group_id),
                _client_in_group(Policy.client_id, broker_id, group_id),
            ),
        )
        .exists()
    )


def _quote_in_group(fk, broker_id: int, group_id: int):
    return (
        select(1)
        .where(
            QuoteRequest.id == fk,
            QuoteRequest.broker_id == broker_id,
            _case_file_in_group(QuoteRequest.case_file_id, broker_id, group_id),
        )
        .exists()
    )


def _asset_in_group(fk, broker_id: int, group_id: int):
    return (
        select(1)
        .where(
            Asset.id == fk,
            Asset.broker_id == broker_id,
            _client_in_group(Asset.client_id, broker_id, group_id),
        )
        .exists()
    )


def _policy_in_case(fk, broker_id: int, case_file_id: int):
    return (
        select(1)
        .where(
            Policy.id == fk,
            Policy.broker_id == broker_id,
            Policy.case_file_id == case_file_id,
        )
        .exists()
    )


def _quote_in_case(fk, broker_id: int, case_file_id: int):
    return (
        select(1)
        .where(
            QuoteRequest.id == fk,
            QuoteRequest.broker_id == broker_id,
            QuoteRequest.case_file_id == case_file_id,
        )
        .exists()
    )


def _client_is_member(fk, broker_id: int, case_file_id: int):
    """Rule 6: membership = ``account_client`` rows UNION ``placement.case_file_id``;
    ``case_file.client_id`` is the contratante."""
    return or_(
        select(1)
        .where(
            AccountClient.case_file_id == case_file_id,
            AccountClient.client_id == fk,
            AccountClient.broker_id == broker_id,
        )
        .exists(),
        select(1)
        .where(
            CaseFile.id == case_file_id,
            CaseFile.broker_id == broker_id,
            CaseFile.client_id == fk,
        )
        .exists(),
        select(1)
        .where(
            Placement.case_file_id == case_file_id,
            Placement.broker_id == broker_id,
            Placement.client_id == fk,
        )
        .exists(),
    )


# ---------------------------------------------------------------------------
# Dimensions + the entity registry
# ---------------------------------------------------------------------------

MONTH = "__month__"


@dataclass(frozen=True)
class Dimension:
    """One thing an entity can be grouped by."""

    key: str
    #: SQLAlchemy expression producing the bucket key, or ``MONTH`` for the
    #: (portable, Python-side) calendar-month bucketing.
    expr: Any
    #: Outer joins the grouped query must add for ``expr`` to resolve.
    joins: tuple[tuple[Any, Any], ...] = ()
    #: How to turn a raw key into a human label: None -> ``str(key)``.
    labeller: str | None = None


@dataclass(frozen=True)
class ExportColumn:
    key: str
    label: str
    kind: str  # text | int | uf | number | date | datetime | bool
    get: Callable[[Any], Any]


@dataclass(frozen=True)
class EntitySpec:
    entity: str
    label: str
    model: Any
    module: str  # RBAC module
    date_field: str
    date_column: Any
    money_field: str | None
    money_column: Any
    status_column: Any
    status_enum: Any
    group_predicate: Callable[[int, int], Any]
    case_predicate: Callable[[int, int], Any]
    dimensions: dict[str, Dimension]
    columns: tuple[ExportColumn, ...]
    order_column: Any
    search_predicate: Callable[[str], Any] | None = None

    @property
    def group_by_keys(self) -> list[str]:
        return list(self.dimensions.keys())


def _attr(*path: str) -> Callable[[Any], Any]:
    """Accessor down a relationship chain, tolerant of a missing link."""

    def _get(row: Any) -> Any:
        value: Any = row
        for name in path:
            if value is None:
                return None
            value = getattr(value, name, None)
        return value

    return _get


def _enum_value(*path: str) -> Callable[[Any], Any]:
    inner = _attr(*path)

    def _get(row: Any) -> Any:
        value = inner(row)
        if value is None:
            return None
        return getattr(value, "value", value)

    return _get


def _group_name(*path: str) -> Callable[[Any], Any]:
    return _attr(*path)


# --- Common dimension factories ---------------------------------------------

def _dim_month(column) -> Dimension:
    return Dimension(key="month", expr=MONTH, joins=(), labeller=None)


def _dim_account_group(expr, joins=()) -> Dimension:
    return Dimension(key="account_group", expr=expr, joins=joins, labeller="account_group")


# ---------------------------------------------------------------------------
# The registry. One entry per entity the Datos / Analítica screens can show.
# ---------------------------------------------------------------------------

def _build_registry() -> dict[str, EntitySpec]:
    from app.models.client import ClientStatus
    from app.models.enums import (
        CaseFileStatus,
        CollectionPlanStatus,
        EndorsementStatus,
        LeadStatus,
    )
    from app.models.inspection import InspectionStatus
    from app.models.offering import OfferingStatus
    from app.models.placement import PlacementStatus
    from app.models.policy import ClaimStatus, PolicyStatus
    from app.models.proposal import ProposalStatus
    from app.models.quote import QuoteRequestStatus

    registry: dict[str, EntitySpec] = {}

    # --- case_files ---------------------------------------------------------
    registry["case_files"] = EntitySpec(
        entity="case_files",
        label="Expedientes",
        model=CaseFile,
        module="CaseFiles",
        date_field="opened_at",
        date_column=CaseFile.opened_at,
        money_field=None,
        money_column=None,
        status_column=CaseFile.status,
        status_enum=CaseFileStatus,
        group_predicate=lambda b, g: CaseFile.account_group_id == g,
        case_predicate=lambda b, c: CaseFile.id == c,
        dimensions={
            "stage": Dimension("stage", CaseFile.stage),
            "status": Dimension("status", CaseFile.status),
            "kind": Dimension("kind", CaseFile.kind),
            "origin": Dimension("origin", CaseFile.origin),
            "account_group": _dim_account_group(CaseFile.account_group_id),
            "insurance_line": Dimension(
                "insurance_line", CaseFile.insurance_line_id, labeller="insurance_line"
            ),
            "month": _dim_month(CaseFile.opened_at),
        },
        columns=(
            ExportColumn("id", "ID", "int", _attr("id")),
            ExportColumn("reference", "Referencia", "text", _attr("reference")),
            ExportColumn("title", "Título", "text", _attr("title")),
            ExportColumn("kind", "Tipo", "text", _enum_value("kind")),
            ExportColumn("stage", "Etapa", "text", _enum_value("stage")),
            ExportColumn("status", "Estado", "text", _enum_value("status")),
            ExportColumn("account_group", "Grupo", "text", _group_name("account_group", "name")),
            ExportColumn("client", "Contratante", "text", _attr("client", "insured", "legal_name")),
            ExportColumn("period_label", "Vigencia", "text", _attr("period_label")),
            ExportColumn("period_start", "Inicio vigencia", "date", _attr("period_start")),
            ExportColumn("period_end", "Fin vigencia", "date", _attr("period_end")),
            ExportColumn("opened_at", "Apertura", "datetime", _attr("opened_at")),
            ExportColumn("due_at", "Vence", "datetime", _attr("due_at")),
            ExportColumn("closed_at", "Cierre", "datetime", _attr("closed_at")),
        ),
        order_column=CaseFile.id,
        search_predicate=lambda term: or_(
            CaseFile.title.ilike(f"%{term}%"), CaseFile.reference.ilike(f"%{term}%")
        ),
    )

    # --- quotes -------------------------------------------------------------
    registry["quotes"] = EntitySpec(
        entity="quotes",
        label="Cotizaciones solicitadas",
        model=QuoteRequest,
        module="Quotes",
        date_field="created_at",
        date_column=QuoteRequest.created_at,
        money_field="declared_value_uf",
        money_column=QuoteRequest.declared_value_uf,
        status_column=QuoteRequest.status,
        status_enum=QuoteRequestStatus,
        group_predicate=lambda b, g: _case_file_in_group(QuoteRequest.case_file_id, b, g),
        case_predicate=lambda b, c: QuoteRequest.case_file_id == c,
        dimensions={
            "status": Dimension("status", QuoteRequest.status),
            "priority": Dimension("priority", QuoteRequest.priority),
            "account_group": _dim_account_group(
                CaseFile.account_group_id,
                joins=((CaseFile, CaseFile.id == QuoteRequest.case_file_id),),
            ),
            "month": _dim_month(QuoteRequest.created_at),
        },
        columns=(
            ExportColumn("id", "ID", "int", _attr("id")),
            ExportColumn("insured_object", "Materia asegurada", "text", _attr("insured_object")),
            ExportColumn("status", "Estado", "text", _enum_value("status")),
            ExportColumn("priority", "Prioridad", "text", _enum_value("priority")),
            ExportColumn("round_no", "Ronda", "int", _attr("round_no")),
            ExportColumn("declared_value_uf", "Valor declarado (UF)", "uf", _attr("declared_value_uf")),
            ExportColumn("case_file", "Expediente", "text", _attr("case_file", "title")),
            ExportColumn("account_group", "Grupo", "text", _attr("case_file", "account_group", "name")),
            ExportColumn("client", "Cliente", "text", _attr("placement", "client", "insured", "legal_name")),
            ExportColumn("desired_start", "Inicio deseado", "date", _attr("desired_start")),
            ExportColumn("desired_end", "Fin deseado", "date", _attr("desired_end")),
            ExportColumn("sent_at", "Enviada", "datetime", _attr("sent_at")),
            ExportColumn("due_at", "Vence", "datetime", _attr("due_at")),
            ExportColumn("created_at", "Creada", "datetime", _attr("created_at")),
        ),
        order_column=QuoteRequest.id,
        search_predicate=lambda term: QuoteRequest.insured_object.ilike(f"%{term}%"),
    )

    # --- proposals ----------------------------------------------------------
    registry["proposals"] = EntitySpec(
        entity="proposals",
        label="Cotizaciones recibidas",
        model=Proposal,
        module="Proposals",
        date_field="created_at",
        date_column=Proposal.created_at,
        money_field="total_premium_uf",
        money_column=Proposal.total_premium_uf,
        status_column=Proposal.status,
        status_enum=ProposalStatus,
        group_predicate=lambda b, g: or_(
            _case_file_in_group(Proposal.case_file_id, b, g),
            _quote_in_group(Proposal.quote_request_id, b, g),
        ),
        case_predicate=lambda b, c: or_(
            Proposal.case_file_id == c, _quote_in_case(Proposal.quote_request_id, b, c)
        ),
        dimensions={
            "status": Dimension("status", Proposal.status),
            "outcome": Dimension("outcome", Proposal.outcome),
            "origin": Dimension("origin", Proposal.origin),
            "insurer": Dimension("insurer", Proposal.insurer_id, labeller="insurer"),
            "account_group": _dim_account_group(
                CaseFile.account_group_id,
                joins=((CaseFile, CaseFile.id == Proposal.case_file_id),),
            ),
            "month": _dim_month(Proposal.created_at),
        },
        columns=(
            ExportColumn("id", "ID", "int", _attr("id")),
            ExportColumn("quotation_number", "N° cotización", "text", _attr("quotation_number")),
            ExportColumn("insurer", "Aseguradora", "text", _attr("insurer", "legal_name")),
            ExportColumn("status", "Estado", "text", _enum_value("status")),
            ExportColumn("outcome", "Resultado", "text", _enum_value("outcome")),
            ExportColumn("taxable_premium_uf", "Prima afecta (UF)", "uf", _attr("taxable_premium_uf")),
            ExportColumn("exempt_premium_uf", "Prima exenta (UF)", "uf", _attr("exempt_premium_uf")),
            ExportColumn("net_premium_uf", "Prima neta (UF)", "uf", _attr("net_premium_uf")),
            ExportColumn("vat_uf", "IVA (UF)", "uf", _attr("vat_uf")),
            ExportColumn("total_premium_uf", "Prima total (UF)", "uf", _attr("total_premium_uf")),
            ExportColumn("commission_pct", "Comisión %", "number", _attr("commission_pct")),
            ExportColumn("case_file", "Expediente", "text", _attr("case_file", "title")),
            ExportColumn("account_group", "Grupo", "text", _attr("case_file", "account_group", "name")),
            ExportColumn("coverage_start", "Inicio cobertura", "date", _attr("coverage_start")),
            ExportColumn("coverage_end", "Fin cobertura", "date", _attr("coverage_end")),
            ExportColumn("received_at", "Recibida", "date", _attr("received_at")),
            ExportColumn("created_at", "Creada", "datetime", _attr("created_at")),
        ),
        order_column=Proposal.id,
        search_predicate=lambda term: Proposal.quotation_number.ilike(f"%{term}%"),
    )

    # --- policies -----------------------------------------------------------
    registry["policies"] = EntitySpec(
        entity="policies",
        label="Pólizas",
        model=Policy,
        module="Policies",
        date_field="start_date",
        date_column=Policy.start_date,
        money_field="total_premium_uf",
        money_column=Policy.total_premium_uf,
        status_column=Policy.status,
        status_enum=PolicyStatus,
        group_predicate=lambda b, g: or_(
            _case_file_in_group(Policy.case_file_id, b, g),
            _client_in_group(Policy.client_id, b, g),
        ),
        case_predicate=lambda b, c: Policy.case_file_id == c,
        dimensions={
            "status": Dimension("status", Policy.status),
            "insurer": Dimension("insurer", Policy.insurer_id, labeller="insurer"),
            "insurance_line": Dimension(
                "insurance_line", Policy.insurance_line_id, labeller="insurance_line"
            ),
            "account_group": _dim_account_group(
                func.coalesce(CaseFile.account_group_id, Client.account_group_id),
                joins=(
                    (CaseFile, CaseFile.id == Policy.case_file_id),
                    (Client, Client.id == Policy.client_id),
                ),
            ),
            "month": _dim_month(Policy.start_date),
        },
        columns=(
            ExportColumn("id", "ID", "int", _attr("id")),
            ExportColumn("policy_number", "N° póliza", "text", _attr("policy_number")),
            ExportColumn("insurer", "Aseguradora", "text", _attr("insurer", "legal_name")),
            ExportColumn("client", "Contratante", "text", _attr("client", "insured", "legal_name")),
            ExportColumn("status", "Estado", "text", _enum_value("status")),
            ExportColumn("start_date", "Inicio vigencia", "date", _attr("start_date")),
            ExportColumn("end_date", "Fin vigencia", "date", _attr("end_date")),
            ExportColumn("insured_amount_uf", "Monto asegurado (UF)", "uf", _attr("insured_amount_uf")),
            ExportColumn("taxable_premium_uf", "Prima afecta (UF)", "uf", _attr("taxable_premium_uf")),
            ExportColumn("exempt_premium_uf", "Prima exenta (UF)", "uf", _attr("exempt_premium_uf")),
            ExportColumn("net_premium_uf", "Prima neta (UF)", "uf", _attr("net_premium_uf")),
            ExportColumn("vat_uf", "IVA (UF)", "uf", _attr("vat_uf")),
            ExportColumn("total_premium_uf", "Prima total (UF)", "uf", _attr("total_premium_uf")),
            ExportColumn("commission_pct", "Comisión %", "number", _attr("commission_pct")),
            ExportColumn("case_file", "Expediente", "text", _attr("case_file", "title")),
            ExportColumn("account_group", "Grupo", "text", _attr("client", "account_group", "name")),
            ExportColumn("issued_at", "Emisión", "date", _attr("issued_at")),
        ),
        order_column=Policy.id,
        search_predicate=lambda term: Policy.policy_number.ilike(f"%{term}%"),
    )

    # --- endorsements -------------------------------------------------------
    registry["endorsements"] = EntitySpec(
        entity="endorsements",
        label="Endosos",
        model=Endorsement,
        module="Endorsements",
        date_field="effective_at",
        date_column=Endorsement.effective_at,
        money_field="total_premium_delta_uf",
        money_column=Endorsement.total_premium_delta_uf,
        status_column=Endorsement.status,
        status_enum=EndorsementStatus,
        group_predicate=lambda b, g: or_(
            _case_file_in_group(Endorsement.case_file_id, b, g),
            _policy_in_group(Endorsement.policy_id, b, g),
        ),
        case_predicate=lambda b, c: or_(
            Endorsement.case_file_id == c, _policy_in_case(Endorsement.policy_id, b, c)
        ),
        dimensions={
            "status": Dimension("status", Endorsement.status),
            "kind": Dimension("kind", Endorsement.kind),
            "account_group": _dim_account_group(
                CaseFile.account_group_id,
                joins=((CaseFile, CaseFile.id == Endorsement.case_file_id),),
            ),
            "month": _dim_month(Endorsement.effective_at),
        },
        columns=(
            ExportColumn("id", "ID", "int", _attr("id")),
            ExportColumn("endorsement_number", "N° endoso", "text", _attr("endorsement_number")),
            ExportColumn("sequence_no", "Secuencia", "int", _attr("sequence_no")),
            ExportColumn("policy_number", "N° póliza", "text", _attr("policy", "policy_number")),
            ExportColumn("kind", "Tipo", "text", _enum_value("kind")),
            ExportColumn("status", "Estado", "text", _enum_value("status")),
            ExportColumn("motive", "Motivo", "text", _attr("motive")),
            ExportColumn("effective_at", "Vigencia desde", "datetime", _attr("effective_at")),
            ExportColumn("ends_at", "Vigencia hasta", "datetime", _attr("ends_at")),
            ExportColumn("issued_at", "Emisión", "date", _attr("issued_at")),
            ExportColumn("net_premium_delta_uf", "Δ Prima neta (UF)", "uf", _attr("net_premium_delta_uf")),
            ExportColumn("total_premium_delta_uf", "Δ Prima total (UF)", "uf", _attr("total_premium_delta_uf")),
            ExportColumn("insured_amount_delta_uf", "Δ Monto asegurado (UF)", "uf", _attr("insured_amount_delta_uf")),
            ExportColumn("batch_key", "Lote", "text", _attr("batch_key")),
            ExportColumn("case_file", "Expediente", "text", _attr("case_file", "title")),
        ),
        order_column=Endorsement.id,
        search_predicate=lambda term: Endorsement.endorsement_number.ilike(f"%{term}%"),
    )

    # --- collections --------------------------------------------------------
    registry["collections"] = EntitySpec(
        entity="collections",
        label="Cobranzas",
        model=CollectionPlan,
        module="Collections",
        date_field="created_at",
        date_column=CollectionPlan.created_at,
        money_field="total_premium_uf",
        money_column=CollectionPlan.total_premium_uf,
        status_column=CollectionPlan.status,
        status_enum=CollectionPlanStatus,
        group_predicate=lambda b, g: or_(
            _case_file_in_group(CollectionPlan.case_file_id, b, g),
            _policy_in_group(CollectionPlan.policy_id, b, g),
        ),
        case_predicate=lambda b, c: or_(
            CollectionPlan.case_file_id == c,
            _policy_in_case(CollectionPlan.policy_id, b, c),
        ),
        dimensions={
            "status": Dimension("status", CollectionPlan.status),
            "payment_mode": Dimension("payment_mode", CollectionPlan.payment_mode),
            "account_group": _dim_account_group(
                CaseFile.account_group_id,
                joins=((CaseFile, CaseFile.id == CollectionPlan.case_file_id),),
            ),
            "month": _dim_month(CollectionPlan.created_at),
        },
        columns=(
            ExportColumn("id", "ID", "int", _attr("id")),
            ExportColumn("plan_number", "N° plan", "text", _attr("plan_number")),
            ExportColumn("policy_number", "N° póliza", "text", _attr("policy", "policy_number")),
            ExportColumn("payment_mode", "Modalidad", "text", _enum_value("payment_mode")),
            ExportColumn("status", "Estado", "text", _enum_value("status")),
            ExportColumn("installment_count", "N° cuotas", "int", _attr("installment_count")),
            ExportColumn("total_premium_uf", "Prima total (UF)", "uf", _attr("total_premium_uf")),
            ExportColumn("bank", "Banco", "text", _attr("bank")),
            ExportColumn("as_of_date", "Al", "date", _attr("as_of_date")),
            ExportColumn("days_without_cover", "Días sin cobertura", "int", _attr("days_without_cover")),
            ExportColumn("case_file", "Expediente", "text", _attr("case_file", "title")),
            ExportColumn("created_at", "Creado", "datetime", _attr("created_at")),
        ),
        order_column=CollectionPlan.id,
        search_predicate=lambda term: CollectionPlan.plan_number.ilike(f"%{term}%"),
    )

    # --- claims -------------------------------------------------------------
    registry["claims"] = EntitySpec(
        entity="claims",
        label="Siniestros",
        model=Claim,
        module="Claims",
        date_field="event_date",
        date_column=Claim.event_date,
        money_field="estimated_amount_uf",
        money_column=Claim.estimated_amount_uf,
        status_column=Claim.status,
        status_enum=ClaimStatus,
        group_predicate=lambda b, g: or_(
            _case_file_in_group(Claim.case_file_id, b, g),
            _client_in_group(Claim.client_id, b, g),
        ),
        case_predicate=lambda b, c: Claim.case_file_id == c,
        dimensions={
            "status": Dimension("status", Claim.status),
            "coverage_ruling": Dimension("coverage_ruling", Claim.coverage_ruling),
            "account_group": _dim_account_group(
                func.coalesce(CaseFile.account_group_id, Client.account_group_id),
                joins=(
                    (CaseFile, CaseFile.id == Claim.case_file_id),
                    (Client, Client.id == Claim.client_id),
                ),
            ),
            "month": _dim_month(Claim.event_date),
        },
        columns=(
            ExportColumn("id", "ID", "int", _attr("id")),
            ExportColumn("claim_number", "N° siniestro", "text", _attr("claim_number")),
            ExportColumn("policy_number", "N° póliza", "text", _attr("policy", "policy_number")),
            ExportColumn("client", "Cliente", "text", _attr("client", "insured", "legal_name")),
            ExportColumn("kind", "Tipo", "text", _attr("kind")),
            ExportColumn("status", "Estado", "text", _enum_value("status")),
            ExportColumn("coverage_ruling", "Dictamen", "text", _enum_value("coverage_ruling")),
            ExportColumn("event_date", "Fecha del siniestro", "date", _attr("event_date")),
            ExportColumn("reported_date", "Fecha de denuncia", "date", _attr("reported_date")),
            ExportColumn("estimated_amount_uf", "Monto estimado (UF)", "uf", _attr("estimated_amount_uf")),
            ExportColumn("settled_amount_uf", "Monto liquidado (UF)", "uf", _attr("settled_amount_uf")),
            ExportColumn("paid_amount_uf", "Monto pagado (UF)", "uf", _attr("paid_amount_uf")),
            ExportColumn("reserve_uf", "Reserva (UF)", "uf", _attr("reserve_uf")),
            ExportColumn("deductible_uf", "Deducible (UF)", "uf", _attr("deductible_uf")),
            ExportColumn("case_file", "Expediente", "text", _attr("case_file", "title")),
        ),
        order_column=Claim.id,
        search_predicate=lambda term: Claim.claim_number.ilike(f"%{term}%"),
    )

    # --- documents ----------------------------------------------------------
    registry["documents"] = EntitySpec(
        entity="documents",
        label="Documentos",
        model=Document,
        module="Documents",
        date_field="created_at",
        date_column=Document.created_at,
        money_field=None,
        money_column=None,
        status_column=None,
        status_enum=None,
        group_predicate=lambda b, g: _case_file_in_group(Document.case_file_id, b, g),
        case_predicate=lambda b, c: Document.case_file_id == c,
        dimensions={
            "category": Dimension("category", Document.category),
            "section": Dimension("section", Document.section),
            "entity_type": Dimension("entity_type", Document.entity_type),
            "account_group": _dim_account_group(
                CaseFile.account_group_id,
                joins=((CaseFile, CaseFile.id == Document.case_file_id),),
            ),
            "month": _dim_month(Document.created_at),
        },
        columns=(
            ExportColumn("id", "ID", "int", _attr("id")),
            ExportColumn("original_name", "Nombre", "text", _attr("original_name")),
            ExportColumn("category", "Categoría", "text", _enum_value("category")),
            ExportColumn("section", "Sección", "text", _enum_value("section")),
            ExportColumn("document_code", "Código", "text", _attr("document_code")),
            ExportColumn("entity_type", "Entidad", "text", _enum_value("entity_type")),
            ExportColumn("entity_id", "ID entidad", "int", _attr("entity_id")),
            ExportColumn("mime_type", "Tipo MIME", "text", _attr("mime_type")),
            ExportColumn("size_bytes", "Tamaño (bytes)", "int", _attr("size_bytes")),
            ExportColumn("case_file", "Expediente", "text", _attr("case_file", "title")),
            ExportColumn("created_at", "Subido", "datetime", _attr("created_at")),
        ),
        order_column=Document.id,
        search_predicate=lambda term: Document.original_name.ilike(f"%{term}%"),
    )

    # --- placements ---------------------------------------------------------
    registry["placements"] = EntitySpec(
        entity="placements",
        label="Colocaciones",
        model=Placement,
        module="Placements",
        date_field="created_at",
        date_column=Placement.created_at,
        money_field=None,
        money_column=None,
        status_column=Placement.status,
        status_enum=PlacementStatus,
        group_predicate=lambda b, g: or_(
            _case_file_in_group(Placement.case_file_id, b, g),
            _client_in_group(Placement.client_id, b, g),
        ),
        case_predicate=lambda b, c: Placement.case_file_id == c,
        dimensions={
            "status": Dimension("status", Placement.status),
            "insurance_line": Dimension(
                "insurance_line", Placement.insurance_line_id, labeller="insurance_line"
            ),
            "account_group": _dim_account_group(
                func.coalesce(CaseFile.account_group_id, Client.account_group_id),
                joins=(
                    (CaseFile, CaseFile.id == Placement.case_file_id),
                    (Client, Client.id == Placement.client_id),
                ),
            ),
            "month": _dim_month(Placement.created_at),
        },
        columns=(
            ExportColumn("id", "ID", "int", _attr("id")),
            ExportColumn("client", "Cliente", "text", _attr("client", "insured", "legal_name")),
            ExportColumn("asset", "Bien", "text", _attr("asset", "name")),
            ExportColumn("insurance_line", "Ramo", "text", _attr("insurance_line", "name")),
            ExportColumn("status", "Estado", "text", _enum_value("status")),
            ExportColumn("period", "Vigencia", "text", _attr("period")),
            ExportColumn("period_start", "Inicio vigencia", "date", _attr("period_start")),
            ExportColumn("period_end", "Fin vigencia", "date", _attr("period_end")),
            ExportColumn("case_file", "Expediente", "text", _attr("case_file", "title")),
            ExportColumn("created_at", "Creada", "datetime", _attr("created_at")),
        ),
        order_column=Placement.id,
        search_predicate=None,
    )

    # --- inspections --------------------------------------------------------
    registry["inspections"] = EntitySpec(
        entity="inspections",
        label="Inspecciones",
        model=Inspection,
        module="Inspections",
        date_field="visit_date",
        date_column=Inspection.visit_date,
        money_field=None,
        money_column=None,
        status_column=Inspection.status,
        status_enum=InspectionStatus,
        group_predicate=lambda b, g: or_(
            _case_file_in_group(Inspection.case_file_id, b, g),
            _asset_in_group(Inspection.asset_id, b, g),
        ),
        case_predicate=lambda b, c: Inspection.case_file_id == c,
        dimensions={
            "status": Dimension("status", Inspection.status),
            "risk_classification": Dimension(
                "risk_classification", Inspection.risk_classification
            ),
            "account_group": _dim_account_group(
                func.coalesce(CaseFile.account_group_id, Client.account_group_id),
                joins=(
                    (CaseFile, CaseFile.id == Inspection.case_file_id),
                    (Asset, Asset.id == Inspection.asset_id),
                    (Client, Client.id == Asset.client_id),
                ),
            ),
            "month": _dim_month(Inspection.visit_date),
        },
        columns=(
            ExportColumn("id", "ID", "int", _attr("id")),
            ExportColumn("folio", "Folio", "text", _attr("folio")),
            ExportColumn("asset", "Bien", "text", _attr("asset", "name")),
            ExportColumn("status", "Estado", "text", _enum_value("status")),
            ExportColumn("version", "Versión", "int", _attr("version")),
            ExportColumn("visit_date", "Visita", "date", _attr("visit_date")),
            ExportColumn("report_date", "Informe", "date", _attr("report_date")),
            ExportColumn("overall_score", "Puntaje global", "number", _attr("overall_score")),
            ExportColumn("pml_pct", "PML %", "number", _attr("pml_pct")),
            ExportColumn("eml_pct", "EML %", "number", _attr("eml_pct")),
            ExportColumn("risk_classification", "Clasificación", "text", _attr("risk_classification")),
            ExportColumn("case_file", "Expediente", "text", _attr("case_file", "title")),
        ),
        order_column=Inspection.id,
        search_predicate=lambda term: Inspection.folio.ilike(f"%{term}%"),
    )

    # --- offerings ----------------------------------------------------------
    registry["offerings"] = EntitySpec(
        entity="offerings",
        label="Presentaciones al asegurado",
        model=Offering,
        module="Offerings",
        date_field="created_at",
        date_column=Offering.created_at,
        money_field=None,
        money_column=None,
        status_column=Offering.status,
        status_enum=OfferingStatus,
        group_predicate=lambda b, g: _quote_in_group(Offering.quote_request_id, b, g),
        case_predicate=lambda b, c: _quote_in_case(Offering.quote_request_id, b, c),
        dimensions={
            "status": Dimension("status", Offering.status),
            "sent_via": Dimension("sent_via", Offering.sent_via),
            "account_group": _dim_account_group(
                CaseFile.account_group_id,
                joins=(
                    (QuoteRequest, QuoteRequest.id == Offering.quote_request_id),
                    (CaseFile, CaseFile.id == QuoteRequest.case_file_id),
                ),
            ),
            "month": _dim_month(Offering.created_at),
        },
        columns=(
            ExportColumn("id", "ID", "int", _attr("id")),
            ExportColumn("quote_request_id", "Solicitud", "int", _attr("quote_request_id")),
            ExportColumn("status", "Estado", "text", _enum_value("status")),
            ExportColumn("sent_via", "Canal", "text", _enum_value("sent_via")),
            ExportColumn("sent_at", "Enviada", "datetime", _attr("sent_at")),
            ExportColumn("viewed_at", "Vista", "datetime", _attr("viewed_at")),
            ExportColumn("decided_at", "Decidida", "datetime", _attr("decided_at")),
            ExportColumn("expires_at", "Expira", "datetime", _attr("expires_at")),
            ExportColumn("created_at", "Creada", "datetime", _attr("created_at")),
        ),
        order_column=Offering.id,
        search_predicate=None,
    )

    # --- leads --------------------------------------------------------------
    registry["leads"] = EntitySpec(
        entity="leads",
        label="Prospectos",
        model=SalesLead,
        module="Leads",
        date_field="created_at",
        date_column=SalesLead.created_at,
        money_field="estimated_premium_uf",
        money_column=SalesLead.estimated_premium_uf,
        status_column=SalesLead.status,
        status_enum=LeadStatus,
        group_predicate=lambda b, g: SalesLead.account_group_id == g,
        case_predicate=lambda b, c: SalesLead.converted_case_file_id == c,
        dimensions={
            "status": Dimension("status", SalesLead.status),
            "source": Dimension("source", SalesLead.source),
            "insurance_line": Dimension(
                "insurance_line", SalesLead.insurance_line_id, labeller="insurance_line"
            ),
            "account_group": _dim_account_group(SalesLead.account_group_id),
            "month": _dim_month(SalesLead.created_at),
        },
        columns=(
            ExportColumn("id", "ID", "int", _attr("id")),
            ExportColumn("name", "Nombre", "text", _attr("name")),
            ExportColumn("rut", "RUT", "text", _attr("rut")),
            ExportColumn("status", "Estado", "text", _enum_value("status")),
            ExportColumn("source", "Origen", "text", _attr("source")),
            ExportColumn("contact_name", "Contacto", "text", _attr("contact_name")),
            ExportColumn("contact_email", "Email", "text", _attr("contact_email")),
            ExportColumn("contact_phone", "Teléfono", "text", _attr("contact_phone")),
            ExportColumn("estimated_premium_uf", "Prima estimada (UF)", "uf", _attr("estimated_premium_uf")),
            ExportColumn("insurance_line", "Ramo", "text", _attr("insurance_line", "name")),
            ExportColumn("account_group", "Grupo", "text", _attr("account_group", "name")),
            ExportColumn("follow_up_on", "Seguimiento", "date", _attr("follow_up_on")),
            ExportColumn("period_start", "Inicio vigencia", "date", _attr("period_start")),
            ExportColumn("period_end", "Fin vigencia", "date", _attr("period_end")),
            ExportColumn("created_at", "Creado", "datetime", _attr("created_at")),
        ),
        order_column=SalesLead.id,
        search_predicate=lambda term: SalesLead.name.ilike(f"%{term}%"),
    )

    # --- clients ------------------------------------------------------------
    registry["clients"] = EntitySpec(
        entity="clients",
        label="Clientes",
        model=Client,
        module="Clients",
        date_field="created_at",
        date_column=Client.created_at,
        money_field=None,
        money_column=None,
        status_column=Client.status,
        status_enum=ClientStatus,
        group_predicate=lambda b, g: Client.account_group_id == g,
        case_predicate=_client_is_member_for,
        dimensions={
            "status": Dimension("status", Client.status),
            "sector": Dimension("sector", Client.sector),
            "account_group": _dim_account_group(Client.account_group_id),
            "month": _dim_month(Client.created_at),
        },
        columns=(
            ExportColumn("id", "ID", "int", _attr("id")),
            ExportColumn("legal_name", "Razón social", "text", _attr("insured", "legal_name")),
            ExportColumn("rut", "RUT", "text", _attr("insured", "rut")),
            ExportColumn("status", "Estado", "text", _enum_value("status")),
            ExportColumn("sector", "Sector", "text", _attr("sector")),
            ExportColumn("account_group", "Grupo", "text", _attr("account_group", "name")),
            ExportColumn("contact_name", "Contacto", "text", _attr("contact_name")),
            ExportColumn("contact_email", "Email", "text", _attr("contact_email")),
            ExportColumn("contact_phone", "Teléfono", "text", _attr("contact_phone")),
            ExportColumn("since", "Cliente desde", "date", _attr("since")),
            ExportColumn("created_at", "Creado", "datetime", _attr("created_at")),
        ),
        order_column=Client.id,
        search_predicate=None,
    )

    return registry


def _client_is_member_for(broker_id: int, case_file_id: int):
    return _client_is_member(Client.id, broker_id, case_file_id)


ENTITIES: dict[str, EntitySpec] = _build_registry()
ENTITY_KEYS: list[str] = list(ENTITIES.keys())


def spec_for(entity: str) -> EntitySpec:
    spec = ENTITIES.get(entity)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "unknown_entity",
                "message": f"No existe la entidad exportable '{entity}'.",
                "entities": ENTITY_KEYS,
            },
        )
    return spec


# ---------------------------------------------------------------------------
# Applying the filters
# ---------------------------------------------------------------------------

def _is_datetime(column) -> bool:
    try:
        return isinstance(column.type, DateTime)
    except AttributeError:  # pragma: no cover - defensive
        return False


def date_window_predicates(column, date_from: date | None, date_to: date | None) -> list[Any]:
    """Half-open-in-spirit but inclusive-in-practice bounds on ``column``.

    For a DATETIME column ``date_to`` means "up to the end of that day", so the
    upper bound is ``< date_to + 1 day`` — otherwise a same-day row at 14:00
    would fall outside an inclusive ``<= date_to 00:00``.
    """
    out: list[Any] = []
    if column is None:
        return out
    if date_from is None and date_to is None:
        return out
    # A NULL date cannot be placed in a window honestly -> exclude it.
    out.append(column.is_not(None))
    if _is_datetime(column):
        if date_from is not None:
            out.append(
                column >= datetime.combine(date_from, time.min, tzinfo=timezone.utc)
            )
        if date_to is not None:
            out.append(
                column
                < datetime.combine(date_to, time.min, tzinfo=timezone.utc)
                + _ONE_DAY
            )
    else:
        if date_from is not None:
            out.append(column >= date_from)
        if date_to is not None:
            out.append(column <= date_to)
    return out



def scope_predicates(
    entity: str, filters: ScopeFilters | None, broker_id: int
) -> list[Any]:
    """The predicate list a router appends to its own ``filters``.

    Always call with the tenant filter already present: everything here hops to
    another table *and re-asserts* ``broker_id`` on that hop, so a foreign group
    or expediente id resolves to no rows rather than to someone else's.
    """
    spec = spec_for(entity)
    out: list[Any] = []
    if filters is None:
        return out
    if filters.account_group_id is not None:
        out.append(spec.group_predicate(broker_id, filters.account_group_id))
    if filters.case_file_id is not None:
        out.append(spec.case_predicate(broker_id, filters.case_file_id))
    out.extend(
        date_window_predicates(spec.date_column, filters.date_from, filters.date_to)
    )
    return out


# ---------------------------------------------------------------------------
# group_by
# ---------------------------------------------------------------------------

def validate_group_by(entity: str, group_by: str | None) -> Dimension | None:
    """Return the Dimension or raise the documented 422 (never ignore silently)."""
    if group_by is None:
        return None
    spec = spec_for(entity)
    dimension = spec.dimensions.get(group_by)
    if dimension is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=unsupported_group_by(entity, group_by, spec.group_by_keys),
        )
    return dimension


def _label_map(db: Session, labeller: str | None, keys: Iterable[Any]) -> dict[Any, str]:
    ids = [k for k in keys if isinstance(k, int)]
    if not labeller or not ids:
        return {}
    if labeller == "account_group":
        rows = db.execute(
            select(AccountGroup.id, AccountGroup.name).where(AccountGroup.id.in_(ids))
        ).all()
    elif labeller == "insurer":
        rows = db.execute(
            select(Insurer.id, Insurer.legal_name).where(Insurer.id.in_(ids))
        ).all()
    elif labeller == "insurance_line":
        rows = db.execute(
            select(InsuranceLine.id, InsuranceLine.name).where(InsuranceLine.id.in_(ids))
        ).all()
    else:  # pragma: no cover - defensive
        return {}
    return {row[0]: row[1] for row in rows}


def _raw_key(value: Any) -> str:
    if value is None:
        return UNASSIGNED
    return str(getattr(value, "value", value))


def _month_key(value: Any) -> str:
    if value is None:
        return UNASSIGNED
    return f"{value.year:04d}-{value.month:02d}"


def _dec(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def build_grouped_summary(
    db: Session,
    entity: str,
    *,
    group_by: str,
    broker_id: int,
    extra_filters: Sequence[Any] = (),
) -> GroupedSummary:
    """Aggregate ``entity`` into chart-ready buckets.

    ``extra_filters`` must already contain the tenant filter and every scope
    predicate — this function adds nothing but the grouping. The ``month``
    dimension is bucketed in Python on purpose: ``strftime`` (SQLite) and
    ``DATE_FORMAT`` (MySQL) are not the same function, and the row volume of one
    broker's table does not justify a dialect switch.
    """
    spec = spec_for(entity)
    dimension = validate_group_by(entity, group_by)
    assert dimension is not None
    model = spec.model
    money = spec.money_column

    where = list(extra_filters)

    if dimension.expr is MONTH:
        stmt = select(spec.date_column, money) if money is not None else select(spec.date_column)
        stmt = stmt.where(*where)
        counts: dict[str, int] = {}
        totals: dict[str, Decimal] = {}
        for row in db.execute(stmt).all():
            key = _month_key(row[0])
            counts[key] = counts.get(key, 0) + 1
            if money is not None:
                totals[key] = totals.get(key, Decimal("0")) + _dec(row[1])
        ordered = sorted(counts.keys(), key=lambda k: (k == UNASSIGNED, k))
        buckets = [
            SummaryBucket(
                key=key,
                label=key,
                count=counts[key],
                total_uf=(totals.get(key) if money is not None else None),
            )
            for key in ordered
        ]
    else:
        columns: list[Any] = [dimension.expr, func.count(model.id)]
        if money is not None:
            columns.append(func.sum(money))
        stmt = select(*columns).select_from(model)
        for target, onclause in dimension.joins:
            stmt = stmt.outerjoin(target, onclause)
        stmt = stmt.where(*where).group_by(dimension.expr)
        rows = db.execute(stmt).all()
        raw = [(row[0], int(row[1] or 0), (row[2] if money is not None else None)) for row in rows]
        labels = _label_map(db, dimension.labeller, [r[0] for r in raw])
        # Merge: two NULL-ish keys (None and "") both land on "unassigned".
        merged: dict[str, tuple[int, Decimal | None, str]] = {}
        for key_value, count, total in raw:
            key = _raw_key(key_value if key_value != "" else None)
            label = labels.get(key_value) or (
                "Sin asignar" if key == UNASSIGNED else str(key)
            )
            prev = merged.get(key)
            new_total = None if money is None else _dec(total)
            if prev is not None:
                new_count = prev[0] + count
                if money is not None:
                    new_total = (prev[1] or Decimal("0")) + (new_total or Decimal("0"))
                merged[key] = (new_count, new_total, prev[2])
            else:
                merged[key] = (count, new_total, label)
        ordered_keys = sorted(
            merged.keys(), key=lambda k: (k == UNASSIGNED, -merged[k][0], k)
        )
        buckets = [
            SummaryBucket(
                key=key,
                label=merged[key][2],
                count=merged[key][0],
                total_uf=merged[key][1],
            )
            for key in ordered_keys
        ]

    total = sum(b.count for b in buckets)
    total_uf = (
        sum((b.total_uf or Decimal("0") for b in buckets), Decimal("0"))
        if money is not None
        else None
    )
    return GroupedSummary(
        group_by=group_by,
        dimensions=spec.group_by_keys,
        money_field=spec.money_field,
        total=total,
        total_uf=total_uf,
        buckets=buckets,
    )


def grouped_or_none(
    db: Session,
    entity: str,
    *,
    group_by: str | None,
    broker_id: int,
    extra_filters: Sequence[Any] = (),
) -> GroupedSummary | None:
    """``build_grouped_summary`` when asked, ``None`` when not — still 422 on a
    dimension the entity cannot answer."""
    if group_by is None:
        return None
    return build_grouped_summary(
        db, entity, group_by=group_by, broker_id=broker_id, extra_filters=extra_filters
    )


def build_entity_summary(
    db: Session,
    entity: str,
    *,
    broker_id: int,
    scoped: Sequence[Any],
    group_by: str | None = None,
) -> EntitySummary:
    """The generic ``/summary`` for the entities that never had a board.

    (endorsements, collections, claims, documents, inspections, offerings.)
    ``scoped`` must already carry the tenant filter and the scope predicates —
    this function only counts. Statuses are zero-filled from the entity's enum so
    a chart never KeyErrors on an empty tenant.
    """
    spec = spec_for(entity)
    by_status: dict[str, int] = {}
    if spec.status_column is not None:
        rows = db.execute(
            select(spec.status_column, func.count(spec.model.id))
            .where(*scoped)
            .group_by(spec.status_column)
        ).all()
        counted = {_raw_key(key): int(value) for key, value in rows}
        if spec.status_enum is not None:
            by_status = {
                member.value: counted.get(member.value, 0) for member in spec.status_enum
            }
            for key, value in counted.items():
                by_status.setdefault(key, value)
        else:  # pragma: no cover - every status column has an enum today
            by_status = counted

    total = int(db.scalar(select(func.count(spec.model.id)).where(*scoped)) or 0)

    total_uf: Decimal | None = None
    if spec.money_column is not None:
        raw = db.scalar(select(func.sum(spec.money_column)).where(*scoped))
        total_uf = _dec(raw).quantize(Decimal("0.0001"))

    return EntitySummary(
        entity=entity,
        total=total,
        by_status=by_status,
        total_uf=total_uf,
        grouped=grouped_or_none(
            db, entity, group_by=group_by, broker_id=broker_id, extra_filters=scoped
        ),
    )


def group_by_query(
    description: str = "Dimensión de agregación para los gráficos.",
):
    """Sugar so every summary declares the param the same way."""
    return Query(default=None, max_length=48, description=description)


__all__ = [
    "ENTITIES",
    "ENTITY_KEYS",
    "EXPORT_ROW_CAP",
    "Dimension",
    "EntitySpec",
    "ExportColumn",
    "ScopeFilters",
    "ScopeFiltersDep",
    "build_entity_summary",
    "build_grouped_summary",
    "date_window_predicates",
    "filters_from_payload",
    "group_by_query",
    "grouped_or_none",
    "scope_filters",
    "scope_predicates",
    "spec_for",
    "validate_group_by",
]
