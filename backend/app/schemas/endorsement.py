"""Pydantic v2 schemas for ``endorsement`` — amendments to an issued policy.

The money invariants are the SAME arithmetic as a proposal's, but applied to
DELTAS and therefore **sign-preserving**::

    net_delta   = taxable_delta + exempt_delta
    vat_delta   = 0.19 * taxable_delta          # on the taxable part, NOT on net
    total_delta = net_delta + vat_delta

A negative delta (an exclusion, a sum-insured decrease) is normal, and an
administrative endorsement (pledge update, policyholder change) is ALL ZERO —
that is valid and must not trip the validator.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import EndorsementKind, EndorsementStatus
from app.schemas.proposal import MONEY_TOLERANCE, VAT_RATE

_MONEY_QUANT = Decimal("0.0001")

#: The five delta fields the invariants tie together, in dependency order.
DELTA_FIELDS = (
    "taxable_premium_delta_uf",
    "exempt_premium_delta_uf",
    "net_premium_delta_uf",
    "vat_delta_uf",
    "total_premium_delta_uf",
)


def _q(value: Any) -> Decimal:
    try:
        return Decimal(str(value)).quantize(_MONEY_QUANT)
    except (InvalidOperation, TypeError, ValueError):  # pragma: no cover - defensive
        return Decimal("0")


def _mismatch(field: str, expected: Decimal, actual: Decimal, rule: str) -> dict[str, Any]:
    return {
        "field": field,
        "rule": rule,
        "expected": str(_q(expected)),
        "received": str(_q(actual)),
        "difference": str(_q(Decimal(str(actual)) - Decimal(str(expected)))),
        "tolerance": str(MONEY_TOLERANCE),
    }


def reconcile_endorsement_money(
    values: dict[str, Any]
) -> tuple[dict[str, Decimal], list[dict[str, Any]]]:
    """Derive the missing delta fields and validate the supplied ones.

    ``values`` is the MERGED state (existing row + incoming patch), so a partial
    update is checked against what the row will look like after the write.
    Signs are preserved throughout — nothing here takes an absolute value.
    """
    taxable = values.get("taxable_premium_delta_uf")
    exempt = values.get("exempt_premium_delta_uf")
    net = values.get("net_premium_delta_uf")
    vat = values.get("vat_delta_uf")
    total = values.get("total_premium_delta_uf")

    derived: dict[str, Decimal] = {}
    errors: list[dict[str, Any]] = []

    # net_delta = taxable_delta + exempt_delta
    if taxable is not None and exempt is not None:
        expected_net = _q(Decimal(str(taxable)) + Decimal(str(exempt)))
        if net is None:
            net = expected_net
            derived["net_premium_delta_uf"] = expected_net
        elif abs(Decimal(str(net)) - expected_net) > MONEY_TOLERANCE:
            errors.append(
                _mismatch(
                    "net_premium_delta_uf", expected_net, Decimal(str(net)),
                    "net_delta = taxable_delta + exempt_delta",
                )
            )
    elif net is not None and taxable is not None and exempt is None:
        exempt = _q(Decimal(str(net)) - Decimal(str(taxable)))
        derived["exempt_premium_delta_uf"] = exempt

    # vat_delta = 0.19 * taxable_delta  (never on net)
    if taxable is not None:
        expected_vat = _q(Decimal(str(taxable)) * VAT_RATE)
        if vat is None:
            vat = expected_vat
            derived["vat_delta_uf"] = expected_vat
        elif abs(Decimal(str(vat)) - expected_vat) > MONEY_TOLERANCE:
            errors.append(
                _mismatch(
                    "vat_delta_uf", expected_vat, Decimal(str(vat)),
                    "vat_delta = 0.19 * taxable_delta (not on net)",
                )
            )

    # total_delta = net_delta + vat_delta
    if net is not None and vat is not None:
        expected_total = _q(Decimal(str(net)) + Decimal(str(vat)))
        if total is None:
            derived["total_premium_delta_uf"] = expected_total
        elif abs(Decimal(str(total)) - expected_total) > MONEY_TOLERANCE:
            errors.append(
                _mismatch(
                    "total_premium_delta_uf", expected_total, Decimal(str(total)),
                    "total_delta = net_delta + vat_delta",
                )
            )

    return derived, errors


# --- Write models -------------------------------------------------------------

class EndorsementMoneyMixin(BaseModel):
    """Deltas carry a sign: no ``ge=0`` anywhere in this block."""

    insured_amount_delta_uf: Decimal | None = None
    taxable_premium_delta_uf: Decimal | None = None
    exempt_premium_delta_uf: Decimal | None = None
    net_premium_delta_uf: Decimal | None = None
    vat_delta_uf: Decimal | None = None
    total_premium_delta_uf: Decimal | None = None
    commission_delta_uf: Decimal | None = None
    prorata_days: int | None = Field(default=None, ge=0)
    unexpired_days: int | None = Field(default=None, ge=0)


class EndorsementCreate(EndorsementMoneyMixin):
    model_config = ConfigDict(extra="forbid")

    policy_id: int = Field(gt=0)
    case_file_id: int | None = Field(default=None, gt=0)
    endorsement_number: str | None = Field(default=None, max_length=64)
    sequence_no: int | None = Field(default=None, ge=1)
    kind: EndorsementKind = EndorsementKind.OTHER
    status: EndorsementStatus = EndorsementStatus.DRAFT

    effective_at: datetime | None = None
    ends_at: datetime | None = None
    issued_at: date | None = None

    proposal_document_id: int | None = Field(default=None, gt=0)
    issued_document_id: int | None = Field(default=None, gt=0)

    motive: str | None = None
    contractual_basis: str | None = Field(default=None, max_length=255)
    effect: dict[str, Any] | list[Any] | None = None
    deductibles: dict[str, Any] | None = None

    extraction_id: int | None = Field(default=None, gt=0)
    extraction_confidence: Decimal | None = Field(default=None, ge=0, le=100)


class EndorsementUpdate(EndorsementMoneyMixin):
    model_config = ConfigDict(extra="forbid")

    endorsement_number: str | None = Field(default=None, max_length=64)
    sequence_no: int | None = Field(default=None, ge=1)
    kind: EndorsementKind | None = None
    status: EndorsementStatus | None = None
    case_file_id: int | None = Field(default=None, gt=0)

    effective_at: datetime | None = None
    ends_at: datetime | None = None
    issued_at: date | None = None

    proposal_document_id: int | None = Field(default=None, gt=0)
    issued_document_id: int | None = Field(default=None, gt=0)

    motive: str | None = None
    contractual_basis: str | None = Field(default=None, max_length=255)
    effect: dict[str, Any] | list[Any] | None = None
    deductibles: dict[str, Any] | None = None

    is_confirmed: bool | None = None
    extraction_id: int | None = Field(default=None, gt=0)
    extraction_confidence: Decimal | None = Field(default=None, ge=0, le=100)


class EndorsementIssue(BaseModel):
    """Attach the carrier's issued document and apply the deltas."""

    model_config = ConfigDict(extra="forbid")

    issued_document_id: int | None = Field(default=None, gt=0)
    endorsement_number: str | None = Field(default=None, max_length=64)
    issued_at: date | None = None
    effective_at: datetime | None = None
    note: str | None = None


