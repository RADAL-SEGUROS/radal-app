"""Past-due cuotas of a policy loaded late — *pending review* (review r2, owner decision D1).

A broker who uploads (or types in) a policy that started months ago gets a
plan whose first cuotas are already past due. Those cuotas say nothing about
the payer — the broker simply has not told Radal yet whether they were paid.
Until the broker decides, they are **pending review**: no ``termination_risk``
/ overdue alert, no payer reminder, and they are not counted as overdue in any
KPI or board total.

No schema change. The state lives in the plan's bitácora (``activity`` rows on
``entity_type=collection_plan``):

* ``collection_plan.past_installments_pending`` — written when the plan is
  created from the policy (the vía lector upload, or *Cargar plan de pago* on
  a vía Manual policy) and some cuota is due before that day (Chile date).
  ``meta.as_of`` = that day: the **cutoff**.
* ``collection_plan.past_installments_decided`` — the broker's decision
  (``meta.decision`` = ``paid`` | ``unpaid``) through
  ``POST /collections/plans/{id}/past-installments``.

A cuota is pending review when its plan's latest marker is a ``pending`` one
(no decision after it) and ``due_date < cutoff``, and it is neither paid nor
voided. :func:`review_cutoffs` answers it for a whole broker in ONE query;
:func:`app.services.collection.status.compute_installment_state` turns it into
the state ``pending_review`` when the caller passes the cutoff.

``paid`` books, through the ledger (the only payment writer), one full
declared payment per pending cuota on its due date with the plan's method and
the note "Declarado pagado al cargar la póliza". ``unpaid`` simply lets the
cuotas follow the cadence again (alerts raise at once). Both write the event;
repeating the same decision is a no-op that returns the current counts.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.enums import EntityType

if TYPE_CHECKING:
    from app.models.collection import CollectionInstallment, CollectionPlan
    from app.models.user import User

PENDING_ACTION = "collection_plan.past_installments_pending"
DECIDED_ACTION = "collection_plan.past_installments_decided"
DECISIONS = ("paid", "unpaid")
PAID_NOTE = "Declarado pagado al cargar la póliza"

__all__ = [
    "DECIDED_ACTION",
    "DECISIONS",
    "PAID_NOTE",
    "PENDING_ACTION",
    "PastReviewError",
    "decide",
    "is_pending",
    "latest_decision",
    "mark_if_past",
    "pending_summary",
    "plan_cutoff",
    "review_cutoffs",
]


class PastReviewError(Exception):
    """A refused decision. ``detail`` is the structured Spanish body."""

    def __init__(self, detail: dict[str, Any], status_code: int = 409) -> None:
        super().__init__(detail.get("reason"))
        self.detail = detail
        self.status_code = status_code


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _events(db: Session, broker_id: int, plan_ids: Iterable[int] | None = None):
    stmt = select(Activity.entity_id, Activity.action, Activity.meta).where(
        Activity.broker_id == broker_id,
        Activity.entity_type == EntityType.COLLECTION_PLAN,
        Activity.action.in_([PENDING_ACTION, DECIDED_ACTION]),
    )
    if plan_ids is not None:
        ids = sorted({int(i) for i in plan_ids if i is not None})
        if not ids:
            return []
        stmt = stmt.where(Activity.entity_id.in_(ids))
    return db.execute(stmt.order_by(Activity.id.asc())).all()


def review_cutoffs(
    db: Session, broker_id: int, plan_ids: Iterable[int] | None = None
) -> dict[int, date]:
    """``plan_id -> cutoff`` for every plan of the broker still awaiting a decision."""
    out: dict[int, date] = {}
    for plan_id, action, meta in _events(db, broker_id, plan_ids):
        if plan_id is None:
            continue
        if action == PENDING_ACTION:
            cutoff = _as_date((meta or {}).get("as_of"))
            if cutoff is not None:
                out[plan_id] = cutoff
        else:
            out.pop(plan_id, None)
    return out


def plan_cutoff(db: Session, plan: "CollectionPlan") -> date | None:
    return review_cutoffs(db, plan.broker_id, [plan.id]).get(plan.id)


def latest_decision(db: Session, plan: "CollectionPlan") -> str | None:
    """The plan's last decision when it is the latest event, else ``None``."""
    last: str | None = None
    for _plan_id, action, meta in _events(db, plan.broker_id, [plan.id]):
        last = (meta or {}).get("decision") if action == DECIDED_ACTION else None
    return last


