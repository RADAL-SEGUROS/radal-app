"""The Cobranza board: every cuota of the broker with its LIVE state (v12, 02 §4).

States are computed at request time with the pure
:func:`app.services.collection.status.compute_installment_state` (``today`` is
Santiago's business date) — never read from the cache, so the board is right
even between two runs of the clock.

Buckets (the four F2 subtabs):

* ``upcoming`` — open (``upcoming`` / ``due_today``) and due within 30 days,
  today included;
* ``overdue``  — ``overdue`` / ``termination_risk`` / ``promised`` (vencida,
  riesgo de término, en acuerdo);
* ``current``  — open and not late, in a plan with nothing overdue (al día);
* ``settled``  — ``paid`` or ``voided``.
* ``review``   — ``pending_review`` (review r2 D1): past cuotas of a policy
  loaded late, awaiting the broker's paid/unpaid decision. Never ``overdue``,
  never counted in the overdue card; a plan with only these is not "late".

Tenant scope: installment, plan AND policy are filtered by ``broker_id``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.activity import Note
from app.models.client import Client
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.enums import (
    EntityType,
    EvidenceLevel,
    EvidenceStatus,
    PaymentStatus,
    PolicyCurrency,
)
from app.models.insured import Insured
from app.models.mail import PaymentEvidence
from app.models.payment import Payment
from app.models.policy import Policy, PolicyStatus
from app.services.collection.resolver import CollectionMode, lead_insurer_id, resolve_collection_mode
from app.services.collection.status import (
    InstallmentState,
    broker_collection_params,
    compute_installment_state,
)

BUCKETS = ("upcoming", "overdue", "current", "settled", "review")
OPEN_STATES = frozenset({"upcoming", "due_today"})
LATE_STATES = frozenset({"overdue", "termination_risk", "promised"})
SETTLED_STATES = frozenset({"paid", "voided"})
REVIEW_STATES = frozenset({"pending_review"})
UPCOMING_WINDOW_DAYS = 30


@dataclass
class BoardRow:
    inst: CollectionInstallment
    plan: CollectionPlan
    policy: Policy
    state: InstallmentState


def _val(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def line_of(policy: Policy):  # noqa: ANN201
    """The catalogue line of a policy: its subtype's line, else its own line."""
    subtype = getattr(policy, "insurance_subtype", None)
    if subtype is not None and getattr(subtype, "insurance_line", None) is not None:
        return subtype.insurance_line
    return getattr(policy, "insurance_line", None)


def owner_ids(row: BoardRow) -> set[int]:
    ids: set[int] = set()
    for case in (getattr(row.plan, "case_file", None), getattr(row.policy, "case_file", None)):
        if case is not None and case.owner_user_id:
            ids.add(case.owner_user_id)
    return ids


def load_rows(
    db: Session,
    broker_id: int,
    *,
    today: date,
    insurer_id: int | None = None,
    line_code: str | None = None,
    method: str | None = None,
    owner_user_id: int | None = None,
    q: str | None = None,
    policy_id: int | None = None,
    plan_id: int | None = None,
) -> list[BoardRow]:
    """Every cuota of the broker matching the SQL-side filters, with its state."""
    stmt = (
        select(CollectionInstallment)
        .join(CollectionPlan, CollectionPlan.id == CollectionInstallment.collection_plan_id)
        .join(Policy, Policy.id == CollectionPlan.policy_id)
        .where(
            CollectionInstallment.broker_id == broker_id,
            CollectionPlan.broker_id == broker_id,
            Policy.broker_id == broker_id,
        )
        .options(
            selectinload(CollectionInstallment.payments),
            selectinload(CollectionInstallment.collection_plan).selectinload(
                CollectionPlan.case_file
            ),
            selectinload(CollectionInstallment.collection_plan)
            .selectinload(CollectionPlan.policy)
            .selectinload(Policy.client)
            .selectinload(Client.insured),
            selectinload(CollectionInstallment.collection_plan)
            .selectinload(CollectionPlan.policy)
            .selectinload(Policy.insurer),
            selectinload(CollectionInstallment.collection_plan)
            .selectinload(CollectionPlan.policy)
            .selectinload(Policy.coinsurance_shares),
            selectinload(CollectionInstallment.collection_plan)
            .selectinload(CollectionPlan.policy)
            .selectinload(Policy.case_file),
        )
    )
    if policy_id is not None:
        stmt = stmt.where(Policy.id == policy_id)
    if plan_id is not None:
        stmt = stmt.where(CollectionPlan.id == plan_id)
    if method:
        stmt = stmt.where(CollectionPlan.payment_mode == method)
    if q:
        like = f"%{q.strip().lower()}%"
        # RUTs are stored ``BODY-DV`` without dots, so a dotted needle
        # (``76.345.678-1``) — or a partial one — matches once its dots go.
        rut_like = f"%{q.strip().lower().replace('.', '').replace(' ', '')}%"
        stmt = (
            stmt.join(Client, Client.id == Policy.client_id)
            .join(Insured, Insured.id == Client.insured_id)
            .where(
                or_(
                    func.lower(Policy.policy_number).like(like),
                    func.lower(Insured.legal_name).like(like),
                    func.lower(func.coalesce(Insured.trade_name, "")).like(like),
                    func.lower(Insured.rut).like(rut_like),
                )
            )
        )
    insts = db.scalars(stmt).unique().all()

    cadence, tolerance = broker_collection_params(db, broker_id)
    from app.services.collection.past_review import review_cutoffs  # noqa: PLC0415

    cutoffs = review_cutoffs(db, broker_id)
    rows: list[BoardRow] = []
    for inst in insts:
        plan = inst.collection_plan
        policy = plan.policy
        if insurer_id is not None and insurer_id not in {policy.insurer_id, lead_insurer_id(policy)}:
            continue
        if line_code:
            line = line_of(policy)
            if line is None or (getattr(line, "code", None) or "").upper() != line_code.upper():
                continue
        state = compute_installment_state(
            inst,
            list(inst.payments),
            today=today,
            cadence_days=cadence,
            tolerance_uf=tolerance,
            review_cutoff=cutoffs.get(plan.id),
        )
        row = BoardRow(inst=inst, plan=plan, policy=policy, state=state)
        if owner_user_id is not None and owner_user_id not in owner_ids(row):
            continue
        rows.append(row)
    return rows


