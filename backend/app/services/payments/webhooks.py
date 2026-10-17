"""Provider events → charges → payments (v12, 03 §5).

The generic webhook route (``routers/webhooks.py``) authenticates the account
(``account`` + ``sig``), stores every POST in ``provider_event`` (the inbox,
deduplicated by ``(provider, dedup_key)``) and then calls
:func:`process_event`. ``payments.sync`` feeds ``GET /receivables`` rows through
the SAME core (:func:`apply_charge_update`), so a missed webhook and a received
one end in the same rows.

Effects of a payment event, idempotent by construction:

* the ``provider_charge`` is found by ``(account, installment.id)`` — OUR
  ``external_installment_id``, a SIGNED field — and only then by
  ``(account, installment.internalId)`` (unsigned, and absent from some
  receivables); a new one is created from ``installment.id``, which links it
  to the intent item, the cuota and the broker. One cuota at the provider is
  therefore ONE charge whatever ``internalId`` a webhook or receivable carries;
* state **2** → exactly one ``payment(source=provider,
  evidence=provider_confirmed)`` per charge (``payment.provider_charge_id`` is
  unique) and never a second ACTIVE one for the same external id. Currency/
  amount come from ``payment.convert`` (CLP + the implied UF rate) when
  present, else UF. The cuota cache is refreshed, a ``payment_received`` fyi is
  raised, and other open links of the cuota are told it is paid
  (:func:`~app.services.payments.checkout.mark_paid_outside`). The provider's
  echo of OUR "paid outside" report (a marked charge, no ``payment`` block)
  is only mirrored;
* state **−99** → on a recurring/debit cuota, ``direct_debit_rejected_at`` +
  a ``direct_debit_rejected`` to-do (a rejected card attempt on a one-off link
  is only mirrored on the charge). It never reverses a payment — that is 12 —
  and a −99 arriving after a 2 is stale and changes nothing;
* state **12** → the provider payment is reversed (``chargeback``) and a
  ``sensitive_action`` to-do is raised;
* any other state is mirrored on the charge. A late "pending/in process/
  rejected" event never downgrades a paid charge;
* the intent's status is rolled up from its items.

Signed provider events are not AI: they commit directly (00 §3).
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.base_class import utcnow
from app.models.collection import CollectionInstallment
from app.models.enums import (
    EventProcessing,
    EvidenceLevel,
    MandateStatus,
    NotificationKind,
    NotificationTray,
    PaymentCurrency,
    PaymentMode,
    PaymentSource,
    PaymentStatus,
    ReversalReason,
)
from app.models.payment import Payment, PaymentIntent, PaymentIntentItem, PaymentMandate
from app.models.provider import ProviderAccount, ProviderCharge, ProviderEvent
from app.services.collection.status import refresh_installment_cache
from app.services.fx import clp_to_uf, get_uf_rate
from app.services.notifications import notify, recipients_for_policy
from app.services.payments.intents import (
    MARKED_PAID_KEY,
    as_aware,
    find_charge,
    is_marked_paid_outside,
    rollup_intent,
    santiago_date,
    santiago_today,
)
from app.services.payments.providers import get_provider
from app.services.payments.providers.base import (
    STATE_IN_PROCESS,
    STATE_IN_VALIDATION,
    STATE_PAID,
    STATE_PENDING,
    STATE_REJECTED,
    STATE_REVERSED,
    ProviderEventIn,
    ReceivableIn,
    account_secret,
    infer_event_type,
    signature_fields_for,
    signed_fields_digest,
)

logger = logging.getLogger(__name__)

UF4 = Decimal("0.0001")
_CLP_CODES = frozenset({"CLP", "PESOS", "PESO", "$"})
_DOWNGRADE_STATES = frozenset({STATE_PENDING, STATE_IN_PROCESS, STATE_IN_VALIDATION, STATE_REJECTED})
ORIGIN_INSTALLMENT = "installment"
ORIGIN_PROVIDER_CHARGE = "provider_charge"


def _val(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def body_digest(raw: bytes) -> str:
    return hashlib.sha256(raw or b"").hexdigest()


def signed_fields(acct: ProviderAccount, payload: dict[str, Any]) -> list[str]:
    """The payload fields the provider's HMAC actually covers for this account.

    Same resolution as the providers' ``verify_webhook`` (the body's own type,
    the account's ``signature_fields`` override, the paid-state extras).
    """
    secret = account_secret(acct)
    return signature_fields_for(
        payload, event_type=infer_event_type(payload), override=secret.get("signature_fields")
    )


def signed_digest(acct: ProviderAccount, payload: dict[str, Any]) -> str:
    """A digest of the SIGNED fields only (see :func:`dedup_key_for`)."""
    return signed_fields_digest(payload, signed_fields(acct, payload))


def dedup_key_for(
    parsed: ProviderEventIn | None,
    *,
    raw: bytes,
    event_type: str,
    valid: bool,
    signed: str | None = None,
) -> str:
    """The inbox key. An INVALID signature never takes a real event's key.

    Otherwise anyone holding the URL could pre-empt a genuine event with a
    forged body carrying the same ``id:state`` and get the real one dropped as
    a duplicate. A VALID event's key is built from signed fields only — the
    parsed ``installment.id:state``, else ``signed`` (:func:`signed_digest`) —
    so a replay of a signed body with a mutated UNSIGNED field is the same
    event and is dropped as a duplicate, never processed as a new one. The raw
    body digest is only the last resort when the caller has no signed digest.
    """
    if not valid:
        return f"invalid:{body_digest(raw)}"[:128]
    key = parsed.dedup_key if parsed is not None else ""
    if not key or key.startswith(":") or key.endswith(":"):
        return f"{event_type}:{signed or body_digest(raw)}"[:128]
    return key[:128]


def payment_mode_for(method: str | None) -> PaymentMode | None:
    """Forpay payment method → our ``PaymentMode``."""
    if not method:
        return None
    m = method.upper()
    if "PAC" in m or "DEBITO" in m or "DEBIT" in m or "FINTOC" in m:
        return PaymentMode.DIRECT_DEBIT
    if "EFT" in m or "TRANSFER" in m or "ETPAY" in m or "TEF" in m:
        return PaymentMode.TRANSFER
    if "WEBPAY" in m or "ONECLICK" in m or "CHECKOUT" in m or "CARD" in m or "TARJETA" in m:
        return PaymentMode.CARD_DEBIT
    return PaymentMode.OTHER


@dataclass
class ChargeOutcome:
    status: EventProcessing
    broker_id: int | None = None
    charge_id: int | None = None
    payment_created: bool = False
    payment_reversed: bool = False
    rejected: bool = False
    error: str | None = None
    notes: list[str] = field(default_factory=list)


def receivable_to_event(rec: ReceivableIn) -> ProviderEventIn:
    """A ``GET /receivables`` row seen as a payment event (same core)."""
    return ProviderEventIn(
        event_type="payment",
        dedup_key=f"{rec.provider_installment_id}:{'' if rec.state is None else rec.state}",
        provider_installment_id=rec.provider_installment_id or None,
        external_installment_id=rec.external_installment_id,
        state=rec.state,
        state_name=rec.state_name,
        amount=rec.amount,
        currency=rec.currency,
        provider_mandate_id=rec.provider_mandate_id,
        payment_id=rec.payment_id,
        authorization_code=rec.authorization_code,
        payment_method=rec.payment_method,
        paid_at=rec.paid_at,
        convert_amount=rec.convert_amount,
        convert_currency=rec.convert_currency,
        convert_date=rec.convert_date,
        raw=rec.raw,
    )


# --- the core ------------------------------------------------------------------

def _find_or_create_charge(
    db: Session, acct: ProviderAccount, ev: ProviderEventIn
) -> tuple[ProviderCharge | None, PaymentIntentItem | None, str | None]:
    internal = ev.provider_installment_id
    pid = internal or ev.external_installment_id
    if not pid:
        return None, None, "evento sin id de cuota"
    charge = find_charge(db, acct.id, ext=ev.external_installment_id, pid=pid)
    if (
        charge is not None
        and ev.external_installment_id
        and charge.external_installment_id
        and charge.external_installment_id != ev.external_installment_id[:64]
    ):
        # Found only by the unsigned internalId, yet it names another cuota.
        return None, None, "el id del proveedor no corresponde a la cuota del evento"
    ext = ev.external_installment_id or (charge.external_installment_id if charge else None)
    item = (
        db.scalar(select(PaymentIntentItem).where(PaymentIntentItem.external_installment_id == ext[:64]))
        if ext
        else None
    )
    if charge is not None:
        if (
            internal
            and charge.provider_installment_id != internal[:64]
            and charge.provider_installment_id == charge.external_installment_id
        ):
            # The row carried our id as a placeholder (a receivable without
            # internalId, or our own "paid outside" mark): adopt Forpay's id.
            # The lookup above proved no other charge of this account holds it.
            charge.provider_installment_id = internal[:64]
        return charge, item, None
    if item is None:
        return None, None, "cuota desconocida (no corresponde a ningún link de Radal)"
    intent = item.intent
    if intent.provider_account_id is not None and intent.provider_account_id != acct.id:
        return None, None, "la cuota pertenece a otra cuenta del proveedor"
    charge = ProviderCharge(
        broker_id=item.broker_id,
        provider_account_id=acct.id,
        intent_id=intent.id,
        installment_id=item.installment_id,
        provider_mandate_id=(ev.provider_mandate_id or intent.provider_mandate_id or None),
        provider_installment_id=pid[:64],
        external_installment_id=ext[:64] if ext else None,
    )
    db.add(charge)
    db.flush()
    return charge, item, None


def _mirror(charge: ProviderCharge, ev: ProviderEventIn) -> None:
    if ev.state is not None and not (charge.state == STATE_PAID and ev.state in _DOWNGRADE_STATES):
        charge.state = ev.state
        if ev.state_name:
            charge.state_name = ev.state_name[:64]
    if ev.amount is not None:
        charge.amount = ev.amount
    if ev.currency:
        charge.currency = ev.currency[:8]
    if ev.authorization_code:
        charge.authorization_code = ev.authorization_code[:64]
    if ev.payment_method:
        charge.payment_method = ev.payment_method[:64]
    if ev.paid_at is not None:
        charge.paid_at = ev.paid_at
    if ev.provider_mandate_id and not charge.provider_mandate_id:
        charge.provider_mandate_id = ev.provider_mandate_id[:64]
    if (
        ev.convert_amount is not None
        and (ev.convert_currency or "").upper() in _CLP_CODES
    ):
        charge.amount_clp = int(ev.convert_amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if ev.raw:
        marked = charge.raw.get(MARKED_PAID_KEY) if isinstance(charge.raw, dict) else None
        charge.raw = {**ev.raw, MARKED_PAID_KEY: marked} if marked else ev.raw


def _payment_figures(
    db: Session, ev: ProviderEventIn, item: PaymentIntentItem | None, inst: CollectionInstallment
) -> tuple[PaymentCurrency, Decimal, Decimal, Decimal | None]:
    """``(currency, amount, amount_uf, uf_rate)`` of a provider payment."""
    paid_on = ev.convert_date or santiago_date(ev.paid_at)
    currency = (ev.currency or "UF").upper()
    if ev.amount is not None and currency == "UF":
        amount_uf = ev.amount
    elif ev.amount is not None and currency in _CLP_CODES:
        rate = get_uf_rate(db, paid_on) if paid_on else None
        amount_uf = clp_to_uf(ev.amount, rate) if rate else (item.amount_uf if item else inst.gross_amount_uf)
    else:
        amount_uf = item.amount_uf if item is not None else inst.gross_amount_uf
    amount_uf = Decimal(str(amount_uf)).quantize(UF4, rounding=ROUND_HALF_UP)
    convert_ccy = (ev.convert_currency or "").upper()
    if ev.convert_amount is not None and convert_ccy in _CLP_CODES and amount_uf > 0:
        rate = (ev.convert_amount / amount_uf).quantize(UF4, rounding=ROUND_HALF_UP)
        return PaymentCurrency.CLP, ev.convert_amount, amount_uf, rate
    return PaymentCurrency.UF, amount_uf, amount_uf, None


def _notify_installment(
    db: Session,
    inst: CollectionInstallment,
    *,
    kind: NotificationKind,
    tray: NotificationTray,
    params: dict[str, Any],
    now: datetime,
    origin_type: str = ORIGIN_INSTALLMENT,
    origin_id: int | None = None,
    level: str | None = None,
) -> None:
    """Raise a provider-event alert about a cuota.

    The cuota's own origin (``installment``) is the CLOCK's: it keeps one open
    row per (user, origin) and rewrites it to whatever the cuota deserves
    today. An alert the clock does not own (a chargeback) therefore goes on
    the charge's origin (``provider_charge``), or the next clock run would
    overwrite it; one the clock DOES own (a debit rejection) passes the same
    ``level`` the clock computes, so its rewrite is a no-op.
    """
    policy = inst.collection_plan.policy
    notify(
        db,
        broker_id=inst.broker_id,
        user_ids=recipients_for_policy(db, policy),
        kind=kind,
        tray=tray,
        origin_type=origin_type,
        origin_id=inst.id if origin_id is None else origin_id,
        level=level,
        params={
            "policy_id": policy.id,
            "policy_number": policy.policy_number,
            "installment_number": inst.number,
            **params,
        },
        link_path=f"/policies/{policy.id}",
        now=now,
        renotify=True,
    )


def _provider_payment(db: Session, charge: ProviderCharge) -> Payment | None:
    return db.scalar(select(Payment).where(Payment.provider_charge_id == charge.id))


def _active_provider_payment_for_cuota(db: Session, charge: ProviderCharge) -> Payment | None:
    """An ACTIVE provider payment on ANY charge of the same ``(account, external id)``."""
    if not charge.external_installment_id or charge.provider_account_id is None:
        return None
    return db.scalars(
        select(Payment)
        .join(ProviderCharge, ProviderCharge.id == Payment.provider_charge_id)
        .where(
            ProviderCharge.provider_account_id == charge.provider_account_id,
            ProviderCharge.external_installment_id == charge.external_installment_id,
            Payment.source == PaymentSource.PROVIDER,
            Payment.status == PaymentStatus.ACTIVE,
        )
        .order_by(Payment.id)
    ).first()


def _on_paid(
    db: Session,
    charge: ProviderCharge,
    item: PaymentIntentItem | None,
    inst: CollectionInstallment,
    ev: ProviderEventIn,
    out: ChargeOutcome,
    *,
    now: datetime,
    marked_outside: bool = False,
) -> None:
    if _provider_payment(db, charge) is not None:
        return  # replay: exactly one payment per charge
    if _active_provider_payment_for_cuota(db, charge) is not None:
        out.notes.append("la cuota ya tiene un pago confirmado por el proveedor")
        return
    if marked_outside and not (ev.payment_id or ev.authorization_code):
        # The provider echoing OUR "paid outside" report: no money moved through it.
        out.notes.append("eco del pago informado por Radal fuera del proveedor; sin pago nuevo")
        return
    currency, amount, amount_uf, rate = _payment_figures(db, ev, item, inst)
    paid_on = ev.convert_date or santiago_date(ev.paid_at) or santiago_today(now)
    target = _val(inst.currency) or "UF"
    if target != "UF":
        # The provider bills UF (checkout refuses non-UF cuotas, but a link
        # minted before the cuota changed currency may still be paid): book
        # the money normalised to the cuota's currency, never UF-as-USD.
        from app.services.collection.ledger import LedgerError, convert_payment  # noqa: PLC0415

        try:
            conv = convert_payment(db, amount=amount, currency=currency, paid_on=paid_on, target=inst.currency)
        except LedgerError as exc:
            out.error = (
                f"pago del proveedor en {_val(currency)} sobre una cuota en {target} sin tipo de cambio "
                f"del {paid_on.isoformat()}: no se registró ({exc.detail})"
            )[:4000]
            _notify_installment(
                db, inst, kind=NotificationKind.SENSITIVE_ACTION, tray=NotificationTray.TODO,
                params={"event": "provider_payment_unconverted", "currency": target}, now=now,
                origin_type=ORIGIN_PROVIDER_CHARGE, origin_id=charge.id,
            )
            # ``failed``: a redelivery (or POST /platform/events/{id}/reprocess)
            # retries once the rate is loaded.
            out.status = EventProcessing.FAILED
            return
        amount_uf = conv.amount_uf
        if currency == PaymentCurrency.UF:
            rate = None
    note = "Pago confirmado por Forpay"
    if ev.authorization_code:
        note += f" (autorización {ev.authorization_code})"
    payment = Payment(
        broker_id=inst.broker_id,
        amount=amount,
        currency=currency,
        uf_rate=rate,
        amount_uf=amount_uf,
        paid_on=paid_on,
        method=payment_mode_for(ev.payment_method),
        source=PaymentSource.PROVIDER,
        evidence_level=EvidenceLevel.PROVIDER_CONFIRMED,
        status=PaymentStatus.ACTIVE,
        provider_charge_id=charge.id,
        note=note,
    )
    payment.installment = inst
    db.add(payment)
    db.flush()
    refresh_installment_cache(db, inst, today=santiago_today(now))
    out.payment_created = True
    _notify_installment(
        db,
        inst,
        kind=NotificationKind.PAYMENT_RECEIVED,
        tray=NotificationTray.FYI,
        params={"amount_uf": amount_uf, "paid_on": paid_on, "source": "provider"},
        now=now,
    )
    from app.services.payments.checkout import mark_paid_outside

    mark_paid_outside(db, inst, now=now, exclude_intent_id=charge.intent_id)


def _reverse(
    db: Session,
    charge: ProviderCharge,
    inst: CollectionInstallment,
    *,
    reason: ReversalReason,
    note: str,
    now: datetime,
) -> bool:
    payment = _provider_payment(db, charge)
    if payment is None or payment.status != PaymentStatus.ACTIVE:
        return False
    payment.status = PaymentStatus.REVERSED
    payment.reversal_reason = reason
    payment.reversal_note = note
    payment.reversed_at = now
    db.flush()
    db.refresh(inst)
    refresh_installment_cache(db, inst, today=santiago_today(now))
    return True


def _is_recurring(db: Session, charge: ProviderCharge, inst: CollectionInstallment) -> bool:
    if charge.mandate_id is not None:
        return True
    mode = _val(inst.collection_plan.payment_mode)
    return mode in (PaymentMode.DIRECT_DEBIT.value, PaymentMode.CARD_DEBIT.value) and charge.intent_id is None


def apply_charge_update(
    db: Session, acct: ProviderAccount, ev: ProviderEventIn, *, now: datetime | None = None
) -> ChargeOutcome:
    """Apply one provider installment state (webhook or receivable). Flushes only."""
    moment = as_aware(now) if now is not None else utcnow()
    charge, item, error = _find_or_create_charge(db, acct, ev)
    if charge is None:
        return ChargeOutcome(status=EventProcessing.IGNORED, error=error)
    out = ChargeOutcome(status=EventProcessing.PROCESSED, broker_id=charge.broker_id, charge_id=charge.id)
    prior_state = charge.state
    marked_outside = is_marked_paid_outside(charge)
    _mirror(charge, ev)
    db.flush()

    inst = db.get(CollectionInstallment, charge.installment_id) if charge.installment_id else None
    if inst is None or inst.broker_id != charge.broker_id:
        out.notes.append("cargo sin cuota asociada")
    elif ev.state == STATE_PAID:
        _on_paid(db, charge, item, inst, ev, out, now=moment, marked_outside=marked_outside)
    elif ev.state == STATE_REVERSED:
        if _reverse(
            db, charge, inst, reason=ReversalReason.CHARGEBACK,
            note="Reversado por Forpay (estado 12)", now=moment,
        ):
            out.payment_reversed = True
            _notify_installment(
                db, inst, kind=NotificationKind.SENSITIVE_ACTION, tray=NotificationTray.TODO,
                params={"event": "chargeback"}, now=moment,
                origin_type=ORIGIN_PROVIDER_CHARGE, origin_id=charge.id,
            )
    elif ev.state == STATE_REJECTED:
        if prior_state == STATE_PAID:
            # Out-of-order delivery of an earlier failed attempt: the cuota is
            # already paid. A real reversal arrives as state 12, never as -99.
            out.notes.append("rechazo tardío ignorado: la cuota ya figura pagada")
        elif _is_recurring(db, charge, inst):
            rejected_on = santiago_today(moment)
            inst.direct_debit_rejected_at = rejected_on
            refresh_installment_cache(db, inst, today=rejected_on)
            out.rejected = True
            _notify_installment(
                db, inst, kind=NotificationKind.DIRECT_DEBIT_REJECTED, tray=NotificationTray.TODO,
                params={"rejected_on": rejected_on}, now=moment,
                # The clock's own level for this alert (alerts.installment_alert).
                level=f"d{rejected_on.isoformat()}",
            )

    if charge.intent_id is not None:
        intent = db.get(PaymentIntent, charge.intent_id)
        if intent is not None:
            rollup_intent(db, intent)
    db.flush()
    return out


_MANDATE_STATES: dict[str, MandateStatus] = {
    "11": MandateStatus.BANK_CONFIRMATION,
    "6": MandateStatus.REVOKED,
    "-99": MandateStatus.FAILED,
    "ACTIVO": MandateStatus.ACTIVE,
    "ACTIVE": MandateStatus.ACTIVE,
    "ACTIVADO": MandateStatus.ACTIVE,
    "ANULADO": MandateStatus.REVOKED,
    "RECHAZADO": MandateStatus.FAILED,
}


#: Where an activation body may carry the mandate state, in order of preference.
_ACTIVATION_STATE_PATHS: tuple[str, ...] = ("mandate.state", "product.state", "state")


def _activation_state(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """``(STATE, dotted path it was read from)`` — e.g. ``("11", "product.state.id")``."""
    for base in _ACTIVATION_STATE_PATHS:
        node: Any = payload
        for part in base.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, dict):
            if node.get("id") is not None:
                return str(node["id"]).upper(), f"{base}.id"
            if node.get("name") is not None:
                return str(node["name"]).upper(), f"{base}.name"
            continue
        if node is not None:
            return str(node).upper(), base
    return None, None


REASON_ACTIVATION_UNSIGNED_ID = "activación sin id de mandato firmado por el proveedor"
REASON_ACTIVATION_UNSIGNED_STATE = (
    "el estado del mandato no viene firmado por el proveedor: no se aplica "
    "(agrega su campo a signature_fields de la cuenta y reprocesa el evento)"
)


def _apply_activation(db: Session, acct: ProviderAccount, payload: dict[str, Any], *, now: datetime) -> ChargeOutcome:
    """Move a recurring mandate's status — ONLY from fields the provider signed.

    The mandate is found by the signed ``product.id`` (never by the unsigned
    ``internalId``, which could retarget the effect onto another mandate of a
    shared account), and the new status is written only when the field it is
    read from is covered by the payload HMAC. Forpay's default activation
    signature (``businessPartnerId · userId · product.id``) does not cover the
    state, so by default an activation is a stored HINT: recorded ``ignored``
    with the reason, never a status change, until the account's
    ``signature_fields`` names the state field (then reprocess).
    """
    product = payload.get("product") if isinstance(payload.get("product"), dict) else {}
    fields = set(signed_fields(acct, payload))
    mandate_id = product.get("id") if "product.id" in fields else None
    if mandate_id is None or str(mandate_id).strip() == "":
        return ChargeOutcome(status=EventProcessing.IGNORED, error=REASON_ACTIVATION_UNSIGNED_ID)
    mandate = db.scalar(
        select(PaymentMandate).where(
            PaymentMandate.provider_mandate_id == str(mandate_id)[:64],
            PaymentMandate.provider_account_id == acct.id,
        )
    )
    if mandate is None:
        # One-off links (ONEPAY) also send activations; nothing to do for them.
        return ChargeOutcome(status=EventProcessing.IGNORED, error="activación sin mandato recurrente asociado")
    state, path = _activation_state(payload)
    target = _MANDATE_STATES.get(state or "")
    if target is None:
        return ChargeOutcome(status=EventProcessing.PROCESSED, broker_id=mandate.broker_id)
    if path not in fields:
        return ChargeOutcome(
            status=EventProcessing.IGNORED, broker_id=mandate.broker_id, error=REASON_ACTIVATION_UNSIGNED_STATE
        )
    mandate.status = target
    if target == MandateStatus.ACTIVE and mandate.activated_at is None:
        mandate.activated_at = now
    if target == MandateStatus.REVOKED and mandate.revoked_at is None:
        mandate.revoked_at = now
    db.flush()
    return ChargeOutcome(status=EventProcessing.PROCESSED, broker_id=mandate.broker_id)


def process_event(
    db: Session, event: ProviderEvent, acct: ProviderAccount, *, now: datetime | None = None
) -> ChargeOutcome:
    """Process one STORED, signature-valid event. Flushes; caller commits.

    Raises on unexpected errors — the route catches, rolls back and records
    ``failed`` (HTTP stays 200; ``payments.sync`` reconciles).
    """
    moment = as_aware(now) if now is not None else utcnow()
    payload = event.payload if isinstance(event.payload, dict) else {}
    event_type = event.event_type or infer_event_type(payload)
    if event_type == "activation" and not isinstance(payload.get("installment"), dict):
        outcome = _apply_activation(db, acct, payload, now=moment)
    else:
        provider = get_provider(_val(acct.provider) or "forpay")
        parsed = provider.parse_webhook(acct, payload)
        outcome = apply_charge_update(db, acct, parsed, now=moment)
    event.processing_status = outcome.status
    event.broker_id = outcome.broker_id
    event.error = outcome.error
    db.flush()
    return outcome


def run_processing(db: Session, event: ProviderEvent, acct: ProviderAccount) -> EventProcessing:
    """Process a stored event and COMMIT; any error → rollback + ``failed`` (never raises).

    Used by the webhook route and ``POST /platform/events/{id}/reprocess``.
    """
    event_id = event.id
    try:
        outcome = process_event(db, event, acct, now=utcnow())
        event.attempts = (event.attempts or 0) + 1
        db.commit()
        return outcome.status
    except Exception as exc:  # noqa: BLE001 - recorded, never raised to the provider
        db.rollback()
        logger.exception("provider event %s: processing failed", event_id)
        event = db.get(ProviderEvent, event_id)
        if event is not None:
            event.processing_status = EventProcessing.FAILED
            event.attempts = (event.attempts or 0) + 1
            event.error = f"{type(exc).__name__}: {exc}"[:4000]
            db.commit()
        return EventProcessing.FAILED


__all__ = [
    "ChargeOutcome",
    "run_processing",
    "apply_charge_update",
    "body_digest",
    "dedup_key_for",
    "payment_mode_for",
    "process_event",
    "receivable_to_event",
    "signed_digest",
    "signed_fields",
]
