"""Native checkout: one payment intent = one Forpay ONEPAY link (v12, 03 §6).

Two doors, one implementation:

* the broker clicks "Generar link de pago" → ``POST /payments/intents`` →
  :func:`create_intent_for_installments`;
* the insured pays from a public share link (``/s/{token}``, audience insured,
  ``can_pay``) → ``POST /public/share/{token}/checkout`` (lane B5) →
  :func:`create_checkout_for_share`.

Rules, in order (any refusal is a :class:`CheckoutError` carrying an HTTP status
and a Spanish ``detail`` shown verbatim):

1. every cuota exists inside the broker (404 otherwise) and, through a share
   link, belongs to the link's entity (404 — the link cannot see it);
2. it is neither paid nor voided (409);
3. it is denominated in UF — the provider bills UF only, so a USD cuota is
   refused (409) — and resolves to the ``native`` level (409 with the
   resolver's reason);
4. its lead insurer has an active SPOT channel (webpay / oneclick / bank
   transfer; PAC is recurring, never spot) — the channel names the provider
   account and the Transbank child store (``f_commerceCode``);
5. all cuotas fit ONE provider account (409 "Paga por separado las cuotas de
   {insurer}") and ONE payer (409);
6. the payer has a RUT and an e-mail (409 — Forpay requires both).

The same set of cuotas already behind an open link returns that link (a double
click never mints two links) — but only while every item still charges the
cuota's CURRENT balance; after a partial payment the old link would overcharge,
so it is superseded instead. Any OTHER open link covering one of these cuotas
is superseded: its unpaid items are annulled at the provider and it is
cancelled, so an old link can never charge a cuota twice.

Amounts are the cuota's remaining balance in UF; Forpay converts on the day of
payment (``currencyConvert: TO_DAY``).

:func:`on_balance_change` closes the gap a partial payment outside the
provider would open: an open link whose frozen amount no longer equals the
balance is superseded at once, not only when a new link is asked for.

:func:`mark_paid_outside` is the other half of the contract (03 §6.5): when a
cuota gets paid by any other path, the provider is told (state 2) for every
open intent item of that cuota, so Forpay never charges or reminds it. The
item's charge is marked (:func:`~app.services.payments.intents.mark_charge_paid_outside`)
so the provider's echo of that state 2 is never booked as a second payment.

Every function flushes only; the CALLER commits (also after a
:class:`CheckoutProviderError`, which leaves the intent ``failed``).
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.base_class import utcnow
from app.models.collection import CollectionInstallment
from app.models.enums import (
    IntentStatus,
    PartyRole,
    PaymentChannel,
    ProviderAccountStatus,
    ShareAudience,
)
from app.models.insurer import Insurer
from app.models.payment import PaymentIntent, PaymentIntentItem
from app.models.provider import ProviderAccount
from app.models.share_link import ShareLink
from app.services.collection.resolver import (
    active_native_channels,
    provider_account_usable,
    resolve_collection_mode,
)
from app.services.collection.status import broker_collection_params, compute_installment_state
from app.services.payments.accounts import webhook_url
from app.services.payments.intents import (
    OPEN_INTENT_STATUSES,
    as_aware,
    mark_charge_paid_outside,
    open_items_for_installment,
    provider_paid_item_ids,
    santiago_today,
)
from app.services.payments.providers import get_provider
from app.services.payments.providers.base import CheckoutItem, PayerData, PaymentProviderError

if TYPE_CHECKING:
    from app.models.policy import Policy
    from app.models.user import User

logger = logging.getLogger(__name__)

MAX_CHECKOUT_ITEMS = 50
#: An open link is reused only if each item still equals the cuota's balance (UF, 4 dp).
AMOUNT_EPSILON_UF = Decimal("0.00005")

#: Channels a one-off checkout may use, in order of preference. PAC is recurring.
SPOT_CHANNELS: tuple[str, ...] = (
    PaymentChannel.WEBPAY.value,
    PaymentChannel.ONECLICK.value,
    PaymentChannel.BANK_TRANSFER.value,
)


class CheckoutError(Exception):
    """A refusal with an HTTP status and a Spanish, user-facing ``detail``."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class CheckoutProviderError(CheckoutError):
    """The provider failed; the intent was left ``failed`` (caller commits)."""

    def __init__(self, detail: str, intent_id: int | None = None) -> None:
        super().__init__(502, detail)
        self.intent_id = intent_id