def buckets_for(rows: Iterable[BoardRow], *, today: date) -> dict[int, set[str]]:
    """``installment id -> buckets`` (a cuota may sit in upcoming AND current)."""
    rows = list(rows)
    late_plans = {r.plan.id for r in rows if r.state.state in LATE_STATES}
    horizon = today + timedelta(days=UPCOMING_WINDOW_DAYS)
    out: dict[int, set[str]] = {}
    for row in rows:
        tags: set[str] = set()
        state = row.state.state
        due = row.inst.due_date
        if state in SETTLED_STATES:
            tags.add("settled")
        elif state in REVIEW_STATES:
            tags.add("review")
        elif state in LATE_STATES:
            tags.add("overdue")
        else:
            if due is not None and today <= due <= horizon:
                tags.add("upcoming")
            if row.plan.id not in late_plans:
                tags.add("current")
        out[row.inst.id] = tags
    return out


def sort_rows(rows: list[BoardRow], bucket: str | None) -> list[BoardRow]:
    if bucket == "settled":
        return sorted(
            rows,
            key=lambda r: (r.state.paid_on or r.inst.due_date or date.min, r.inst.id),
            reverse=True,
        )
    return sorted(rows, key=lambda r: (r.inst.due_date or date.max, r.inst.number, r.inst.id))


class ModeCache:
    """``resolve_collection_mode`` depends on the plan, not the cuota: cache it."""

    def __init__(self, db: Session, broker_id: int) -> None:
        self.db = db
        self.broker_id = broker_id
        self._by_plan: dict[int, CollectionMode] = {}

    def get(self, row: BoardRow) -> CollectionMode:
        mode = self._by_plan.get(row.plan.id)
        if mode is None:
            mode = resolve_collection_mode(self.db, row.inst, broker_id=self.broker_id)
            self._by_plan[row.plan.id] = mode
        return mode


def last_notes(db: Session, broker_id: int, installment_ids: list[int]) -> dict[int, Note]:
    """The latest gestión note per cuota (notes on ``EntityType.INSTALLMENT``)."""
    if not installment_ids:
        return {}
    notes = db.scalars(
        select(Note)
        .where(
            Note.broker_id == broker_id,
            Note.entity_type == EntityType.INSTALLMENT,
            Note.entity_id.in_(installment_ids),
        )
        .order_by(Note.id.asc())
    ).all()
    out: dict[int, Note] = {}
    for note in notes:
        out[note.entity_id] = note
    return out


def policies_without_plan_stmt(broker_id: int, *, today: date):
    """Live policies of the broker with no cuota on any plan ("sin plan de pago").

    ONE definition shared with the Pólizas main view (``GET /policies/summary``
    ``no_plan`` and ``?collection=no_plan``): computed status ``active`` or
    ``upcoming`` (:func:`app.services.policy_registry.computed_status_clause`)
    and no ``collection_installment`` — an empty plan is still "sin plan". The
    Cobranza card, the clock's ``plan_missing`` to-do and the Pólizas card agree.
    """
    from app.services.policy_registry import computed_status_clause  # noqa: PLC0415

    has_cuotas = (
        select(CollectionInstallment.id)
        .join(CollectionPlan, CollectionPlan.id == CollectionInstallment.collection_plan_id)
        .where(CollectionPlan.policy_id == Policy.id, CollectionPlan.broker_id == broker_id)
        .exists()
    )
    live = or_(
        computed_status_clause("active", today), computed_status_clause("upcoming", today)
    )
    return select(Policy).where(Policy.broker_id == broker_id, live, ~has_cuotas)


