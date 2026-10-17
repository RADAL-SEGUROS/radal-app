"""Pydantic v2 schemas for the closed ramo/subtipo catalogue (v12, 02 §1).

Read-only: brokers never write the catalogue. ``name`` is Spanish display DATA
seeded from the team CSV, not an identifier.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import PlacementRoute, SuggestedEntry


class SubtypeRead(BaseModel):
    """One subtipo, e.g. ``PRO-02`` Todo Riesgo."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    suggested_entry: SuggestedEntry
    suggested_route: PlacementRoute
    cmf_codes: list[Any] = Field(default_factory=list)

    @field_validator("cmf_codes", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return [] if value is None else value


class SubtypeDetail(SubtypeRead):
    """``GET /catalog/subtypes/{id}`` — the subtipo plus its ramo."""

    insurance_line_id: int
    line_code: str | None = None
    line_name: str | None = None
    is_active: bool = True


class CatalogLineRead(BaseModel):
    """One global ramo with its active subtipos, in catalogue order."""

    id: int
    code: str | None = None
    name: str
    sort_order: int = 0
    subtypes: list[SubtypeRead] = Field(default_factory=list)


class SubtypeRef(BaseModel):
    """The compact ``{id, code, name}`` reference rows and lists embed."""

    id: int
    code: str
    name: str


class LineRef(BaseModel):
    id: int
    code: str | None = None
    name: str


__all__ = ["CatalogLineRead", "LineRef", "SubtypeDetail", "SubtypeRead", "SubtypeRef"]
