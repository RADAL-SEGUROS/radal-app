"""``/collections`` — payment plans, their cuotas, payments and the Cobranza board.

The invariant every plan in the corpus obeys::

    Σ installment.gross_amount_uf == policy gross premium
                                     + Σ endorsement.total_premium_delta_uf

is checked with the existing ``MONEY_TOLERANCE`` on every write that touches the
ledger. A mismatch is a **422** carrying the expected/received/difference triple
— never a silent correction, because in the demo files the difference is exactly
where a missing endorsement hides.

v12 (docs/v12/02-backend-api.md §4, 03 §1/§8): payments are the truth of a cuota
(``payment`` rows, never deleted — reversed), ``collection_installment.status``
is the cache :mod:`app.services.collection.status` writes. Every payment write
goes through :mod:`app.services.collection.ledger`, which refreshes the cache
and closes / re-raises the cuota's bell alert. The legacy
``PATCH /{plan}/installments/{n}`` ``paid_on`` write is converted into a
``payment(source=manual, evidence=declared)`` so the two never diverge.

Route order matters: every literal path (``/summary``, ``/installments``,
``/payments``, ``/settings``, ``/templates``, ``/plans``) is registered BEFORE
the legacy ``/{plan_id}`` routes, and those use the ``:int`` path convertor so a
literal path of another router mounted under ``/collections`` (lane B4's
``/collections/evidence``, ``/collections/notices``) is never captured by them.

Tenant scope: every lookup filters ``broker_id``; another tenant's plan, cuota
or payment is a 404 by every path.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.api.routers.clients import record_activity
from app.api.routers.policies import get_policy_or_404
from app.models.activity import Note
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.enums import (
    CollectionLevelPreference,
    CollectionPlanStatus,
    EntityType,
    InstallmentStatus,
    MailSender,
    NotificationKind,
    PaymentMode,
    PaymentStatus,
)
from app.models.mail import MailAccount
from app.models.payment import Payment
from app.models.user import User
from app.schemas.collection import (
    CollectionAlert,
    CollectionPlanCreate,
    CollectionPlanPage,
    CollectionPlanRead,
    CollectionPlanUpdate,
    CollectionStatusRead,
    InstallmentCreate,
    InstallmentRead,
    InstallmentReplace,
    InstallmentUpdate,
    check_installment_total,
)
from app.schemas.collection_board import (
    Bucket,
    CollectionSettingsRead,
    CollectionSettingsUpdate,
    CollectionsSummary,
    DirectDebitRejected,
    InstallmentResult,
    InstallmentRow,
    InstallmentRowPage,
    InstallmentStateRead,
    LastNote,
    Level,
    ModeRead,
    NoticeTemplateRead,
    NoticeTemplatesApprove,
    NoticeTemplatesRead,
    NoticeTemplatesUpdate,
    PaymentCreate,
    PaymentRead,
    PaymentResult,
    PaymentReverse,
    PlanStart,
    PromiseCreate,
)
from app.services import fx
from app.services.analytics import (
    ScopeFilters,
    build_entity_summary,
    group_by_query,
    scope_filters,
    scope_predicates,
)
from app.services.collection import board
from app.services.collection import clock as collection_clock
from app.services.collection import ledger
from app.services.collection import templates as notice_templates
from app.services.collection.alerts import close_installment_alerts
from app.services.collection.resolver import resolve_collection_mode
from app.services.collection.settings import get_or_create_settings
from app.services.collection.status import InstallmentState, plan_termination_date
from app.services.collection.totals import endorsement_delta_total  # noqa: F401 - re-exported
from app.services.notifications import close_for_origin

router = APIRouter(prefix="/collections", tags=["collections"])

#: Instalments that no longer owe anything.
SETTLED_STATUSES = (
    InstallmentStatus.PAID,
    InstallmentStatus.PAID_LATE,
    InstallmentStatus.CREDITED,
    InstallmentStatus.CANCELLED,
)
PAID_STATUSES = (InstallmentStatus.PAID, InstallmentStatus.PAID_LATE)

#: Plan columns that are NOT NULL: an explicit ``null`` in a PATCH is ignored.
_NOT_NULL_PLAN_FIELDS = frozenset({"payment_mode", "status", "level_preference", "notices_enabled"})


def direct_debit_kind(mode) -> str | None:  # noqa: ANN001
    """``pac`` for a bank-account debit, ``pat`` for a card debit, else ``None``."""
    value = getattr(mode, "value", mode)
    if value == PaymentMode.DIRECT_DEBIT.value:
        return "pac"
    if value == PaymentMode.CARD_DEBIT.value:
        return "pat"
    return None


def _d(value) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal("0")


def _today() -> date:
    """Santiago's business date (module attribute: tests pin it)."""
    return collection_clock.business_today()


def _ledger_http(exc: ledger.LedgerError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


# --- Helpers -------------------------------------------------------------------

def get_plan_or_404(db: Session, plan_id: int, broker_id: int) -> CollectionPlan:
    plan = db.scalars(
        select(CollectionPlan)
        .where(CollectionPlan.id == plan_id, CollectionPlan.broker_id == broker_id)
        .options(selectinload(CollectionPlan.installments))
    ).first()
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection plan not found"
        )
    return plan


def get_installment_or_404(
    db: Session, installment_id: int, broker_id: int
) -> CollectionInstallment:
    inst = db.scalars(
        select(CollectionInstallment)
        .join(CollectionPlan, CollectionPlan.id == CollectionInstallment.collection_plan_id)
        .where(
            CollectionInstallment.id == installment_id,
            CollectionInstallment.broker_id == broker_id,
            CollectionPlan.broker_id == broker_id,
        )
        .options(selectinload(CollectionInstallment.payments))
    ).first()
    if inst is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cuota no encontrada")
    return inst


