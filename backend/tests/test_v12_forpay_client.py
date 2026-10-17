"""The real Forpay client (``ForpayProvider``) against ``httpx.MockTransport`` — NO network.

Checks the request shapes the White Label API documents (03 §3): headers on
every call, JWE-encrypted login, the JWT cache, ONEPAY/DYNAMIC mandate bodies,
the installment state change, receivables paging, and that errors never echo a
secret.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwe

from app.core.crypto import encrypt_secret
from app.services.payments.providers.base import (
    CheckoutItem,
    PayerData,
    PaymentProviderError,
)
from app.services.payments.providers.fake import FakePaymentProvider
from app.services.payments.providers.forpay import ForpayProvider

BASE = "https://forpay.test/white/label"


@pytest.fixture(scope="module")
def rsa_keys() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return public, private


def _acct(public_pem: str, **overrides):
    secret = {
        "api_key": "API-KEY-SECRET",
        "password": "PASSWORD-SECRET",
        "hmac_key": "HMAC-SECRET",
        "public_key_pem": public_pem,
        **overrides.pop("secret", {}),
    }
    fields = {
        "id": 7,
        "provider": "forpay",
        "owner": "radal_mall",
        "base_url": BASE,
        "commerce": "RADAL",
        "channel_header": "WEB",
        "process_id": "WHITE_LABEL",
        "username": "76111111.1",
        "sales_executive_id": "EXEC-1",
        "secret_ciphertext": encrypt_secret(secret),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class Recorder:
    """A scripted Forpay: records requests, answers per (method, path)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], list] = {}

    def on(self, method: str, path: str, *responses) -> None:  # noqa: ANN002
        self.routes[(method, path)] = list(responses)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.replace("/white/label", "", 1)
        queue = self.routes.get((request.method, path))
        if not queue:
            return httpx.Response(404, json={"message": "no route"})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(item):
            return item(request)
        status, body = item
        return httpx.Response(status, json=body)

    def to(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path.endswith(path)]

    def provider(self, **kwargs) -> ForpayProvider:  # noqa: ANN003
        return ForpayProvider(client=httpx.Client(transport=httpx.MockTransport(self.handler)), **kwargs)


def _payer() -> PayerData:
    return PayerData(rut="11111111-1", name="Ana", last_name="Pérez", email="ana@example.test")


def _items() -> list[CheckoutItem]:
    return [
        CheckoutItem(1, "rdl-cuota-1-abc", Decimal("4.2"), date(2026, 10, 12), "597055555536", "cuota 1"),
        CheckoutItem(2, "rdl-cuota-2-abc", Decimal("312.0000"), date(2026, 11, 12), "597055555537"),
    ]


REQUIRED_HEADERS = (
    "x-api-key",
    "country",
    "commerce",
    "channel",
    "requestdatetime",
    "correlationid",
    "processid",
    "salesexecutiveid",
)


def test_login_encrypts_password_with_jwe_and_sends_every_header(rsa_keys):
    public, private = rsa_keys
    rec = Recorder()
    rec.on("POST", "/login", (200, {"token": "JWT-1"}))
    provider = rec.provider()

    assert provider.login(_acct(public)) == "JWT-1"
    (req,) = rec.to("POST", "/login")
    for header in REQUIRED_HEADERS:
        assert req.headers.get(header), header
    assert req.headers["x-api-key"] == "API-KEY-SECRET"
    assert req.headers["country"] == "CL"
    assert req.headers["processid"] == "WHITE_LABEL"
    assert req.headers["salesexecutiveid"] == "EXEC-1"
    body = json.loads(req.content)
    assert body["username"] == "76111111.1"
    assert body["password"] != "PASSWORD-SECRET"
    assert jwe.decrypt(body["password"], private) == b"PASSWORD-SECRET"
    header = jwe.get_unverified_header(body["password"])
    assert header["alg"] == "RSA-OAEP-256" and header["enc"] == "A256GCM"


