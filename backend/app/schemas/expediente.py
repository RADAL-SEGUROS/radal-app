"""Schemas for the **expediente completo** — the super overview of an account.

One aggregate (:class:`AccountExpediente`) answering "everything we know about
this grupo-cuenta, right now": identity, the empresas on the folder, the five
journey milestones with their REAL completion dates, the antecedentes index, the
comparación, the propuesta, the pólizas, every document, the money core and the
last bitácora entries.

Two conventions the UI depends on:

* **Identifiers are English, copy is Spanish** (rule 1). ``status`` /
  ``role`` / ``source`` carry English tokens; ``label``, ``summary`` and every
  ``*_pending_reason`` carry ready-to-render Spanish.
* **Nothing is silently blank.** A section that does not exist yet is ``null``
  and its sibling ``*_pending_reason`` explains why in Spanish — typically the
  "Pronto, al cerrar X" line the overview renders in place of the section.

Rule 8 holds here as everywhere: :class:`ExpedienteDocument` carries no S3 key.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.account_group import AccountGroupStatus
from app.models.enums import CaseFileKind, CaseFileStatus, CaseOrigin, CaseStage
from app.schemas.account_group import AccountGroupIconRead

#: The five milestones of the broker journey (v9), in order. English keys; the
#: Spanish label travels on the entry itself.
MilestoneKey = Literal[
    "antecedentes", "technical_basis", "comparison", "proposal", "policies"
]

#: Where a milestone stands. ``complete`` is read off a REAL stage event.
MilestoneStatus = Literal["complete", "in_progress", "pending"]

#: Where the money core was read from — never recomputed by hand.
MoneySource = Literal["policy", "broker_proposal", "proposal"]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Identity ----------------------------------------------------------------

class ExpedienteCaseFile(ApiModel):
    """The folder itself: what it is, when it runs, whether it is frozen."""

    id: int
    reference: str | None = None
    title: str
    kind: CaseFileKind
    stage: CaseStage
    status: CaseFileStatus
    origin: CaseOrigin
    #: The AUTHORITATIVE vigencia (rule 1 of the group layer: locked after intake).
    period_start: date | None = None
    period_end: date | None = None
    #: The grouping label only (``2026-2027``), never a shared range.
    period_label: str | None = None
    period_locked: bool = False
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    due_at: datetime | None = None
    insurance_line_id: int | None = None
    insurance_line_name: str | None = None
    #: The ramo template that drives the recommended files (advisory, not a schema).
    ramo_name: str | None = None
    summary: str | None = None


class ExpedienteGroup(ApiModel):
    """The grupo-cuenta the folder hangs off. Carries no money and no stage."""

    id: int
    name: str
    slug: str | None = None
    status: AccountGroupStatus | None = None
    icon: AccountGroupIconRead | None = None


class ExpedienteClient(ApiModel):
    """One RUT of the account: ``account_client`` rows ∪ the placements' clients."""

    id: int
    rut: str | None = None
    legal_name: str | None = None
    trade_name: str | None = None
    #: English token: ``policyholder`` | ``insured``.
    role: str = "insured"
    #: The contratante (``case_file.client_id``) — always the first entry.
    is_contratante: bool = False


# --- Journey -----------------------------------------------------------------

class ExpedienteJourneyStep(ApiModel):
    """One milestone of Antecedentes → Bases Técnicas → Comparación → Propuesta
    → Pólizas.

    ``completed_at`` comes from a real ``case_file_stage_event`` and is ``null``
    when the machine never recorded the move (an imported or force-moved folder),
    even if the milestone reads ``complete``. It is never inferred.
    """

    key: MilestoneKey
    label: str
    #: The representative stage of the milestone (what the tab is "at").
    stage: CaseStage
    #: Every ``case_stage`` the milestone covers, in flow order.
    stages: list[CaseStage] = Field(default_factory=list)
    status: MilestoneStatus
    completed_at: datetime | None = None
    #: A short Spanish factual line ("4 de 4 archivos recomendados").
    summary: str | None = None
    #: Spanish; ``null`` when the milestone is complete. A real guard reason when
    #: the machine blocks the move, else the "Pronto, al cerrar X" line.
    pending_reason: str | None = None


# --- Antecedentes ------------------------------------------------------------

class ExpedienteAntecedentesSlot(ApiModel):
    """One recommended-file slot of the ramo, filled or missing."""

    key: str
    label: str
    category: str | None = None
    doc_type: str | None = None
    format: str | None = None
    required: bool = False
    filled: bool = False
    documents_count: int = 0
    document_ids: list[int] = Field(default_factory=list)
    description: str | None = None


class ExpedienteAntecedentes(ApiModel):
    """The intake index — recommended slots + free uploads + extractions.

    Scoped exactly like ``ai._antecedentes_documents``: cotización / comparison
    categories never leak in (they belong to Comparación).
    """

    ramo_name: str | None = None
    #: ``draft`` | ``processing`` | ``review`` | ``registered``.
    record_status: str = "draft"
    registered_at: datetime | None = None
    slots: list[ExpedienteAntecedentesSlot] = Field(default_factory=list)
    recommended_total: int = 0
    recommended_filled: int = 0
    #: Uploads that no recommended slot claims ("Otros archivos").
    free_uploads_count: int = 0
    documents_count: int = 0
    extractions_count: int = 0
    complete: bool = False
    #: Spanish labels of the required recommended files still missing.
    missing_required: list[str] = Field(default_factory=list)