def get_payment_or_404(db: Session, payment_id: int, broker_id: int) -> Payment:
    payment = db.scalars(
        select(Payment)
        .join(CollectionInstallment, CollectionInstallment.id == Payment.installment_id)
        .where(
            Payment.id == payment_id,
            Payment.broker_id == broker_id,
            CollectionInstallment.broker_id == broker_id,
        )
    ).first()
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pago no encontrado")
    return payment


def _validate_total(
    db: Session, *, broker_id: int, policy_id: int, installments: list
) -> None:
    """422 unless Σ instalments == policy gross + Σ endorsement deltas."""
    policy = get_policy_or_404(db, policy_id, broker_id)
    total = sum((_d(getattr(row, "gross_amount_uf", None)) for row in installments), Decimal("0"))
    error = check_installment_total(
        installments_total=total,
        policy_gross_uf=policy.total_premium_uf,
        endorsement_delta_uf=endorsement_delta_total(
            db, broker_id=broker_id, policy_id=policy_id
        ),
    )
    if error is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=[error]
        )


def _read(plan: CollectionPlan) -> CollectionPlanRead:
    payload = CollectionPlanRead.model_validate(plan)
    payload.installments = [
        InstallmentRead.model_validate(row)
        for row in sorted(plan.installments, key=lambda r: r.number)
    ]
    return payload


def _rows_from(payload: list[InstallmentCreate], broker_id: int) -> list[CollectionInstallment]:
    seen: set[int] = set()
    rows: list[CollectionInstallment] = []
    for item in payload:
        if item.number in seen:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Duplicate instalment number {item.number}",
            )
        seen.add(item.number)
        rows.append(
            CollectionInstallment(broker_id=broker_id, **item.model_dump(exclude_unset=False))
        )
    return rows


def _state_read(state: InstallmentState) -> InstallmentStateRead:
    return InstallmentStateRead(
        state=state.state,
        flags=sorted(state.flags),
        paid_uf=state.paid_uf,
        balance_uf=state.balance_uf,
        days_late=state.days_late,
        evidence=state.evidence,
        paid_on=state.paid_on,
    )


#: Plan fields whose change re-derives every cuota (termination / rehabilitation).
_TERMINATION_FIELDS = frozenset({"status", "terminated_at", "rehabilitated_at"})


def _apply_plan_update(plan: CollectionPlan, payload: CollectionPlanUpdate) -> list[str]:
    changes = payload.model_dump(exclude_unset=True)
    applied: list[str] = []
    for field, value in changes.items():
        if value is None and field in _NOT_NULL_PLAN_FIELDS:
            continue
        setattr(plan, field, value)
        applied.append(field)
    if "notices_paused_until" in changes and changes["notices_paused_until"] is None:
        plan.notices_pause_reason = changes.get("notices_pause_reason")
    return sorted(applied)


# =============================================================================
# The Cobranza board (static paths — registered before ``/{plan_id}``)
# =============================================================================

@router.get("/summary", response_model=CollectionsSummary)
def get_collections_summary(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
    scope: ScopeFilters = Depends(scope_filters),
    group_by: str | None = group_by_query(),
) -> CollectionsSummary:
    """The cobranza board: the v12 cards + the legacy status split of the plans.

    Cards (``due_7d``, ``due_30d``, ``overdue {count, uf}``,
    ``termination_risk``, ``collected_month_uf``, ``unconfirmed_payments``,
    ``pending_evidence``, ``plans_missing``) are broker-wide and computed from
    the LIVE cuota state. The legacy part (``entity``, ``total``,
    ``by_status``, ``total_uf``, ``grouped``) keeps honouring the Portafolio
    scope: ``account_group_id`` resolves through the plan's own case file OR its
    policy, the date window sits on ``created_at``, ``group_by`` accepts
    ``status | payment_mode | account_group | month``.
    """
    scoped = [
        CollectionPlan.broker_id == broker_id,
        *scope_predicates("collections", scope, broker_id),
    ]
    base = build_entity_summary(
        db, "collections", broker_id=broker_id, scoped=scoped, group_by=group_by
    )
    cards = board.summary_cards(db, broker_id, today=_today())
    return CollectionsSummary(**base.model_dump(), **cards)