def is_pending(inst: "CollectionInstallment", cutoff: date | None) -> bool:
    """Due before the cutoff (paid/voided is decided by the state machine)."""
    due = getattr(inst, "due_date", None)
    return cutoff is not None and due is not None and due < cutoff


def _states(db: Session, plan: "CollectionPlan", *, today: date):
    from app.services.collection.status import (  # noqa: PLC0415 - status imports this module
        broker_collection_params,
        compute_installment_state,
    )

    cadence, tolerance = broker_collection_params(db, plan.broker_id)
    for inst in sorted(plan.installments or [], key=lambda r: (r.number or 0, r.id or 0)):
        yield inst, compute_installment_state(
            inst, list(inst.payments), today=today, cadence_days=cadence, tolerance_uf=tolerance
        )


def _pending_rows(db: Session, plan: "CollectionPlan", *, today: date, cutoff: date | None):
    if cutoff is None:
        return []
    return [
        (inst, state)
        for inst, state in _states(db, plan, today=today)
        if is_pending(inst, cutoff) and state.state not in ("paid", "voided")
    ]


def _summary(plan: "CollectionPlan", rows) -> dict[str, Any] | None:
    if not rows:
        return None
    dues = [inst.due_date for inst, _ in rows]
    return {
        "plan_id": plan.id,
        "count": len(rows),
        "total_uf": sum((Decimal(str(state.balance_uf)) for _, state in rows), Decimal("0")),
        "first_due": min(dues),
        "last_due": max(dues),
    }


def pending_summary(
    db: Session, plan: "CollectionPlan", *, today: date, cutoff: Any = ...
) -> dict[str, Any] | None:
    """``{plan_id, count, total_uf, first_due, last_due}`` of the cuotas still
    pending review, or ``None`` (nothing pending / already decided)."""
    if cutoff is ...:
        cutoff = plan_cutoff(db, plan)
    return _summary(plan, _pending_rows(db, plan, today=today, cutoff=cutoff))


def mark_if_past(
    db: Session, plan: "CollectionPlan", *, today: date, user: "User | None"
) -> dict[str, Any] | None:
    """Write the ``pending`` marker when the new plan has cuotas due before
    ``today``. Returns the pending summary (``None`` when nothing is past)."""
    db.flush()
    db.expire(plan, ["installments"])
    past = [
        inst
        for inst in (plan.installments or [])
        if inst.due_date is not None and inst.due_date < today
    ]
    if not past:
        return None
    total = sum((Decimal(str(inst.gross_amount_uf or 0)) for inst in past), Decimal("0"))
    db.add(
        Activity(
            broker_id=plan.broker_id,
            user_id=user.id if user is not None else None,
            action=PENDING_ACTION,
            entity_type=EntityType.COLLECTION_PLAN,
            entity_id=plan.id,
            description=(
                f"{len(past)} cuota(s) con vencimiento anterior a la carga de la póliza: "
                "por confirmar si ya están pagadas"
            ),
            meta={
                "as_of": today.isoformat(),
                "count": len(past),
                "total_uf": str(total),
                "policy_id": plan.policy_id,
            },
        )
    )
    db.flush()
    return pending_summary(db, plan, today=today, cutoff=today)


