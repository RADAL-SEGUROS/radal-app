"""``validate_policy_core`` — the fixed minimal-core check for a policy payload.

The v8 policy is read DYNAMICALLY (a full ``payload`` from ``PolicyExtraction``)
but a policy is only usable when a small, invariant core is present. This module
is that core check and NOTHING else: a PURE function, no AI, no DB writes, so it
is trivially testable and its verdict persists verbatim onto
``policy.is_core_valid`` / ``policy.core_validation``.

The core:

  * **corredor** — the placing broker must be named (the policy is the broker's
    own artifact; an issued policy with no corredor is malformed);
  * **vigencia / fecha** — a cover period start and end must be present;
  * **prima** — the Chilean premium block must RECONCILE through the shared
    :func:`app.schemas.proposal.reconcile_money` (never re-implemented here);
  * **asegurado** — CONDITIONALLY required: skipped when the account already
    carries a complete identity (a registered ``RecordExpediente`` / a client on
    the case), because the policy then inherits it and need not restate it.

The money math is delegated so there is exactly one implementation of the
Chilean premium invariants (rule 5).
"""
from __future__ import annotations

from typing import Any

from app.schemas.proposal import reconcile_money

__all__ = ["validate_policy_core", "MONEY_UF_KEYS"]

# The premium columns ``reconcile_money`` reasons about.
MONEY_UF_KEYS = (
    "taxable_premium_uf",
    "exempt_premium_uf",
    "net_premium_uf",
    "vat_uf",
    "total_premium_uf",
    "taxable_rate_permille",
    "exempt_rate_permille",
    "comprehensive_rate_permille",
)

# Candidate keys (searched recursively, case-insensitive) for each core signal.
_CORREDOR_KEYS = (
    "corredor",
    "corredor_name",
    "broker",
    "broker_name",
    "corredora",
    "insurance_broker",
)
_VIGENCIA_START_KEYS = (
    "coverage_start",
    "period_start_at",
    "start_at",
    "start_date",
    "vigencia_desde",
    "vigencia_inicio",
    "inicio_vigencia",
)
_VIGENCIA_END_KEYS = (
    "coverage_end",
    "period_end_at",
    "end_at",
    "end_date",
    "vigencia_hasta",
    "vigencia_termino",
    "termino_vigencia",
)
_ASEGURADO_KEYS = (
    "asegurado",
    "insured",
    "policyholder",
    "contratante",
    "insured_name",
    "asegurado_rut",
    "insured_rut",
)


def _walk(node: Any):
    """Yield every (key, value) pair anywhere in a nested dict/list payload."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _present(payload: Any, keys: tuple[str, ...]) -> bool:
    """True when any of ``keys`` appears anywhere with a non-empty value."""
    wanted = {k.lower() for k in keys}
    for key, value in _walk(payload):
        if not isinstance(key, str) or key.lower() not in wanted:
            continue
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict, tuple, set)) and len(value) == 0:
            continue
        return True
    return False


def _first_number(payload: Any, key: str) -> Any:
    """The first non-null value found for ``key`` anywhere in the payload."""
    for found_key, value in _walk(payload):
        if isinstance(found_key, str) and found_key.lower() == key and value is not None:
            return value
    return None


def _has_account_identity(case: Any) -> bool:
    """Does the account already carry a complete identity?

    Kept pure: reads an explicit flag off ``case`` (a dict or an object) rather
    than touching the DB. The caller (the router) decides completeness — e.g. a
    registered ``RecordExpediente`` or a resolved client — and passes it in.
    Accepted shapes: ``case["has_account_identity"]`` / ``case.has_account_identity``
    (preferred), or a truthy ``record_expediente_registered`` / ``client_id``.
    """
    if case is None:
        return False

    def _get(name: str) -> Any:
        if isinstance(case, dict):
            return case.get(name)
        return getattr(case, name, None)

    for flag in ("has_account_identity", "record_expediente_registered"):
        value = _get(flag)
        if value is not None:
            return bool(value)
    # A resolved client on the case counts as identity carried by the account.
    return bool(_get("client_id"))


def validate_policy_core(payload: dict[str, Any] | None, case: Any = None) -> dict[str, Any]:
    """Check a policy payload's fixed minimal core. PURE — no AI, no DB.

    Returns ``{is_core_valid, missing, detail}`` ready to persist onto
    ``policy.is_core_valid`` / ``policy.core_validation``. ``missing`` names the
    absent/failed core signals; ``detail`` carries the per-check booleans, the
    money errors from :func:`reconcile_money`, and whether the asegurado check
    was skipped because the account already holds the identity.
    """
    payload = payload or {}
    missing: list[str] = []

    has_corredor = _present(payload, _CORREDOR_KEYS)
    if not has_corredor:
        missing.append("corredor")

    has_start = _present(payload, _VIGENCIA_START_KEYS)
    has_end = _present(payload, _VIGENCIA_END_KEYS)
    if not (has_start and has_end):
        missing.append("vigencia")

    # Premium: reconcile the components found anywhere in the payload. A single
    # taxable figure derives the rest; contradictions come back as errors.
    money_values = {key: _first_number(payload, key) for key in MONEY_UF_KEYS}
    money_values = {k: v for k, v in money_values.items() if v is not None}
    money_errors: list[dict[str, Any]] = []
    prima_present = bool(
        money_values.get("total_premium_uf")
        or money_values.get("net_premium_uf")
        or money_values.get("taxable_premium_uf")
    )
    if not prima_present:
        missing.append("prima")
    else:
        try:
            _derived, money_errors = reconcile_money(money_values)
        except Exception:  # noqa: BLE001 - a malformed figure is a soft failure, never a raise
            money_errors = [{"field": "prima", "rule": "reconcile_money raised"}]
        if money_errors:
            missing.append("prima")

    account_has_identity = _has_account_identity(case)
    asegurado_required = not account_has_identity
    has_asegurado = _present(payload, _ASEGURADO_KEYS)
    if asegurado_required and not has_asegurado:
        missing.append("asegurado")

    detail = {
        "corredor_present": has_corredor,
        "vigencia_present": has_start and has_end,
        "vigencia_start_present": has_start,
        "vigencia_end_present": has_end,
        "prima_present": prima_present,
        "prima_reconciles": prima_present and not money_errors,
        "money_errors": money_errors,
        "asegurado_present": has_asegurado,
        "asegurado_required": asegurado_required,
        "asegurado_skipped": not asegurado_required,
        "account_has_identity": account_has_identity,
    }

    return {"is_core_valid": not missing, "missing": missing, "detail": detail}