@router.get("/installments", response_model=InstallmentRowPage)
def list_board_installments(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "View")),
    bucket: Bucket | None = Query(default=None),
    insurer_id: int | None = Query(default=None, gt=0),
    line_code: str | None = Query(default=None, max_length=8),
    method: PaymentMode | None = Query(default=None),
    level: Level | None = Query(default=None),
    owner: str | None = Query(default=None, pattern="^me$"),
    q: str | None = Query(default=None, max_length=120),
    policy_id: int | None = Query(default=None, gt=0),
    plan_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> InstallmentRowPage:
    """THE Cobranza list: every cuota with its live state, level and last gestión.

    ``bucket``: ``upcoming`` (open, due within 30 days incl. today), ``overdue``
    (vencida / riesgo de término / en acuerdo), ``current`` (open, plan with
    nothing overdue), ``settled`` (pagada / anulada). ``counts`` gives the size
    of every bucket over the other filters (subtab badges).
    """
    today = _today()
    rows = board.load_rows(
        db,
        broker_id,
        today=today,
        insurer_id=insurer_id,
        line_code=line_code,
        method=method.value if method else None,
        owner_user_id=current_user.id if owner == "me" else None,
        q=q,
        policy_id=policy_id,
        plan_id=plan_id,
    )
    modes = board.ModeCache(db, broker_id)
    if level is not None:
        rows = [row for row in rows if modes.get(row).level == level]
    tags = board.buckets_for(rows, today=today)
    counts = {name: sum(1 for row in rows if name in tags[row.inst.id]) for name in board.BUCKETS}
    if bucket is not None:
        rows = [row for row in rows if bucket in tags[row.inst.id]]
    rows = board.sort_rows(rows, bucket)
    total = len(rows)
    page = rows[offset : offset + limit]

    uf_rate = fx.get_uf_rate(db, today)
    notes = board.last_notes(db, broker_id, [row.inst.id for row in page])
    items: list[InstallmentRow] = []
    for row in page:
        mode = modes.get(row)
        inst, plan, policy, state = row.inst, row.plan, row.policy, row.state
        client = policy.client
        insured = client.insured if client is not None else None
        insurer = policy.insurer
        line = board.line_of(policy)
        note = notes.get(inst.id)
        is_uf = getattr(inst.currency, "value", inst.currency) == "UF"
        items.append(
            InstallmentRow(
                id=inst.id,
                plan_id=plan.id,
                policy_id=policy.id,
                case_file_id=plan.case_file_id or policy.case_file_id,
                number=inst.number,
                installment_count=plan.installment_count or len(plan.installments),
                coupon_number=inst.coupon_number,
                due_date=inst.due_date,
                currency=inst.currency,
                gross_amount_uf=inst.gross_amount_uf,
                paid_uf=state.paid_uf,
                balance_uf=state.balance_uf,
                balance_clp=(
                    fx.uf_to_clp(state.balance_uf, uf_rate)
                    if (uf_rate is not None and is_uf)
                    else None
                ),
                state=state.state,
                flags=sorted(state.flags),
                buckets=sorted(tags[inst.id]),
                days_late=state.days_late,
                evidence=state.evidence,
                paid_on=state.paid_on,
                status=inst.status,
                level=mode.level,
                level_reason=mode.reason,
                level_missing=list(mode.missing),
                channels=list(mode.channels),
                promise_until=inst.promise_until,
                direct_debit_rejected_at=inst.direct_debit_rejected_at,
                last_note=(
                    LastNote(
                        id=note.id,
                        body=note.body,
                        created_at=note.created_at,
                        follow_up_on=note.follow_up_on,
                        author_id=note.author_id,
                    )
                    if note is not None
                    else None
                ),
                policy_number=policy.policy_number,
                insured_name=getattr(insured, "legal_name", None),
                insured_rut=getattr(insured, "rut", None),
                insurer_id=policy.insurer_id,
                insurer_name=(insurer.trade_name or insurer.legal_name) if insurer else None,
                line_code=getattr(line, "code", None),
                line_name=getattr(line, "name", None),
                payment_mode=plan.payment_mode,
                direct_debit_kind=direct_debit_kind(plan.payment_mode),
                notices_enabled=bool(plan.notices_enabled),
            )
        )
    return InstallmentRowPage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        as_of=today,
        uf_rate=uf_rate,
        counts=counts,
    )


