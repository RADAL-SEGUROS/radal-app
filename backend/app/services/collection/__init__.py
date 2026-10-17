"""Cobranza services (v12, docs/v12/03-collections-forpay.md).

* :mod:`app.services.collection.status` — the cuota state machine: payments
  are the truth, ``collection_installment.status`` is the cache it writes.
* :mod:`app.services.collection.resolver` — which collection level
  (``record`` / ``assisted`` / ``native``) applies to a cuota right now.

Both are written in phase 1 and are the contract every lane builds on: lane B2
may EXTEND them but must not change the public signatures.
"""
from __future__ import annotations

from app.services.collection.resolver import (
    CollectionMode,
    active_native_channels,
    insurer_contact_email,
    lead_insurer_id,
    payer_contact_email,
    resolve_collection_mode,
)
from app.services.collection.status import (
    EVIDENCE_ORDER,
    InstallmentState,
    compute_installment_state,
    legacy_status_for,
    refresh_installment_cache,
)

__all__ = [
    "EVIDENCE_ORDER",
    "CollectionMode",
    "InstallmentState",
    "active_native_channels",
    "compute_installment_state",
    "insurer_contact_email",
    "lead_insurer_id",
    "legacy_status_for",
    "payer_contact_email",
    "refresh_installment_cache",
    "resolve_collection_mode",
]
