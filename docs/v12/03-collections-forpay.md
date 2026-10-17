# v12 — collections, levels, Forpay

## 1. Cuota state (team rule 3, extended) — `app/services/collection/status.py` (written in PHASE 1)

```python
@dataclass(frozen=True)
class InstallmentState:
    state: Literal["voided", "paid", "promised", "termination_risk", "overdue", "due_today", "upcoming"]
    flags: frozenset[str]          # {"partial", "direct_debit_rejected", "unconfirmed", "no_receipt"}
    paid_uf: Decimal
    balance_uf: Decimal
    days_late: int                 # 0 when not late
    evidence: str | None           # best evidence_level among active payments

def compute_installment_state(inst, payments, *, today: date, cadence_days: list[int],
                              tolerance_uf: Decimal = Decimal("0.009")) -> InstallmentState
def refresh_installment_cache(db, inst, *, today) -> InstallmentState   # writes inst.status (+paid_on/days_late) mapping below
```
Order: voided (`voided_by_endorsement_id` or plan terminated & due after `terminated_on`) → paid (Σ active `amount_uf` ≥
`gross_amount_uf − tolerance`) → promised (`promise_until ≥ today`) → termination_risk (`today > due + max(cadence_days)`) →
overdue (`today > due`) → due_today → upcoming. Flags: `partial` (active payments and balance > tol), `direct_debit_rejected`
(`direct_debit_rejected_at` after last active payment), `unconfirmed` (paid but best evidence < insurer_confirmed),
`no_receipt` (paid with no `receipt_document_id` on any active payment). Cache mapping to the legacy `InstallmentStatus`:
voided→CANCELLED, paid→PAID or PAID_LATE (paid_on > due), promised/overdue/termination_risk→OVERDUE, due_today→DUE, upcoming→PENDING.
**Legacy writes** (`PATCH /collections/{plan}/installments/{n}` with `paid_on`) must keep working: B2 converts them into a
`payment(source=manual, evidence=declared)` so the two never diverge. Existing tests in `test_collection_sums.py`/`test_post_sale.py`
must keep passing.

## 2. Collection level resolver — `app/services/collection/resolver.py` (PHASE 1)

```python
@dataclass(frozen=True)
class CollectionMode:
    level: Literal["record", "assisted", "native"]
    reason: str                    # Spanish, shown verbatim (like stage-guard reasons)
    channels: tuple[str, ...]      # native channels usable now (PaymentChannel values)
    missing: tuple[str, ...]       # keys: payer_contact, insurer_contact, plan, templates_unapproved, mail_sender
def resolve_collection_mode(db, installment, *, broker_id) -> CollectionMode
```
Rules (in order):
1. plan `level_preference == record` → record ("Cobro solo de registro (elegido por la corredora)").
2. native iff: preference `auto`, broker `collection_settings.native_collection_enabled`, broker override for the lead insurer
   not `native_enabled=false`, and ≥1 `insurer_payment_channel` of the lead insurer with `status=active` and an active
   `provider_account`. Lead insurer = `coinsurance_share.is_leader` if any, else `policy.insurer_id` (R18).
   reason "{insurer} cobra en línea con Radal".
3. else assisted (ALWAYS available — product decision): reason "Cobro asistido: Radal recuerda al pagador y consulta a {insurer}".
   `missing` lists what would make it work better: `payer_contact` (no payer/policyholder email), `insurer_contact` (no
   collections_email in profile/override), `templates_unapproved`.
4. NEVER expose `onboarding_status`/`expected_live_on` to brokers. When an insurer flips to native, B2's clock emits a
   `native_collection_available` fyi notification once per broker having open cuotas with that insurer.

## 3. Forpay — facts from the public docs (forpaydocs.readme.io; researched 2026-10-16)

- Model = **mandates with installments**. `POST /mandate` types: `ONEPAY` (spot link; `needPayNow`, `terminateWhenCharged`,
  `isMall`), `DYNAMIC` (recurring, installments add/edit/annul any time), `PERMANENT`, `SUBSCRIPTION` (not used).
- Installment fields: `id` (OUR external id), `installmentNumber`, `amount`, `currencyId` (`UF`|`PESOS`),
  `currencyConvert` (`TO_DAY`|`TO_EXPIRATION`), `installmentDueDate`, `metadata` (free k/v; `f_commerceCode` = Transbank child
  store when `isMall`). Response of create: `activation.url` (the payment link / Magic Link for enrolment).
