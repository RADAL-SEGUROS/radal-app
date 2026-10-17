"""HDI Seguros — the first (showcase) portal connector (06-rpa §2).

Quotes ``VEH-01`` (Vehículos livianos). The connector is thin on purpose: the
portal itself is described by ``selectors/hdi.json`` (URLs, selectors, the step
list), which :class:`~app.services.rpa.interpreter.StepRunner` executes. When
HDI changes its portal, the fix is usually a JSON edit plus a ``version`` bump.

Input (validated before a run is queued, so a bad form is a 422, never a
browser launch): ``{plate, brand, model, year, use, commune, insured_rut,
start_date}``. ``use`` is ``particular`` | ``commercial`` (English values; the
map's ``value_map`` turns them into HDI's option values).
"""
from __future__ import annotations

import copy
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.identifiers import InvalidRut, validate_rut
from app.services.rpa.base import InputInvalid, PortalResult, RunContext
from app.services.rpa.interpreter import StepRunner, rows_to_quotes

SELECTORS_PATH = Path(__file__).resolve().parent.parent / "selectors" / "hdi.json"

_PLATE_RE = re.compile(r"^(?:[A-Z]{2}\d{4}|[A-Z]{4}\d{2})$")
USE_VALUES = ("particular", "commercial")

#: The form the frontend renders (labels live in the ``rpa`` i18n namespace).
INPUT_FIELDS: list[dict[str, Any]] = [
    {"name": "plate", "type": "text", "required": True},
    {"name": "brand", "type": "text", "required": True},
    {"name": "model", "type": "text", "required": True},
    {"name": "year", "type": "integer", "required": True},
    {"name": "use", "type": "select", "required": True, "options": list(USE_VALUES)},
    {"name": "commune", "type": "text", "required": True},
    {"name": "insured_rut", "type": "rut", "required": True},
    {"name": "start_date", "type": "date", "required": True},
]


def load_spec(path: Path = SELECTORS_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _text(data: dict[str, Any], name: str, errors: list[dict[str, str]], *, max_len: int, label: str) -> str:
    raw = data.get(name)
    value = str(raw).strip() if raw is not None else ""
    if not value:
        errors.append({"field": name, "message": f"Falta {label}."})
    elif len(value) > max_len:
        errors.append({"field": name, "message": f"{label.capitalize()} es demasiado largo (máximo {max_len})."})
    return value


def validate_vehicle_input(data: Any, *, today: date | None = None) -> dict[str, Any]:
    """Normalise and validate the VEH-01 form. Raises :class:`InputInvalid`."""
    if not isinstance(data, dict):
        raise InputInvalid([{"field": "input", "message": "El formulario de cotización es obligatorio."}])
    today = today or date.today()
    errors: list[dict[str, str]] = []

    plate = re.sub(r"[\s.\-·]", "", str(data.get("plate") or "")).upper()
    if not plate:
        errors.append({"field": "plate", "message": "Falta la patente."})
    elif not _PLATE_RE.match(plate):
        errors.append({"field": "plate", "message": "Patente inválida (ej. ABCD12 o AB1234)."})

    brand = _text(data, "brand", errors, max_len=60, label="la marca")
    model = _text(data, "model", errors, max_len=80, label="el modelo")
    commune = _text(data, "commune", errors, max_len=80, label="la comuna")

    year: int | None = None
    try:
        year = int(str(data.get("year")).strip())
        if year < 1950 or year > today.year + 1:
            errors.append({"field": "year", "message": f"Año fuera de rango (1950–{today.year + 1})."})
    except (TypeError, ValueError):
        errors.append({"field": "year", "message": "El año debe ser un número."})

    use = str(data.get("use") or "").strip().lower()
    if use not in USE_VALUES:
        errors.append({"field": "use", "message": "Uso inválido: particular o commercial."})

    insured_rut = ""
    try:
        insured_rut = validate_rut(str(data.get("insured_rut") or ""))
    except InvalidRut:
        errors.append({"field": "insured_rut", "message": "RUT del asegurado inválido."})

    start: date | None = None
    raw_start = data.get("start_date")
    try:
        start = raw_start if isinstance(raw_start, date) else date.fromisoformat(str(raw_start or "").strip()[:10])
    except ValueError:
        errors.append({"field": "start_date", "message": "Fecha de inicio inválida (AAAA-MM-DD)."})

    if errors:
        raise InputInvalid(errors)
    return {
        "plate": plate,
        "brand": brand,
        "model": model,
        "year": year,
        "use": use,
        "commune": commune,
        "insured_rut": insured_rut,
        "start_date": start.isoformat() if start else None,
    }


class HdiConnector:
    """``PortalConnector`` for HDI Seguros, driven by ``selectors/hdi.json``."""

    def __init__(self, spec: dict[str, Any] | None = None, *, base_url: str | None = None) -> None:
        self.spec = copy.deepcopy(spec) if spec is not None else load_spec()
        self._base_url = base_url
        self.key: str = self.spec.get("key", "hdi")
        self.name: str = self.spec.get("name", "HDI Seguros")
        self.version: str = str(self.spec.get("version", "0"))
        self.supported_subtypes: tuple[str, ...] = tuple(self.spec.get("supported_subtypes") or ())
        self.requires_credentials: bool = bool(self.spec.get("requires_credentials", True))

    @property
    def base_url(self) -> str:
        """``settings.RPA_HDI_BASE_URL`` (read live, so ops/tests can switch it)."""
        if self._base_url is not None:
            return self._base_url.strip()
        return (settings.RPA_HDI_BASE_URL or "").strip()

    def validate_input(self, data: dict[str, Any]) -> dict[str, Any]:
        return validate_vehicle_input(data)

    def input_fields(self) -> list[dict[str, Any]]:
        return copy.deepcopy(INPUT_FIELDS)

    def insurer_identity(self) -> dict[str, str]:
        ident = self.spec.get("insurer") or {}
        return {"rut": str(ident.get("rut") or ""), "cmf_code": str(ident.get("cmf_code") or "")}

    def _extract_columns(self) -> dict[str, dict[str, str]]:
        for step in self.spec.get("steps") or []:
            if step.get("op") == "extract_table":
                return step.get("columns") or {}
        return {}

    def run(self, ctx: RunContext) -> PortalResult:
        ctx.base_url = ctx.base_url or self.base_url
        rows = StepRunner(self.spec, ctx).run()
        return PortalResult(quotes=rows_to_quotes(rows, self._extract_columns()))


__all__ = ["HdiConnector", "INPUT_FIELDS", "SELECTORS_PATH", "USE_VALUES", "load_spec", "validate_vehicle_input"]
