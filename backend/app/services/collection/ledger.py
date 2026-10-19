"""Payment writes against cuotas (v12, 03 §1) — the ONE place they happen.

Every write here ends with :func:`after_installment_change`: the status cache is
recomputed (:func:`app.services.collection.status.refresh_installment_cache`)
and the cuota's bell alert is closed (paid / voided) or re-evaluated
(:mod:`app.services.collection.alerts`). Payments are never deleted — an error,
a bounced debit or a chargeback is a reversal.

Money (non-negotiable 5: UF everywhere, CLP integers, never float):

* a cuota is denominated in its ``currency`` (``UF`` almost always, ``USD`` for
  dollar policies); ``payment.amount_uf`` holds the payment NORMALISED to that
  currency — the figure the state machine compares with ``gross_amount_uf``;
* ``CLP`` converts with the UF of ``paid_on`` (``services.fx.get_uf_rate``);
  ``USD`` -> UF via the observed dollar and the UF of that day; no rate within
  the look-back window -> :class:`LedgerError` 422 (refuse, never guess);
* tolerance: a CLP/USD payment within ``tolerance_clp`` pesos or
  ``tolerance_pct`` % of the open balance settles the balance exactly (the
  payer used another day's UF); a UF payment is compared by the state machine
  with ``tolerance_uf``.

Functions flush only; the CALLER commits and writes the activity row.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.models.base_class import utcnow
from app.models.document import Document
from app.models.enums import (
    EvidenceLevel,
    InstallmentStatus,
    PaymentCurrency,
    PaymentMode,
    PaymentSource,
    PaymentStatus,
    PolicyCurrency,
    ReversalReason,
)
from app.models.payment import Payment
from app.services import fx
from app.services.collection.alerts import sync_installment_notifications
from app.services.collection.settings import settings_or_none
from app.services.collection.status import (
    EVIDENCE_ORDER,
    InstallmentState,
    broker_collection_params,
    compute_installment_state,
    plan_termination_date,
    refresh_installment_cache,
)

if TYPE_CHECKING:
    from app.models.collection import CollectionInstallment
    from app.models.user import User

UF4 = Decimal("0.0001")
DEFAULT_TOLERANCE_PCT = Decimal("1.000")
DEFAULT_TOLERANCE_CLP = 1000

LEGACY_NOTE = "Registrado desde el plan de pago (edición directa de la cuota)"


class LedgerError(Exception):
    """A payment write the API refuses. ``detail`` is Spanish, shown verbatim."""

    def __init__(self, detail: Any, status_code: int = 422) -> None:
        super().__init__(str(detail))
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class Conversion:
    amount_uf: Decimal          # normalised to the cuota's currency
    uf_rate: Decimal | None     # UF value used (CLP / USD payments)
    usd_rate: Decimal | None    # observed dollar used (USD involved)
    snapped: bool = False       # settled the balance by tolerance


def _dec(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise LedgerError("Monto inválido") from exc


def _val(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def current_state(db: Session, inst: "CollectionInstallment", *, today: date) -> InstallmentState:
    """The live state, ``pending_review`` included (review r2 D1) — balance and
    paid/voided are the same with or without it."""
    from app.services.collection.status import installment_review_cutoff  # noqa: PLC0415

    cadence, tolerance = broker_collection_params(db, inst.broker_id)
    return compute_installment_state(
        inst,
        list(inst.payments),
        today=today,
        cadence_days=cadence,
        tolerance_uf=tolerance,
        review_cutoff=installment_review_cutoff(db, inst),
    )


def _need_rate(rate: Decimal | None, label: str, on: date) -> Decimal:
    if rate is None:
        raise LedgerError(
            f"No hay valor {label} disponible para el {on.strftime('%d-%m-%Y')}: "
            "registra el pago en UF o carga el valor del día."
        )
    return rate


def convert_payment(
    db: Session,
    *,
    amount: Decimal,
    currency: PaymentCurrency,
    paid_on: date,
    target: PolicyCurrency | str = PolicyCurrency.UF,
    balance: Decimal | None = None,
    tolerance_pct: Decimal = DEFAULT_TOLERANCE_PCT,
    tolerance_clp: int = DEFAULT_TOLERANCE_CLP,
) -> Conversion:
    """Normalise a payment into the cuota's currency (see module doc)."""
    amount = _dec(amount)
    currency = PaymentCurrency(_val(currency))
    target = PolicyCurrency(_val(target) or PolicyCurrency.UF.value)
    if currency == PaymentCurrency.CLP and amount != amount.to_integral_value():
        raise LedgerError("Los montos en pesos van sin decimales.")

    uf_rate: Decimal | None = None
    usd_rate: Decimal | None = None
    clp_per_unit: Decimal | None = None  # pesos per unit of the target currency

    if currency.value == target.value:
        normalised = amount
    elif target == PolicyCurrency.UF:
        uf_rate = _need_rate(fx.get_uf_rate(db, paid_on), "de la UF", paid_on)
        clp_per_unit = uf_rate
        if currency == PaymentCurrency.CLP:
            normalised = fx.clp_to_uf(amount, uf_rate)
        else:  # USD -> CLP -> UF
            usd_rate = _need_rate(fx.get_usd_rate(db, paid_on), "del dólar observado", paid_on)
            normalised = (amount * usd_rate / uf_rate).quantize(UF4, rounding=ROUND_HALF_UP)
    else:  # target USD
        usd_rate = _need_rate(fx.get_usd_rate(db, paid_on), "del dólar observado", paid_on)
        clp_per_unit = usd_rate
        if currency == PaymentCurrency.CLP:
            normalised = (amount / usd_rate).quantize(UF4, rounding=ROUND_HALF_UP)
        else:  # UF -> CLP -> USD
            uf_rate = _need_rate(fx.get_uf_rate(db, paid_on), "de la UF", paid_on)
            normalised = (amount * uf_rate / usd_rate).quantize(UF4, rounding=ROUND_HALF_UP)

    snapped = False
    if currency.value != target.value and balance is not None and balance > 0:
        diff = abs(normalised - balance)
        within_pct = diff <= (balance * _dec(tolerance_pct) / Decimal(100))
        within_clp = False
        if currency == PaymentCurrency.CLP and clp_per_unit is not None:
            balance_clp = fx.uf_to_clp(balance, clp_per_unit)
            within_clp = abs(int(amount) - balance_clp) <= int(tolerance_clp)
        if within_pct or within_clp:
            normalised = balance
            snapped = True
    return Conversion(amount_uf=normalised, uf_rate=uf_rate, usd_rate=usd_rate, snapped=snapped)