- Customer: `POST /user` (`customer.userId`, RUT document, name, lastName, email required).
- `PUT /installments/status/change` → mark installments paid (state 2) or annulled (6) by external ids — call it when a cuota is
  paid OUTSIDE Forpay so Forpay never charges/reminds it.
- `GET /receivables` (filters `externalInstallmentId`, `installmentStatus`, `paymentDateFrom/To`, `hasPayout`, `dispersionId`;
  pageSize ≤ 500) → installments with `payment{id, authorizationCode, amount, dateTime, method, convert{date, amount, currency}}`
  and `payout{dispersionId, creditedAmount, companyDepositDate, destinationAccount, bankTransactionNumber, statusName,
  customerStatusName:"ABONADO AL COMERCIO"}`.
- Auth: every call `x-api-key`, `country: CL`, `commerce`, `channel`, `requestDateTime` (ISO), `correlationId` (uuid4),
  `processId` (`WHITE_LABEL` — Radal speaks to payers; never `HYBRID`), `salesExecutiveId`. `POST /login {username, password}`
  → JWT valid 30 min (`Authorization: Bearer`). `password` must be **JWE-encrypted with Forpay's public key** (use
  `jose.jwe.encrypt(password, public_key_pem, algorithm="RSA-OAEP-256", encryption="A256GCM")`; algorithms configurable on the
  account — the exact pair is a question for Forpay, keep it a setting on `provider_account` secret JSON: `jwe_alg`, `jwe_enc`).
- Webhooks: URLs are passed **per mandate** in the create body: `urls.callback`, `urls.activationWebhook`, `urls.paymentWebhook`.
  Payload: `businessPartner{…}`, `customer{userId, internalUserId, …}`, `product{type:"MANDATE", internalId, id, serviceId}`,
  `installment{state{id,name}, internalId, id, amount, currency, installmentDueDate, metadata{…}}`, `payment{…}` (only state 2),
  `signature`. Signature = Base64(HMAC-SHA256(hmac_key, concatenation of specific fields in a fixed order)); field order
  differs per webhook type — implement as a **configurable field list** per event type (default for payment webhooks:
  `businessPartner.businessPartnerId`, `customer.userId`, `product.id`, `installment.id`, `installment.state.id`,
  `installment.amount`, then `payment.id`, `payment.authorizationCode`, `payment.amount` when state=2), stored in the account
  secret JSON as `signature_fields` override. Idempotency key = `installment.internalId + ":" + installment.state.id`.
- Installment states: 1 pendiente · 2 pagada · 6 anulada · 9 en proceso · 10 repactada · 11 finalizada · 12 reversada ·
  15 en validación · −99 rechazada.
- Base URLs: dev `https://devapi.forpayservices.cl/white/label`; production not public (account field).
- NOT documented: refunds API, link expiry, sub-merchant onboarding API, webhook retry policy. Do not invent them.

## 4. Provider seam — `app/services/payments/providers/base.py` (PHASE 1 writes the Protocol + FakeProvider; B3 writes Forpay)

```python
class PaymentProvider(Protocol):
    name: str
    def ensure_customer(self, acct, payer: PayerData) -> str: ...
    def create_spot_checkout(self, acct, payer, items: list[CheckoutItem], *, external_id: str,
                             return_url: str, webhook_url: str) -> CheckoutSession: ...   # ONEPAY isMall
    def create_recurring_mandate(self, acct, payer, items, *, external_id, methods: list[str],
                                 webhook_urls: dict[str, str]) -> MandateSession: ...      # DYNAMIC
    def set_installment_state(self, acct, external_ids: list[str], state: Literal["paid", "annulled"]) -> None: ...
    def search_receivables(self, acct, **filters) -> list[ReceivableIn]: ...
    def verify_webhook(self, acct, payload: dict) -> bool: ...
    def parse_webhook(self, acct, payload: dict) -> ProviderEventIn: ...
def get_provider(name: str) -> PaymentProvider    # registry; tests monkeypatch to FakeProvider
```
`CheckoutItem{installment_id, external_installment_id, amount_uf, due_date, store_commerce_code, description}`.
HTTP via `httpx` (already a dependency through tests? if not, use `urllib`/`requests`-free `httpx` — check requirements; add
`httpx` to the runtime block if needed). Timeouts 20 s. Never log secrets. JWT cached per account in-process (30 min − 60 s).

## 5. The generic webhook — security model (product decision §1.5)

