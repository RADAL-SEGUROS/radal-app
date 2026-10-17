"""Payments: the provider seam (Forpay), checkout, webhook processing and sync (v12).

Lane B3. Modules:

* ``providers/`` — the ``PaymentProvider`` protocol + registry (phase 1), the
  fake, and ``forpay.py`` (the real White Label client over ``httpx``);
* ``accounts`` — provider-account secrets (write-only, encrypted) and the
  generic webhook URL / ``sig`` (with 30-day rotation grace);
* ``checkout`` — ``create_intent_for_installments``, ``create_checkout_for_share``,
  ``cancel_intent`` and ``mark_paid_outside``;
* ``webhooks`` — provider events → charges → payments (``process_event``,
  ``apply_charge_update``, ``run_processing``);
* ``sync`` — the ``payments.sync`` / ``payments.payouts`` job bodies;
* ``insurers`` — the broker-facing insurer capability (merged instructions,
  never the onboarding status);
* ``intents`` — shared intent helpers (rollup, Santiago dates).
"""
