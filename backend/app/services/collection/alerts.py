"""Bell alerts about cuotas (v12, 03 §8) — one origin per cuota.

Every cuota has at most ONE open notification per user, origin
``("installment", id)``; :func:`app.services.notifications.notify` escalates it
in place. Which alert a cuota deserves TODAY is a pure function of its state
(:func:`installment_alert`), in this priority:

1. ``termination_risk`` (todo, level ``term``) — past the last cadence step, or
   an Art. 528 notice is on file;
2. ``promise_broken`` (todo, level ``p<promise date>``) — *Promete pagar el*
   passed unpaid on a cuota already late (a new promise that breaks is a new
   level -> a fresh alert; a promise dated before the due date is not broken
   until the cuota itself falls due);
3. ``direct_debit_rejected`` (todo, level ``d<rejection date>``);
4. the cadence step reached (``[-15, -5, 0, 3, 10]`` by default): steps ≤ 0 are
   fyi ``installment_due``, steps > 0 todo ``installment_overdue``; the level is
   the signed step (``-15``, ``-5``, ``0``, ``+3``, ``+10``).

A ``promised`` cuota (promise still in the future) raises nothing new — the
broker already managed it. Paid or voided -> every alert about it is closed.
A ``pending_review`` cuota (a past cuota of a policy loaded late, review r2 D1)
raises nothing and any alert it had is closed until the broker decides.

Recipients: the plan's account owner, else the policy's
(:func:`app.services.notifications.recipients_for_policy`: owner, else every
broker admin). Flushes only; the caller commits.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.models.case_file import CaseFile
from app.models.enums import (
    CollectionPlanStatus,
    NotificationKind,
    NotificationTray,
)
from app.models.policy import PolicyStatus
from app.models.user import User
from app.services.collection.status import InstallmentState
from app.services.notifications import close_for_origin, notify, recipients_for_policy

if TYPE_CHECKING:
    from app.models.collection import CollectionInstallment, CollectionPlan

ORIGIN_INSTALLMENT = "installment"

#: The kinds this module raises about a cuota (what a payment closes).
INSTALLMENT_KINDS: tuple[NotificationKind, ...] = (
    NotificationKind.INSTALLMENT_DUE,
    NotificationKind.INSTALLMENT_OVERDUE,
    NotificationKind.TERMINATION_RISK,
    NotificationKind.PROMISE_BROKEN,
    NotificationKind.DIRECT_DEBIT_REJECTED,
)

_CLOSED_PLAN = (CollectionPlanStatus.TERMINATED, CollectionPlanStatus.SETTLED)


def cadence_level_label(step: int) -> str:
    """``-15`` / ``-5`` / ``0`` / ``+3`` / ``+10``."""
    return "0" if step == 0 else f"{step:+d}"


def installment_alert(
    inst: "CollectionInstallment",
    state: InstallmentState,
    *,
    today: date,
    cadence_days: list[int],
) -> tuple[NotificationKind, NotificationTray, str] | None:
    """The single alert this cuota deserves today, or ``None``. Pure."""
    if state.state in ("paid", "voided", "promised", "pending_review"):
        return None
    due = inst.due_date
    if due is None:
        return None
    if state.state == "termination_risk":
        return NotificationKind.TERMINATION_RISK, NotificationTray.TODO, "term"
    promise = inst.promise_until
    if promise is not None and promise < today and today > due:
        return NotificationKind.PROMISE_BROKEN, NotificationTray.TODO, f"p{promise.isoformat()}"
    if "direct_debit_rejected" in state.flags and inst.direct_debit_rejected_at is not None:
        return (
            NotificationKind.DIRECT_DEBIT_REJECTED,
            NotificationTray.TODO,
            f"d{inst.direct_debit_rejected_at.isoformat()}",
        )
    offset = (today - due).days
    reached = [int(step) for step in cadence_days if offset >= int(step)]
    if not reached:
        return None
    step = max(reached)
    if step <= 0:
        return NotificationKind.INSTALLMENT_DUE, NotificationTray.FYI, cadence_level_label(step)
    return NotificationKind.INSTALLMENT_OVERDUE, NotificationTray.TODO, cadence_level_label(step)


def recipients_for_plan(db: Session, plan: "CollectionPlan") -> list[int]:
    """The plan's account owner (when active, same broker), else the policy's."""
    if plan.case_file_id:
        case = db.get(CaseFile, plan.case_file_id)
        if case is not None and case.broker_id == plan.broker_id and case.owner_user_id:
            owner = db.get(User, case.owner_user_id)
            if owner is not None and owner.is_active and owner.broker_id == plan.broker_id:
                return [owner.id]
    return recipients_for_policy(db, plan.policy)


def _label(insurer: Any) -> str | None:
    if insurer is None:
        return None
    return getattr(insurer, "trade_name", None) or getattr(insurer, "legal_name", None)


def installment_params(inst: "CollectionInstallment", state: InstallmentState) -> dict[str, Any]:
    """Render data for the bell (JSON-safe after ``notify``)."""
    plan = inst.collection_plan
    policy = plan.policy
    client = getattr(policy, "client", None)
    insured = getattr(client, "insured", None) if client is not None else None
    return {
        "installment_id": inst.id,
        "plan_id": plan.id,
        "policy_id": policy.id,
        "policy_number": policy.policy_number,
        "number": inst.number,
        "installment_count": plan.installment_count or len(plan.installments or []),
        "due_date": inst.due_date,
        "amount_uf": state.balance_uf if state.balance_uf else Decimal(str(inst.gross_amount_uf)),
        "currency": inst.currency,
        "days_late": state.days_late,
        "state": state.state,
        "promise_until": inst.promise_until,
        "insured_name": getattr(insured, "legal_name", None),
        "insurer_name": _label(getattr(policy, "insurer", None)),
    }


def alerts_apply(inst: "CollectionInstallment") -> bool:
    """Only cuotas of live policies/plans raise alerts (history stays quiet)."""
    plan = inst.collection_plan
    policy = plan.policy
    if policy is None or policy.status != PolicyStatus.ACTIVE:
        return False
    if policy.terminated_on is not None:
        return False
    return plan.status not in _CLOSED_PLAN


def sync_installment_notifications(
    db: Session,
    inst: "CollectionInstallment",
    state: InstallmentState,
    *,
    today: date,
    cadence_days: list[int],
    now: datetime | None = None,
    renotify: bool = False,
) -> str:
    """Raise / escalate / close the cuota's alert. Returns what happened.

    ``closed`` (paid/voided), ``raised`` (an alert was created or updated),
    ``none`` (nothing due today, or a historic policy).
    """
    if state.state in ("paid", "voided", "pending_review"):
        closed = close_for_origin(
            db,
            broker_id=inst.broker_id,
            origin_type=ORIGIN_INSTALLMENT,
            origin_id=inst.id,
            kinds=list(INSTALLMENT_KINDS),
            now=now,
        )
        return "closed" if closed else "none"
    if not alerts_apply(inst):
        return "none"
    alert = installment_alert(inst, state, today=today, cadence_days=cadence_days)
    if alert is None:
        return "none"
    kind, tray, level = alert
    rows = notify(
        db,
        broker_id=inst.broker_id,
        user_ids=recipients_for_plan(db, inst.collection_plan),
        kind=kind,
        tray=tray,
        origin_type=ORIGIN_INSTALLMENT,
        origin_id=inst.id,
        params=installment_params(inst, state),
        link_path=f"/collections/{inst.collection_plan_id}",
        level=level,
        now=now,
        renotify=renotify,
    )
    return "raised" if rows else "none"


def close_installment_alerts(
    db: Session, inst: "CollectionInstallment", *, now: datetime | None = None
) -> int:
    """Close the cuota's open alert (e.g. the broker registered a promise)."""
    return close_for_origin(
        db,
        broker_id=inst.broker_id,
        origin_type=ORIGIN_INSTALLMENT,
        origin_id=inst.id,
        kinds=list(INSTALLMENT_KINDS),
        now=now,
    )


__all__ = [
    "INSTALLMENT_KINDS",
    "ORIGIN_INSTALLMENT",
    "alerts_apply",
    "cadence_level_label",
    "close_installment_alerts",
    "installment_alert",
    "installment_params",
    "recipients_for_plan",
    "sync_installment_notifications",
]