def over_balance_detail(inst: "CollectionInstallment", balance: Decimal) -> str:
    """``"El monto supera el saldo de la cuota (saldo UF 25,00)"`` (Spanish, verbatim)."""
    from app.services.notices import format_uf  # noqa: PLC0415 - notices imports the ledger's siblings

    unit = "USD" if _val(inst.currency) == PolicyCurrency.USD.value else "UF"
    return f"El monto supera el saldo de la cuota (saldo {unit} {format_uf(balance)})"


def check_not_over_balance(
    db: Session, inst: "CollectionInstallment", amount_uf: Decimal, state: InstallmentState
) -> None:
    """409 when a payment would push the cuota over gross + tolerance (review v12 #1).

    ``amount_uf`` is already normalised to the cuota's currency (and snapped to
    the balance when a CLP/USD figure fell within tolerance — so an exact CLP
    settlement such as ``986.159`` still closes the cuota). A UF payment within
    the broker's ``tolerance_uf`` of the balance is accepted, like the state
    machine's own paid rule.
    """
    _cadence, tolerance = broker_collection_params(db, inst.broker_id)
    if _dec(amount_uf) > state.balance_uf + _dec(tolerance):
        raise LedgerError(over_balance_detail(inst, state.balance_uf), status_code=409)


def upgrade_declared_payments(
    db: Session,
    inst: "CollectionInstallment",
    *,
    user: "User | None" = None,
    now: datetime | None = None,
    exclude_id: int | None = None,
) -> list[Payment]:
    """The insurer reported the cuota PAID: its earlier active ``declared`` payments
    were real — raise them to ``insurer_confirmed`` (review v12 #1). Returns them.
    Flushes; the caller refreshes the cuota and writes the activity row.
    """
    moment = now or utcnow()
    upgraded: list[Payment] = []
    for payment in inst.payments:
        if payment.id is not None and payment.id == exclude_id:
            continue
        if payment.status != PaymentStatus.ACTIVE:
            continue
        if (_val(payment.evidence_level) or EvidenceLevel.DECLARED.value) != EvidenceLevel.DECLARED.value:
            continue
        payment.evidence_level = EvidenceLevel.INSURER_CONFIRMED
        payment.confirmed_by_insurer = True
        payment.confirmed_at = moment
        payment.confirmed_by_user_id = user.id if user is not None else None
        upgraded.append(payment)
    if upgraded:
        db.flush()
    return upgraded