# --- Read models --------------------------------------------------------------

class EndorsementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    policy_id: int
    case_file_id: int | None = None
    endorsement_number: str | None = None
    sequence_no: int
    kind: EndorsementKind
    status: EndorsementStatus

    effective_at: datetime | None = None
    ends_at: datetime | None = None
    issued_at: date | None = None

    proposal_document_id: int | None = None
    issued_document_id: int | None = None

    motive: str | None = None
    contractual_basis: str | None = None

    insured_amount_delta_uf: Decimal | None = None
    taxable_premium_delta_uf: Decimal | None = None
    exempt_premium_delta_uf: Decimal | None = None
    net_premium_delta_uf: Decimal | None = None
    vat_delta_uf: Decimal | None = None
    total_premium_delta_uf: Decimal | None = None
    commission_delta_uf: Decimal | None = None
    prorata_days: int | None = None
    unexpired_days: int | None = None

    effect: dict[str, Any] | list[Any] | None = None
    deductibles: dict[str, Any] | None = None

    extraction_id: int | None = None
    extraction_confidence: Decimal | None = None
    is_confirmed: bool
    confirmed_by_id: int | None = None
    confirmed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # UUID shared by the N endorsements of one prórroga (spec v3 §2.3);
    # server-minted by ``POST /endorsements/batch``, never client-supplied.
    batch_key: str | None = None


