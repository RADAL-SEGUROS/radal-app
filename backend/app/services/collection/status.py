"""The cuota state machine (v12, docs/v12/03-collections-forpay.md §1).

Payments are the truth; ``collection_installment.status`` is a CACHE. Every
payment write (register, reverse, confirm, webhook) and the daily clock call
:func:`refresh_installment_cache`, which runs the PURE
:func:`compute_installment_state` and writes the legacy ``InstallmentStatus``
(+ ``paid_on`` / ``days_late``) that lists and old screens filter on.

The order is binding — the first rule that matches wins:

1. ``voided``           annulled by an endorsement, or the plan/policy was
                        terminated and the cuota falls due AFTER the
                        termination date;
2. ``paid``             Σ active ``amount_uf`` ≥ ``gross_amount_uf − tolerance``;
3. ``promised``         ``promise_until ≥ today`` and ``today > due``
                        (*Promete pagar el* on a cuota already late — a
                        promise on a cuota not yet due changes nothing);
4. ``termination_risk`` ``today > due + max(cadence_days)`` — or an Art. 528
                        notice was already sent on an overdue cuota;
5. ``overdue``          ``today > due``;
6. ``due_today``        ``today == due``;
7. ``upcoming``         everything else (incl. a cuota with no due date).

``pending_review`` (review r2, D1) sits between 2 and 3: a cuota of a policy
loaded late, due before the plan's review cutoff, while the broker has not
said whether it was paid (:mod:`app.services.collection.past_review`). It is
never late, raises no alert and is cached as ``pending``. Only callers that
pass ``review_cutoff`` see it — the payment writes (balance checks) do not need it.

Flags are orthogonal: ``partial`` (active payments and balance > tolerance),
``direct_debit_rejected`` (rejection dated after the last active payment),
``unconfirmed`` (paid, best evidence weaker than ``insurer_confirmed``),
``no_receipt`` (paid, no active payment carries a comprobante).

Legacy rows: a cuota with NO payment rows at all whose cached status is
``paid``/``paid_late``/``credited`` (written before v12, or by the legacy
``PATCH .../installments/{n}``) is read as paid at ``declared`` evidence, and a
legacy ``cancelled`` one as voided — so the daily clock never erases history
that predates the ``payment`` table. As soon as one payment row exists, the
payments alone decide.

The function is pure: no session, no clock. ``today`` is always injected
(business dates are America/Santiago — the caller decides what "today" is).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Iterable, Literal

from sqlalchemy import select

from app.models.collection_settings import DEFAULT_CADENCE_DAYS, CollectionSettings
from app.models.enums import (
    CollectionPlanStatus,
    EvidenceLevel,
    InstallmentStatus,
    PaymentStatus,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.collection import CollectionInstallment
    from app.models.payment import Payment

StateName = Literal[
    "voided",
    "paid",
    "pending_review",
    "promised",
    "termination_risk",
    "overdue",
    "due_today",
    "upcoming",
]

#: Weakest -> strongest. ``unconfirmed`` = best evidence below insurer_confirmed.
EVIDENCE_ORDER: tuple[str, ...] = (
    EvidenceLevel.DECLARED.value,
    EvidenceLevel.REPORTED.value,
    EvidenceLevel.INSURER_CONFIRMED.value,
    EvidenceLevel.PROVIDER_CONFIRMED.value,
    EvidenceLevel.SETTLED.value,
)
_RANK = {level: rank for rank, level in enumerate(EVIDENCE_ORDER)}

DEFAULT_TOLERANCE_UF = Decimal("0.009")

_LEGACY_PAID = frozenset(
    {InstallmentStatus.PAID.value, InstallmentStatus.PAID_LATE.value, InstallmentStatus.CREDITED.value}
)
_LATE_STATES = frozenset({"promised", "termination_risk", "overdue"})


@dataclass(frozen=True)
class InstallmentState:
    """The derived state of one cuota. ``paid_on`` = the day it became fully paid."""

    state: StateName
    flags: frozenset[str]
    paid_uf: Decimal
    balance_uf: Decimal
    days_late: int
    evidence: str | None
    paid_on: date | None = None
    #: True when the state came from a pre-v12 cached status (no payment rows).
    legacy: bool = False


# --- helpers -----------------------------------------------------------------

def _dec(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _val(value: Any) -> str | None:
    """Enum or plain string -> its string value."""
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _is_active(payment: Any) -> bool:
    return (_val(getattr(payment, "status", None)) or PaymentStatus.ACTIVE.value) == (
        PaymentStatus.ACTIVE.value
    )


def _evidence_of(payment: Any) -> str:
    level = _val(getattr(payment, "evidence_level", None)) or EvidenceLevel.DECLARED.value
    if getattr(payment, "confirmed_by_insurer", False) and _RANK.get(level, 0) < _RANK[
        EvidenceLevel.INSURER_CONFIRMED.value
    ]:
        return EvidenceLevel.INSURER_CONFIRMED.value
    return level


def best_evidence(payments: Iterable[Any]) -> str | None:
    """The strongest evidence level among ACTIVE payments (``None`` when none)."""
    levels = [_evidence_of(p) for p in payments if _is_active(p)]
    if not levels:
        return None
    return max(levels, key=lambda lvl: _RANK.get(lvl, -1))


def termination_date(inst: Any) -> date | None:
    """When cover ended: ``policy.terminated_on``, else a terminated plan's date."""
    return plan_termination_date(getattr(inst, "collection_plan", None))