def capped_payment_amounts(inst: CollectionInstallment) -> list[tuple[date | None, Decimal]]:
    """``(paid_on, amount counted)`` per ACTIVE payment, capped at the cuota's gross.

    Payments are applied in ``(paid_on, id)`` order; once the running total
    reaches the gross, a later payment counts only what was still owed (0 when
    nothing was). So "Cobrado este mes" never counts an over-payment twice —
    the KPI, the cuota panel and the plan page agree (review v12 #1).
    """
    gross = Decimal(str(inst.gross_amount_uf or 0))
    active = sorted(
        (p for p in inst.payments if _val(p.status) == PaymentStatus.ACTIVE.value),
        key=lambda p: (p.paid_on or date.max, p.id or 0),
    )
    out: list[tuple[date | None, Decimal]] = []
    running = Decimal("0")
    for payment in active:
        amount = Decimal(str(payment.amount_uf or 0))
        counted = max(Decimal("0"), min(amount, gross - running)) if gross > 0 else amount
        running += amount
        out.append((payment.paid_on, counted))
    return out


def _money(rows: Iterable[BoardRow]) -> dict[str, Any]:
    rows = list(rows)
    return {
        "count": len(rows),
        "uf": sum((r.state.balance_uf for r in rows if _val(r.inst.currency) != "USD"), Decimal("0")),
    }


def summary_cards(
    db: Session, broker_id: int, *, today: date, owner_user_id: int | None = None
) -> dict[str, Any]:
    """The Cobranza cards (02 §4).

    ``owner_user_id`` narrows the CUOTA cards to that person's book (the
    board's ``owner=me``); ``unconfirmed_payments``, ``pending_evidence`` and
    ``plans_missing`` stay broker-wide (team inboxes).
    """
    rows = load_rows(db, broker_id, today=today, owner_user_id=owner_user_id)
    open_rows = [r for r in rows if r.state.state in OPEN_STATES]
    due_7 = [
        r for r in open_rows
        if r.inst.due_date is not None and today <= r.inst.due_date <= today + timedelta(days=7)
    ]
    due_30 = [
        r for r in open_rows
        if r.inst.due_date is not None
        and today <= r.inst.due_date <= today + timedelta(days=UPCOMING_WINDOW_DAYS)
    ]
    late = [r for r in rows if r.state.state in LATE_STATES]
    review = [r for r in rows if r.state.state in REVIEW_STATES]

    month_start = today.replace(day=1)
    collected = sum(
        (
            amount
            for row in rows
            if _val(row.inst.currency) == PolicyCurrency.UF.value
            for paid_on, amount in capped_payment_amounts(row.inst)
            if paid_on is not None and month_start <= paid_on <= today
        ),
        Decimal("0"),
    )
    unconfirmed = db.scalar(
        select(func.count(Payment.id)).where(
            Payment.broker_id == broker_id,
            Payment.status == PaymentStatus.ACTIVE,
            Payment.confirmed_by_insurer.is_(False),
            Payment.evidence_level.in_([EvidenceLevel.DECLARED, EvidenceLevel.REPORTED]),
        )
    )
    pending_evidence = db.scalar(
        select(func.count(PaymentEvidence.id)).where(
            PaymentEvidence.broker_id == broker_id,
            PaymentEvidence.status.in_([EvidenceStatus.SUGGESTED, EvidenceStatus.NEEDS_REVIEW]),
        )
    )
    plans_missing = db.scalar(
        select(func.count()).select_from(policies_without_plan_stmt(broker_id, today=today).subquery())
    )
    return {
        "as_of": today,
        "due_7d": _money(due_7),
        "due_30d": _money(due_30),
        "overdue": _money(late),
        "termination_risk": _money(r for r in late if r.state.state == "termination_risk"),
        # Review r2 (D1): past cuotas awaiting the broker's paid/unpaid answer.
        "past_installments_pending": _money(review),
        "collected_month_uf": Decimal(str(collected or 0)),
        "unconfirmed_payments": int(unconfirmed or 0),
        "pending_evidence": int(pending_evidence or 0),
        "plans_missing": int(plans_missing or 0),
    }


__all__ = [
    "BUCKETS",
    "BoardRow",
    "LATE_STATES",
    "ModeCache",
    "OPEN_STATES",
    "REVIEW_STATES",
    "SETTLED_STATES",
    "buckets_for",
    "capped_payment_amounts",
    "last_notes",
    "line_of",
    "load_rows",
    "policies_without_plan_stmt",
    "sort_rows",
    "summary_cards",
]