def test_jwe_algorithms_come_from_the_account_secret(rsa_keys):
    public, private = rsa_keys
    rec = Recorder()
    rec.on("POST", "/login", (200, {"data": {"accessToken": "JWT-X"}}))
    acct = _acct(public, secret={"jwe_alg": "RSA-OAEP", "jwe_enc": "A128GCM"})
    assert rec.provider().login(acct) == "JWT-X"
    token = json.loads(rec.to("POST", "/login")[0].content)["password"]
    hdr = jwe.get_unverified_header(token)
    assert hdr["alg"] == "RSA-OAEP" and hdr["enc"] == "A128GCM"
    assert jwe.decrypt(token, private) == b"PASSWORD-SECRET"


def test_token_is_cached_per_account_and_refreshed_after_ttl(rsa_keys):
    public, _ = rsa_keys
    clock = {"t": 1000.0}
    rec = Recorder()
    rec.on("POST", "/login", (200, {"token": "JWT-1"}), (200, {"token": "JWT-2"}))
    rec.on("PUT", "/installments/status/change", (200, {}))
    provider = rec.provider(clock=lambda: clock["t"])
    acct = _acct(public)

    provider.set_installment_state(acct, ["a"], "paid")
    provider.set_installment_state(acct, ["b"], "paid")
    assert len(rec.to("POST", "/login")) == 1
    puts = rec.to("PUT", "/installments/status/change")
    assert all(r.headers["authorization"] == "Bearer JWT-1" for r in puts)
    # correlation ids are fresh per request
    assert puts[0].headers["correlationid"] != puts[1].headers["correlationid"]

    clock["t"] += 29 * 60 + 1
    provider.set_installment_state(acct, ["c"], "paid")
    assert len(rec.to("POST", "/login")) == 2
    assert rec.to("PUT", "/installments/status/change")[-1].headers["authorization"] == "Bearer JWT-2"

    other = _acct(public, id=8)
    provider.set_installment_state(other, ["d"], "paid")
    assert len(rec.to("POST", "/login")) == 3


def test_a_401_drops_the_token_and_retries_once(rsa_keys):
    public, _ = rsa_keys
    rec = Recorder()
    rec.on("POST", "/login", (200, {"token": "OLD"}), (200, {"token": "NEW"}))
    rec.on("PUT", "/installments/status/change", (401, {"message": "expired"}), (200, {}))
    rec.provider().set_installment_state(_acct(public), ["x"], "annulled")
    puts = rec.to("PUT", "/installments/status/change")
    assert [r.headers["authorization"] for r in puts] == ["Bearer OLD", "Bearer NEW"]


def test_ensure_customer_posts_user_and_tolerates_already_exists(rsa_keys):
    public, _ = rsa_keys
    rec = Recorder()
    rec.on("POST", "/login", (200, {"token": "T"}))
    rec.on("POST", "/user", (200, {"customer": {"userId": "11111111-1"}}), (400, {"message": "El usuario ya existe"}))
    provider = rec.provider()
    acct = _acct(public)
    assert provider.ensure_customer(acct, _payer()) == "11111111-1"
    assert provider.ensure_customer(acct, _payer()) == "11111111-1"
    body = json.loads(rec.to("POST", "/user")[0].content)["customer"]
    assert body["userId"] == "11111111-1"
    assert body["documentNumber"] == "11111111-1"
    assert body["name"] == "Ana" and body["lastName"] == "Pérez" and body["email"] == "ana@example.test"

    rec.on("POST", "/user", (500, {"message": "boom"}))
    with pytest.raises(PaymentProviderError):
        provider.ensure_customer(acct, _payer())