@router.get("/installments/{installment_id}/mode", response_model=ModeRead)
def get_installment_mode(
    installment_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> ModeRead:
    """Which collection level applies to this cuota now, why, and what is missing."""
    inst = get_installment_or_404(db, installment_id, broker_id)
    mode = resolve_collection_mode(db, inst, broker_id=broker_id)
    return ModeRead(
        level=mode.level, reason=mode.reason, channels=list(mode.channels), missing=list(mode.missing)
    )


@router.get("/installments/{installment_id}/payments", response_model=list[PaymentRead])
def list_installment_payments(
    installment_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> list[PaymentRead]:
    """Every payment of the cuota (active AND reversed), oldest first."""
    inst = get_installment_or_404(db, installment_id, broker_id)
    return [PaymentRead.model_validate(p) for p in inst.payments]


@router.post(
    "/installments/{installment_id}/payments",
    response_model=PaymentResult,
    status_code=status.HTTP_201_CREATED,
)
def register_installment_payment(
    installment_id: int,
    payload: PaymentCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> PaymentResult:
    """Register a (partial) payment: ``source=manual``, evidence ``declared`` —
    or ``insurer_confirmed`` when the broker ticks *Confirmado por la compañía*.

    CLP converts with the UF of ``paid_on`` (422 when no rate is on file), USD
    via the observed dollar; a payment within the broker's tolerance of the open
    balance settles it.
    """
    inst = get_installment_or_404(db, installment_id, broker_id)
    try:
        payment, state = ledger.register_payment(
            db,
            inst,
            amount=payload.amount,
            currency=payload.currency,
            paid_on=payload.paid_on,
            today=_today(),
            user=current_user,
            method=payload.method,
            confirmed_by_insurer=payload.confirmed_by_insurer,
            receipt_document_id=payload.receipt_document_id,
            note=payload.note,
        )
    except ledger.LedgerError as exc:
        db.rollback()
        raise _ledger_http(exc) from exc
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="payment.registered",
        entity_type=EntityType.COLLECTION_PLAN,
        entity_id=inst.collection_plan_id,
        description=(
            f"Pago cuota {inst.number}: {payment.amount} {payment.currency.value}"
            f" ({payment.amount_uf} {inst.currency.value})"
        ),
        meta={
            "installment_id": inst.id,
            "payment_id": payment.id,
            "evidence": payment.evidence_level.value,
            "state": state.state,
        },
    )
    db.commit()
    db.refresh(payment)
    db.refresh(inst)
    return PaymentResult(
        payment=PaymentRead.model_validate(payment),
        installment_id=inst.id,
        status=inst.status,
        state=_state_read(state),
    )


@router.post("/payments/{payment_id}/reverse", response_model=PaymentResult)
def reverse_installment_payment(
    payment_id: int,
    payload: PaymentReverse,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> PaymentResult:
    """Reverse a payment (never delete). The cuota reopens and its alert re-raises."""
    payment = get_payment_or_404(db, payment_id, broker_id)
    try:
        state = ledger.reverse_payment(
            db,
            payment,
            reason=payload.reason,
            note=payload.note,
            today=_today(),
            user=current_user,
        )
    except ledger.LedgerError as exc:
        db.rollback()
        raise _ledger_http(exc) from exc
    inst = payment.installment
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="payment.reversed",
        entity_type=EntityType.COLLECTION_PLAN,
        entity_id=inst.collection_plan_id,
        description=f"Pago revertido en cuota {inst.number} ({payload.reason.value})",
        meta={"installment_id": inst.id, "payment_id": payment.id, "reason": payload.reason.value},
    )
    db.commit()
    db.refresh(payment)
    db.refresh(inst)
    return PaymentResult(
        payment=PaymentRead.model_validate(payment),
        installment_id=inst.id,
        status=inst.status,
        state=_state_read(state),
    )


@router.post("/payments/{payment_id}/confirm", response_model=PaymentResult)
def confirm_installment_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> PaymentResult:
    """The insurer confirmed the payment: evidence rises to ``insurer_confirmed``."""
    payment = get_payment_or_404(db, payment_id, broker_id)
    try:
        state = ledger.confirm_payment(db, payment, today=_today(), user=current_user)
    except ledger.LedgerError as exc:
        db.rollback()
        raise _ledger_http(exc) from exc
    inst = payment.installment
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="payment.confirmed",
        entity_type=EntityType.COLLECTION_PLAN,
        entity_id=inst.collection_plan_id,
        description=f"Pago de la cuota {inst.number} confirmado por la compañía",
        meta={"installment_id": inst.id, "payment_id": payment.id},
    )
    db.commit()
    db.refresh(payment)
    db.refresh(inst)
    return PaymentResult(
        payment=PaymentRead.model_validate(payment),
        installment_id=inst.id,
        status=inst.status,
        state=_state_read(state),
    )


@router.post(
    "/installments/{installment_id}/direct-debit-rejected", response_model=InstallmentResult
)
def mark_direct_debit_rejected(
    installment_id: int,
    payload: DirectDebitRejected,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> InstallmentResult:
    """PAC / card debit bounced on ``rejected_on`` (flag + todo alert)."""
    inst = get_installment_or_404(db, installment_id, broker_id)
    today = _today()
    if payload.rejected_on > today:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La fecha del rechazo no puede ser futura.",
        )
    if ledger.current_state(db, inst, today=today).state == "voided":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La cuota está anulada.",
        )
    inst.direct_debit_rejected_at = payload.rejected_on
    state = ledger.after_installment_change(db, inst, today=today, renotify=True)
    note_id = None
    if payload.note:
        note = Note(
            broker_id=broker_id,
            entity_type=EntityType.INSTALLMENT,
            entity_id=inst.id,
            author_id=current_user.id,
            body=payload.note,
            is_internal=True,
            phase="cobranza",
        )
        db.add(note)
        db.flush()
        note_id = note.id
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="installment.direct_debit_rejected",
        entity_type=EntityType.COLLECTION_PLAN,
        entity_id=inst.collection_plan_id,
        description=f"Cuota {inst.number}: cargo automático rechazado el {payload.rejected_on.strftime('%d-%m-%Y')}",
        meta={"installment_id": inst.id, "rejected_on": payload.rejected_on.isoformat()},
    )
    db.commit()
    db.refresh(inst)
    return InstallmentResult(
        installment_id=inst.id,
        status=inst.status,
        state=_state_read(state),
        promise_until=inst.promise_until,
        direct_debit_rejected_at=inst.direct_debit_rejected_at,
        note_id=note_id,
    )


@router.post("/installments/{installment_id}/promise", response_model=InstallmentResult)
def register_payment_promise(
    installment_id: int,
    payload: PromiseCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> InstallmentResult:
    """*Promete pagar el*: a gestión note with ``follow_up_on`` + ``promise_until``.

    On a cuota already late it reads ``promised`` until that date and its
    open alert is closed (the broker managed it); if the date passes unpaid
    the clock raises ``promise_broken``. On a cuota not yet due the promise is
    recorded but the cuota stays upcoming / due_today — never "vencida".
    """
    inst = get_installment_or_404(db, installment_id, broker_id)
    today = _today()
    if payload.promise_until < today:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La fecha prometida no puede ser anterior a hoy.",
        )
    current = ledger.current_state(db, inst, today=today).state
    if current in ("paid", "voided"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "La cuota ya figura pagada." if current == "paid" else "La cuota está anulada."
            ),
        )
    inst.promise_until = payload.promise_until
    note = Note(
        broker_id=broker_id,
        entity_type=EntityType.INSTALLMENT,
        entity_id=inst.id,
        author_id=current_user.id,
        body=payload.note or f"Promete pagar el {payload.promise_until.strftime('%d-%m-%Y')}",
        is_internal=True,
        phase="cobranza",
        follow_up_on=payload.promise_until,
    )
    db.add(note)
    state = ledger.after_installment_change(db, inst, today=today)
    close_installment_alerts(db, inst)
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="installment.promise",
        entity_type=EntityType.COLLECTION_PLAN,
        entity_id=inst.collection_plan_id,
        description=(
            f"Cuota {inst.number}: promete pagar el {payload.promise_until.strftime('%d-%m-%Y')}"
        ),
        meta={
            "installment_id": inst.id,
            "promise_until": payload.promise_until.isoformat(),
            "note_id": note.id,
        },
    )
    db.commit()
    db.refresh(inst)
    return InstallmentResult(
        installment_id=inst.id,
        status=inst.status,
        state=_state_read(state),
        promise_until=inst.promise_until,
        direct_debit_rejected_at=inst.direct_debit_rejected_at,
        note_id=note.id,
    )


