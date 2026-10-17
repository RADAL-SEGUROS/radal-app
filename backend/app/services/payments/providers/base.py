"""The payment-provider seam (v12, docs/v12/03-collections-forpay.md §3-§5).

Phase 1 writes the CONTRACT: the :class:`PaymentProvider` protocol, the value
objects that cross it, the payload-signature helpers and the shared webhook
parser. Lane B3 writes the Forpay implementation
(``app/services/payments/providers/forpay.py``, class ``ForpayProvider``);
tests use :class:`app.services.payments.providers.fake.FakePaymentProvider`.

``acct`` is always a ``provider_account`` row. Its secrets live encrypted in
``secret_ciphertext`` as one JSON bundle (``{api_key, password, hmac_key,
public_key_pem, jwe_alg?, jwe_enc?, signature_fields?}``) — read it with
:func:`account_secret`, never log it.

Forpay's webhook signature is ``Base64(HMAC-SHA256(hmac_key, concat(fields)))``
over a FIXED, type-specific field order that Forpay has not fully documented,
so the order is DATA: :data:`DEFAULT_SIGNATURE_FIELDS` per event type,
overridable per account (``signature_fields`` in the secret bundle, either a
list or a ``{event_type: [...]}`` map). Field paths are dotted
(``installment.state.id``); a missing field concatenates as ``""``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from app.core.crypto import SecretDecryptionError, decrypt_secret

if TYPE_CHECKING:
    from app.models.provider import ProviderAccount


class PaymentProviderError(RuntimeError):
    """A provider call failed (network, auth, 4xx/5xx). Message never carries secrets."""


class ProviderUnavailable(PaymentProviderError):
    """No implementation is registered/importable for the requested provider name."""


# --- value objects -----------------------------------------------------------

@dataclass(frozen=True)
class PayerData:
    """The person paying (Forpay ``POST /user``: userId, RUT, name, lastName, email)."""

    rut: str
    name: str
    email: str
    last_name: str = ""
    phone: str | None = None
    #: Our stable customer id at the provider (defaults to the RUT when blank).
    user_id: str | None = None


@dataclass(frozen=True)
class CheckoutItem:
    """One cuota inside a checkout / mandate (amounts in UF, ``currencyConvert TO_DAY``)."""

    installment_id: int
    external_installment_id: str
    amount_uf: Decimal
    due_date: date | None
    store_commerce_code: str | None = None
    description: str = ""


@dataclass(frozen=True)
class CheckoutSession:
    """A created spot checkout (Forpay ONEPAY mandate)."""

    provider_mandate_id: str
    checkout_url: str
    external_id: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MandateSession:
    """A created recurring mandate (Forpay DYNAMIC) and its enrolment Magic Link."""

    provider_mandate_id: str
    magic_link_url: str
    external_id: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReceivableIn:
    """One installment as reported by ``GET /receivables`` (+ payment / payout)."""

    provider_installment_id: str
    external_installment_id: str | None
    state: int | None
    state_name: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    provider_mandate_id: str | None = None
    payment_id: str | None = None
    authorization_code: str | None = None
    payment_method: str | None = None
    paid_at: datetime | None = None
    #: ``payment.convert`` — what was actually charged (usually CLP) and when.
    convert_amount: Decimal | None = None
    convert_currency: str | None = None
    convert_date: date | None = None
    #: ``payout{dispersionId, creditedAmount, companyDepositDate, ...}`` verbatim.
    payout: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderEventIn:
    """A parsed webhook. ``dedup_key`` = ``installment.internalId + ":" + state.id``."""

    event_type: str
    dedup_key: str
    #: Forpay's own installment id (``installment.internalId``).
    provider_installment_id: str | None
    #: OUR external id (``installment.id`` = ``payment_intent_item.external_installment_id``).
    external_installment_id: str | None
    state: int | None
    state_name: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    provider_mandate_id: str | None = None
    customer_user_id: str | None = None
    payment_id: str | None = None
    authorization_code: str | None = None
    payment_method: str | None = None
    paid_at: datetime | None = None
    convert_amount: Decimal | None = None
    convert_currency: str | None = None
    convert_date: date | None = None
    raw: dict[str, Any] = field(default_factory=dict)


#: Forpay installment states (03 §3).
STATE_PENDING = 1
STATE_PAID = 2
STATE_ANNULLED = 6
STATE_IN_PROCESS = 9
STATE_RENEGOTIATED = 10
STATE_FINISHED = 11
STATE_REVERSED = 12
STATE_IN_VALIDATION = 15
STATE_REJECTED = -99


# --- the protocol ------------------------------------------------------------

@runtime_checkable
class PaymentProvider(Protocol):
    name: str

    def ensure_customer(self, acct: "ProviderAccount", payer: PayerData) -> str: ...

    def create_spot_checkout(
        self,
        acct: "ProviderAccount",
        payer: PayerData,
        items: list[CheckoutItem],
        *,
        external_id: str,
        return_url: str,
        webhook_url: str,
    ) -> CheckoutSession: ...

    def create_recurring_mandate(
        self,
        acct: "ProviderAccount",
        payer: PayerData,
        items: list[CheckoutItem],
        *,
        external_id: str,
        methods: list[str],
        webhook_urls: dict[str, str],
    ) -> MandateSession: ...

    def set_installment_state(
        self,
        acct: "ProviderAccount",
        external_ids: list[str],
        state: Literal["paid", "annulled"],
    ) -> None: ...

    def search_receivables(self, acct: "ProviderAccount", **filters: Any) -> list[ReceivableIn]: ...

    def verify_webhook(self, acct: "ProviderAccount", payload: dict[str, Any]) -> bool: ...

    def parse_webhook(self, acct: "ProviderAccount", payload: dict[str, Any]) -> ProviderEventIn: ...


# --- secrets -----------------------------------------------------------------

def account_secret(acct: "ProviderAccount") -> dict[str, Any]:
    """The decrypted secret bundle of an account (``{}`` when none / unreadable)."""
    token = getattr(acct, "secret_ciphertext", None)
    if not token:
        return {}
    try:
        value = decrypt_secret(token)
    except SecretDecryptionError:
        return {}
    return value if isinstance(value, dict) else {}


# --- payload signature -------------------------------------------------------

#: Default field order per webhook type. ``payment`` appends the payment fields
#: only when the installment state is 2 (pagada).
DEFAULT_SIGNATURE_FIELDS: dict[str, list[str]] = {
    "payment": [
        "businessPartner.businessPartnerId",
        "customer.userId",
        "product.id",
        "installment.id",
        "installment.state.id",
        "installment.amount",
    ],
    "activation": [
        "businessPartner.businessPartnerId",
        "customer.userId",
        "product.id",
    ],
}
PAID_SIGNATURE_FIELDS: list[str] = ["payment.id", "payment.authorizationCode", "payment.amount"]


def _path(payload: dict[str, Any], dotted: str) -> Any:
    node: Any = payload
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _field_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _state_id(payload: dict[str, Any]) -> int | None:
    raw = _path(payload, "installment.state.id")
    try:
        return int(raw) if raw is not None and str(raw).strip() != "" else None
    except (TypeError, ValueError):
        return None


def infer_event_type(payload: dict[str, Any]) -> str:
    """``payment`` when the body carries an installment, else ``activation``.

    The webhook route also knows the kind from its ``kind=`` query param; a
    provider may prefer that. This is the fallback when only the body is known.
    """
    if isinstance(payload, dict) and isinstance(payload.get("installment"), dict):
        return "payment"
    return "activation"


def signature_fields_for(
    payload: dict[str, Any],
    *,
    event_type: str = "payment",
    override: list[str] | dict[str, list[str]] | None = None,
) -> list[str]:
    """The field order to sign for this payload (account override wins)."""
    if isinstance(override, dict):
        override = override.get(event_type)
    if override:
        return list(override)
    fields = list(DEFAULT_SIGNATURE_FIELDS.get(event_type, DEFAULT_SIGNATURE_FIELDS["payment"]))
    if event_type == "payment" and _state_id(payload) == STATE_PAID:
        fields += PAID_SIGNATURE_FIELDS
    return fields


def signed_fields_digest(payload: dict[str, Any], fields: list[str]) -> str:
    """SHA-256 over the values of the SIGNED fields only (an inbox dedup key).

    Two bodies that agree on every signed field are the same event for us,
    whatever an unsigned field says.
    """
    message = "\x1f".join(f"{f}={_field_text(_path(payload, f))}" for f in fields)
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def compute_payload_signature(hmac_key: str, payload: dict[str, Any], fields: list[str]) -> str:
    """``Base64(HMAC-SHA256(hmac_key, concat(fields)))``."""
    message = "".join(_field_text(_path(payload, f)) for f in fields)
    digest = hmac.new(hmac_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_payload_signature(
    hmac_key: str | None,
    payload: dict[str, Any],
    *,
    event_type: str = "payment",
    override: list[str] | dict[str, list[str]] | None = None,
) -> bool:
    """Constant-time check of ``payload["signature"]``. No key -> False (fail closed)."""
    if not hmac_key or not isinstance(payload, dict):
        return False
    given = payload.get("signature")
    if not isinstance(given, str) or not given:
        return False
    fields = signature_fields_for(payload, event_type=event_type, override=override)
    expected = compute_payload_signature(hmac_key, payload, fields)
    return hmac.compare_digest(expected.encode("ascii"), given.encode("ascii", "replace"))


def sign_payload(
    hmac_key: str,
    payload: dict[str, Any],
    *,
    event_type: str = "payment",
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Return a copy of ``payload`` carrying a valid ``signature`` (tests, fakes)."""
    signed = dict(payload)
    signed.pop("signature", None)
    order = fields or signature_fields_for(signed, event_type=event_type)
    signed["signature"] = compute_payload_signature(hmac_key, signed, order)
    return signed


