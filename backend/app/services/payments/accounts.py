"""Provider-account secrets and the generic webhook URL (v12, 03 §5).

A ``provider_account`` carries two encrypted things (``app.core.crypto``):

* ``secret_ciphertext`` — the provider credential bundle ``{api_key, password,
  hmac_key, public_key_pem, jwe_alg, jwe_enc, signature_fields, …}`` plus, after
  a rotation, ``previous_webhook_secret`` / ``previous_webhook_secret_until``;
* ``webhook_secret_ciphertext`` — the random secret behind the ``sig`` query
  param of the generic webhook.

The webhook URL handed to the provider (per mandate) is::

    {PUBLIC_API_URL}/api/v1/webhooks/{provider}?account={webhook_key}&sig={sig}&kind=payment|activation

with ``sig = hex(HMAC-SHA256(webhook_secret, f"{provider}:{webhook_key}"))``. It
is static per account; a rotation keeps the previous secret valid for
:data:`PREVIOUS_SECRET_GRACE` so mandates created before it keep reaching us.

Nothing here logs or returns a secret value. API responses expose only
``has_secret`` and the NAMES of the keys that are set.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from app.core.config import settings
from app.core.crypto import SecretDecryptionError, decrypt_secret, encrypt_secret
from app.models.base_class import utcnow
from app.models.provider import ProviderAccount
from app.services.payments.providers.base import account_secret

#: How long a rotated-out webhook secret keeps validating ``sig``.
PREVIOUS_SECRET_GRACE = timedelta(days=30)

#: Secret-bundle keys a platform admin may write (write-only).
WRITABLE_SECRET_KEYS: tuple[str, ...] = (
    "api_key",
    "password",
    "hmac_key",
    "public_key_pem",
    "jwe_alg",
    "jwe_enc",
    "signature_fields",
)
#: Keys the system manages itself inside the bundle (never writable, never listed).
_INTERNAL_SECRET_KEYS = frozenset({"previous_webhook_secret", "previous_webhook_secret_until"})

WEBHOOK_KINDS: tuple[str, ...] = ("payment", "activation")


def _val(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


# --- secret bundle -----------------------------------------------------------

def new_webhook_key() -> str:
    return secrets.token_urlsafe(18)


def new_webhook_secret() -> str:
    return secrets.token_urlsafe(32)


def secret_keys_set(acct: ProviderAccount) -> list[str]:
    """Names (never values) of the writable secret keys that carry a value."""
    bundle = account_secret(acct)
    return sorted(k for k in WRITABLE_SECRET_KEYS if bundle.get(k) not in (None, "", [], {}))


def has_secret(acct: ProviderAccount) -> bool:
    return bool(secret_keys_set(acct))


def merge_secret_bundle(acct: ProviderAccount, updates: dict[str, Any]) -> None:
    """Write-only merge into the encrypted bundle.

    A key absent from ``updates`` (or ``None``) is kept; an empty string /
    empty list clears it. Unknown keys are ignored.
    """
    bundle = account_secret(acct)
    changed = False
    for key in WRITABLE_SECRET_KEYS:
        if key not in updates or updates[key] is None:
            continue
        value = updates[key]
        if value in ("", [], {}):
            if key in bundle:
                bundle.pop(key)
                changed = True
            continue
        bundle[key] = value
        changed = True
    if changed or acct.secret_ciphertext is None:
        acct.secret_ciphertext = encrypt_secret(bundle) if bundle else None


# --- webhook secret / sig ----------------------------------------------------

def webhook_secret(acct: ProviderAccount) -> str | None:
    token = acct.webhook_secret_ciphertext
    if not token:
        return None
    try:
        value = decrypt_secret(token)
    except SecretDecryptionError:
        return None
    return value if isinstance(value, str) and value else None


def compute_sig(secret: str, provider: str, webhook_key: str) -> str:
    """``hex(HMAC-SHA256(secret, f"{provider}:{webhook_key}"))``."""
    return hmac.new(
        secret.encode("utf-8"), f"{provider}:{webhook_key}".encode("utf-8"), hashlib.sha256
    ).hexdigest()


def account_sig(acct: ProviderAccount) -> str | None:
    secret = webhook_secret(acct)
    if secret is None:
        return None
    return compute_sig(secret, _val(acct.provider), acct.webhook_key)


def verify_sig(acct: ProviderAccount, sig: str | None, *, now: datetime | None = None) -> bool:
    """Constant-time check of the ``sig`` query param (current or rotated-out secret)."""
    if not sig or not isinstance(sig, str) or len(sig) > 128:
        return False
    provider = _val(acct.provider)
    candidates: list[str] = []
    current = webhook_secret(acct)
    if current:
        candidates.append(current)
    bundle = account_secret(acct)
    previous = bundle.get("previous_webhook_secret")
    until_raw = bundle.get("previous_webhook_secret_until")
    if previous and until_raw:
        try:
            until = _aware(datetime.fromisoformat(str(until_raw)))
        except ValueError:
            until = None
        if until is not None and until > (_aware(now) or utcnow()):
            candidates.append(str(previous))
    given = sig.encode("ascii", "replace")
    ok = False
    for secret in candidates:
        expected = compute_sig(secret, provider, acct.webhook_key).encode("ascii")
        # Evaluate every candidate (no early exit) — constant work per request.
        ok = hmac.compare_digest(expected, given) or ok
    return ok


def rotate_webhook_secret(acct: ProviderAccount, *, now: datetime | None = None) -> None:
    """New ``sig`` secret; the old one stays valid for :data:`PREVIOUS_SECRET_GRACE`."""
    moment = _aware(now) or utcnow()
    old = webhook_secret(acct)
    bundle = account_secret(acct)
    if old:
        bundle["previous_webhook_secret"] = old
        bundle["previous_webhook_secret_until"] = (moment + PREVIOUS_SECRET_GRACE).isoformat()
    acct.secret_ciphertext = encrypt_secret(bundle) if bundle else None
    acct.webhook_secret_ciphertext = encrypt_secret(new_webhook_secret())


def previous_secret_until(acct: ProviderAccount) -> datetime | None:
    raw = account_secret(acct).get("previous_webhook_secret_until")
    if not raw:
        return None
    try:
        return _aware(datetime.fromisoformat(str(raw)))
    except ValueError:
        return None


def webhook_url(
    acct: ProviderAccount, kind: str = "payment", *, api_base: str | None = None
) -> str | None:
    """The full webhook URL for this account (``None`` when no secret is readable).

    The origin is the BACKEND's: ``PUBLIC_API_URL`` when set, else ``api_base``
    (the origin the API request reached — the backend, never the SPA host),
    else ``settings.public_api_url``. Review v12: the Mantenedor printed
    ``localhost:5500/api/...`` (the Vite host, which does not proxy ``/api``).
    """
    sig = account_sig(acct)
    if sig is None:
        return None
    query = urlencode({"account": acct.webhook_key, "sig": sig, "kind": kind})
    return f"{webhook_origin(api_base)}{settings.API_V1_PREFIX}/webhooks/{_val(acct.provider)}?{query}"


def webhook_origin(api_base: str | None = None) -> str:
    """Where a provider must POST: ``PUBLIC_API_URL`` > the request's own origin >
    the configured fallback. No trailing slash, no ``/api/v1``."""
    explicit = (settings.PUBLIC_API_URL or "").strip()
    if explicit:
        return explicit.rstrip("/")
    if api_base and api_base.strip():
        return api_base.strip().rstrip("/")
    return settings.public_api_url


__all__ = [
    "PREVIOUS_SECRET_GRACE",
    "WEBHOOK_KINDS",
    "WRITABLE_SECRET_KEYS",
    "account_sig",
    "compute_sig",
    "has_secret",
    "merge_secret_bundle",
    "new_webhook_key",
    "new_webhook_secret",
    "previous_secret_until",
    "rotate_webhook_secret",
    "secret_keys_set",
    "verify_sig",
    "webhook_origin",
    "webhook_secret",
    "webhook_url",
]