# =============================================================================
# Settings & notice templates
# =============================================================================

@router.get("/settings", response_model=CollectionSettingsRead)
def get_collection_settings(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> CollectionSettingsRead:
    """The broker's cobranza settings (created with the defaults on first read)."""
    row = get_or_create_settings(db, broker_id)
    db.commit()
    db.refresh(row)
    return CollectionSettingsRead.model_validate(row)


@router.put("/settings", response_model=CollectionSettingsRead)
def update_collection_settings(
    payload: CollectionSettingsUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Settings", "Manage")),
) -> CollectionSettingsRead:
    """Cadence, tolerances, master switch, sender, native/inquiry preferences."""
    row = get_or_create_settings(db, broker_id)
    changes = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    for field in list(changes):
        if changes[field] is None and field != "sender_mail_account_id":
            changes.pop(field)

    if changes.get("sender_mail_account_id") is not None:
        account = db.get(MailAccount, changes["sender_mail_account_id"])
        if account is None or account.broker_id != broker_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Cuenta de correo no encontrada"
            )
    sender = changes.get("default_sender", row.default_sender)
    sender_account = changes.get("sender_mail_account_id", row.sender_mail_account_id)
    if sender == MailSender.BROKER_ACCOUNT and sender_account is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Elige la cuenta de correo desde la que se envían los avisos.",
        )
    if changes.get("notices_enabled") is True and row.notices_approved_at is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Aprueba las plantillas de aviso antes de activar los recordatorios.",
        )
    for field, value in changes.items():
        setattr(row, field, value)
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="collection_settings.updated",
        entity_type=EntityType.BROKER,
        entity_id=broker_id,
        description=f"Configuración de cobranza actualizada ({', '.join(sorted(changes))})",
        meta={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(row)
    return CollectionSettingsRead.model_validate(row)


def _templates_read(db: Session, broker_id: int) -> NoticeTemplatesRead:
    settings_row = get_or_create_settings(db, broker_id)
    effective = notice_templates.effective_templates(db, broker_id)
    approver_ids = {t.approved_by_user_id for t in effective if t.approved_by_user_id}
    names = (
        dict(
            db.execute(
                select(User.id, User.full_name).where(
                    User.id.in_(approver_ids), User.broker_id == broker_id
                )
            ).all()
        )
        if approver_ids
        else {}
    )
    items = []
    for t in effective:
        approved = notice_templates.is_approved(t)
        items.append(
            NoticeTemplateRead(
                **t.as_dict(),
                is_approved=approved,
                approved_by_name=names.get(t.approved_by_user_id) if approved else None,
            )
        )
    active = [i for i in items if i.is_active]
    approved_count = sum(1 for i in items if i.is_approved)
    return NoticeTemplatesRead(
        items=items,
        variables=notice_templates.template_variables(),
        approved=bool(active) and all(i.is_approved for i in active),
        approved_at=settings_row.notices_approved_at,
        approved_by_user_id=settings_row.notices_approved_by_user_id,
        approved_count=approved_count,
        pending_count=sum(1 for i in active if not i.is_approved),
    )


@router.get("/templates", response_model=NoticeTemplatesRead)
def get_notice_templates(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> NoticeTemplatesRead:
    """Effective templates: the broker's own rows, else Radal's Spanish defaults."""
    result = _templates_read(db, broker_id)
    db.commit()
    return result


@router.put("/templates", response_model=NoticeTemplatesRead)
def update_notice_templates(
    payload: NoticeTemplatesUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Settings", "Manage")),
) -> NoticeTemplatesRead:
    """Override templates by slot. A content change withdraws THAT template's approval."""
    try:
        written, changed = notice_templates.upsert_templates(
            db, broker_id, [item.model_dump() for item in payload.templates]
        )
    except notice_templates.TemplateError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="notice_templates.updated",
        entity_type=EntityType.BROKER,
        entity_id=broker_id,
        description=(
            f"Plantillas de aviso editadas ({written})"
            + (" — las editadas requieren nueva aprobación" if changed else "")
        ),
        meta={"written": written, "approval_cleared": changed},
    )
    db.commit()
    return _templates_read(db, broker_id)


@router.post("/templates/approve", response_model=NoticeTemplatesRead)
def approve_notice_templates(
    payload: NoticeTemplatesApprove | None = None,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Settings", "Manage")),
) -> NoticeTemplatesRead:
    """Approve templates — ``{items: [{kind, cadence_level, channel?}]}`` for
    specific ones, no body / ``items`` omitted for every current template.
    Defaults are frozen into the broker's rows."""
    items = payload.items if payload is not None else None
    slots = (
        [(item.kind, item.cadence_level, item.channel) for item in items]
        if items is not None
        else None
    )
    try:
        notice_templates.approve_templates(
            db, broker_id, user_id=current_user.id, slots=slots
        )
    except notice_templates.TemplateError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="notice_templates.approved",
        entity_type=EntityType.BROKER,
        entity_id=broker_id,
        description=(
            "Plantillas de aviso aprobadas"
            if slots is None
            else f"Plantillas de aviso aprobadas ({len(slots)})"
        ),
        meta={"slots": [[k.value, c, ch.value] for k, c, ch in slots]} if slots else None,
    )
    db.commit()
    return _templates_read(db, broker_id)


# =============================================================================
# Plans
# =============================================================================