def plan_termination_date(plan: Any) -> date | None:
    """:func:`termination_date` for a plan (``None`` plan -> ``None``)."""
    policy = getattr(plan, "policy", None) if plan is not None else None
    terminated_on = getattr(policy, "terminated_on", None) if policy is not None else None
    if terminated_on is not None:
        return terminated_on
    if plan is not None and _val(getattr(plan, "status", None)) == CollectionPlanStatus.TERMINATED.value:
        terminated_at = getattr(plan, "terminated_at", None)
        if isinstance(terminated_at, datetime):
            return terminated_at.date()
        if isinstance(terminated_at, date):
            return terminated_at
    return None


def _is_voided(inst: Any) -> bool:
    if getattr(inst, "voided_by_endorsement_id", None) is not None:
        return True
    ended = termination_date(inst)
    due = getattr(inst, "due_date", None)
    return ended is not None and due is not None and due > ended


# --- the pure state function -------------------------------------------------

def compute_installment_state(
    inst: "CollectionInstallment",
    payments: "Iterable[Payment]",
    *,
    today: date,
    cadence_days: list[int],
    tolerance_uf: Decimal = DEFAULT_TOLERANCE_UF,
    review_cutoff: date | None = None,
) -> InstallmentState:
    """Derive the state of one cuota from its payments. Pure — see module doc.

    ``review_cutoff``: the plan's past-installments cutoff while the broker has
    not decided (``past_review.review_cutoffs``) — an open cuota due before it
    reads ``pending_review``.

    ``payments`` is every payment of the cuota (active AND reversed); reversed
    rows are ignored for sums and evidence. ``cadence_days`` drives the
    termination-risk threshold (``max``); empty -> the default cadence.
    """
    payments = list(payments or [])
    tolerance = _dec(tolerance_uf)
    gross = _dec(getattr(inst, "gross_amount_uf", None))
    due: date | None = getattr(inst, "due_date", None)
    cadence = [int(d) for d in (cadence_days or DEFAULT_CADENCE_DAYS)]

    active = sorted(
        (p for p in payments if _is_active(p)),
        key=lambda p: (getattr(p, "paid_on", None) or date.max, getattr(p, "id", None) or 0),
    )
    raw_paid = sum((_dec(getattr(p, "amount_uf", None)) for p in active), Decimal("0"))
    balance = gross - raw_paid
    if balance < 0:
        balance = Decimal("0")
    # Every summary reads the CAPPED figure: a cuota never shows more paid than
    # it costs (review v12 #1 — "Pagado UF 32,60" on a UF 28,80 cuota).
    paid_uf = min(raw_paid, gross) if gross > 0 else raw_paid
    evidence = best_evidence(active)
    cached = _val(getattr(inst, "status", None))

    # Legacy (pre-v12 / legacy PATCH) rows: no payment rows at all.
    legacy_paid = not payments and cached in _LEGACY_PAID
    legacy_cancelled = not payments and cached == InstallmentStatus.CANCELLED.value

    flags: set[str] = set()

    # 1. voided
    if _is_voided(inst) or legacy_cancelled:
        return InstallmentState(
            state="voided",
            flags=frozenset(),
            paid_uf=paid_uf,
            balance_uf=Decimal("0"),
            days_late=0,
            evidence=evidence,
            legacy=legacy_cancelled and not _is_voided(inst),
        )

    # 2. paid
    if legacy_paid:
        paid_on = getattr(inst, "paid_on", None)
        late = max(0, (paid_on - due).days) if (paid_on and due) else int(getattr(inst, "days_late", 0) or 0)
        return InstallmentState(
            state="paid",
            flags=frozenset({"unconfirmed", "no_receipt"}),
            paid_uf=gross,
            balance_uf=Decimal("0"),
            days_late=late,
            evidence=EvidenceLevel.DECLARED.value,
            paid_on=paid_on,
            legacy=True,
        )

    if active and raw_paid >= gross - tolerance:
        # The day the running total first covered the cuota.
        running = Decimal("0")
        paid_on = None
        for payment in active:
            running += _dec(getattr(payment, "amount_uf", None))
            paid_on = getattr(payment, "paid_on", None)
            if running >= gross - tolerance:
                break
        if _RANK.get(evidence or "", -1) < _RANK[EvidenceLevel.INSURER_CONFIRMED.value]:
            flags.add("unconfirmed")
        if not any(getattr(p, "receipt_document_id", None) for p in active):
            flags.add("no_receipt")
        if _debit_rejected(inst, active):
            flags.add("direct_debit_rejected")
        late = max(0, (paid_on - due).days) if (paid_on and due) else 0
        return InstallmentState(
            state="paid",
            flags=frozenset(flags),
            paid_uf=paid_uf,
            balance_uf=Decimal("0"),
            days_late=late,
            evidence=evidence,
            paid_on=paid_on,
        )

    # Not paid: flags that apply to open cuotas.
    if active and balance > tolerance:
        flags.add("partial")
    if _debit_rejected(inst, active):
        flags.add("direct_debit_rejected")

    days_late = max(0, (today - due).days) if due is not None else 0
    promise = getattr(inst, "promise_until", None)

    if review_cutoff is not None and due is not None and due < review_cutoff:
        return InstallmentState(
            state="pending_review",
            flags=frozenset(flags),
            paid_uf=paid_uf,
            balance_uf=balance,
            days_late=0,
            evidence=evidence,
        )

    # A promise only matters once the cuota is late: a promise on a cuota that
    # has not fallen due (or falls due today) leaves it upcoming / due_today —
    # never in the overdue card, bucket or rail chip.
    if promise is not None and promise >= today and due is not None and today > due:
        state: StateName = "promised"
    elif due is not None and (
        today > due + timedelta(days=max(cadence))
        or (today > due and getattr(inst, "insurer_notice_sent_on", None) is not None)
    ):
        state = "termination_risk"
    elif due is not None and today > due:
        state = "overdue"
    elif due is not None and today == due:
        state = "due_today"
    else:
        state = "upcoming"

    return InstallmentState(
        state=state,
        flags=frozenset(flags),
        paid_uf=paid_uf,
        balance_uf=balance,
        days_late=days_late if state in _LATE_STATES else 0,
        evidence=evidence,
    )


