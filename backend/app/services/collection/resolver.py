"""Which collection level applies to a cuota right now (v12, 03 §2).

Three levels per cuota (product decision §1.3):

* ``record``   — the broker only records what happened (chosen on the plan);
* ``assisted`` — Radal reminds the payer and asks the insurer. ALWAYS available,
                 whatever the insurer's Forpay status;
* ``native``   — the payer pays online through Radal + Forpay.

Native requires, all at once: plan preference ``auto``; the broker's
``collection_settings.native_collection_enabled`` (default True when the row
does not exist yet); no broker override opting the lead insurer out
(``broker_insurer_collection.native_enabled = false``); and at least one
``insurer_payment_channel`` of the lead insurer with ``status = active`` served
by an ``active`` ``provider_account`` that has passed *Verificar* (and whose last
verify did not fail — :func:`provider_account_verified`). The lead insurer is the coinsurance
leader when the policy is coinsured, else ``policy.insurer_id`` (team R18).

Never exposes ``onboarding_status`` / ``expected_live_on``: a broker does not
learn an insurer is "on its way" — it just upgrades when it is live.

``reason`` is Spanish and shown verbatim (like the stage-guard reasons).
``missing`` names what would make assisted collection work better, as stable
keys the frontend translates: ``payer_contact``, ``insurer_contact``,
``templates_unapproved``, ``mail_sender`` (and ``plan``, reserved for a policy
without a plan — a cuota always has one).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sqlalchemy import or_, select

from app.models.collection_settings import CollectionSettings
from app.models.enums import (
    ChannelStatus,
    CollectionLevelPreference,
    MailAccountStatus,
    MailSender,
    PartyRole,
    ProviderAccountStatus,
)
from app.models.insurer import Insurer
from app.models.insurer_collection import (
    BrokerInsurerCollection,
    InsurerCollectionProfile,
    InsurerPaymentChannel,
)
from app.models.mail import MailAccount
from app.models.provider import ProviderAccount

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.collection import CollectionInstallment
    from app.models.policy import Policy

Level = Literal["record", "assisted", "native"]

REASON_RECORD = "Cobro solo de registro (elegido por la corredora)"
REASON_NATIVE = "{insurer} cobra en línea con Radal"
REASON_ASSISTED = "Cobro asistido: Radal recuerda al pagador y consulta a {insurer}"
REASON_FOREIGN_CURRENCY = (
    "Cobro asistido: la cuota está en {currency} y el pago en línea solo cobra en UF; "
    "Radal recuerda al pagador y consulta a {insurer}"
)


@dataclass(frozen=True)
class CollectionMode:
    level: Level
    reason: str
    channels: tuple[str, ...]
    missing: tuple[str, ...]
    #: The insurer the level was resolved against (coinsurance leader or the
    #: policy's insurer). Not part of the API response contract; handy for
    #: grouping checkouts by insurer.
    insurer_id: int | None = None


def _val(value) -> str | None:  # noqa: ANN001
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def lead_insurer_id(policy: "Policy") -> int | None:
    """The coinsurance leader when coinsured, else ``policy.insurer_id`` (R18)."""
    for share in getattr(policy, "coinsurance_shares", None) or []:
        if share.is_leader:
            return share.insurer_id
    return getattr(policy, "insurer_id", None)


def provider_account_verified(acct: ProviderAccount | None) -> bool:
    """The account passed *Verificar* and its LAST verify did not fail.

    ``verify`` stamps ``last_verified_at`` and clears ``last_error`` on success,
    and records ``last_error`` (keeping the older ``last_verified_at``) on
    failure — so "verified now" is ``last_verified_at`` set AND no
    ``last_error`` (review v12 #13/#17: a failed *Verificar* left the card
    "Activa · Verificada" and the cuotas resolving to *En línea*).
    """
    return bool(
        acct is not None
        and acct.last_verified_at is not None
        and not (acct.last_error or "").strip()
    )


def provider_account_usable(acct: ProviderAccount | None) -> bool:
    """Native collection may ride on this account: ``active`` AND verified."""
    return bool(
        acct is not None
        and _val(acct.status) == ProviderAccountStatus.ACTIVE.value
        and provider_account_verified(acct)
    )


def verify_status(acct: ProviderAccount) -> str:
    """``ok`` | ``failed`` | ``never`` — the last *Verificar* result."""
    if (acct.last_error or "").strip():
        return "failed"
    if acct.last_verified_at is not None:
        return "ok"
    return "never"


def active_native_channels(db: "Session", insurer_id: int | None) -> list[InsurerPaymentChannel]:
    """The lead insurer's ACTIVE channels served by an ACTIVE **and verified**
    provider account (:func:`provider_account_usable`). Forpay is paused by
    decision: an account that never passed *Verificar* — or whose last verify
    failed — never makes a cuota *En línea*."""
    if insurer_id is None:
        return []
    stmt = (
        select(InsurerPaymentChannel)
        .join(ProviderAccount, ProviderAccount.id == InsurerPaymentChannel.provider_account_id)
        .where(
            InsurerPaymentChannel.insurer_id == insurer_id,
            InsurerPaymentChannel.status == ChannelStatus.ACTIVE,
            ProviderAccount.status == ProviderAccountStatus.ACTIVE,
            ProviderAccount.last_verified_at.is_not(None),
            or_(ProviderAccount.last_error.is_(None), ProviderAccount.last_error == ""),
        )
        .order_by(InsurerPaymentChannel.id)
    )
    return list(db.scalars(stmt))


def payer_contact_email(policy: "Policy") -> str | None:
    """Who receives payer reminders: payer, else policyholder (parties).

    When the policy has NO payer/policyholder party at all, the policyholder
    defaults to the insured (team rule), i.e. the client's contact e-mail or
    the canonical insured's e-mail.
    """
    parties = list(getattr(policy, "parties", None) or [])
    by_role = {PartyRole.PAYER.value: [], PartyRole.POLICYHOLDER.value: []}
    for party in parties:
        role = _val(party.role)
        if role in by_role:
            by_role[role].append(party)
    for role in (PartyRole.PAYER.value, PartyRole.POLICYHOLDER.value):
        for party in by_role[role]:
            if party.email and getattr(party, "notify_collections", True):
                return party.email
    if by_role[PartyRole.PAYER.value] or by_role[PartyRole.POLICYHOLDER.value]:
        return None
    client = getattr(policy, "client", None)
    if client is None:
        return None
    if getattr(client, "contact_email", None):
        return client.contact_email
    insured = getattr(client, "insured", None)
    return getattr(insured, "email", None) or None


def insurer_contact_email(db: "Session", *, broker_id: int, insurer_id: int | None) -> str | None:
    """Where status inquiries go: broker override, else Radal's profile."""
    if insurer_id is None:
        return None
    override = db.scalar(
        select(BrokerInsurerCollection).where(
            BrokerInsurerCollection.broker_id == broker_id,
            BrokerInsurerCollection.insurer_id == insurer_id,
        )
    )
    if override is not None and override.collections_email:
        return override.collections_email
    profile = db.get(InsurerCollectionProfile, insurer_id)
    return profile.collections_email if profile is not None and profile.collections_email else None


