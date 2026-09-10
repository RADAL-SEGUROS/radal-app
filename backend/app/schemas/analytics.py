"""Shared analytics shapes: the uniform scope filters, the group-by buckets and
the export contract.

Three vocabularies live here, and every data table / analytics view in the app
speaks them:

1. **Scope filters** — ``account_group_id`` (the grupo), ``case_file_id`` (the
   grupo-cuenta) and ``date_from`` / ``date_to`` on each entity's natural date
   column. Declared as a FastAPI dependency in
   :mod:`app.services.analytics`; the schema half lives here so the export body
   can reuse it verbatim.
2. **Group-by buckets** — the stable chart payload
   ``{"group_by": ..., "buckets": [{"key", "label", "count", "total_uf"}], ...}``.
   Labels are *data* (an enum value, a group name, an insurer name, ``2026-09``),
   never translated prose: the frontend owns i18n (non-negotiable 1/4).
3. **Export request** — ``POST /exports/{entity}`` with ``format``, ``filters``,
   ``columns`` and ``group_by``.

Nothing here touches the database or an S3 key (non-negotiable 8): an export is
streamed, never filed as a ``document`` row.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# --- Scope filters -----------------------------------------------------------

class ScopeFilterPayload(BaseModel):
    """The uniform filter vocabulary, as a request body (used by the exports).

    Mirrors the query params every list / summary endpoint accepts. ``status``
    and the entity-specific extras are passed through ``extra`` so one body can
    serve thirteen entities without thirteen schemas.
    """

    model_config = ConfigDict(extra="forbid")

    account_group_id: int | None = Field(
        default=None, description="El grupo (account_group). Ajeno -> página vacía."
    )
    case_file_id: int | None = Field(
        default=None, description="El grupo-cuenta (case_file / expediente)."
    )
    date_from: date | None = Field(
        default=None, description="Desde (inclusive) sobre la fecha natural de la entidad."
    )
    date_to: date | None = Field(
        default=None, description="Hasta (inclusive) sobre la fecha natural de la entidad."
    )
    status: str | None = Field(
        default=None, max_length=64, description="Estado de la entidad, si aplica."
    )
    search: str | None = Field(default=None, max_length=120)


# --- Group-by buckets --------------------------------------------------------

class SummaryBucket(BaseModel):
    """One bar / slice of a grouped summary."""

    key: str = Field(description="Clave estable (enum, id o 'unassigned').")
    label: str = Field(description="Etiqueta legible derivada del dato, no traducida.")
    count: int
    total_uf: Decimal | None = Field(
        default=None, description="Suma de la columna de dinero de la entidad, si tiene."
    )


class GroupedSummary(BaseModel):
    """The chart-ready payload returned by ``?group_by=`` on a summary."""

    group_by: str
    dimensions: list[str] = Field(
        default_factory=list, description="Las dimensiones que esta entidad sabe responder."
    )
    money_field: str | None = Field(
        default=None, description="Qué columna suma ``total_uf`` (None = la entidad no lleva plata)."
    )
    total: int
    total_uf: Decimal | None = None
    buckets: list[SummaryBucket] = Field(default_factory=list)


class EntitySummary(BaseModel):
    """The generic summary returned by the entities that had no board of their own.

    (endorsements, collections, claims, documents, inspections, offerings)
    """

    entity: str
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)
    total_uf: Decimal | None = None
    grouped: GroupedSummary | None = None


# --- Exports -----------------------------------------------------------------

ExportFormat = Literal["xlsx", "pdf"]


class ExportRequest(BaseModel):
    """Body of ``POST /exports/{entity}``."""

    model_config = ConfigDict(extra="forbid")

    format: ExportFormat = Field(default="xlsx")
    filters: ScopeFilterPayload = Field(default_factory=ScopeFilterPayload)
    columns: list[str] | None = Field(
        default=None,
        description="Subconjunto ordenado de columnas; None = todas las de la entidad.",
    )
    group_by: str | None = Field(
        default=None,
        description=(
            "Si se indica, el archivo exporta los buckets agregados en vez de las filas."
        ),
    )


class ExportColumnInfo(BaseModel):
    """One exportable column, for the frontend's column picker."""

    key: str
    label: str
    kind: str


class ExportEntityInfo(BaseModel):
    """What ``GET /exports/entities`` advertises for one entity."""

    entity: str
    label: str
    module: str
    date_field: str
    money_field: str | None = None
    group_by: list[str] = Field(default_factory=list)
    columns: list[ExportColumnInfo] = Field(default_factory=list)
    row_cap: int


class ExportCatalog(BaseModel):
    formats: list[str]
    row_cap: int
    entities: list[ExportEntityInfo]


def unsupported_group_by(entity: str, group_by: str, supported: list[str]) -> dict[str, Any]:
    """The 422 body for a dimension the entity cannot answer."""
    return {
        "code": "unsupported_group_by",
        "message": (
            f"La entidad '{entity}' no puede agrupar por '{group_by}'. "
            f"Dimensiones disponibles: {', '.join(supported)}."
        ),
        "entity": entity,
        "group_by": group_by,
        "supported": supported,
    }


__all__ = [
    "EntitySummary",
    "ExportCatalog",
    "ExportColumnInfo",
    "ExportEntityInfo",
    "ExportFormat",
    "ExportRequest",
    "GroupedSummary",
    "ScopeFilterPayload",
    "SummaryBucket",
    "unsupported_group_by",
]