@router.patch("/plans/{plan_id}", response_model=CollectionPlanRead)
def patch_collection_plan(
    plan_id: int,
    payload: CollectionPlanUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> CollectionPlanRead:
    """Edit a plan (incl. ``notices_enabled``, ``level_preference``, pause)."""
    return _update_plan(db, plan_id, payload, broker_id, current_user)


@router.post("/plans/{plan_id}/start", response_model=CollectionPlanRead)
def start_collection(
    plan_id: int,
    payload: PlanStart | None = None,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> CollectionPlanRead:
    """*Iniciar cobranza*: payer reminders ON for this plan — any insurer.

    A ``record`` plan moves to ``auto`` (reminders make no sense on a
    record-only plan); asking for ``record`` here is a 422. Clears any pause.
    """
    plan = get_plan_or_404(db, plan_id, broker_id)
    wanted = payload.level_preference if payload is not None else None
    if wanted == CollectionLevelPreference.RECORD:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Con cobro solo de registro no se envían recordatorios: "
                "elige cobro automático o asistido."
            ),
        )
    if wanted is not None:
        plan.level_preference = wanted
    elif plan.level_preference == CollectionLevelPreference.RECORD:
        plan.level_preference = CollectionLevelPreference.AUTO
    plan.notices_enabled = True
    plan.notices_paused_until = None
    plan.notices_pause_reason = None
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="collection_plan.started",
        entity_type=EntityType.COLLECTION_PLAN,
        entity_id=plan.id,
        description="Cobranza iniciada (recordatorios al pagador activados)",
        meta={"level_preference": plan.level_preference.value},
    )
    db.commit()
    db.refresh(plan)
    return _read(plan)


@router.get("", response_model=CollectionPlanPage)
def list_collection_plans(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
    policy_id: int | None = Query(default=None, gt=0),
    scope: ScopeFilters = Depends(scope_filters),
    status_filter: list[CollectionPlanStatus] | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> CollectionPlanPage:
    """List the broker's planes de pago.

    Scope params: ``account_group_id`` (the grupo — via the plan's case file or
    its policy), ``case_file_id`` (the grupo-cuenta) and ``date_from`` /
    ``date_to`` on ``created_at``.
    """
    stmt = select(CollectionPlan).where(
        CollectionPlan.broker_id == broker_id,
        *scope_predicates("collections", scope, broker_id),
    )
    if policy_id is not None:
        stmt = stmt.where(CollectionPlan.policy_id == policy_id)
    if status_filter:
        stmt = stmt.where(CollectionPlan.status.in_(status_filter))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(CollectionPlan.id.desc())
        .options(selectinload(CollectionPlan.installments))
        .offset(offset)
        .limit(limit)
    ).all()
    return CollectionPlanPage(
        items=[_read(row) for row in rows], total=int(total), limit=limit, offset=offset
    )


@router.post("", response_model=CollectionPlanRead, status_code=status.HTTP_201_CREATED)
def create_collection_plan(
    payload: CollectionPlanCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Create")),
) -> CollectionPlanRead:
    """Register the plan de pago. The ledger's Σ is validated before it is written."""
    policy = get_policy_or_404(db, payload.policy_id, broker_id)

    data = payload.model_dump(exclude_unset=False)
    data.pop("installments", None)
    start_now = bool(data.pop("start_collection", False))
    rows = _rows_from(payload.installments, broker_id)
    if rows:
        _validate_total(db, broker_id=broker_id, policy_id=policy.id, installments=rows)

    plan = CollectionPlan(broker_id=broker_id, **data)
    plan.installments = rows
    if plan.installment_count is None and rows:
        plan.installment_count = len(rows)
    if plan.total_premium_uf is None and rows:
        plan.total_premium_uf = sum(
            (_d(row.gross_amount_uf) for row in rows), Decimal("0")
        )
    db.add(plan)
    db.flush()
    # The policy has a plan now: its ``plan_missing`` alert is resolved.
    close_for_origin(
        db,
        broker_id=broker_id,
        origin_type="policy",
        origin_id=policy.id,
        kinds=[NotificationKind.PLAN_MISSING],
    )

    if start_now:
        if plan.level_preference == CollectionLevelPreference.RECORD:
            plan.level_preference = CollectionLevelPreference.AUTO
        plan.notices_enabled = True
        plan.notices_paused_until = None
        plan.notices_pause_reason = None
    today = _today()
    warnings = _past_due_warnings(rows, today=today)
    # A cuota born overdue raises its bell alert NOW, not at the next clock run
    # (review v12 #5d); the status cache is derived the same way.
    _sync_plan_cuotas(db, plan, today=today)

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="collection_plan.created",
        entity_type=EntityType.COLLECTION_PLAN,
        entity_id=plan.id,
        description=f"Plan de pago {plan.plan_number or plan.id} · {len(rows)} cuota(s)",
        meta={"policy_id": policy.id, "installments": len(rows)},
    )
    if start_now:
        record_activity(
            db,
            broker_id=broker_id,
            user=current_user,
            action="collection_plan.started",
            entity_type=EntityType.COLLECTION_PLAN,
            entity_id=plan.id,
            description="Cobranza iniciada (recordatorios al pagador activados)",
            meta={"level_preference": plan.level_preference.value, "on_create": True},
        )
    db.commit()
    db.refresh(plan)
    result = _read(plan)
    result.warnings = warnings
    return result


def _past_due_warnings(rows: list[CollectionInstallment], *, today: date) -> list[str]:
    """Spanish heads-up when cuotas are born overdue (review v12 #15)."""
    past = sorted(
        (row for row in rows if row.due_date is not None and row.due_date < today),
        key=lambda r: (r.due_date, r.number),
    )
    if not past:
        return []
    first = past[0]
    if len(past) == 1:
        return [
            f"La cuota {first.number} vence el {first.due_date.strftime('%d-%m-%Y')}, "
            "antes de hoy: nace vencida."
        ]
    return [
        f"{len(past)} cuotas tienen vencimiento anterior a hoy (la primera, la cuota "
        f"{first.number}, el {first.due_date.strftime('%d-%m-%Y')}): nacen vencidas."
    ]