URL handed to Forpay (per mandate): `{PUBLIC_API_URL}/api/v1/webhooks/forpay?account={webhook_key}&sig={sig}&kind=payment|activation`
- `webhook_key`: public random id of the `provider_account` (`secrets.token_urlsafe(18)`).
- `sig = hex(HMAC-SHA256(webhook_secret, f"{provider}:{webhook_key}"))` — static per account, rotatable
  (`/platform/provider-accounts/{id}/rotate-webhook-secret`; rotation invalidates old URLs only for NEW mandates — keep the
  previous secret for 30 days in the encrypted secret JSON as `previous_webhook_secret` and accept either).
- Handler order: (1) look up account by `webhook_key` → 404 if unknown/disabled; (2) constant-time compare `sig` → 401;
  (3) parse JSON (raw body, size ≤ 256 KB) → 400 if invalid; (4) verify provider payload HMAC → store event with
  `signature_valid` and, if invalid, 401 after storing; (5) insert `provider_event` with dedup key (duplicate → 200
  `{"status":"duplicate"}`); (6) process idempotently in the same request (`processing_status`), errors → `failed` + 200
  (Forpay must not hammer retries for our bugs; `payments.sync` reconciles).
- Processing a payment webhook: find `provider_charge` by `(account, installment.internalId)` or create it from
  `installment.id` (= our `external_installment_id`) → link installment/intent/broker; state 2 → `payment(source=provider,
  evidence=provider_confirmed, currency=CLP|UF from payment.convert, uf_rate from convert)` unique per charge; −99 → set
  `direct_debit_rejected_at` (mandates) + notification; 12 → reverse the payment (`chargeback`); update intent status
  (all items paid → paid; some → partially_paid).
- Infra note: behind CloudFront OAC a third party cannot POST (needs `x-amz-content-sha256`). The route must ALSO be reachable via
  the webhook ingress (API Gateway HTTP API → backend Lambda, path `/api/v1/webhooks/*`). See `08-infra.md`. Code is the same.

## 6. Checkout flow (native)

1. Insured opens `/s/{token}` (share link audience insured, `can_pay`) or the broker clicks "Generar link de pago".
2. `create_checkout_for_share` / `POST /payments/intents`: validate each installment belongs to the link's policy/plan, is
   not paid/voided, resolves to `native`; group by lead insurer channel → `store_commerce_code`; pick the `provider_account`
   (the radal_mall account for webpay/oneclick; insurer-owned account for bank_transfer if that's how the channel is set).
   One intent per provider account (if two accounts would be needed → 409 "Paga por separado las cuotas de {insurer}").
3. Provider `ensure_customer` (payer RUT) + `create_spot_checkout` with installments in **UF, currencyConvert TO_DAY**,
   `external_installment_id`s, `isMall` when >1 store or always for the mall account.
4. Store `checkout_url`, return it. Payer pays at Forpay/Transbank. Webhook → payment.
5. A cuota paid outside Forpay while an intent is open → B3 calls `set_installment_state(paid)` and cancels the intent item.

## 7. Assisted flow

- Payer reminders (`collection.notices`): only when broker master switch `notices_enabled` AND templates approved AND plan
  `notices_enabled` AND not paused AND payer contact exists. Content: amount, due date, insurer instructions
  (profile ⊕ broker override), a share link (insured) — never a Forpay link unless level is native.
- Status inquiries (`POST /inquiries` or job): email to insurer `collections_email` (override ⊕ profile), subject contains the
  token `[RADAL-INQ-XXXXXX]`, body lists policy number, insured, cuota numbers, due dates, amounts, and the insurer share link
  (audience insurer, `can_report_status`). Replies are matched by token in subject/body or `In-Reply-To`.
- Evidence → human confirm → payment (`reported` from an insurer reply email; `insurer_confirmed` when the insurer used the share
  link? NO — a share-link click is still unauthenticated: use `reported` for both; only the broker's explicit
  "Confirmado por la compañía" or an uploaded insurer statement row gives `insurer_confirmed`).

## 8. Notifications from the clock (cadence `[-15,-5,0,3,10]`)

level −15, −5, 0 → fyi `installment_due`; +3, +10 → todo `installment_overdue`; > last → todo `termination_risk`; promise broken →
todo `promise_broken`; plan missing (active policy, no plan, every 7 days) → todo `plan_missing`; new evidence → todo
`evidence_pending`; inquiry reply → todo `inquiry_reply`. Recipient: case-file/plan owner (`case_file.owner_user_id`) else every
broker_admin of the broker. One open row per (user, origin) — escalate in place. Paid/voided → close.