def test_spot_checkout_is_a_onepay_mall_mandate_in_uf_to_day(rsa_keys):
    public, _ = rsa_keys
    rec = Recorder()
    rec.on("POST", "/login", (200, {"token": "T"}))
    rec.on(
        "POST",
        "/mandate",
        (200, {"activation": {"url": "https://checkout.forpay.test?token=abc"}, "mandate": {"internalId": "M-77"}}),
    )
    webhook = "https://hooks.test/api/v1/webhooks/forpay?account=k&sig=s&kind=payment"
    session = rec.provider().create_spot_checkout(
        _acct(public),
        _payer(),
        _items(),
        external_id="rdl-intent-abc",
        return_url="https://app.test/s/tok?pago=retorno",
        webhook_url=webhook,
    )
    assert session.checkout_url == "https://checkout.forpay.test?token=abc"
    assert session.provider_mandate_id == "M-77"
    body = json.loads(rec.to("POST", "/mandate")[0].content)
    mandate = body["mandate"]
    assert mandate["id"] == "rdl-intent-abc"
    assert mandate["type"] == "ONEPAY"
    assert mandate["isMall"] is True and mandate["needPayNow"] is True and mandate["terminateWhenCharged"] is True
    assert mandate["charges"]["totalAmounts"] == [{"currencyId": "UF", "amount": 316.2}]
    first, second = body["installments"]
    assert first == {
        "id": "rdl-cuota-1-abc",
        "installmentNumber": 1,
        "amount": 4.2,
        "currencyId": "UF",
        "currencyConvert": "TO_DAY",
        "installmentDueDate": "2026-10-12",
        "metadata": {"f_commerceCode": "597055555536", "description": "cuota 1"},
    }
    assert second["metadata"]["f_commerceCode"] == "597055555537"
    assert body["urls"]["callback"] == "https://app.test/s/tok?pago=retorno"
    assert body["urls"]["paymentWebhook"] == webhook
    assert body["urls"]["activationWebhook"].endswith("kind=activation")
    assert body["customer"] == {"userId": "11111111-1"}


def test_spot_checkout_without_activation_url_is_an_error(rsa_keys):
    public, _ = rsa_keys
    rec = Recorder()
    rec.on("POST", "/login", (200, {"token": "T"}))
    rec.on("POST", "/mandate", (200, {"mandate": {"internalId": "M"}}))
    with pytest.raises(PaymentProviderError, match="activation.url"):
        rec.provider().create_spot_checkout(
            _acct(public), _payer(), _items(), external_id="e", return_url="r", webhook_url="w"
        )


