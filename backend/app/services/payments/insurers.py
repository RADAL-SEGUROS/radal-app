"""What a broker sees of an insurer's cobranza (v12, 02 §5 ``GET /payments/insurers``).

The merge rule: RADAL's ``insurer_collection_profile`` ⊕ the broker's
``broker_insurer_collection`` override, field by field (a non-empty override
value wins). ``insurer.payment_url`` is the legacy fallback for ``portal_url``.

NEVER exposes ``onboarding_status`` / ``expected_live_on`` / ``status_note``
(product decision §1.3: a broker does not learn an insurer is "on its way").
Reused by reminders and the share page (B4/B5) through :func:`merged_instructions`.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.urls import safe_http_url
from app.models.collection_settings import CollectionSettings
from app.models.insurer import Insurer
from app.models.insurer_collection import BrokerInsurerCollection, InsurerCollectionProfile
from app.services.collection.resolver import active_native_channels

INSTRUCTION_KEYS: tuple[str, ...] = (
    "portal_url",
    "bank_name",
    "account_type",
    "account_number",
    "account_holder",
    "account_rut",
    "transfer_email",
    "receipt_email",
    "notes",
)


def _val(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def clean_instructions(raw: Any) -> dict[str, str]:
    """Only the known keys, as trimmed strings; empty values dropped.

    THE choke point every reader goes through (broker view, reminders, the
    public share page): ``portal_url`` survives only as an absolute ``http(s)``
    URL — a ``javascript:`` / ``data:`` value is dropped, never rendered.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in INSTRUCTION_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if key == "portal_url":
            text = safe_http_url(text) or ""
        if text:
            out[key] = text[:1000]
    return out


def broker_override(db: Session, *, broker_id: int, insurer_id: int) -> BrokerInsurerCollection | None:
    return db.scalar(
        select(BrokerInsurerCollection).where(
            BrokerInsurerCollection.broker_id == broker_id,
            BrokerInsurerCollection.insurer_id == insurer_id,
        )
    )


def merged_instructions(
    db: Session,
    *,
    broker_id: int,
    insurer_id: int,
    profile: InsurerCollectionProfile | None = None,
    override: BrokerInsurerCollection | None = None,
    insurer: Insurer | None = None,
) -> dict[str, str]:
    """Profile ⊕ override (override wins per field); legacy ``payment_url`` fallback."""
    profile = profile if profile is not None else db.get(InsurerCollectionProfile, insurer_id)
    override = override if override is not None else broker_override(db, broker_id=broker_id, insurer_id=insurer_id)
    merged = clean_instructions(profile.payment_instructions if profile else None)
    merged.update(clean_instructions(override.payment_instructions if override else None))
    if "portal_url" not in merged:
        insurer = insurer if insurer is not None else db.get(Insurer, insurer_id)
        legacy = safe_http_url(insurer.payment_url) if insurer is not None else None
        if legacy:
            merged["portal_url"] = legacy
    return merged


def insurer_capability(
    db: Session, *, broker_id: int, insurer: Insurer, settings_row: CollectionSettings | None = None
) -> dict[str, Any]:
    """One row of ``GET /payments/insurers`` (no onboarding status, ever)."""
    profile = db.get(InsurerCollectionProfile, insurer.id)
    override = broker_override(db, broker_id=broker_id, insurer_id=insurer.id)
    channels = active_native_channels(db, insurer.id)
    native_on = settings_row is None or bool(settings_row.native_collection_enabled)
    opted_out = override is not None and override.native_enabled is False
    level = "native" if channels and native_on and not opted_out else "assisted"
    email = (override.collections_email if override and override.collections_email else None) or (
        profile.collections_email if profile else None
    )
    return {
        "insurer": {
            "id": insurer.id,
            "name": insurer.trade_name or insurer.legal_name,
            "legal_name": insurer.legal_name,
            "rut": insurer.rut,
        },
        "level_available": level,
        "channels": list(dict.fromkeys(_val(c.channel) for c in channels)) if level == "native" else [],
        "instructions": merged_instructions(
            db, broker_id=broker_id, insurer_id=insurer.id, profile=profile, override=override, insurer=insurer
        ),
        "collections_email": email,
        "broker_override": (
            {
                "collections_email": override.collections_email,
                "contact_name": override.contact_name,
                "payment_instructions": clean_instructions(override.payment_instructions),
                "native_enabled": bool(override.native_enabled),
                "notes": override.notes,
                "updated_at": override.updated_at,
            }
            if override is not None
            else None
        ),
    }


__all__ = [
    "INSTRUCTION_KEYS",
    "broker_override",
    "clean_instructions",
    "insurer_capability",
    "merged_instructions",
]