def after_installment_change(
    db: Session,
    inst: "CollectionInstallment",
    *,
    today: date,
    now: datetime | None = None,
    renotify: bool = False,
) -> InstallmentState:
    """Recompute the cache and close / re-evaluate the cuota's bell alert.

    When the cuota ends up PAID, any open provider checkout (Forpay intent item)
    for it is told "paid outside" (03 §6.5, lane B3's
    :func:`~app.services.payments.checkout.mark_paid_outside`) so the payer is
    never charged twice; when it ends up UNPAID, an open link frozen at another
    balance is superseded (:func:`~app.services.payments.checkout.on_balance_change`)
    so a partial payment never leaves the old full amount chargeable. Every write here is non-provider (manual, insurer
    reply, share-link evidence, legacy PATCH); signed webhooks book their own
    payments in ``services.payments.webhooks``. Never raises.
    """
    db.flush()
    state = refresh_installment_cache(db, inst, today=today)
    cadence, _tolerance = broker_collection_params(db, inst.broker_id)
    sync_installment_notifications(
        db, inst, state, today=today, cadence_days=cadence, now=now, renotify=renotify
    )
    if state.state == "paid":
        from app.services.payments.checkout import mark_paid_outside  # noqa: PLC0415 - cycle

        mark_paid_outside(db, inst, now=now)
    else:
        # A partial payment / reversal / re-price: an open link frozen at the
        # old balance would overcharge (or undercharge) — supersede it.
        from app.services.payments.checkout import on_balance_change  # noqa: PLC0415 - cycle

        on_balance_change(db, inst, now=now)
    db.flush()
    return state


def sync_plan_installments(db: Session, plan: Any, *, today: date, now: datetime | None = None) -> None:
    """After a plan/cuota write (created, replaced, read from a policy): refresh
    every cuota's cache and bell alert NOW — a plan loaded with past due dates
    raises its overdue alerts at once, not at the next clock run (review v12 #5)."""
    db.flush()
    db.expire(plan, ["installments"])
    for inst in sorted(plan.installments or [], key=lambda r: (r.number or 0, r.id or 0)):
        after_installment_change(db, inst, today=today, now=now)


def _check_receipt(db: Session, broker_id: int, document_id: int | None) -> None:
    if document_id is None:
        return
    doc = db.get(Document, document_id)
    if doc is None or doc.broker_id != broker_id:
        raise LedgerError("Documento no encontrado", status_code=404)


