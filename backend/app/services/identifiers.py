"""Identifier validation & normalization for Chilean RUT and CMF código.

RUT (Rol Único Tributario) — validated with the módulo-11 algorithm (weights
cycling 2..7 over the reversed body). Stored canonically as ``BODY-DV`` (no dots,
uppercase K, no leading zeros).

CMF código — the public CMF registry is RUT-keyed and there is no stable public
"código de corredor" API, so here we only validate a permissive *format* and
expose a manual verification hook (``verify_codigo_cmf_registry``) that a human /
future integration can implement. See docs/data-model-v2.md §2.2.
"""
from __future__ import annotations

import re

__all__ = [
    "InvalidRut",
    "InvalidCmf",
    "normalize_rut",
    "compute_dv",
    "is_valid_rut",
    "validate_rut",
    "format_rut",
    "validate_codigo_cmf",
    "normalize_codigo_cmf",
    "verify_codigo_cmf_registry",
]


class InvalidRut(ValueError):
    """Raised when a RUT fails format or módulo-11 validation."""


class InvalidCmf(ValueError):
    """Raised when a CMF código fails format validation."""


_RUT_CLEAN_RE = re.compile(r"[.\s]")
_RUT_SHAPE_RE = re.compile(r"^(\d+)-([\dkK])$")


def _strip(rut: str) -> str:
    """Remove dots/spaces and uppercase, keeping the hyphen if present."""
    return _RUT_CLEAN_RE.sub("", (rut or "").strip()).upper()


def compute_dv(body: str) -> str:
    """Compute the módulo-11 check digit for a numeric RUT body.

    Returns "0".."9" or "K".
    """
    if not body.isdigit():
        raise InvalidRut("El cuerpo del RUT debe ser numérico")
    total = 0
    factor = 2
    for digit in reversed(body):
        total += int(digit) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = 11 - (total % 11)
    if resto == 11:
        return "0"
    if resto == 10:
        return "K"
    return str(resto)


def normalize_rut(rut: str) -> str:
    """Normalize to canonical ``BODY-DV`` form.

    - Strips dots/spaces, uppercases K.
    - Strips leading zeros from the body.
    - Accepts input with or without the hyphen (last char is the DV).
    - Does NOT validate the check digit (use :func:`validate_rut` for that).
    """
    cleaned = _strip(rut)
    if not cleaned:
        raise InvalidRut("RUT vacío")

    if "-" in cleaned:
        m = _RUT_SHAPE_RE.match(cleaned)
        if not m:
            raise InvalidRut(f"Formato de RUT inválido: {rut!r}")
        body, dv = m.group(1), m.group(2)
    else:
        if len(cleaned) < 2:
            raise InvalidRut(f"RUT demasiado corto: {rut!r}")
        body, dv = cleaned[:-1], cleaned[-1]

    if not body.isdigit() or dv not in "0123456789K":
        raise InvalidRut(f"Formato de RUT inválido: {rut!r}")

    body = body.lstrip("0") or "0"
    return f"{body}-{dv}"


def is_valid_rut(rut: str) -> bool:
    """True iff `rut` has a valid format AND a correct módulo-11 check digit."""
    try:
        validate_rut(rut)
        return True
    except InvalidRut:
        return False


def validate_rut(rut: str) -> str:
    """Validate format + módulo-11 and return the normalized ``BODY-DV``.

    Raises :class:`InvalidRut` on any failure.
    """
    normalized = normalize_rut(rut)
    body, dv = normalized.split("-", 1)
    expected = compute_dv(body)
    if dv != expected:
        raise InvalidRut(
            f"Dígito verificador inválido para {body}: esperado {expected}, recibido {dv}"
        )
    return normalized


def format_rut(rut: str) -> str:
    """Render a (normalized-or-not) RUT for display as ``12.345.678-5``."""
    normalized = normalize_rut(rut)
    body, dv = normalized.split("-", 1)
    # Insert thousands separators from the right.
    groups = []
    while len(body) > 3:
        groups.insert(0, body[-3:])
        body = body[:-3]
    groups.insert(0, body)
    return f"{'.'.join(groups)}-{dv}"


# --- CMF código --------------------------------------------------------------

# Permissive format: 3–20 chars of digits, letters, dot or hyphen; or the
# sentinel "TBD" (Radal's código is pending — see A2 in data-model-v2.md).
_CMF_RE = re.compile(r"^[A-Za-z0-9.\-]{3,20}$")
_CMF_TBD = "TBD"


def normalize_codigo_cmf(codigo: str) -> str:
    """Trim/uppercase a CMF código; leaves the sentinel 'TBD' as-is."""
    c = (codigo or "").strip()
    if c.upper() == _CMF_TBD:
        return _CMF_TBD
    return c.upper()


def validate_codigo_cmf(codigo: str, *, allow_tbd: bool = True) -> str:
    """Validate the *format* of a CMF código and return it normalized.

    The CMF registry is RUT-keyed with no stable public API, so this only checks
    a permissive shape. Cross-checking against the real registry is a manual /
    future hook (:func:`verify_codigo_cmf_registry`). Raises :class:`InvalidCmf`.
    """
    normalized = normalize_codigo_cmf(codigo)
    if normalized == _CMF_TBD:
        if allow_tbd:
            return _CMF_TBD
        raise InvalidCmf("Código CMF pendiente (TBD) no permitido aquí")
    if not _CMF_RE.match(normalized):
        raise InvalidCmf(f"Formato de código CMF inválido: {codigo!r}")
    return normalized


def verify_codigo_cmf_registry(rut: str, codigo_cmf: str) -> dict[str, str | None]:
    """Hook to cross-check a corredora/aseguradora against the CMF registry.

    STUB — the CMF public registry has no stable API; verification is *manual*
    for now (a human confirms against https://www.cmfchile.cl by RUT and records
    ``cmf_estado`` / ``cmf_verified_at``). Kept as a single injection point so a
    future scraper/certificate integration is a one-function change.

    Returns a dict describing the (currently unknown) verification result.
    """
    return {
        "rut": normalize_rut(rut) if rut else None,
        "codigo_cmf": normalize_codigo_cmf(codigo_cmf) if codigo_cmf else None,
        "cmf_estado": "unknown",
        "cmf_verification_method": "manual",
        "detalle": "Verificación CMF manual/TBD (sin API pública estable).",
    }