# --- Comparación -------------------------------------------------------------

class ExpedienteRecommendation(ApiModel):
    """The named AI pick of the comparison (v9 requires a pick + rationale)."""

    pick_label: str | None = None
    rationale: str | None = None
    caveats: list[str] = Field(default_factory=list)


class ExpedienteComparisonColumn(ApiModel):
    """One insurer column of the aligned matrix."""

    label: str
    recommended: bool = False
    wrong_file: bool = False
    total_premium_uf: Decimal | None = None


class ExpedienteComparison(ApiModel):
    id: int
    #: ``draft`` | ``aligned`` | ``superseded``.
    status: str
    canonical_version: int = 1
    entry_count: int = 0
    columns: list[ExpedienteComparisonColumn] = Field(default_factory=list)
    recommendation: ExpedienteRecommendation | None = None
    pdf_document_id: int | None = None
    updated_at: datetime | None = None


# --- Propuesta ---------------------------------------------------------------

class ExpedienteProposal(ApiModel):
    """The outbound ``broker_proposal`` (never an insurer's inbound offer)."""

    id: int
    #: ``draft`` | ``issued`` | ``ratified`` | ``superseded``.
    status: str
    is_ratified: bool = False
    ratified_at: datetime | None = None
    content_hash: str | None = None
    insurer_id: int | None = None
    insurer_name: str | None = None
    insured_name: str | None = None
    insured_rut: str | None = None
    coverage_start: str | None = None
    coverage_end: str | None = None
    net_premium_uf: Decimal | None = None
    vat_uf: Decimal | None = None
    total_premium_uf: Decimal | None = None
    commission_pct: Decimal | None = None
    pdf_document_id: int | None = None


# --- Pólizas -----------------------------------------------------------------

class ExpedientePolicy(ApiModel):
    id: int
    policy_number: str
    status: str
    insurer_id: int | None = None
    insurer_name: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    insured_amount_uf: Decimal | None = None
    net_premium_uf: Decimal | None = None
    vat_uf: Decimal | None = None
    total_premium_uf: Decimal | None = None
    issued_at: date | None = None


# --- Documentos --------------------------------------------------------------

class ExpedienteDocument(ApiModel):
    """A document of the folder. **No S3 key ever** (rule 8) — the bytes are
    fetched through ``GET /documents/{id}/content`` like everywhere else."""

    id: int
    filename: str
    category: str
    category_label: str
    section: str | None = None
    section_label: str | None = None
    document_code: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    created_at: datetime | None = None


# --- Money -------------------------------------------------------------------

class ExpedienteMoney(ApiModel):
    """The account's money core, reconciled through ``reconcile_money``.

    ``net = taxable + exempt`` · ``vat = 0.19 × taxable`` (never on net) ·
    ``total = net + vat``. ``derived_fields`` names what code computed because
    the source left it blank; ``warnings`` carries the soft (rate) checks.
    """

    source: MoneySource
    currency: str = "UF"
    taxable_premium_uf: Decimal | None = None
    exempt_premium_uf: Decimal | None = None
    net_premium_uf: Decimal | None = None
    vat_uf: Decimal | None = None
    total_premium_uf: Decimal | None = None
    commission_pct: Decimal | None = None
    derived_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# --- Bitácora ----------------------------------------------------------------

class ExpedienteActivity(ApiModel):
    """One bitácora row — stage events, activity rows and notes, newest first."""

    kind: Literal["stage", "activity", "note"]
    occurred_at: datetime | None = None
    title: str
    detail: str | None = None
    user_id: int | None = None
    user_name: str | None = None
    from_stage: str | None = None
    to_stage: str | None = None
    meta: dict[str, Any] | None = None


# --- The aggregate -----------------------------------------------------------

class AccountExpediente(ApiModel):
    """``GET /case-files/{id}/expediente`` — everything, always up to date."""

    case_file: ExpedienteCaseFile
    group: ExpedienteGroup | None = None
    group_pending_reason: str | None = None
    clients: list[ExpedienteClient] = Field(default_factory=list)

    journey: list[ExpedienteJourneyStep] = Field(default_factory=list)

    antecedentes: ExpedienteAntecedentes | None = None
    antecedentes_pending_reason: str | None = None

    comparison: ExpedienteComparison | None = None
    comparison_pending_reason: str | None = None

    proposal: ExpedienteProposal | None = None
    proposal_pending_reason: str | None = None

    policies: list[ExpedientePolicy] = Field(default_factory=list)
    policies_pending_reason: str | None = None

    documents: list[ExpedienteDocument] = Field(default_factory=list)
    documents_count: int = 0

    money: ExpedienteMoney | None = None
    money_pending_reason: str | None = None

    activity: list[ExpedienteActivity] = Field(default_factory=list)

    generated_at: datetime


__all__ = [
    "AccountExpediente",
    "ExpedienteActivity",
    "ExpedienteAntecedentes",
    "ExpedienteAntecedentesSlot",
    "ExpedienteCaseFile",
    "ExpedienteClient",
    "ExpedienteComparison",
    "ExpedienteComparisonColumn",
    "ExpedienteDocument",
    "ExpedienteGroup",
    "ExpedienteJourneyStep",
    "ExpedienteMoney",
    "ExpedientePolicy",
    "ExpedienteProposal",
    "ExpedienteRecommendation",
    "MilestoneKey",
    "MilestoneStatus",
    "MoneySource",
]
