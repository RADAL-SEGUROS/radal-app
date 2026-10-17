"""Forpay White Label API client — the real :class:`PaymentProvider` (v12, 03 §3-§4).

Facts used here come from Forpay's public docs (forpaydocs.readme.io, read
2026-10-16; see docs/v12/03-collections-forpay.md §3). Where the docs leave a
shape open (the login token key, the receivables page envelope, the body of
``PUT /installments/status/change``) the code is TOLERANT on the way in and
keeps the outbound shape in ONE builder per call, so adjusting it after the
Forpay meeting is a one-function change.

* Every call carries ``x-api-key``, ``country: CL``, ``commerce``, ``channel``,
  ``requestDateTime`` (ISO), ``correlationId`` (uuid4), ``processId``
  (``WHITE_LABEL`` — Radal speaks to payers, never ``HYBRID``) and
  ``salesExecutiveId``.
* ``POST /login {username, password}`` with the password JWE-encrypted with
  Forpay's public key (``jwe_alg``/``jwe_enc`` from the account secret, default
  ``RSA-OAEP-256``/``A256GCM``) → a JWT valid 30 min, cached per account for
  :data:`TOKEN_TTL_SECONDS` (29 min) in-process. A 401 drops the cached token
  and retries once.
* Timeouts: 20 s. Secrets are never logged — log lines carry the method, the
  path and the status code only; error messages never echo a request body.

Tests inject an ``httpx.Client`` built on ``httpx.MockTransport``; no network.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Literal

import httpx

from app.services.payments.providers.base import (
    STATE_ANNULLED,
    STATE_PAID,
    CheckoutItem,
    CheckoutSession,
    MandateSession,
    PayerData,
    PaymentProviderError,
    ProviderEventIn,
    ReceivableIn,
    _date,
    _datetime,
    _decimal,
    _text,
    account_secret,
    infer_event_type,
    parse_installment_webhook,
    verify_payload_signature,
)

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20.0
#: Forpay's JWT lives 30 min; refresh a minute early.
TOKEN_TTL_SECONDS = 29 * 60
DEFAULT_JWE_ALG = "RSA-OAEP-256"
DEFAULT_JWE_ENC = "A256GCM"
DEFAULT_PROCESS_ID = "WHITE_LABEL"
RECEIVABLES_PAGE_SIZE = 500
RECEIVABLES_MAX_PAGES = 40
#: Spot checkout payment methods unless the account secret sets ``spot_payment_methods``.
DEFAULT_SPOT_METHODS: tuple[str, ...] = ("WEBPAY_PLUS",)

_UF_WIRE = Decimal("0.0001")

#: Filter aliases accepted by :meth:`ForpayProvider.search_receivables` (snake -> Forpay).
_RECEIVABLE_FILTERS: dict[str, str] = {
    "external_installment_id": "externalInstallmentId",
    "installment_status": "installmentStatus",
    "state": "installmentStatus",
    "payment_date_from": "paymentDateFrom",
    "payment_date_to": "paymentDateTo",
    "has_payout": "hasPayout",
    "dispersion_id": "dispersionId",
}
_RECEIVABLE_PARAMS = frozenset(_RECEIVABLE_FILTERS.values()) | {"pageSize", "page"}


def _val(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _wire_amount(value: Decimal | int | str) -> float:
    """UF amount for the JSON body: 4 dp, rounded half-up (JSON has no decimal type)."""
    return float(Decimal(str(value)).quantize(_UF_WIRE, rounding=ROUND_HALF_UP))


def _wire_param(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    if isinstance(value, (list, tuple, set)):
        return [_wire_param(v) for v in value]
    return value


def _first(node: Any, *paths: str) -> Any:
    """The first non-empty value among dotted ``paths`` of ``node``."""
    for dotted in paths:
        cur = node
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                cur = None
                break
            cur = cur[part]
        if cur not in (None, ""):
            return cur
    return None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


class ForpayProvider:
    """Forpay over HTTP (``httpx``). One instance serves every account."""

    name = "forpay"

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        token_ttl: float = TOKEN_TTL_SECONDS,
    ) -> None:
        self._client = client
        self._clock = clock
        self._token_ttl = token_ttl
        self._tokens: dict[tuple[Any, ...], tuple[str, float]] = {}
        self._lock = threading.Lock()

    # --- plumbing ------------------------------------------------------------
    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=TIMEOUT_SECONDS)
        return self._client

    @staticmethod
    def _base(acct) -> str:  # noqa: ANN001
        base = (getattr(acct, "base_url", None) or "").strip().rstrip("/")
        if not base:
            raise PaymentProviderError("La cuenta Forpay no tiene URL base configurada")
        return base

    @staticmethod
    def _cache_key(acct, secret: dict[str, Any]) -> tuple[Any, ...]:  # noqa: ANN001
        # Credentials are part of the key, so editing them invalidates the token.
        fingerprint = hashlib.sha256(
            json.dumps(
                [secret.get("api_key"), secret.get("password"), secret.get("public_key_pem")],
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return (getattr(acct, "id", None), getattr(acct, "base_url", None), getattr(acct, "username", None), fingerprint)

    def _headers(self, acct, secret: dict[str, Any], token: str | None) -> dict[str, str]:  # noqa: ANN001
        api_key = secret.get("api_key")
        if not api_key:
            raise PaymentProviderError("La cuenta Forpay no tiene api_key configurada")
        headers = {
            "x-api-key": str(api_key),
            "country": "CL",
            "commerce": str(getattr(acct, "commerce", None) or ""),
            "channel": str(getattr(acct, "channel_header", None) or ""),
            "requestDateTime": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "correlationId": str(uuid.uuid4()),
            "processId": str(getattr(acct, "process_id", None) or DEFAULT_PROCESS_ID),
            "salesExecutiveId": str(getattr(acct, "sales_executive_id", None) or ""),
            "Accept": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        path: str,
    ) -> httpx.Response:
        try:
            response = self._http().request(
                method,
                url,
                headers={**headers, **({"Content-Type": "application/json"} if body is not None else {})},
                content=json.dumps(body).encode("utf-8") if body is not None else None,
                params=params,
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.TimeoutException as exc:
            logger.warning("forpay %s %s: timeout", method, path)
            raise PaymentProviderError(f"Forpay no respondió a tiempo ({path})") from exc
        except httpx.HTTPError as exc:
            logger.warning("forpay %s %s: %s", method, path, type(exc).__name__)
            raise PaymentProviderError(f"No se pudo conectar con Forpay ({path})") from exc
        logger.info("forpay %s %s -> %s", method, path, response.status_code)
        return response

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return ""
        message = _first(data, "message", "error.message", "error", "detail", "description")
        return str(message)[:200] if isinstance(message, (str, int)) else ""

    def _raise_for(self, response: httpx.Response, path: str) -> None:
        if response.is_success:
            return
        detail = self._error_message(response)
        suffix = f": {detail}" if detail else ""
        raise PaymentProviderError(f"Forpay respondió {response.status_code} en {path}{suffix}")

    @staticmethod
    def _json(response: httpx.Response, path: str) -> Any:
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise PaymentProviderError(f"Forpay devolvió una respuesta ilegible en {path}") from exc

    # --- auth ----------------------------------------------------------------
    @staticmethod
    def _encrypt_password(secret: dict[str, Any]) -> str:
        password = secret.get("password")
        public_key = secret.get("public_key_pem")
        if not password:
            raise PaymentProviderError("La cuenta Forpay no tiene contraseña configurada")
        if not public_key:
            raise PaymentProviderError("La cuenta Forpay no tiene la llave pública (public_key_pem)")
        from jose import jwe  # local: python-jose is only needed on this path
        from jose.exceptions import JOSEError

        try:
            token = jwe.encrypt(
                str(password),
                str(public_key),
                algorithm=str(secret.get("jwe_alg") or DEFAULT_JWE_ALG),
                encryption=str(secret.get("jwe_enc") or DEFAULT_JWE_ENC),
            )
        except (JOSEError, ValueError, TypeError) as exc:
            raise PaymentProviderError("No se pudo cifrar la contraseña con la llave pública de Forpay") from exc
        return token.decode("ascii") if isinstance(token, bytes) else str(token)

    def login(self, acct) -> str:  # noqa: ANN001
        """``POST /login`` → a fresh JWT (also refreshes the cache)."""
        secret = account_secret(acct)
        username = getattr(acct, "username", None)
        if not username:
            raise PaymentProviderError("La cuenta Forpay no tiene usuario configurado")
        body = {"username": str(username), "password": self._encrypt_password(secret)}
        path = "/login"
        response = self._send(
            "POST", self._base(acct) + path, headers=self._headers(acct, secret, None), body=body, path=path
        )
        self._raise_for(response, path)
        data = self._json(response, path)
        token = _first(
            data,
            "token",
            "accessToken",
            "access_token",
            "jwt",
            "idToken",
            "data.token",
            "data.accessToken",
            "data.access_token",
        )
        if not isinstance(token, str) or not token:
            raise PaymentProviderError("Forpay no devolvió un token en /login")
        with self._lock:
            self._tokens[self._cache_key(acct, secret)] = (token, self._clock() + self._token_ttl)
        return token

    def _token(self, acct, secret: dict[str, Any]) -> str:  # noqa: ANN001
        key = self._cache_key(acct, secret)
        with self._lock:
            cached = self._tokens.get(key)
        if cached is not None and cached[1] > self._clock():
            return cached[0]
        return self.login(acct)

    def _drop_token(self, acct, secret: dict[str, Any]) -> None:  # noqa: ANN001
        with self._lock:
            self._tokens.pop(self._cache_key(acct, secret), None)

    def verify_credentials(self, acct) -> bool:  # noqa: ANN001
        """A login attempt (``/platform/provider-accounts/{id}/verify``). Raises on failure."""
        self.login(acct)
        return True

    def _call(
        self,
        acct,  # noqa: ANN001
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        secret = account_secret(acct)
        url = self._base(acct) + path
        for attempt in (1, 2):
            token = self._token(acct, secret)
            response = self._send(
                method, url, headers=self._headers(acct, secret, token), body=body, params=params, path=path
            )
            if response.status_code == 401 and attempt == 1:
                self._drop_token(acct, secret)
                continue
            return response
        return response  # pragma: no cover - loop always returns

    # --- protocol: customers -------------------------------------------------
    @staticmethod
    def customer_body(payer: PayerData) -> dict[str, Any]:
        user_id = payer.user_id or payer.rut
        customer: dict[str, Any] = {
            "userId": user_id,
            "documentType": "RUT",
            "documentNumber": payer.rut,
            "name": payer.name,
            "lastName": payer.last_name or "",
            "email": payer.email,
        }
        if payer.phone:
            customer["phone"] = payer.phone
        return {"customer": customer}

    def ensure_customer(self, acct, payer: PayerData) -> str:  # noqa: ANN001
        """``POST /user``; an "already exists" answer is success. Returns the userId."""
        user_id = payer.user_id or payer.rut
        path = "/user"
        response = self._call(acct, "POST", path, body=self.customer_body(payer))
        if response.is_success:
            data = self._json(response, path)
            return str(_first(data, "customer.userId", "userId", "data.userId") or user_id)
        if response.status_code in (400, 409, 422):
            message = self._error_message(response).lower()
            if response.status_code == 409 or "exist" in message or "registrad" in message:
                return user_id
        self._raise_for(response, path)
        return user_id  # pragma: no cover

    # --- protocol: mandates --------------------------------------------------
    @staticmethod
    def installment_body(item: CheckoutItem, number: int) -> dict[str, Any]:
        body: dict[str, Any] = {
            "id": item.external_installment_id,
            "installmentNumber": number,
            "amount": _wire_amount(item.amount_uf),
            "currencyId": "UF",
            "currencyConvert": "TO_DAY",
            "metadata": {},
        }
        if item.due_date is not None:
            body["installmentDueDate"] = item.due_date.isoformat()
        if item.store_commerce_code:
            body["metadata"]["f_commerceCode"] = item.store_commerce_code
        if item.description:
            body["metadata"]["description"] = item.description[:120]
        return body

    @staticmethod
    def _activation_url_from(webhook_url: str) -> str:
        if "kind=payment" in webhook_url:
            return webhook_url.replace("kind=payment", "kind=activation")
        return webhook_url

    def spot_checkout_body(
        self,
        acct,  # noqa: ANN001
        payer: PayerData,
        items: list[CheckoutItem],
        *,
        external_id: str,
        return_url: str,
        webhook_url: str,
    ) -> dict[str, Any]:
        stores = {i.store_commerce_code for i in items if i.store_commerce_code}
        is_mall = _val(getattr(acct, "owner", "")) == "radal_mall" or len(stores) > 1
        total = sum((Decimal(str(i.amount_uf)) for i in items), Decimal("0"))
        methods = account_secret(acct).get("spot_payment_methods") or list(DEFAULT_SPOT_METHODS)
        return {
            "customer": {"userId": payer.user_id or payer.rut},
            "mandate": {
                "id": external_id,
                "type": "ONEPAY",
                "isMall": is_mall,
                "needPayNow": True,
                "terminateWhenCharged": True,
                "charges": {
                    "wayToPay": "AUTOMATICO",
                    "totalAmounts": [{"currencyId": "UF", "amount": _wire_amount(total)}],
                },
            },
            "paymentMethods": list(methods),
            "installments": [self.installment_body(item, n) for n, item in enumerate(items, start=1)],
            "metadata": {"radal_external_id": external_id},
            "urls": {
                "callback": return_url,
                "paymentWebhook": webhook_url,
                "activationWebhook": self._activation_url_from(webhook_url),
            },
        }

    @staticmethod
    def _mandate_result(data: Any, external_id: str, path: str) -> tuple[str, str]:
        url = _first(data, "activation.url", "data.activation.url", "mandate.activation.url")
        if not isinstance(url, str) or not url:
            raise PaymentProviderError(f"Forpay no devolvió activation.url en {path}")
        mandate_id = _first(
            data,
            "mandate.internalId",
            "internalId",
            "mandate.id",
            "id",
            "data.mandate.internalId",
            "data.internalId",
        )
        return str(mandate_id or external_id), url

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
        """``POST /mandate`` type ONEPAY (UF, ``TO_DAY``) → ``activation.url``."""
        if not items:
            raise PaymentProviderError("Un link de pago necesita al menos una cuota")
        path = "/mandate"
        body = self.spot_checkout_body(
            acct, payer, items, external_id=external_id, return_url=return_url, webhook_url=webhook_url
        )
        response = self._call(acct, "POST", path, body=body)
        self._raise_for(response, path)
        data = self._json(response, path)
        mandate_id, url = self._mandate_result(data, external_id, path)
        return CheckoutSession(
            provider_mandate_id=mandate_id,
            checkout_url=url,
            external_id=external_id,
            raw=data if isinstance(data, dict) else {"data": data},
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
        """``POST /mandate`` type DYNAMIC → the enrolment Magic Link."""
        path = "/mandate"
        urls: dict[str, str] = {}
        for key, target in (
            ("callback", "callback"),
            ("payment", "paymentWebhook"),
            ("paymentWebhook", "paymentWebhook"),
            ("activation", "activationWebhook"),
            ("activationWebhook", "activationWebhook"),
        ):
            if webhook_urls.get(key):
                urls[target] = webhook_urls[key]
        body = {
            "customer": {"userId": payer.user_id or payer.rut},
            "mandate": {"id": external_id, "type": "DYNAMIC"},
            "paymentMethods": list(methods),
            "installments": [self.installment_body(item, n) for n, item in enumerate(items, start=1)],
            "metadata": {"radal_external_id": external_id},
            "urls": urls,
        }
        response = self._call(acct, "POST", path, body=body)
        self._raise_for(response, path)
        data = self._json(response, path)
        mandate_id, url = self._mandate_result(data, external_id, path)
        return MandateSession(
            provider_mandate_id=mandate_id,
            magic_link_url=url,
            external_id=external_id,
            raw=data if isinstance(data, dict) else {"data": data},
        )

    # --- protocol: installment state -------------------------------------------
    @staticmethod
    def state_change_body(external_ids: list[str], state: str) -> dict[str, Any]:
        state_id = STATE_PAID if state == "paid" else STATE_ANNULLED
        return {"installments": [{"id": ext, "state": {"id": state_id}} for ext in external_ids]}

    def set_installment_state(
        self, acct, external_ids: list[str], state: Literal["paid", "annulled"]  # noqa: ANN001
    ) -> None:
        """``PUT /installments/status/change`` — paid (2) or annulled (6), by OUR ids."""
        if state not in ("paid", "annulled"):
            raise ValueError("state must be 'paid' or 'annulled'")
        ids = [str(e) for e in external_ids if e]
        if not ids:
            return
        path = "/installments/status/change"
        response = self._call(acct, "PUT", path, body=self.state_change_body(ids, state))
        self._raise_for(response, path)

    # --- protocol: receivables -------------------------------------------------
    @staticmethod
    def _rows(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        rows = _first(data, "data.receivables", "receivables", "data.items", "items", "data.content", "content", "data")
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
        return []

    @staticmethod
    def _total_pages(data: Any) -> int | None:
        return _int(_first(data, "totalPages", "pagination.totalPages", "data.totalPages", "page.totalPages"))

    @staticmethod
    def parse_receivable(row: dict[str, Any]) -> ReceivableIn:
        """One ``GET /receivables`` row → :class:`ReceivableIn` (installment may be nested)."""
        inst = row.get("installment") if isinstance(row.get("installment"), dict) else row
        payment = row.get("payment") if isinstance(row.get("payment"), dict) else {}
        convert = payment.get("convert") if isinstance(payment.get("convert"), dict) else {}
        state = inst.get("state")
        state_id = _int(state.get("id")) if isinstance(state, dict) else _int(state if state is not None else _first(inst, "stateId", "installmentStatus"))
        state_name = _text(state.get("name")) if isinstance(state, dict) else _text(_first(inst, "stateName"))
        internal = _text(_first(inst, "internalId", "installmentInternalId")) or _text(_first(row, "internalId"))
        external = _text(_first(inst, "id", "externalInstallmentId")) or _text(_first(row, "externalInstallmentId"))
        payout = row.get("payout") if isinstance(row.get("payout"), dict) and row.get("payout") else None
        return ReceivableIn(
            provider_installment_id=internal or external or "",
            external_installment_id=external,
            state=state_id,
            state_name=state_name,
            amount=_decimal(_first(inst, "amount")),
            currency=_text(_first(inst, "currency", "currencyId")),
            provider_mandate_id=_text(_first(row, "product.internalId", "mandate.internalId", "mandateId", "product.id")),
            payment_id=_text(payment.get("id")),
            authorization_code=_text(payment.get("authorizationCode")),
            payment_method=_text(payment.get("method")),
            paid_at=_datetime(payment.get("dateTime")),
            convert_amount=_decimal(convert.get("amount")),
            convert_currency=_text(convert.get("currency")),
            convert_date=_date(convert.get("date")),
            payout=payout,
            raw=row,
        )

    def search_receivables(self, acct, **filters: Any) -> list[ReceivableIn]:  # noqa: ANN001
        """``GET /receivables`` with every page (``pageSize`` ≤ 500)."""
        params: dict[str, Any] = {}
        for key, value in filters.items():
            if value is None:
                continue
            target = _RECEIVABLE_FILTERS.get(key, key)
            if target not in _RECEIVABLE_PARAMS:
                continue
            params[target] = _wire_param(value)
        size = min(int(params.pop("pageSize", RECEIVABLES_PAGE_SIZE) or RECEIVABLES_PAGE_SIZE), RECEIVABLES_PAGE_SIZE)
        start = int(params.pop("page", 1) or 1)
        path = "/receivables"
        out: list[ReceivableIn] = []
        for page in range(start, start + RECEIVABLES_MAX_PAGES):
            response = self._call(acct, "GET", path, params={**params, "pageSize": size, "page": page})
            self._raise_for(response, path)
            data = self._json(response, path)
            rows = self._rows(data)
            out.extend(self.parse_receivable(r) for r in rows)
            total_pages = self._total_pages(data)
            if total_pages is not None:
                if page >= total_pages:
                    break
            elif len(rows) < size:
                break
        return out

    # --- protocol: webhooks ----------------------------------------------------
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


__all__ = ["ForpayProvider", "TIMEOUT_SECONDS", "TOKEN_TTL_SECONDS"]