def _debit_rejected(inst: Any, active: list[Any]) -> bool:
    rejected = getattr(inst, "direct_debit_rejected_at", None)
    if rejected is None:
        return False
    dates = [getattr(p, "paid_on", None) for p in active if getattr(p, "paid_on", None)]
    return not dates or rejected > max(dates)


# --- the cache ---------------------------------------------------------------

def legacy_status_for(state: InstallmentState) -> InstallmentStatus:
    """Map a derived state onto the legacy ``InstallmentStatus`` cache value."""
    if state.state == "voided":
        return InstallmentStatus.CANCELLED
    if state.state == "paid":
        if state.days_late > 0:
            return InstallmentStatus.PAID_LATE
        return InstallmentStatus.PAID
    if state.state in _LATE_STATES:
        return InstallmentStatus.OVERDUE
    if state.state == "due_today":
        return InstallmentStatus.DUE
    return InstallmentStatus.PENDING


def broker_collection_params(db: "Session", broker_id: int) -> tuple[list[int], Decimal]:
    """``(cadence_days, tolerance_uf)`` of a broker; defaults when no settings row.

    Read-only: it never creates the ``collection_settings`` row (that is the
    settings endpoint's lazy create), so the clock can call it freely.
    """
    row = db.scalar(select(CollectionSettings).where(CollectionSettings.broker_id == broker_id))
    if row is None:
        return list(DEFAULT_CADENCE_DAYS), DEFAULT_TOLERANCE_UF
    cadence = [int(d) for d in (row.cadence_days or DEFAULT_CADENCE_DAYS)]
    tolerance = _dec(row.tolerance_uf) if row.tolerance_uf is not None else DEFAULT_TOLERANCE_UF
    return cadence, tolerance