@dataclass(frozen=True)
class CheckoutResult:
    checkout_url: str
    intent_id: int
    reused: bool = False


def _val(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def insurer_label(insurer: Insurer | None) -> str:
    if insurer is None:
        return "la compañía"
    return insurer.trade_name or insurer.legal_name


# --- payer -------------------------------------------------------------------

@dataclass(frozen=True)
class _Payer:
    data: PayerData | None
    party_id: int | None
    missing: str | None


def _split_name(full: str) -> tuple[str, str]:
    parts = (full or "").strip().split(" ", 1)
    first = parts[0] if parts and parts[0] else "Pagador"
    return first, (parts[1].strip() if len(parts) > 1 else "")


def resolve_payer(policy: "Policy") -> _Payer:
    """Payer party → policyholder party → the client's insured (team rule)."""
    parties = list(getattr(policy, "parties", None) or [])
    for role in (PartyRole.PAYER.value, PartyRole.POLICYHOLDER.value):
        for party in parties:
            if _val(party.role) != role:
                continue
            if not party.rut:
                return _Payer(None, party.id, "Falta el RUT del pagador para generar el link de pago")
            if not party.email:
                return _Payer(None, party.id, "Falta el correo del pagador para generar el link de pago")
            name, last = _split_name(party.name)
            return _Payer(
                PayerData(rut=party.rut, name=name, last_name=last, email=party.email, phone=party.phone),
                party.id,
                None,
            )
    client = getattr(policy, "client", None)
    insured = getattr(client, "insured", None) if client is not None else None
    if insured is None or not insured.rut:
        return _Payer(None, None, "Falta el RUT del pagador para generar el link de pago")
    email = (getattr(client, "contact_email", None) or insured.email) if client is not None else insured.email
    if not email:
        return _Payer(None, None, "Falta el correo del pagador para generar el link de pago")
    name, last = _split_name(insured.legal_name)
    phone = getattr(client, "contact_phone", None) or insured.phone
    return _Payer(PayerData(rut=insured.rut, name=name, last_name=last, email=email, phone=phone), None, None)


# --- share-link scope ----------------------------------------------------------

def _link_installment_ids(db: Session, link: ShareLink) -> set[int]:
    """The cuota ids ``link`` reaches — the SAME scope the public page projects.

    Delegates to :func:`app.services.share_links.resolve_share_scope`, so the
    page's "Pagar" button and this checkout can never disagree (a ``case_file``
    link also reaches ``case.policy_id`` and the plans filed in a collection
    case). A link whose target is gone reaches nothing.
    """
    from app.services.share_links import resolve_share_scope

    scope = resolve_share_scope(db, link)
    if scope is None:
        return set()
    return {inst.id for inst in scope.installments}


def _belongs_to_link(db: Session, inst: CollectionInstallment, link: ShareLink) -> bool:
    return inst.id in _link_installment_ids(db, link)


# --- intent helpers ------------------------------------------------------------

def _new_hex() -> str:
    return secrets.token_hex(6)


def not_native_reason(insurer: Insurer | None, number: int | None) -> str:
    """The Spanish 409 when a cuota cannot be paid online (Forpay not usable)."""
    who = insurer_label(insurer) if insurer is not None else "la compañía"
    which = f"la cuota {number}" if number is not None else "esta cuota"
    return (
        f"El pago en línea no está disponible para {which}. Paga con las instrucciones "
        f"de {who} (transferencia o sus canales habituales) y sube el comprobante "
        "para que lo revisemos."
    )


def _return_url(share_link: ShareLink | None) -> str:
    base = settings.public_app_url
    if share_link is not None:
        return f"{base}/s/{share_link.token}?pago=retorno"
    return f"{base}/"


def _pick_channel(db: Session, insurer_id: int | None):  # noqa: ANN202
    channels = [c for c in active_native_channels(db, insurer_id) if _val(c.channel) in SPOT_CHANNELS]
    channels.sort(key=lambda c: SPOT_CHANNELS.index(_val(c.channel)))
    return channels[0] if channels else None


def _annul_and_cancel(db: Session, intent: PaymentIntent, *, now: datetime) -> None:
    """Supersede an open intent: annul its unpaid items at the provider, cancel it."""
    paid = provider_paid_item_ids(db, intent)
    pending = [i.external_installment_id for i in intent.items if i.id not in paid]
    acct = db.get(ProviderAccount, intent.provider_account_id) if intent.provider_account_id else None
    if pending and acct is not None and intent.provider_mandate_id:
        try:
            get_provider(_val(acct.provider) or "forpay").set_installment_state(acct, pending, "annulled")
        except PaymentProviderError as exc:
            raise CheckoutProviderError(
                "No pudimos anular el link de pago anterior. Intenta de nuevo en unos minutos."
            ) from exc
    if not paid:
        intent.status = IntentStatus.CANCELLED


def _amounts_current(intent: PaymentIntent, balances: dict[int, Decimal]) -> bool:
    for item in intent.items:
        balance = balances.get(item.installment_id)
        if balance is None or abs(Decimal(str(item.amount_uf)) - Decimal(str(balance))) > AMOUNT_EPSILON_UF:
            return False
    return True


def _existing_same_set(
    db: Session, broker_id: int, ids: set[int], balances: dict[int, Decimal]
) -> tuple[PaymentIntent | None, list[PaymentIntent]]:
    """(an open intent over exactly ``ids`` at the current balances with a link, the other open overlapping intents)."""
    rows = db.scalars(
        select(PaymentIntent)
        .join(PaymentIntentItem, PaymentIntentItem.intent_id == PaymentIntent.id)
        .where(
            PaymentIntent.broker_id == broker_id,
            PaymentIntent.status.in_(OPEN_INTENT_STATUSES),
            PaymentIntentItem.installment_id.in_(list(ids)),
        )
        .order_by(PaymentIntent.id.desc())
    ).unique().all()
    same: PaymentIntent | None = None
    others: list[PaymentIntent] = []
    for intent in rows:
        item_ids = {item.installment_id for item in intent.items}
        if (
            same is None
            and item_ids == ids
            and intent.checkout_url
            and intent.status in (IntentStatus.CREATED, IntentStatus.PENDING)
            and _amounts_current(intent, balances)
        ):
            same = intent
        else:
            others.append(intent)
    return same, others


# --- the two doors --------------------------------------------------------------

def create_intent_for_installments(
    db: Session,
    *,
    broker_id: int,
    installment_ids: list[int],
    user: "User | None",
    share_link: ShareLink | None = None,
    now: datetime | None = None,
) -> CheckoutResult:
    """Validate, group, create the intent and its provider checkout. See module doc."""
    moment = as_aware(now) if now is not None else utcnow()
    today = santiago_today(moment)
    ids: list[int] = list(dict.fromkeys(int(i) for i in installment_ids or []))
    if not ids:
        raise CheckoutError(422, "Elige al menos una cuota para pagar")
    if len(ids) > MAX_CHECKOUT_ITEMS:
        raise CheckoutError(422, f"Un link de pago admite hasta {MAX_CHECKOUT_ITEMS} cuotas")
    if share_link is not None and share_link.broker_id != broker_id:
        raise CheckoutError(404, "Link no encontrado")

    rows = {
        inst.id: inst
        for inst in db.scalars(
            select(CollectionInstallment).where(
                CollectionInstallment.id.in_(ids), CollectionInstallment.broker_id == broker_id
            )
        )
    }
    installments: list[CollectionInstallment] = []
    link_scope = _link_installment_ids(db, share_link) if share_link is not None else None
    for inst_id in ids:
        inst = rows.get(inst_id)
        if inst is None or (link_scope is not None and inst.id not in link_scope):
            raise CheckoutError(404, "Cuota no encontrada")
        installments.append(inst)

    cadence, tolerance = broker_collection_params(db, broker_id)
    groups: dict[int, list[tuple[CollectionInstallment, Any, Decimal, int | None]]] = {}
    group_insurers: dict[int, list[int | None]] = {}
    payer: _Payer | None = None
    balances: dict[int, Decimal] = {}
    for inst in installments:
        state = compute_installment_state(
            inst, list(inst.payments), today=today, cadence_days=cadence, tolerance_uf=tolerance
        )
        if state.state == "paid":
            raise CheckoutError(409, f"La cuota {inst.number} ya está pagada")
        if state.state == "voided":
            raise CheckoutError(409, f"La cuota {inst.number} está anulada")
        if state.balance_uf <= 0:
            raise CheckoutError(409, f"La cuota {inst.number} no tiene saldo por pagar")
        currency = _val(getattr(inst, "currency", None)) or "UF"
        if currency != "UF":
            # The provider bills UF only: the balance of a USD cuota is in dollars.
            label = "dólares" if currency == "USD" else currency
            raise CheckoutError(409, f"La cuota {inst.number} está en {label}: no se puede pagar en línea")
        balances[inst.id] = state.balance_uf
        mode = resolve_collection_mode(db, inst, broker_id=broker_id)
        insurer = db.get(Insurer, mode.insurer_id) if mode.insurer_id else None
        if mode.level == "record":
            raise CheckoutError(
                409, f"La cuota {inst.number} no se puede pagar en línea: {mode.reason}"
            )
        if mode.level != "native":
            # Forpay is paused / not usable for this insurer: say so plainly and
            # point to what DOES work (review v12 #13 — a 502 "intenta de nuevo"
            # invited paying twice).
            raise CheckoutError(409, not_native_reason(insurer, inst.number))
        channel = _pick_channel(db, mode.insurer_id)
        if channel is None or channel.provider_account_id is None:
            raise CheckoutError(
                409, f"{insurer_label(insurer)} no tiene un medio de pago inmediato en línea"
            )
        policy = inst.collection_plan.policy
        this_payer = resolve_payer(policy)
        if this_payer.missing:
            raise CheckoutError(409, this_payer.missing)
        if payer is None:
            payer = this_payer
        elif payer.data.rut != this_payer.data.rut:  # type: ignore[union-attr]
            raise CheckoutError(409, "Paga por separado las cuotas de distintos pagadores")
        groups.setdefault(channel.provider_account_id, []).append(
            (inst, channel, state.balance_uf, mode.insurer_id)
        )
        group_insurers.setdefault(channel.provider_account_id, []).append(mode.insurer_id)

    if len(groups) > 1:
        first = next(iter(groups))
        names: list[str] = []
        for account_id, insurer_ids in group_insurers.items():
            if account_id == first:
                continue
            for insurer_id in insurer_ids:
                label = insurer_label(db.get(Insurer, insurer_id) if insurer_id else None)
                if label not in names:
                    names.append(label)
        raise CheckoutError(409, f"Paga por separado las cuotas de {', '.join(names)}")

    account_id, entries = next(iter(groups.items()))
    acct = db.get(ProviderAccount, account_id)
    if not provider_account_usable(acct):
        raise CheckoutError(409, not_native_reason(None, None))

    url = webhook_url(acct, "payment")
    if url is None:
        raise CheckoutError(409, not_native_reason(None, None))

    same, others = _existing_same_set(db, broker_id, set(ids), balances)
    if same is not None and (share_link is None or same.share_link_id in (None, share_link.id)):
        return CheckoutResult(checkout_url=same.checkout_url or "", intent_id=same.id, reused=True)
    if same is not None:
        others.append(same)
    for intent in others:
        _annul_and_cancel(db, intent, now=moment)

    hex_id = _new_hex()
    intent = PaymentIntent(
        broker_id=broker_id,
        external_id=f"rdl-intent-{hex_id}",
        share_link_id=share_link.id if share_link is not None else None,
        payer_party_id=payer.party_id if payer else None,
        provider_account_id=acct.id,
        status=IntentStatus.CREATED,
        created_by_user_id=getattr(user, "id", None),
    )
    items: list[CheckoutItem] = []
    for inst, channel, balance, insurer_id in entries:
        ext = f"rdl-cuota-{inst.id}-{hex_id}"
        intent.items.append(
            PaymentIntentItem(
                broker_id=broker_id,
                installment_id=inst.id,
                insurer_id=insurer_id,
                amount_uf=balance,
                store_commerce_code=channel.store_commerce_code,
                external_installment_id=ext,
            )
        )
        policy = inst.collection_plan.policy
        items.append(
            CheckoutItem(
                installment_id=inst.id,
                external_installment_id=ext,
                amount_uf=balance,
                due_date=inst.due_date,
                store_commerce_code=channel.store_commerce_code,
                description=f"Póliza {policy.policy_number or policy.id} cuota {inst.number}",
            )
        )
    db.add(intent)
    db.flush()

    provider = get_provider(_val(acct.provider) or "forpay")
    try:
        user_id = provider.ensure_customer(acct, payer.data)  # type: ignore[union-attr]
        payer_data = payer.data  # type: ignore[union-attr]
        if user_id and user_id != (payer_data.user_id or payer_data.rut):
            payer_data = PayerData(
                rut=payer_data.rut,
                name=payer_data.name,
                last_name=payer_data.last_name,
                email=payer_data.email,
                phone=payer_data.phone,
                user_id=user_id,
            )
        session = provider.create_spot_checkout(
            acct,
            payer_data,
            items,
            external_id=intent.external_id,
            return_url=_return_url(share_link),
            webhook_url=url,
        )
    except PaymentProviderError as exc:
        logger.warning("checkout %s: provider error %s", intent.external_id, type(exc).__name__)
        intent.status = IntentStatus.FAILED
        db.flush()
        raise CheckoutProviderError(
            "No pudimos generar el link de pago. Intenta de nuevo en unos minutos.", intent.id
        ) from exc

    intent.provider_mandate_id = (session.provider_mandate_id or "")[:64] or None
    intent.checkout_url = session.checkout_url[:1024]
    intent.status = IntentStatus.PENDING
    db.flush()
    return CheckoutResult(checkout_url=intent.checkout_url, intent_id=intent.id)


def create_checkout_for_share(
    db: Session,
    link: ShareLink,
    installment_ids: list[int],
    *,
    now: datetime | None = None,
) -> CheckoutResult:
    """The public door (B5's ``POST /public/share/{token}/checkout``).

    The caller has already resolved a LIVE link. This re-checks audience and
    ``can_pay`` (403 — the link may not pay) and scopes every cuota to the
    link's entity inside the link's broker.
    """
    from app.services.share_links import link_permissions

    if _val(link.audience) != ShareAudience.INSURED.value or not link_permissions(link).get("can_pay"):
        raise CheckoutError(403, "Este link no permite pagar")
    return create_intent_for_installments(
        db,
        broker_id=link.broker_id,
        installment_ids=installment_ids,
        user=None,
        share_link=link,
        now=now,
    )


def cancel_intent(db: Session, intent: PaymentIntent, *, now: datetime | None = None) -> PaymentIntent:
    """Cancel an open link: annul its unpaid items at the provider (502 on failure)."""
    if intent.status in (IntentStatus.PAID, IntentStatus.PARTIALLY_PAID):
        raise CheckoutError(409, "El link de pago ya registra pagos; no se puede anular")
    if intent.status not in (IntentStatus.CREATED, IntentStatus.PENDING):
        raise CheckoutError(409, "El link de pago ya está cerrado")
    _annul_and_cancel(db, intent, now=as_aware(now) if now else utcnow())
    intent.status = IntentStatus.CANCELLED
    db.flush()
    return intent


def mark_paid_outside(
    db: Session,
    installment: CollectionInstallment,
    *,
    now: datetime | None = None,
    exclude_intent_id: int | None = None,
) -> int:
    """Tell the provider a cuota was paid by another path (03 §6.5). Never raises.

    No-op unless the cuota is fully paid. For every OPEN intent item of the
    cuota not already charged through the provider, calls
    ``set_installment_state(paid)``; an intent left with nothing chargeable and
    no provider payment is cancelled. Returns how many items were reported.
    Provider failures are logged, not raised (``payments.sync`` reconciles).
    """
    moment = as_aware(now) if now is not None else utcnow()
    try:
        cadence, tolerance = broker_collection_params(db, installment.broker_id)
        state = compute_installment_state(
            installment,
            list(installment.payments),
            today=santiago_today(moment),
            cadence_days=cadence,
            tolerance_uf=tolerance,
        )
        if state.state != "paid":
            return 0
        items = open_items_for_installment(
            db, installment.id, broker_id=installment.broker_id, exclude_intent_id=exclude_intent_id
        )
        reported = 0
        for item in items:
            intent = item.intent
            if item.id in provider_paid_item_ids(db, intent):
                continue
            acct = db.get(ProviderAccount, intent.provider_account_id) if intent.provider_account_id else None
            if acct is not None and intent.provider_mandate_id:
                try:
                    get_provider(_val(acct.provider) or "forpay").set_installment_state(
                        acct, [item.external_installment_id], "paid"
                    )
                    mark_charge_paid_outside(db, intent, item, now=moment)
                    reported += 1
                except PaymentProviderError:
                    logger.warning("mark_paid_outside: provider refused item %s", item.id)
                    continue
            if not provider_paid_item_ids(db, intent) and _all_settled(db, intent, today=santiago_today(moment)):
                intent.status = IntentStatus.CANCELLED
        db.flush()
        return reported
    except Exception:  # noqa: BLE001 - callers (payment writes) must never fail on this
        logger.exception("mark_paid_outside failed for installment %s", getattr(installment, "id", None))
        return 0


def on_balance_change(
    db: Session,
    installment: CollectionInstallment,
    *,
    now: datetime | None = None,
) -> int:
    """Supersede open links that no longer charge the cuota's balance. Never raises.

    An intent item freezes ``amount_uf`` = the balance when the link is minted.
    When a non-provider write (manual / evidence / legacy partial payment, a
    reversal, a re-priced or voided cuota) leaves the cuota UNPAID with another
    balance, the open link would charge the old figure — an overcharge after a
    partial payment. Every open intent with such a stale, not-yet-charged item
    is annulled at the provider and cancelled (:func:`_annul_and_cancel`), so
    the payer has to use a fresh link at the current balance. A PAID cuota is
    :func:`mark_paid_outside`'s job. Provider failures are logged, not raised
    (``payments.sync`` reconciles). Returns how many intents were superseded.
    """
    moment = as_aware(now) if now is not None else utcnow()
    try:
        cadence, tolerance = broker_collection_params(db, installment.broker_id)
        state = compute_installment_state(
            installment,
            list(installment.payments),
            today=santiago_today(moment),
            cadence_days=cadence,
            tolerance_uf=tolerance,
        )
        if state.state == "paid":
            return 0
        balance = Decimal("0") if state.state == "voided" else Decimal(str(state.balance_uf))
        items = open_items_for_installment(db, installment.id, broker_id=installment.broker_id)
        superseded = 0
        seen: set[int] = set()
        for item in items:
            intent = item.intent
            if intent.id in seen or item.id in provider_paid_item_ids(db, intent):
                continue
            if abs(Decimal(str(item.amount_uf)) - balance) <= AMOUNT_EPSILON_UF:
                continue
            seen.add(intent.id)
            try:
                _annul_and_cancel(db, intent, now=moment)
                superseded += 1
            except CheckoutProviderError:
                logger.warning("on_balance_change: provider refused to annul intent %s", intent.id)
        db.flush()
        return superseded
    except Exception:  # noqa: BLE001 - callers (payment writes) must never fail on this
        logger.exception("on_balance_change failed for installment %s", getattr(installment, "id", None))
        return 0


def _all_settled(db: Session, intent: PaymentIntent, *, today) -> bool:  # noqa: ANN001
    for item in intent.items:
        inst = item.installment
        cadence, tolerance = broker_collection_params(db, inst.broker_id)
        state = compute_installment_state(
            inst, list(inst.payments), today=today, cadence_days=cadence, tolerance_uf=tolerance
        )
        if state.state not in ("paid", "voided"):
            return False
    return True


__all__ = [
    "MAX_CHECKOUT_ITEMS",
    "SPOT_CHANNELS",
    "CheckoutError",
    "CheckoutProviderError",
    "CheckoutResult",
    "cancel_intent",
    "create_checkout_for_share",
    "create_intent_for_installments",
    "insurer_label",
    "mark_paid_outside",
    "on_balance_change",
    "resolve_payer",
]