# --- shared webhook parsing --------------------------------------------------

def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _text(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def parse_installment_webhook(payload: dict[str, Any], *, event_type: str = "payment") -> ProviderEventIn:
    """Parse Forpay's installment webhook shape (shared by Forpay and the fake)."""
    installment = payload.get("installment") or {}
    state = installment.get("state") or {}
    payment = payload.get("payment") or {}
    convert = payment.get("convert") or {}
    product = payload.get("product") or {}
    customer = payload.get("customer") or {}
    state_id = _state_id(payload)
    internal = _text(installment.get("internalId"))
    return ProviderEventIn(
        event_type=event_type,
        # Built from SIGNED fields only (``installment.id`` is OUR id and is
        # signed; ``internalId`` is not): a replay with a mutated unsigned
        # field must land on the same inbox key and be dropped as a duplicate.
        dedup_key=f"{_text(installment.get('id')) or ''}:{'' if state_id is None else state_id}",
        provider_installment_id=internal,
        external_installment_id=_text(installment.get("id")),
        state=state_id,
        state_name=_text(state.get("name")),
        amount=_decimal(installment.get("amount")),
        currency=_text(installment.get("currency")),
        provider_mandate_id=_text(product.get("internalId") or product.get("id")),
        customer_user_id=_text(customer.get("userId")),
        payment_id=_text(payment.get("id")),
        authorization_code=_text(payment.get("authorizationCode")),
        payment_method=_text(payment.get("method")),
        paid_at=_datetime(payment.get("dateTime")),
        convert_amount=_decimal(convert.get("amount")),
        convert_currency=_text(convert.get("currency")),
        convert_date=_date(convert.get("date")),
        raw=payload,
    )


__all__ = [
    "DEFAULT_SIGNATURE_FIELDS",
    "PAID_SIGNATURE_FIELDS",
    "STATE_ANNULLED",
    "STATE_FINISHED",
    "STATE_IN_PROCESS",
    "STATE_IN_VALIDATION",
    "STATE_PAID",
    "STATE_PENDING",
    "STATE_REJECTED",
    "STATE_RENEGOTIATED",
    "STATE_REVERSED",
    "CheckoutItem",
    "CheckoutSession",
    "MandateSession",
    "PayerData",
    "PaymentProvider",
    "PaymentProviderError",
    "ProviderEventIn",
    "ProviderUnavailable",
    "ReceivableIn",
    "account_secret",
    "compute_payload_signature",
    "infer_event_type",
    "parse_installment_webhook",
    "sign_payload",
    "signature_fields_for",
    "signed_fields_digest",
    "verify_payload_signature",
]
