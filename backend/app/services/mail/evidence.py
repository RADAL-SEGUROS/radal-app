"""Payment evidence — the "Respuestas por confirmar" inbox (v12, 03 §7, 04 §4).

A ``payment_evidence`` row is a SUGGESTION that a cuota's status changed
(an insurer reply mail, an insurer statement, an insurer share-link report, a
payer's uploaded receipt). Nothing is committed to the ledger until a human
confirms (rule 6: suggest -> confirm -> commit):

* :func:`confirm_evidence` — for ``paid`` / ``partial`` (or ``unknown`` with an
  explicit amount) writes ONE ``payment`` through B2's
  :func:`app.services.collection.ledger.register_payment` (FX, tolerance snap,
  cache refresh, cuota alert sync) — ``source`` mapped from the evidence
  source, ``evidence_level = reported`` (a mail or an unauthenticated link is
  not the insurer's signature; ``insurer_confirmed`` only from an UPLOADED
  insurer statement — never one read from a mail — or when the broker ticks
  *Confirmado por la compañía*) — links ``payment.evidence_id`` <->
  ``evidence.payment_id`` and closes the matching bell rows. ``unpaid`` /
  ``cancelled`` confirm the information without touching money.
* :func:`reject_evidence` — closes it.

Money: the ledger normalises the payment to the cuota's currency (UF, or USD
for dollar policies) with ``currency_rate`` (:mod:`app.services.fx`) and snaps
a CLP/USD figure within tolerance to the open balance; a missing rate is a 422
— refuse, never guess. Flushes only; the CALLER commits.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.base_class import utcnow
from app.models.collection import CollectionInstallment
from app.models.enums import (
    EvidenceLevel,
    EvidenceSource,
    EvidenceStatus,
    InquiryStatus,
    PaymentCurrency,
    PaymentSource,
    PolicyCurrency,
    ReportedStatus,
)
from app.models.mail import PaymentEvidence, StatusInquiry
from app.models.payment import Payment
from app.services.collection import ledger
from app.services.notices import business_today, format_clp, format_uf, installment_state
from app.services.notifications import close_for_origin

OPEN_EVIDENCE = (EvidenceStatus.SUGGESTED, EvidenceStatus.NEEDS_REVIEW)

ORIGIN_EVIDENCE = "payment_evidence"
ORIGIN_INQUIRY = "status_inquiry"
ORIGIN_INSTALLMENT = "installment"  # B2's cuota alerts (services/collection/alerts.py)

SOURCE_MAP = {
    EvidenceSource.INSURER_REPLY.value: PaymentSource.INSURER_REPLY,
    EvidenceSource.INSURER_SHARE_LINK.value: PaymentSource.INSURER_SHARE_LINK,
    EvidenceSource.INSURER_STATEMENT.value: PaymentSource.INSURER_STATEMENT,
    EvidenceSource.PAYER_UPLOAD.value: PaymentSource.PAYER_UPLOAD,
}

REASON_DECIDED = "Esta respuesta ya fue {status}"
REASON_NO_INSTALLMENT = "Indica a qué cuota corresponde esta respuesta"
REASON_WRONG_POLICY = "La cuota elegida no pertenece a la póliza de la consulta"
REASON_NEEDS_AMOUNT = "Indica el monto pagado para registrar un pago parcial"
REASON_SETTLED = "La cuota ya figura pagada o anulada: revierte el pago anterior si corresponde"
REASON_FUTURE = "La fecha de pago no puede ser futura"
REASON_AMOUNT = "El monto del pago debe ser mayor que cero"

_DECIDED_LABELS = {
    EvidenceStatus.CONFIRMED.value: "confirmada",
    EvidenceStatus.REJECTED.value: "rechazada",
    EvidenceStatus.SUPERSEDED.value: "reemplazada por otra",
}


class EvidenceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _val(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise EvidenceError(422, "Monto inválido") from exc


def normalise_amount(
    db: Session,
    *,
    amount: Decimal,
    currency: PaymentCurrency,
    target: PolicyCurrency | str,
    paid_on: date,
) -> tuple[Decimal, Decimal | None]:
    """``(amount in the cuota's currency, uf_rate used)`` — B2's ledger
    conversion without the tolerance snap. Missing rate -> 422."""
    try:
        conversion = ledger.convert_payment(
            db, amount=amount, currency=currency, paid_on=paid_on, target=target
        )
    except ledger.LedgerError as exc:
        raise EvidenceError(exc.status_code, exc.detail) from None
    return conversion.amount_uf, conversion.uf_rate


def _installment_for(
    db: Session, evidence: PaymentEvidence, installment_id: int | None
) -> CollectionInstallment:
    target_id = installment_id or evidence.installment_id
    if target_id is None:
        raise EvidenceError(422, REASON_NO_INSTALLMENT)
    inst = db.get(CollectionInstallment, target_id)
    if inst is None or inst.broker_id != evidence.broker_id:
        raise EvidenceError(404, "Cuota no encontrada")
    inquiry = evidence.status_inquiry
    if inquiry is not None and inst.collection_plan.policy_id != inquiry.policy_id:
        raise EvidenceError(422, REASON_WRONG_POLICY)
    if evidence.installment_id is not None and installment_id is not None:
        current = db.get(CollectionInstallment, evidence.installment_id)
        if current is not None and current.collection_plan.policy_id != inst.collection_plan.policy_id:
            raise EvidenceError(422, REASON_WRONG_POLICY)
    return inst


def _close_after(db: Session, evidence: PaymentEvidence, *, now: datetime) -> None:
    close_for_origin(
        db, broker_id=evidence.broker_id, origin_type=ORIGIN_EVIDENCE, origin_id=evidence.id, now=now
    )
    if evidence.mail_message_id is not None:
        still_open_for_mail = db.scalar(
            select(PaymentEvidence.id).where(
                PaymentEvidence.broker_id == evidence.broker_id,
                PaymentEvidence.mail_message_id == evidence.mail_message_id,
                PaymentEvidence.status.in_(OPEN_EVIDENCE),
                PaymentEvidence.id != evidence.id,
            )
        )
        if still_open_for_mail is None:
            close_for_origin(
                db,
                broker_id=evidence.broker_id,
                origin_type="mail_message",
                origin_id=evidence.mail_message_id,
                now=now,
            )
    inquiry = evidence.status_inquiry
    if inquiry is None:
        return
    remaining = db.scalar(
        select(PaymentEvidence.id).where(
            PaymentEvidence.broker_id == evidence.broker_id,
            PaymentEvidence.status_inquiry_id == inquiry.id,
            PaymentEvidence.status.in_(OPEN_EVIDENCE),
            PaymentEvidence.id != evidence.id,
        )
    )
    if remaining is None:
        close_for_origin(
            db, broker_id=evidence.broker_id, origin_type=ORIGIN_INQUIRY, origin_id=inquiry.id, now=now
        )
        if _val(inquiry.status) == InquiryStatus.REPLIED.value:
            inquiry.status = InquiryStatus.RESOLVED
            inquiry.resolved_at = now


def _guard_open(evidence: PaymentEvidence) -> None:
    status_v = _val(evidence.status)
    if status_v not in {s.value for s in OPEN_EVIDENCE}:
        raise EvidenceError(409, REASON_DECIDED.format(status=_DECIDED_LABELS.get(status_v, status_v)))


#: Evidence that comes FROM the insurer (a payer's receipt is not the insurer's word).
INSURER_SOURCES = frozenset(
    {
        EvidenceSource.INSURER_REPLY.value,
        EvidenceSource.INSURER_SHARE_LINK.value,
        EvidenceSource.INSURER_STATEMENT.value,
    }
)

REASON_TARGET_PAID = "La cuota elegida ya figura pagada o anulada: elige otra cuota"


def _cuota_unit(inst: CollectionInstallment) -> PaymentCurrency:
    return PaymentCurrency(_val(inst.currency) or "UF")


def suggested_amount_uf(
    db: Session, evidence: PaymentEvidence, inst: CollectionInstallment, *, today: date
) -> Decimal | None:
    """What the confirm form pre-fills, in the cuota's currency (review v12 #1).

    The open BALANCE when the report says paid / unknown and the reported figure
    covers it (or there is none); else the reported figure normalised to the
    cuota's currency — never above the balance. ``None`` for a settled cuota or
    a partial report without a figure.
    """
    state = installment_state(db, inst, today=today)
    if state.state in ("paid", "voided"):
        return None
    balance = state.balance_uf
    reported = _val(evidence.reported_status) or ReportedStatus.UNKNOWN.value
    figure: Decimal | None = None
    if evidence.reported_amount is not None:
        cur = PaymentCurrency(
            _val(evidence.reported_currency) or _val(inst.currency) or "UF"
        )
        try:
            figure, _rate = normalise_amount(
                db,
                amount=_dec(evidence.reported_amount),
                currency=cur,
                target=inst.currency,
                paid_on=evidence.reported_paid_on or today,
            )
        except EvidenceError:
            figure = None
    if reported in (ReportedStatus.PAID.value, ReportedStatus.UNKNOWN.value):
        if figure is None or figure >= balance:
            return balance
        return figure
    if reported == ReportedStatus.PARTIAL.value and figure is not None:
        return min(figure, balance)
    return None


def confirm_evidence(
    db: Session,
    evidence: PaymentEvidence,
    *,
    user: Any,
    installment_id: int | None = None,
    amount: Decimal | str | int | None = None,
    currency: PaymentCurrency | str | None = None,
    paid_on: date | None = None,
    confirmed_by_insurer: bool = False,
    note: str | None = None,
    now: datetime | None = None,
) -> Payment | None:
    """Confirm a suggestion; returns the payment it produced (or ``None``).

    Money rules (review v12 #1 — a reply "Pagada UF 28,80" on a cuota with a
    UF 3,80 *Declarado* payment booked UF 32,60):

    * the payment never pushes the cuota over gross + tolerance: without an
      explicit ``amount``, a ``paid`` report books ONLY the open balance and a
      ``partial`` one its figure capped at the balance; an explicit ``amount``
      above the balance is a 409 "El monto supera el saldo de la cuota (saldo
      UF X)" (the ledger enforces it for every door);
    * when the INSURER reports the cuota paid, the cuota's earlier active
      ``declared`` payments are raised to ``insurer_confirmed`` (they were
      real) — also when the cuota already reads paid off those payments, in
      which case no new payment is written;
    * reassigning to another cuota (*Otra cuota*, ``installment_id``) applies
      the same rules to THAT cuota; a fully paid / voided target is a 409.
    """
    _guard_open(evidence)
    moment = now or utcnow()
    today = business_today(moment)
    reported = _val(evidence.reported_status) or ReportedStatus.UNKNOWN.value
    creates_money = reported in (ReportedStatus.PAID.value, ReportedStatus.PARTIAL.value) or (
        reported == ReportedStatus.UNKNOWN.value and amount is not None
    )
    source_v = _val(evidence.source) or EvidenceSource.INSURER_REPLY.value
    from_insurer = source_v in INSURER_SOURCES
    reassigned = installment_id is not None and installment_id != evidence.installment_id
    payment: Payment | None = None
    inst: CollectionInstallment | None = None
    upgraded: list[Payment] = []

    if creates_money:
        inst = _installment_for(db, evidence, installment_id)
        state = installment_state(db, inst, today=today)
        already_paid_by_declared = (
            state.state == "paid"
            and not state.legacy
            and not reassigned
            and amount is None
            and reported == ReportedStatus.PAID.value
            and from_insurer
        )
        if already_paid_by_declared:
            # The insurer confirms what the broker had declared: no new money.
            upgraded = ledger.upgrade_declared_payments(db, inst, user=user, now=moment)
            if upgraded:
                ledger.after_installment_change(db, inst, today=today, now=moment)
        elif state.state in ("paid", "voided"):
            raise EvidenceError(409, REASON_TARGET_PAID if reassigned else REASON_SETTLED)
        else:
            cur = PaymentCurrency(
                _val(currency) or _val(evidence.reported_currency) or _val(inst.currency) or "UF"
            )
            day = paid_on or evidence.reported_paid_on or today
            if day > today:
                raise EvidenceError(422, REASON_FUTURE)
            booking_note = note
            if amount is not None:
                value = _dec(amount)
            elif (
                reported == ReportedStatus.PAID.value
                and (from_insurer or evidence.reported_amount is None)
            ):
                # "Pagada": the open balance, in the cuota's currency — never the
                # reported gross (the cuota may carry earlier partial payments).
                value = state.balance_uf
                cur = _cuota_unit(inst)
                if evidence.reported_amount is not None and booking_note is None:
                    booking_note = (
                        "La compañía informó la cuota pagada: se registra el saldo pendiente"
                    )
            elif evidence.reported_amount is not None:
                figure, _rate = normalise_amount(
                    db,
                    amount=_dec(evidence.reported_amount),
                    currency=PaymentCurrency(
                        _val(evidence.reported_currency) or _val(inst.currency) or "UF"
                    ),
                    target=inst.currency,
                    paid_on=day,
                )
                if figure >= state.balance_uf:
                    value = state.balance_uf
                    cur = _cuota_unit(inst)
                    if booking_note is None:
                        unit = _val(evidence.reported_currency) or _val(inst.currency) or "UF"
                        shown = (
                            f"${format_clp(int(_dec(evidence.reported_amount)))}"
                            if unit == "CLP"
                            else f"{unit} {format_uf(evidence.reported_amount)}"
                        )
                        booking_note = (
                            f"Monto informado: {shown}; se registra solo el saldo pendiente de la cuota"
                        )
                else:
                    value = _dec(evidence.reported_amount)
            else:
                raise EvidenceError(422, REASON_NEEDS_AMOUNT)
            if value <= 0:
                raise EvidenceError(422, REASON_AMOUNT)
            # Only the broker's explicit "Confirmado por la compañía", or an UPLOADED
            # insurer statement (not one READ from a mail — a From can be forged),
            # is insurer_confirmed; everything else read from mail is "reported".
            insurer_backed = bool(confirmed_by_insurer) or (
                source_v == EvidenceSource.INSURER_STATEMENT.value and evidence.mail_message_id is None
            )
            # B2's ledger is the ONE place payments are written: FX + tolerance
            # snap, the over-balance 409, cache refresh and the cuota's alert sync.
            try:
                payment, new_state = ledger.register_payment(
                    db,
                    inst,
                    amount=value,
                    currency=cur,
                    paid_on=day,
                    today=today,
                    user=user,
                    source=SOURCE_MAP.get(source_v, PaymentSource.INSURER_REPLY),
                    evidence=(
                        EvidenceLevel.INSURER_CONFIRMED if insurer_backed else EvidenceLevel.REPORTED
                    ),
                    confirmed_by_insurer=bool(confirmed_by_insurer),
                    receipt_document_id=(
                        evidence.document_id if source_v == EvidenceSource.PAYER_UPLOAD.value else None
                    ),
                    note=booking_note,
                    now=moment,
                )
            except ledger.LedgerError as exc:
                raise EvidenceError(exc.status_code, exc.detail) from None
            payment.evidence_id = evidence.id
            evidence.payment_id = payment.id
            db.flush()
            if (
                from_insurer
                and reported == ReportedStatus.PAID.value
                and new_state.state == "paid"
            ):
                upgraded = ledger.upgrade_declared_payments(
                    db, inst, user=user, now=moment, exclude_id=payment.id
                )
                if upgraded:
                    new_state = ledger.after_installment_change(db, inst, today=today, now=moment)
            # Other open suggestions about the same cuota are now moot.
            if new_state.state == "paid":
                for other in db.scalars(
                    select(PaymentEvidence).where(
                        PaymentEvidence.broker_id == evidence.broker_id,
                        PaymentEvidence.installment_id == inst.id,
                        PaymentEvidence.id != evidence.id,
                        PaymentEvidence.status.in_(OPEN_EVIDENCE),
                    )
                ):
                    other.status = EvidenceStatus.SUPERSEDED
                    other.decided_at = moment
                    other.decided_by_user_id = getattr(user, "id", None)
                    close_for_origin(
                        db,
                        broker_id=other.broker_id,
                        origin_type=ORIGIN_EVIDENCE,
                        origin_id=other.id,
                        now=moment,
                    )
        evidence.installment_id = inst.id
        db.flush()
    elif installment_id is not None:
        inst = _installment_for(db, evidence, installment_id)
        evidence.installment_id = inst.id

    evidence.status = EvidenceStatus.CONFIRMED
    evidence.decided_at = moment
    evidence.decided_by_user_id = getattr(user, "id", None)
    if note:
        evidence.note = note
    db.flush()
    _close_after(db, evidence, now=moment)
    db.flush()
    # Read by the router for the activity row (never persisted).
    evidence._upgraded_payment_ids = [p.id for p in upgraded]  # type: ignore[attr-defined]
    return payment


def reject_evidence(
    db: Session,
    evidence: PaymentEvidence,
    *,
    user: Any,
    note: str | None = None,
    now: datetime | None = None,
) -> None:
    _guard_open(evidence)
    moment = now or utcnow()
    evidence.status = EvidenceStatus.REJECTED
    evidence.decided_at = moment
    evidence.decided_by_user_id = getattr(user, "id", None)
    if note:
        evidence.note = note
    db.flush()
    _close_after(db, evidence, now=moment)
    db.flush()


__all__ = [
    "EvidenceError",
    "INSURER_SOURCES",
    "suggested_amount_uf",
    "OPEN_EVIDENCE",
    "ORIGIN_EVIDENCE",
    "ORIGIN_INQUIRY",
    "confirm_evidence",
    "normalise_amount",
    "reject_evidence",
]