def _sync_plan_cuotas(db: Session, plan: CollectionPlan, *, today: date) -> None:
    """Refresh every cuota's cache and bell alert right after a plan write."""
    ledger.sync_plan_installments(db, plan, today=today)


# =============================================================================
# Legacy plan routes (``:int`` convertor — never capture a literal path)
# =============================================================================

# 02 §4 names the plan create ``POST /collections/plans``; the legacy door is
# ``POST /collections``. Same handler, same gate (Collections.Create) and shape.
router.add_api_route(
    "/plans",
    create_collection_plan,
    methods=["POST"],
    response_model=CollectionPlanRead,
    status_code=status.HTTP_201_CREATED,
)


@router.get("/{plan_id:int}", response_model=CollectionPlanRead)
def get_collection_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> CollectionPlanRead:
    return _read(get_plan_or_404(db, plan_id, broker_id))


def _update_plan(
    db: Session,
    plan_id: int,
    payload: CollectionPlanUpdate,
    broker_id: int,
    current_user: User,
) -> CollectionPlanRead:
    plan = get_plan_or_404(db, plan_id, broker_id)
    previous_end = plan_termination_date(plan)
    applied = _apply_plan_update(plan, payload)
    reopened = 0
    if _TERMINATION_FIELDS.intersection(applied):
        # A termination voids its cuotas (and closes their alerts) now; a
        # rehabilitation reopens the cuotas the old termination voided.
        reopened = ledger.reconcile_plan_installments(
            db, plan, previous_end=previous_end, today=_today()
        )
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="collection_plan.updated",
        entity_type=EntityType.COLLECTION_PLAN,
        entity_id=plan.id,
        description=f"Plan de pago actualizado ({', '.join(applied)})",
        meta={"fields": applied, **({"reopened_installments": reopened} if reopened else {})},
    )
    db.commit()
    db.refresh(plan)
    return _read(plan)


@router.patch("/{plan_id:int}", response_model=CollectionPlanRead)
def update_collection_plan(
    plan_id: int,
    payload: CollectionPlanUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> CollectionPlanRead:
    return _update_plan(db, plan_id, payload, broker_id, current_user)


# --- Instalments -----------------------------------------------------------------

@router.get("/{plan_id:int}/installments", response_model=list[InstallmentRead])
def list_installments(
    plan_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> list[InstallmentRead]:
    plan = get_plan_or_404(db, plan_id, broker_id)
    return [
        InstallmentRead.model_validate(row)
        for row in sorted(plan.installments, key=lambda r: r.number)
    ]


@router.put("/{plan_id:int}/installments", response_model=CollectionPlanRead)
def replace_installments(
    plan_id: int,
    payload: InstallmentReplace,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> CollectionPlanRead:
    """Full replace of the ledger. The Σ is validated BEFORE anything is written.

    Refused (422) once any cuota carries a payment: payments are never deleted,
    and replacing the cuotas would cascade them away.
    """
    plan = get_plan_or_404(db, plan_id, broker_id)
    rows = _rows_from(payload.installments, broker_id)
    _validate_total(db, broker_id=broker_id, policy_id=plan.policy_id, installments=rows)
    with_payments = db.scalar(
        select(func.count(Payment.id))
        .join(CollectionInstallment, CollectionInstallment.id == Payment.installment_id)
        .where(
            CollectionInstallment.collection_plan_id == plan.id,
            Payment.broker_id == broker_id,
        )
    )
    if with_payments:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "El plan tiene pagos registrados: no se pueden reemplazar sus cuotas. "
                "Edita cada cuota o revierte los pagos primero."
            ),
        )

    plan.installments.clear()
    db.flush()
    plan.installments = rows
    plan.installment_count = len(rows)
    plan.total_premium_uf = sum((_d(row.gross_amount_uf) for row in rows), Decimal("0"))
    today = _today()
    warnings = _past_due_warnings(rows, today=today)
    _sync_plan_cuotas(db, plan, today=today)

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="collection_plan.installments_replaced",
        entity_type=EntityType.COLLECTION_PLAN,
        entity_id=plan.id,
        description=f"Cuotas del plan reemplazadas ({len(rows)} cuotas)",
        meta={"installments": len(rows)},
    )
    db.commit()
    db.refresh(plan)
    result = _read(plan)
    result.warnings = warnings
    return result