def test_single_store_insurer_account_is_not_mall(rsa_keys):
    public, _ = rsa_keys
    provider = ForpayProvider(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    body = provider.spot_checkout_body(
        _acct(public, owner="insurer"), _payer(), _items()[:1], external_id="e", return_url="r", webhook_url="w"
    )
    assert body["mandate"]["isMall"] is False


def test_recurring_mandate_is_dynamic_with_methods_and_webhooks(rsa_keys):
    public, _ = rsa_keys
    rec = Recorder()
    rec.on("POST", "/login", (200, {"token": "T"}))
    rec.on("POST", "/mandate", (200, {"activation": {"url": "https://magic.test/x"}, "internalId": "D-1"}))
    session = rec.provider().create_recurring_mandate(
        _acct(public),
        _payer(),
        _items(),
        external_id="rdl-mandate-1",
        methods=["DEBITO_DIRECTO"],
        webhook_urls={"payment": "https://h/p", "activation": "https://h/a"},
    )
    assert session.magic_link_url == "https://magic.test/x" and session.provider_mandate_id == "D-1"
    body = json.loads(rec.to("POST", "/mandate")[0].content)
    assert body["mandate"] == {"id": "rdl-mandate-1", "type": "DYNAMIC"}
    assert body["paymentMethods"] == ["DEBITO_DIRECTO"]
    assert body["urls"] == {"paymentWebhook": "https://h/p", "activationWebhook": "https://h/a"}


def test_set_installment_state_body_and_noop_for_empty(rsa_keys):
    public, _ = rsa_keys
    rec = Recorder()
    rec.on("POST", "/login", (200, {"token": "T"}))
    rec.on("PUT", "/installments/status/change", (200, {}))
    provider = rec.provider()
    provider.set_installment_state(_acct(public), [], "paid")
    assert rec.requests == []
    provider.set_installment_state(_acct(public), ["a", "b"], "annulled")
    body = json.loads(rec.to("PUT", "/installments/status/change")[0].content)
    assert body == {"installments": [{"id": "a", "state": {"id": 6}}, {"id": "b", "state": {"id": 6}}]}
    with pytest.raises(ValueError):
        provider.set_installment_state(_acct(public), ["a"], "refunded")  # type: ignore[arg-type]


def test_search_receivables_pages_and_parses(rsa_keys):
    public, _ = rsa_keys
    rec = Recorder()
    rec.on("POST", "/login", (200, {"token": "T"}))

    def page(request: httpx.Request) -> httpx.Response:
        n = int(request.url.params["page"])
        rows = {
            1: [
                {
                    "installment": {"internalId": "I-1", "id": "rdl-cuota-1-a", "amount": 4.2, "currency": "UF",
                                    "state": {"id": 2, "name": "PAGADA"}},
                    "payment": {"id": "P-1", "authorizationCode": "A1", "amount": 4.2, "dateTime": "2026-10-16T12:00:00-03:00",
                                "method": "WEBPAY_PLUS", "convert": {"date": "2026-10-16", "amount": 160000, "currency": "PESOS"}},
                    "payout": {"dispersionId": "D-9", "creditedAmount": 158000, "customerStatusName": "ABONADO AL COMERCIO"},
                }
            ],
            2: [{"installment": {"internalId": "I-2", "id": "rdl-cuota-2-a", "state": {"id": 1}}}],
        }[n]
        return httpx.Response(200, json={"data": rows, "totalPages": 2})

    rec.on("GET", "/receivables", page)
    rows = rec.provider().search_receivables(
        _acct(public), externalInstallmentId=["rdl-cuota-1-a", "rdl-cuota-2-a"], has_payout=True, bogus="x"
    )
    assert [r.provider_installment_id for r in rows] == ["I-1", "I-2"]
    first = rows[0]
    assert first.state == 2 and first.external_installment_id == "rdl-cuota-1-a"
    assert first.convert_amount == Decimal("160000") and first.convert_currency == "PESOS"
    assert first.payout["dispersionId"] == "D-9"
    gets = rec.to("GET", "/receivables")
    assert len(gets) == 2
    params = gets[0].url.params
    assert params.get_list("externalInstallmentId") == ["rdl-cuota-1-a", "rdl-cuota-2-a"]
    assert params["hasPayout"] == "true" and params["pageSize"] == "500"
    assert "bogus" not in params


def test_errors_and_timeouts_never_echo_secrets(rsa_keys):
    public, _ = rsa_keys
    rec = Recorder()
    rec.on("POST", "/login", (403, {"message": "credenciales inválidas"}))
    with pytest.raises(PaymentProviderError) as info:
        rec.provider().login(_acct(public))
    text = str(info.value)
    assert "403" in text and "credenciales" in text
    for secret in ("API-KEY-SECRET", "PASSWORD-SECRET", "HMAC-SECRET"):
        assert secret not in text

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    provider = ForpayProvider(client=httpx.Client(transport=httpx.MockTransport(boom)))
    with pytest.raises(PaymentProviderError, match="no respondió"):
        provider.login(_acct(public))


def test_missing_credentials_fail_before_any_request(rsa_keys):
    rec = Recorder()
    provider = rec.provider()
    with pytest.raises(PaymentProviderError, match="llave pública"):
        provider.login(_acct("", secret={"public_key_pem": ""}))
    with pytest.raises(PaymentProviderError, match="api_key"):
        provider.login(_acct(rsa_keys[0], secret={"api_key": ""}))
    assert rec.requests == []


def test_webhook_verify_and_parse_share_the_fake_signature(rsa_keys):
    public, _ = rsa_keys
    provider = ForpayProvider(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    acct = _acct(public)
    payload = FakePaymentProvider.build_webhook_payload(
        "HMAC-SECRET", internal_id="I-1", external_installment_id="rdl-cuota-1-a", state=2
    )
    assert provider.verify_webhook(acct, payload) is True
    event = provider.parse_webhook(acct, payload)
    assert event.dedup_key == "rdl-cuota-1-a:2" and event.external_installment_id == "rdl-cuota-1-a"
    tampered = FakePaymentProvider.build_webhook_payload(
        "HMAC-SECRET", internal_id="I-1", external_installment_id="rdl-cuota-1-a", state=2, tamper=True
    )
    assert provider.verify_webhook(acct, tampered) is False
    assert provider.verify_webhook(_acct(public, secret={"hmac_key": "other"}), payload) is False


def test_registry_builds_the_real_provider(monkeypatch):
    from app.services.payments import providers as registry

    monkeypatch.setattr(registry, "_OVERRIDE", None)
    monkeypatch.setattr(registry, "_INSTANCES", {})
    assert isinstance(registry.get_provider("forpay"), ForpayProvider)
