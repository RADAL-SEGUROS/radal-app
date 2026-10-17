"""Shared v12 test helpers and fakes (docs/v12/09-testing.md). Hermetic: no network.

The fakes themselves live in the app (so a local demo can use them too) and are
re-exported here; the ``fake_transport`` / ``fake_provider`` fixtures in
``tests/conftest.py`` install them behind ``get_transport()`` /
``get_provider()``.

Builders (every one adds + flushes; the CALLER commits):

* :func:`make_policy_with_plan` — an active policy + a balanced collection plan
  (Σ cuotas == policy gross, last cuota absorbs rounding), monthly due dates;
* :func:`make_payment` — one payment row against a cuota;
* :func:`make_provider_account` — a Forpay account with ENCRYPTED secrets
  (``app.core.crypto``) and a webhook key/secret, plus :func:`webhook_sig`;
* :func:`make_native_channel` — an insurer payment channel on an account;
* :func:`make_collection_settings`, :func:`make_insurer_profile`,
  :func:`make_broker_override`, :func:`make_party`;
* :func:`seed_catalog` — the closed ramo catalogue (same as the ``catalog``
  fixture).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.crypto import encrypt_secret
from app.db.seed_v12 import seed_catalog  # noqa: F401  (re-export)
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.collection_settings import CollectionSettings
from app.models.enums import (
    ChannelStatus,
    CollectionLevelPreference,
    CollectionPlanSource,
    EvidenceLevel,
    InstallmentStatus,
    PartyRole,
    PaymentChannel,
    PaymentCurrency,
    PaymentMode,
    PaymentProviderName,
    PaymentSource,
    PaymentStatus,
    ProviderAccountStatus,
    ProviderEnvironment,
    ProviderOwner,
)
from app.models.insurer_collection import (
    BrokerInsurerCollection,
    InsurerCollectionProfile,
    InsurerPaymentChannel,
)
from app.models.payment import Payment
from app.models.policy_party import PolicyParty
from app.models.base_class import utcnow
from app.models.provider import ProviderAccount
from app.services.mail.fake import FakeTransport, build_message  # noqa: F401  (re-export)
from app.services.payments.providers.fake import FakePaymentProvider  # noqa: F401  (re-export)
from tests.conftest import Tenant, make_policy

UF4 = Decimal("0.0001")

TEST_HMAC_KEY = "test-forpay-hmac-key"
TEST_WEBHOOK_SECRET = "test-webhook-secret"


def _add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    # Clamp to the month's last day (31 Jan + 1 month -> 28/29 Feb).
    for candidate in (day.day, 30, 29, 28):
        try:
            return date(year, month, min(day.day, candidate))
        except ValueError:
            continue
    raise ValueError("unreachable")  # pragma: no cover


def split_amount(total: Decimal, n: int) -> list[Decimal]:
    """``n`` UF amounts summing EXACTLY to ``total``; the last absorbs rounding."""
    if n < 1:
        raise ValueError("n must be >= 1")
    each = (total / n).quantize(UF4, rounding=ROUND_HALF_UP)
    amounts = [each] * (n - 1)
    amounts.append(total - each * (n - 1))
    return amounts


def make_policy_with_plan(
    db: Session,
    tenant: Tenant,
    insurer,  # noqa: ANN001
    *,
    n_installments: int = 3,
    start: date = date(2026, 1, 5),
    policy_number: str | None = None,
    payment_mode: PaymentMode = PaymentMode.COUPON_BOOK,
    level_preference: CollectionLevelPreference = CollectionLevelPreference.AUTO,
    statuses: list[InstallmentStatus] | None = None,
    **policy_overrides: Any,
) -> tuple[Any, CollectionPlan]:
    """An active policy of ``tenant`` + a plan whose cuotas sum to its gross.

    Due dates are monthly from ``start``. Returns ``(policy, plan)``; the plan's
    ``installments`` are loaded and ordered by number.
    """
    if policy_number is not None:
        policy_overrides["policy_number"] = policy_number
    policy_overrides.setdefault("start_date", start)
    policy_overrides.setdefault("end_date", _add_months(start, 12))
    policy = make_policy(db, tenant, insurer, **policy_overrides)
    gross = Decimal(str(policy.total_premium_uf or "0"))
    plan = CollectionPlan(
        broker_id=tenant.broker_id,
        policy_id=policy.id,
        case_file_id=policy.case_file_id,
        plan_number=f"PP-{policy.id}",
        payment_mode=payment_mode,
        installment_count=n_installments,
        total_premium_uf=gross,
        source=CollectionPlanSource.MANUAL,
        level_preference=level_preference,
    )
    for index, amount in enumerate(split_amount(gross, n_installments), start=1):
        plan.installments.append(
            CollectionInstallment(
                broker_id=tenant.broker_id,
                number=index,
                coupon_number=f"{index:03d}",
                due_date=_add_months(start, index - 1),
                gross_amount_uf=amount,
                status=(statuses[index - 1] if statuses else InstallmentStatus.PENDING),
            )
        )
    db.add(plan)
    db.flush()
    return policy, plan


def make_payment(
    db: Session,
    installment: CollectionInstallment,
    *,
    amount_uf: Decimal | str | None = None,
    paid_on: date = date(2026, 1, 5),
    evidence: EvidenceLevel = EvidenceLevel.DECLARED,
    source: PaymentSource = PaymentSource.MANUAL,
    status: PaymentStatus = PaymentStatus.ACTIVE,
    receipt_document_id: int | None = None,
    **fields: Any,
) -> Payment:
    """One UF payment (the full cuota by default)."""
    amount = Decimal(str(amount_uf)) if amount_uf is not None else Decimal(
        str(installment.gross_amount_uf)
    )
    payment = Payment(
        broker_id=installment.broker_id,
        installment_id=installment.id,
        amount=amount,
        currency=PaymentCurrency.UF,
        amount_uf=amount,
        paid_on=paid_on,
        source=source,
        evidence_level=evidence,
        status=status,
        receipt_document_id=receipt_document_id,
        **fields,
    )
    db.add(payment)
    db.flush()
    db.refresh(installment)
    return payment


def webhook_sig(webhook_secret: str, webhook_key: str, provider: str = "forpay") -> str:
    """The ``sig`` query param of the generic webhook (03 §5)."""
    return hmac.new(
        webhook_secret.encode("utf-8"), f"{provider}:{webhook_key}".encode("utf-8"), hashlib.sha256
    ).hexdigest()


def make_provider_account(
    db: Session,
    *,
    owner: ProviderOwner = ProviderOwner.RADAL_MALL,
    insurer_id: int | None = None,
    status: ProviderAccountStatus = ProviderAccountStatus.ACTIVE,
    label: str = "Radal mall (test)",
    hmac_key: str = TEST_HMAC_KEY,
    webhook_secret: str = TEST_WEBHOOK_SECRET,
    webhook_key: str | None = None,
    secret_extra: dict[str, Any] | None = None,
    base_url: str = "http://127.0.0.1:9/no-forpay-in-tests",
    verified: bool = True,
) -> ProviderAccount:
    """A Forpay provider account whose secrets are really encrypted.

    ``verified`` (default) stamps ``last_verified_at``: since review v12 only an
    ACTIVE account that passed *Verificar* serves native collection.
    """
    secret = {
        "api_key": "test-api-key",
        "password": "test-password",
        "hmac_key": hmac_key,
        "public_key_pem": "",
        **(secret_extra or {}),
    }
    account = ProviderAccount(
        provider=PaymentProviderName.FORPAY,
        label=label,
        owner=owner,
        insurer_id=insurer_id,
        environment=ProviderEnvironment.SANDBOX,
        base_url=base_url,
        commerce="test-commerce",
        channel_header="test-channel",
        process_id="WHITE_LABEL",
        username="test-user",
        secret_ciphertext=encrypt_secret(secret),
        webhook_key=webhook_key or secrets.token_urlsafe(18),
        webhook_secret_ciphertext=encrypt_secret(webhook_secret),
        status=status,
        last_verified_at=utcnow() if verified else None,
    )
    db.add(account)
    db.flush()
    return account


def make_native_channel(
    db: Session,
    insurer,  # noqa: ANN001
    account: ProviderAccount | None,
    *,
    channel: PaymentChannel = PaymentChannel.WEBPAY,
    status: ChannelStatus = ChannelStatus.ACTIVE,
    store_commerce_code: str | None = "597000000001",
) -> InsurerPaymentChannel:
    row = InsurerPaymentChannel(
        insurer_id=insurer.id,
        channel=channel,
        provider_account_id=account.id if account is not None else None,
        store_commerce_code=store_commerce_code,
        status=status,
    )
    db.add(row)
    db.flush()
    return row


def make_collection_settings(db: Session, broker_id: int, **fields: Any) -> CollectionSettings:
    row = CollectionSettings(broker_id=broker_id, **fields)
    db.add(row)
    db.flush()
    return row


def make_insurer_profile(db: Session, insurer, **fields: Any) -> InsurerCollectionProfile:  # noqa: ANN001
    row = db.get(InsurerCollectionProfile, insurer.id)
    if row is None:
        row = InsurerCollectionProfile(insurer_id=insurer.id)
        db.add(row)
    for key, value in fields.items():
        setattr(row, key, value)
    db.flush()
    return row


def make_broker_override(
    db: Session, broker_id: int, insurer, **fields: Any  # noqa: ANN001
) -> BrokerInsurerCollection:
    row = BrokerInsurerCollection(broker_id=broker_id, insurer_id=insurer.id, **fields)
    db.add(row)
    db.flush()
    return row


def make_party(
    db: Session,
    policy,  # noqa: ANN001
    *,
    role: PartyRole = PartyRole.PAYER,
    name: str = "Pagador de prueba",
    email: str | None = "pagador@example.test",
    **fields: Any,
) -> PolicyParty:
    row = PolicyParty(
        broker_id=policy.broker_id, policy_id=policy.id, role=role, name=name, email=email, **fields
    )
    db.add(row)
    db.flush()
    db.refresh(policy)
    return row


__all__ = [
    "TEST_HMAC_KEY",
    "TEST_WEBHOOK_SECRET",
    "FakePaymentProvider",
    "FakeTransport",
    "build_message",
    "make_broker_override",
    "make_collection_settings",
    "make_insurer_profile",
    "make_native_channel",
    "make_party",
    "make_payment",
    "make_policy_with_plan",
    "make_provider_account",
    "seed_catalog",
    "split_amount",
    "webhook_sig",
]