def register_payment(
    db: Session,
    inst: "CollectionInstallment",
    *,
    amount: Decimal | str | int,
    currency: PaymentCurrency | str,
    paid_on: date,
    today: date,
    user: "User | None" = None,
    method: PaymentMode | str | None = None,
    source: PaymentSource = PaymentSource.MANUAL,
    evidence: EvidenceLevel | None = None,
    confirmed_by_insurer: bool = False,
    receipt_document_id: int | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> tuple[Payment, InstallmentState]:
    """Register one (partial) payment and recompute the cuota.

    ``evidence`` defaults to ``declared`` (``insurer_confirmed`` when the broker
    ticks *Confirmado por la compañía*). Refuses (422) a voided or already-paid
    cuota, a non-positive amount, a future date and a missing FX rate, and
    (409) a payment above the open balance + tolerance.
    """
    amount = _dec(amount)
    if amount <= 0:
        raise LedgerError("El monto del pago debe ser mayor que cero.")
    if paid_on > today:
        raise LedgerError("La fecha de pago no puede ser futura.")
    _check_receipt(db, inst.broker_id, receipt_document_id)

    state = current_state(db, inst, today=today)
    if state.state == "voided":
        raise LedgerError("La cuota está anulada: no admite pagos.")
    if state.state == "paid":
        raise LedgerError(
            "La cuota ya figura pagada. Si el pago anterior es un error, reviértelo primero."
        )

    settings_row = settings_or_none(db, inst.broker_id)
    conversion = convert_payment(
        db,
        amount=amount,
        currency=PaymentCurrency(_val(currency)),
        paid_on=paid_on,
        target=inst.currency,
        balance=state.balance_uf,
        tolerance_pct=(
            settings_row.tolerance_pct if settings_row is not None else DEFAULT_TOLERANCE_PCT
        ),
        tolerance_clp=(
            settings_row.tolerance_clp if settings_row is not None else DEFAULT_TOLERANCE_CLP
        ),
    )
    check_not_over_balance(db, inst, conversion.amount_uf, state)
    moment = now or utcnow()
    level = evidence or (
        EvidenceLevel.INSURER_CONFIRMED if confirmed_by_insurer else EvidenceLevel.DECLARED
    )
    payment = Payment(
        broker_id=inst.broker_id,
        amount=amount,
        currency=PaymentCurrency(_val(currency)),
        uf_rate=conversion.uf_rate,
        amount_uf=conversion.amount_uf,
        paid_on=paid_on,
        method=PaymentMode(_val(method)) if method else inst.collection_plan.payment_mode,
        source=source,
        evidence_level=level,
        status=PaymentStatus.ACTIVE,
        confirmed_by_insurer=bool(confirmed_by_insurer),
        confirmed_at=moment if confirmed_by_insurer else None,
        confirmed_by_user_id=(user.id if (user is not None and confirmed_by_insurer) else None),
        receipt_document_id=receipt_document_id,
        registered_by_user_id=user.id if user is not None else None,
        note=note,
    )
    inst.payments.append(payment)
    db.flush()
    new_state = after_installment_change(db, inst, today=today, now=moment)
    return payment, new_state


def reverse_payment(
    db: Session,
    payment: Payment,
    *,
    reason: ReversalReason | str,
    note: str | None,
    today: date,
    user: "User | None" = None,
    now: datetime | None = None,
) -> InstallmentState:
    """Reverse (never delete) a payment; the cuota reopens and alerts re-raise."""
    if payment.status == PaymentStatus.REVERSED:
        raise LedgerError("El pago ya fue revertido.", status_code=409)
    moment = now or utcnow()
    payment.status = PaymentStatus.REVERSED
    payment.reversal_reason = ReversalReason(_val(reason))
    payment.reversal_note = note
    payment.reversed_at = moment
    payment.reversed_by_user_id = user.id if user is not None else None
    inst = payment.installment
    return after_installment_change(db, inst, today=today, now=moment, renotify=True)


def confirm_payment(
    db: Session,
    payment: Payment,
    *,
    today: date,
    user: "User | None" = None,
    now: datetime | None = None,
) -> InstallmentState:
    """The insurer confirmed it: evidence rises to at least ``insurer_confirmed``."""
    if payment.status == PaymentStatus.REVERSED:
        raise LedgerError("No se puede confirmar un pago revertido.")
    moment = now or utcnow()
    rank = {level: i for i, level in enumerate(EVIDENCE_ORDER)}
    current = _val(payment.evidence_level) or EvidenceLevel.DECLARED.value
    if rank.get(current, 0) < rank[EvidenceLevel.INSURER_CONFIRMED.value]:
        payment.evidence_level = EvidenceLevel.INSURER_CONFIRMED
    payment.confirmed_by_insurer = True
    payment.confirmed_at = moment
    payment.confirmed_by_user_id = user.id if user is not None else None
    return after_installment_change(db, payment.installment, today=today, now=moment)


def reconcile_plan_installments(
    db: Session,
    plan: Any,
    *,
    previous_end: date | None,
    today: date,
    now: datetime | None = None,
) -> int:
    """Re-derive every cuota of a plan after its termination changed.

    Call it whenever ``plan.status`` / ``terminated_at`` / ``rehabilitated_at``
    (or ``policy.terminated_on``) changes, passing ``previous_end`` = the
    :func:`~app.services.collection.status.plan_termination_date` read BEFORE
    the change. The status cache of a cuota voided by a termination is
    ``CANCELLED``; with no payment rows that cache is self-sustaining (the
    legacy rule reads it as voided), so a rehabilitation (Art. 528) would leave
    the cuotas voided forever. Here a cuota is reopened (``PENDING``, then
    re-derived) when ALL hold:

    * cached ``CANCELLED`` and no payment rows (with payments, payments decide);
    * not annulled by an endorsement (``voided_by_endorsement_id`` is NULL);
    * the OLD termination voided it (``due > previous_end``) and the CURRENT
      one does not — so a cuota cancelled for any other reason stays cancelled.

    Then every cuota goes through :func:`after_installment_change`: a new
    termination voids its cuotas and closes their alerts at once; a reopened
    cuota re-raises what is owed (``renotify=True``, like a reversal — the
    termination closed its alert); the rest keep ``renotify=False``. Flushes only.
    Returns how many cuotas were reopened.
    """
    current_end = plan_termination_date(plan)
    reopened = 0
    for inst in list(plan.installments or []):
        due = inst.due_date
        if (
            previous_end is not None
            and inst.status == InstallmentStatus.CANCELLED
            and not inst.payments
            and inst.voided_by_endorsement_id is None
            and due is not None
            and due > previous_end
            and not (current_end is not None and due > current_end)
        ):
            inst.status = InstallmentStatus.PENDING
            inst.paid_on = None
            inst.days_late = None
            reopened += 1
            # Back in cobranza: its alert re-lights even if the termination closed it.
            after_installment_change(db, inst, today=today, now=now, renotify=True)
            continue
        after_installment_change(db, inst, today=today, now=now)
    return reopened


def legacy_mark_paid(
    db: Session,
    inst: "CollectionInstallment",
    *,
    paid_on: date | None,
    today: date,
    user: "User | None" = None,
    now: datetime | None = None,
) -> list[Payment]:
    """The legacy ``PATCH .../installments/{n}`` ``paid_on`` write, as payments.

    * ``paid_on`` set, cuota open -> one ``payment(manual, declared)`` for the
      open balance on that date;
    * ``paid_on`` set, cuota already paid on ANOTHER date (or a pre-v12 cached
      paid row) -> the active payments are reversed (``error``) and one payment
      for the full cuota is registered on the new date; same date -> no-op;
    * ``paid_on = null`` -> every active payment is reversed (``error``); a
      pre-v12 cached paid row is reopened.

    Returns the payments created. Does NOT refresh the cache — the caller runs
    :func:`after_installment_change` once every legacy field is applied.
    """
    moment = now or utcnow()
    state = current_state(db, inst, today=today)
    active = [p for p in inst.payments if p.status == PaymentStatus.ACTIVE]

    def _reverse_all(reason_note: str) -> None:
        for payment in active:
            payment.status = PaymentStatus.REVERSED
            payment.reversal_reason = ReversalReason.ERROR
            payment.reversal_note = reason_note
            payment.reversed_at = moment
            payment.reversed_by_user_id = user.id if user is not None else None

    if paid_on is None:
        _reverse_all("Pago anulado desde el plan de pago (edición directa de la cuota)")
        if state.legacy and not inst.payments:
            inst.status = InstallmentStatus.PENDING
            inst.paid_on = None
            inst.days_late = None
        return []

    if state.state == "paid" and not state.legacy and state.paid_on == paid_on:
        return []

    gross = Decimal(str(inst.gross_amount_uf))
    if state.state == "paid" or state.legacy:
        _reverse_all("Fecha de pago corregida desde el plan de pago (edición directa)")
        amount = gross
    else:
        amount = gross - state.paid_uf
    if amount <= 0:
        return []
    currency = (
        PaymentCurrency.USD
        if _val(inst.currency) == PolicyCurrency.USD.value
        else PaymentCurrency.UF
    )
    payment = Payment(
        broker_id=inst.broker_id,
        amount=amount,
        currency=currency,
        amount_uf=amount,
        paid_on=paid_on,
        method=inst.collection_plan.payment_mode,
        source=PaymentSource.MANUAL,
        evidence_level=EvidenceLevel.DECLARED,
        status=PaymentStatus.ACTIVE,
        registered_by_user_id=user.id if user is not None else None,
        note=LEGACY_NOTE,
    )
    inst.payments.append(payment)
    db.flush()
    return [payment]


__all__ = [
    "Conversion",
    "LEGACY_NOTE",
    "LedgerError",
    "after_installment_change",
    "check_not_over_balance",
    "confirm_payment",
    "convert_payment",
    "current_state",
    "legacy_mark_paid",
    "reconcile_plan_installments",
    "register_payment",
    "reverse_payment",
    "sync_plan_installments",
    "upgrade_declared_payments",
]