def installment_review_cutoff(db: "Session", inst: "CollectionInstallment") -> date | None:
    """The cuota's plan review cutoff (``None`` when nothing awaits a decision)."""
    from app.services.collection.past_review import review_cutoffs  # noqa: PLC0415

    plan_id = getattr(inst, "collection_plan_id", None)
    if plan_id is None:
        return None
    return review_cutoffs(db, inst.broker_id, [plan_id]).get(plan_id)


_UNSET: Any = object()


def refresh_installment_cache(
    db: "Session",
    inst: "CollectionInstallment",
    *,
    today: date,
    review_cutoff: Any = _UNSET,
) -> InstallmentState:
    """Recompute one cuota and write the cache columns. The caller commits.

    Writes ``status`` (legacy mapping), ``paid_on`` and ``days_late``. A legacy
    row (no payments, cached paid/cancelled) is left exactly as it is.
    ``review_cutoff`` defaults to the plan's (one query); batch callers pass it.
    """
    cadence, tolerance = broker_collection_params(db, inst.broker_id)
    if review_cutoff is _UNSET:
        review_cutoff = installment_review_cutoff(db, inst)
    state = compute_installment_state(
        inst,
        list(inst.payments),
        today=today,
        cadence_days=cadence,
        tolerance_uf=tolerance,
        review_cutoff=review_cutoff,
    )
    if state.legacy:
        return state
    if not inst.payments and _val(inst.status) in _LEGACY_PAID:
        # A legacy paid row voided by a termination reads ``voided`` while the
        # termination stands, but its cache (the only record of that payment)
        # is never overwritten — a rehabilitation must find it paid again.
        return state
    inst.status = legacy_status_for(state)
    if state.state == "paid":
        inst.paid_on = state.paid_on
        inst.days_late = state.days_late
    else:
        inst.paid_on = None
        inst.days_late = state.days_late if state.state in _LATE_STATES else None
    return state


__all__ = [
    "DEFAULT_TOLERANCE_UF",
    "EVIDENCE_ORDER",
    "InstallmentState",
    "best_evidence",
    "broker_collection_params",
    "compute_installment_state",
    "installment_review_cutoff",
    "legacy_status_for",
    "plan_termination_date",
    "refresh_installment_cache",
    "termination_date",
]