@router.patch("/{plan_id:int}/installments/{number:int}", response_model=InstallmentRead)
def update_installment(
    plan_id: int,
    number: int,
    payload: InstallmentUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> InstallmentRead:
    """Edit one cuota (legacy door). A ``paid_on`` write becomes a payment.

    ``paid_on = <date>`` registers a ``payment(source=manual, evidence=declared)``
    for the open balance on that date (a paid cuota moved to another date has its
    payments reversed and re-registered); ``paid_on = null`` reverses the active
    payments. The cache (``status``/``paid_on``/``days_late``) is then
    recomputed from the payments, so this door and ``POST
    /collections/installments/{id}/payments`` never diverge. An explicit
    ``status`` that contradicts the payments is a 422.
    """
    plan = get_plan_or_404(db, plan_id, broker_id)
    row = next((item for item in plan.installments if item.number == number), None)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Instalment {number} not found"
        )

    changes = payload.model_dump(exclude_unset=True)
    if "gross_amount_uf" in changes and changes["gross_amount_uf"] is not None:
        projected = [
            item for item in plan.installments if item.number != number
        ]

        class _Shim:  # noqa: D401 - a value carrier for the Σ check
            gross_amount_uf = changes["gross_amount_uf"]

        _validate_total(
            db,
            broker_id=broker_id,
            policy_id=plan.policy_id,
            installments=[*projected, _Shim()],
        )

    paid_on_given = "paid_on" in changes
    new_paid_on = changes.pop("paid_on", None)
    explicit_status = changes.pop("status", None)
    explicit_days_late = changes.pop("days_late", None) if "days_late" in changes else None
    for field, value in changes.items():
        setattr(row, field, value)

    today = _today()
    if paid_on_given:
        ledger.legacy_mark_paid(db, row, paid_on=new_paid_on, today=today, user=current_user)
    state = ledger.after_installment_change(db, row, today=today, renotify=paid_on_given)

    if paid_on_given and explicit_status in PAID_STATUSES:
        # The legacy ledger sends ``status: paid|paid_late`` (+ ``days_late``)
        # along with ``paid_on``: the payments decide both, so they are ignored.
        explicit_status = None
        explicit_days_late = None
    if explicit_status is not None and explicit_status != row.status:
        has_active = any(p.status == PaymentStatus.ACTIVE for p in row.payments)
        if has_active or paid_on_given:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "El estado de la cuota se deriva de sus pagos: registra o revierte "
                    "el pago en lugar de cambiar el estado."
                ),
            )
        row.status = explicit_status  # legacy direct write (no payment rows)
    if explicit_days_late is not None:
        row.days_late = explicit_days_late

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="collection_installment.updated",
        entity_type=EntityType.COLLECTION_PLAN,
        entity_id=plan.id,
        description=f"Cuota {number}: {row.status.value}",
        meta={
            "number": number,
            "fields": sorted(
                [*changes, *(["paid_on"] if paid_on_given else []),
                 *(["status"] if explicit_status is not None else [])]
            ),
            "state": state.state,
        },
    )
    db.commit()
    db.refresh(row)
    return InstallmentRead.model_validate(row)


# --- The derived dashboard --------------------------------------------------------

@router.get("/{plan_id:int}/status", response_model=CollectionStatusRead)
def get_collection_status(
    plan_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> CollectionStatusRead:
    """Outstanding, overdue, compliance % and the alerts the tracker raises."""
    plan = get_plan_or_404(db, plan_id, broker_id)
    policy = get_policy_or_404(db, plan.policy_id, broker_id)
    today = plan.as_of_date or date.today()

    rows = sorted(plan.installments, key=lambda r: r.number)
    scheduled = sum((_d(row.gross_amount_uf) for row in rows), Decimal("0"))
    # The LIVE per-cuota state (payments are the truth), capped at each cuota's
    # gross: the plan page, the cuota panel and the board KPIs read the same
    # figures (review v12 #1 — "Pagado UF 57,6" vs a panel saying 32,60).
    states = {row.id: ledger.current_state(db, row, today=today) for row in rows}
    paid = sum(
        (
            states[row.id].paid_uf
            for row in rows
            if states[row.id].state != "voided"
        ),
        Decimal("0"),
    )
    outstanding = sum(
        (
            states[row.id].balance_uf
            for row in rows
            if states[row.id].state not in ("paid", "voided")
        ),
        Decimal("0"),
    )
    overdue_rows = [
        row
        for row in rows
        if states[row.id].state not in ("paid", "voided")
        and row.due_date is not None
        and row.due_date < today
    ]
    overdue = sum((states[row.id].balance_uf for row in overdue_rows), Decimal("0"))
    paid_count = sum(1 for row in rows if states[row.id].state == "paid")
    compliance = (
        (Decimal(paid_count) * 100 / Decimal(len(rows))).quantize(Decimal("0.01"))
        if rows
        else Decimal("0")
    )
    next_due = next(
        (
            row.due_date
            for row in rows
            if states[row.id].state not in ("paid", "voided") and row.due_date is not None
        ),
        None,
    )

    expected = None
    balances = True
    if policy.total_premium_uf is not None:
        expected = _d(policy.total_premium_uf) + endorsement_delta_total(
            db, broker_id=broker_id, policy_id=policy.id
        )
        balances = (
            check_installment_total(
                installments_total=scheduled,
                policy_gross_uf=policy.total_premium_uf,
                endorsement_delta_uf=expected - _d(policy.total_premium_uf),
            )
            is None
        )

    alerts: list[CollectionAlert] = []
    if overdue_rows:
        alerts.append(
            CollectionAlert(
                code="installments_overdue",
                severity="high",
                detail=f"{len(overdue_rows)} cuota(s) vencida(s) por {overdue} UF",
            )
        )
    if plan.status in (CollectionPlanStatus.SUSPENDED, CollectionPlanStatus.TERMINATED):
        alerts.append(
            CollectionAlert(
                code=f"plan_{plan.status.value}",
                severity="high",
                detail="La cobertura está afectada por el estado del plan (Art. 528)",
            )
        )
    if plan.days_without_cover:
        alerts.append(
            CollectionAlert(
                code="days_without_cover",
                severity="high",
                detail=f"{plan.days_without_cover} día(s) sin cobertura",
            )
        )
    if not balances:
        alerts.append(
            CollectionAlert(
                code="ledger_out_of_balance",
                severity="medium",
                detail=(
                    "La suma de las cuotas no coincide con la prima de la póliza más "
                    "los endosos emitidos"
                ),
            )
        )

    return CollectionStatusRead(
        collection_plan_id=plan.id,
        policy_id=policy.id,
        status=plan.status,
        as_of_date=plan.as_of_date,
        installment_count=len(rows),
        total_scheduled_uf=scheduled,
        paid_uf=paid,
        outstanding_uf=outstanding,
        overdue_uf=overdue,
        overdue_count=len(overdue_rows),
        paid_count=paid_count,
        compliance_pct=compliance,
        next_due_date=next_due,
        expected_total_uf=expected,
        balances=balances,
        alerts=alerts,
    )


__all__ = [
    "router",
    "get_installment_or_404",
    "get_payment_or_404",
    "get_plan_or_404",
    "endorsement_delta_total",
]