# --- Prórroga: the batch endorsement (spec v3 §4.3) ---------------------------

#: The only motive that is fanned out over N policies today. ``POST
#: /endorsements`` refuses it (a prórroga is a manual multi-select, rule 3);
#: widening this tuple is the single edit needed to batch another motive.
BATCH_KINDS: tuple[EndorsementKind, ...] = (EndorsementKind.PERIOD_EXTENSION,)


class EndorsementBatchCreate(BaseModel):
    """``POST /endorsements/batch`` — one prórroga over N policies.

    The policies are chosen EXPLICITLY (never derived from a period, rule 3)
    and must all sit under the same ``account_group_id``, else 422
    ``policies_span_groups``. The server mints one ``batch_key`` (uuid4) and
    writes one ``Endorsement`` + one ``case_file(kind=endorsement)`` per
    policy, all with ZERO premium deltas — a prórroga moves
    ``policy.end_date`` only, never the account folder's period.
    """

    model_config = ConfigDict(extra="forbid")

    policy_ids: list[int] = Field(min_length=1)
    kind: EndorsementKind = EndorsementKind.PERIOD_EXTENSION
    #: The new ``policy.end_date``; the router also moves ``period_end_at``.
    new_end_date: date
    effective_at: datetime
    issued_document_id: int | None = Field(default=None, gt=0)
    note: str | None = None

    @field_validator("policy_ids")
    @classmethod
    def _policy_ids_unique(cls, value: list[int]) -> list[int]:
        if any(pid <= 0 for pid in value):
            raise ValueError("policy_ids must be positive integers")
        if len(set(value)) != len(value):
            raise ValueError("policy_ids must not contain duplicates")
        return value

    @field_validator("kind")
    @classmethod
    def _kind_is_batchable(cls, value: EndorsementKind) -> EndorsementKind:
        if value not in BATCH_KINDS:
            allowed = ", ".join(kind.value for kind in BATCH_KINDS)
            raise ValueError(f"only these motives can be batched: {allowed}")
        return value


class EndorsementBatchIssue(BaseModel):
    """``POST /endorsements/batch/{batch_key}/issue`` — issues every member
    through the existing single-endorsement path and applies the period
    extension once per policy. The carrier usually sends ONE document for the
    whole prórroga, so ``issued_document_id`` is shared by all members."""

    model_config = ConfigDict(extra="forbid")

    issued_document_id: int | None = Field(default=None, gt=0)
    issued_at: date | None = None
    note: str | None = None


class EndorsementBatchItem(BaseModel):
    """One policy's share of a batch."""

    policy_id: int
    endorsement_id: int
    case_file_id: int | None = None
    #: Echoed by the ISSUE response only — the policy's end date after the
    #: extension was applied. ``None`` on the create response.
    policy_end_date: date | None = None


class EndorsementBatchResponse(BaseModel):
    """``{batch_key, items}`` — the answer to both batch endpoints.

    ``GET /endorsements?batch_key=<key>`` returns the same members as an
    ordinary ``EndorsementPage``.
    """

    batch_key: str
    kind: EndorsementKind = EndorsementKind.PERIOD_EXTENSION
    new_end_date: date | None = None
    items: list[EndorsementBatchItem] = Field(default_factory=list)


class EndorsementPage(BaseModel):
    items: list[EndorsementRead]
    total: int
    limit: int
    offset: int


class EndorsementIssueResult(BaseModel):
    """What issuing an endorsement moved, in one payload."""

    endorsement: EndorsementRead
    policy_id: int
    policy_total_premium_uf: Decimal | None = None
    policy_insured_amount_uf: Decimal | None = None
    collection_plan_id: int | None = None
    collection_total_premium_uf: Decimal | None = None


__all__ = [
    "DELTA_FIELDS",
    "reconcile_endorsement_money",
    "EndorsementCreate",
    "EndorsementUpdate",
    "EndorsementIssue",
    "BATCH_KINDS",
    "EndorsementBatchCreate",
    "EndorsementBatchIssue",
    "EndorsementBatchItem",
    "EndorsementBatchResponse",
    "EndorsementRead",
    "EndorsementPage",
    "EndorsementIssueResult",
]
