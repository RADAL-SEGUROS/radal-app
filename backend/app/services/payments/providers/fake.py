"""An in-memory payment provider for tests and local demos (v12). No network.

* records every call in :attr:`FakePaymentProvider.calls` as ``(method, kwargs)``;
* returns DETERMINISTIC ids and urls derived from the caller's external id
  (``https://pay.fake-provider.test/checkout/<external_id>``);
* serves scripted receivables (:meth:`add_receivable`);
* verifies/parses webhooks with the SAME helpers the real provider uses, so a
  payload built by :meth:`build_webhook_payload` exercises the real signature
  path end to end.

Install it with the ``fake_provider`` fixture (tests/conftest.py), which makes
``get_provider(<any name>)`` return it.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from app.services.payments.providers.base import (
    STATE_PAID,
    CheckoutItem,
    CheckoutSession,
    MandateSession,
    PayerData,
    PaymentProviderError,
    ProviderEventIn,
    ReceivableIn,
    account_secret,
    infer_event_type,
    parse_installment_webhook,
    sign_payload,
    verify_payload_signature,
)

FAKE_PAY_BASE = "https://pay.fake-provider.test"


class FakePaymentProvider:
    """Deterministic stand-in for :class:`PaymentProvider`."""

    def __init__(self, name: str = "forpay") -> None:
        self.name = name
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.receivables: list[ReceivableIn] = []
        self.installment_states: dict[str, str] = {}
        #: Set to an exception instance to make the NEXT call raise it once.
        self.fail_next: Exception | None = None

    # --- bookkeeping ---------------------------------------------------------
    def _record(self, method: str, **kwargs: Any) -> None:
        self.calls.append((method, kwargs))
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise exc

    def calls_to(self, method: str) -> list[dict[str, Any]]:
        return [kwargs for name, kwargs in self.calls if name == method]

    # --- protocol ------------------------------------------------------------
    def ensure_customer(self, acct, payer: PayerData) -> str:  # noqa: ANN001
        self._record("ensure_customer", account_id=getattr(acct, "id", None), payer=payer)
        return payer.user_id or f"fake-customer-{payer.rut}"

    def create_spot_checkout(
        self,
        acct,  # noqa: ANN001
        payer: PayerData,
        items: list[CheckoutItem],
        *,
        external_id: str,
        return_url: str,
        webhook_url: str,
    ) -> CheckoutSession:
        self._record(
            "create_spot_checkout",
            account_id=getattr(acct, "id", None),
            payer=payer,
            items=list(items),
            external_id=external_id,
            return_url=return_url,
            webhook_url=webhook_url,
        )
        if not items:
            raise PaymentProviderError("checkout without items")
        return CheckoutSession(
            provider_mandate_id=f"fake-mandate-{external_id}",
            checkout_url=f"{FAKE_PAY_BASE}/checkout/{external_id}",
            external_id=external_id,
            raw={"fake": True, "items": [i.external_installment_id for i in items]},
        )

    def create_recurring_mandate(
        self,
        acct,  # noqa: ANN001
        payer: PayerData,
        items: list[CheckoutItem],
        *,
        external_id: str,
        methods: list[str],
        webhook_urls: dict[str, str],
    ) -> MandateSession:
        self._record(
            "create_recurring_mandate",
            account_id=getattr(acct, "id", None),
            payer=payer,
            items=list(items),
            external_id=external_id,
            methods=list(methods),
            webhook_urls=dict(webhook_urls),
        )
        return MandateSession(
            provider_mandate_id=f"fake-dynamic-{external_id}",
            magic_link_url=f"{FAKE_PAY_BASE}/enrol/{external_id}",
            external_id=external_id,
            raw={"fake": True},
        )

    def set_installment_state(
        self, acct, external_ids: list[str], state: Literal["paid", "annulled"]  # noqa: ANN001
    ) -> None:
        self._record(
            "set_installment_state",
            account_id=getattr(acct, "id", None),
            external_ids=list(external_ids),
            state=state,
        )
        for ext in external_ids:
            self.installment_states[ext] = state

    def search_receivables(self, acct, **filters: Any) -> list[ReceivableIn]:  # noqa: ANN001
        self._record("search_receivables", account_id=getattr(acct, "id", None), **filters)
        rows = list(self.receivables)
        ext = filters.get("externalInstallmentId") or filters.get("external_installment_id")
        if ext:
            wanted = set(ext) if isinstance(ext, (list, tuple, set)) else {ext}
            rows = [r for r in rows if r.external_installment_id in wanted]
        state = filters.get("installmentStatus") or filters.get("state")
        if state is not None:
            rows = [r for r in rows if r.state == int(state)]
        if filters.get("hasPayout") is True:
            rows = [r for r in rows if r.payout]
        return rows

    def verify_webhook(self, acct, payload: dict[str, Any]) -> bool:  # noqa: ANN001
        secret = account_secret(acct)
        return verify_payload_signature(
            secret.get("hmac_key"),
            payload,
            event_type=infer_event_type(payload),
            override=secret.get("signature_fields"),
        )

    def parse_webhook(self, acct, payload: dict[str, Any]) -> ProviderEventIn:  # noqa: ANN001
        return parse_installment_webhook(payload, event_type=infer_event_type(payload))

    # --- test helpers --------------------------------------------------------
    def add_receivable(self, **fields: Any) -> ReceivableIn:
        row = ReceivableIn(**fields)
        self.receivables.append(row)
        return row

    @staticmethod
    def build_webhook_payload(
        hmac_key: str,
        *,
        internal_id: str,
        external_installment_id: str,
        state: int = STATE_PAID,
        amount: Decimal | str | int = "1.5000",
        currency: str = "UF",
        product_id: str = "fake-mandate-1",
        customer_user_id: str = "fake-customer",
        business_partner_id: str = "radal",
        payment_id: str = "pay-1",
        authorization_code: str = "AUTH01",
        payment_amount: Decimal | str | int | None = None,
        paid_at: str = "2026-10-16T12:00:00-03:00",
        convert: dict[str, Any] | None = None,
        fields: list[str] | None = None,
        event_type: str = "payment",
        tamper: bool = False,
    ) -> dict[str, Any]:
        """A Forpay-shaped webhook body signed with ``hmac_key`` over ``fields``.

        ``tamper=True`` changes the amount AFTER signing (signature invalid).
        """
        payload: dict[str, Any] = {
            "businessPartner": {"businessPartnerId": business_partner_id},
            "customer": {"userId": customer_user_id, "internalUserId": f"int-{customer_user_id}"},
            "product": {"type": "MANDATE", "internalId": product_id, "id": product_id},
            "installment": {
                "state": {"id": state, "name": _STATE_NAMES.get(state, str(state))},
                "internalId": internal_id,
                "id": external_installment_id,
                "amount": str(amount),
                "currency": currency,
                "installmentDueDate": "2026-10-31",
                "metadata": {},
            },
        }
        if state == STATE_PAID:
            payload["payment"] = {
                "id": payment_id,
                "authorizationCode": authorization_code,
                "amount": str(payment_amount if payment_amount is not None else amount),
                "dateTime": paid_at,
                "method": "WEBPAY",
                "convert": convert or {"date": paid_at[:10], "amount": "60000", "currency": "PESOS"},
            }
        signed = sign_payload(hmac_key, payload, event_type=event_type, fields=fields)
        if tamper:
            signed["installment"] = {**signed["installment"], "amount": "999999"}
        return signed


_STATE_NAMES = {
    1: "PENDIENTE",
    2: "PAGADA",
    6: "ANULADA",
    9: "EN PROCESO",
    10: "REPACTADA",
    11: "FINALIZADA",
    12: "REVERSADA",
    15: "EN VALIDACION",
    -99: "RECHAZADA",
}


__all__ = ["FAKE_PAY_BASE", "FakePaymentProvider"]