def _insurer_label(db: "Session", insurer_id: int | None) -> str:
    insurer = db.get(Insurer, insurer_id) if insurer_id is not None else None
    if insurer is None:
        return "la compañía"
    return insurer.trade_name or insurer.legal_name


def _missing(
    db: "Session",
    *,
    broker_id: int,
    policy: "Policy",
    insurer_id: int | None,
    settings_row: CollectionSettings | None,
) -> tuple[str, ...]:
    missing: list[str] = []
    if not payer_contact_email(policy):
        missing.append("payer_contact")
    if not insurer_contact_email(db, broker_id=broker_id, insurer_id=insurer_id):
        missing.append("insurer_contact")
    if settings_row is None or settings_row.notices_approved_at is None:
        missing.append("templates_unapproved")
    if (
        settings_row is not None
        and _val(settings_row.default_sender) == MailSender.BROKER_ACCOUNT.value
    ):
        account = (
            db.get(MailAccount, settings_row.sender_mail_account_id)
            if settings_row.sender_mail_account_id
            else None
        )
        if (
            account is None
            or account.broker_id != broker_id
            or _val(account.status) != MailAccountStatus.CONNECTED.value
            or not account.send_enabled
        ):
            missing.append("mail_sender")
    return tuple(missing)


def resolve_collection_mode(
    db: "Session", installment: "CollectionInstallment", *, broker_id: int
) -> CollectionMode:
    """The level, its Spanish reason, usable native channels and what is missing.

    Tenant guard: an installment of another broker raises ``LookupError`` — the
    router has already 404'd by then, so reaching it is a programming error.
    """
    if installment.broker_id != broker_id:
        raise LookupError("installment not found for this broker")
    plan = installment.collection_plan
    policy = plan.policy
    insurer_id = lead_insurer_id(policy)
    preference = _val(plan.level_preference) or CollectionLevelPreference.AUTO.value

    if preference == CollectionLevelPreference.RECORD.value:
        return CollectionMode(
            level="record", reason=REASON_RECORD, channels=(), missing=(), insurer_id=insurer_id
        )

    settings_row = db.scalar(
        select(CollectionSettings).where(CollectionSettings.broker_id == broker_id)
    )
    label = _insurer_label(db, insurer_id)
    missing = _missing(
        db, broker_id=broker_id, policy=policy, insurer_id=insurer_id, settings_row=settings_row
    )

    if preference == CollectionLevelPreference.AUTO.value:
        native_on = settings_row is None or bool(settings_row.native_collection_enabled)
        override = (
            db.scalar(
                select(BrokerInsurerCollection).where(
                    BrokerInsurerCollection.broker_id == broker_id,
                    BrokerInsurerCollection.insurer_id == insurer_id,
                )
            )
            if insurer_id is not None
            else None
        )
        opted_out = override is not None and override.native_enabled is False
        currency = _val(getattr(installment, "currency", None)) or "UF"
        if currency != "UF":
            # The provider charges UF only (``currencyId: "UF"``): a dollar
            # cuota would be billed its USD balance as UF. Never native.
            return CollectionMode(
                level="assisted",
                reason=REASON_FOREIGN_CURRENCY.format(currency=currency, insurer=label),
                channels=(),
                missing=missing,
                insurer_id=insurer_id,
            )
        if native_on and not opted_out:
            channels = active_native_channels(db, insurer_id)
            if channels:
                names = tuple(dict.fromkeys(_val(c.channel) for c in channels))
                return CollectionMode(
                    level="native",
                    reason=REASON_NATIVE.format(insurer=label),
                    channels=names,
                    missing=missing,
                    insurer_id=insurer_id,
                )

    return CollectionMode(
        level="assisted",
        reason=REASON_ASSISTED.format(insurer=label),
        channels=(),
        missing=missing,
        insurer_id=insurer_id,
    )


__all__ = [
    "CollectionMode",
    "REASON_ASSISTED",
    "REASON_FOREIGN_CURRENCY",
    "REASON_NATIVE",
    "REASON_RECORD",
    "active_native_channels",
    "provider_account_usable",
    "provider_account_verified",
    "verify_status",
    "insurer_contact_email",
    "lead_insurer_id",
    "payer_contact_email",
    "resolve_collection_mode",
]