def _counts(db: Session, plan: "CollectionPlan", *, today: date) -> dict[str, int]:
    """Live state -> number of cuotas of the plan (``pending_review`` included)."""
    from app.services.collection.status import (  # noqa: PLC0415
        broker_collection_params,
        compute_installment_state,
    )

    db.expire(plan, ["installments"])
    cutoff = plan_cutoff(db, plan)
    out: dict[str, int] = {}
    cadence, tolerance = broker_collection_params(db, plan.broker_id)
    for inst in plan.installments or []:
        state = compute_installment_state(
            inst,
            list(inst.payments),
            today=today,
            cadence_days=cadence,
            tolerance_uf=tolerance,
            review_cutoff=cutoff,
        )
        out[state.state] = out.get(state.state, 0) + 1
    return out


def decide(
    db: Session,
    plan: "CollectionPlan",
    *,
    decision: str,
    user: "User | None",
    today: date,
) -> dict[str, Any]:
    """Apply the broker's decision (see the module doc). Flushes; the caller commits."""
    from app.models.enums import EvidenceLevel, PaymentCurrency  # noqa: PLC0415
    from app.services.collection import ledger  # noqa: PLC0415 - ledger imports status

    if decision not in DECISIONS:
        raise PastReviewError(
            {"code": "invalid_decision", "reason": "La decisión debe ser «paid» o «unpaid»."},
            status_code=422,
        )
    cutoff = plan_cutoff(db, plan)
    if cutoff is None:
        previous = latest_decision(db, plan)
        if previous is None:
            raise PastReviewError(
                {
                    "code": "no_past_installments",
                    "reason": "Este plan no tiene cuotas con vencimiento pasado por revisar.",
                    "plan_id": plan.id,
                }
            )
        if previous != decision:
            raise PastReviewError(
                {
                    "code": "past_installments_decided",
                    "reason": (
                        "Ya decidiste sobre las cuotas pasadas de este plan. Registra o "
                        "revierte los pagos desde cada cuota."
                    ),
                    "plan_id": plan.id,
                    "decision": previous,
                }
            )
        return {
            "plan_id": plan.id,
            "decision": decision,
            "already_decided": True,
            "payments_registered": 0,
            "past_installments_pending": None,
            "counts": _counts(db, plan, today=today),
        }

    rows = _pending_rows(db, plan, today=today, cutoff=cutoff)
    payment_ids: list[int] = []
    if decision == "paid":
        for inst, state in rows:
            currency = getattr(inst.currency, "value", inst.currency) or "UF"
            payment, _new_state = ledger.register_payment(
                db,
                inst,
                amount=state.balance_uf,
                currency=PaymentCurrency(currency),
                paid_on=inst.due_date,
                today=today,
                user=user,
                method=plan.payment_mode,
                evidence=EvidenceLevel.DECLARED,
                note=PAID_NOTE,
            )
            payment_ids.append(payment.id)
    total = sum((Decimal(str(state.balance_uf)) for _, state in rows), Decimal("0"))
    label = "pagadas" if decision == "paid" else "impagas (siguen la cobranza)"
    db.add(
        Activity(
            broker_id=plan.broker_id,
            user_id=user.id if user is not None else None,
            action=DECIDED_ACTION,
            entity_type=EntityType.COLLECTION_PLAN,
            entity_id=plan.id,
            description=f"{len(rows)} cuota(s) pasada(s) declarada(s) {label}",
            meta={
                "decision": decision,
                "as_of": cutoff.isoformat(),
                "installment_ids": [inst.id for inst, _ in rows],
                "payment_ids": payment_ids,
                "total_uf": str(total),
            },
        )
    )
    db.flush()
    if decision == "unpaid":
        # The cuotas follow the cadence again: their alerts raise now.
        ledger.sync_plan_installments(db, plan, today=today)
    return {
        "plan_id": plan.id,
        "decision": decision,
        "already_decided": False,
        "payments_registered": len(payment_ids),
        "past_installments_pending": None,
        "counts": _counts(db, plan, today=today),
    }
