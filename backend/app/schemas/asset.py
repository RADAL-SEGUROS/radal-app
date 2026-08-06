"""Pydantic v2 schemas for ``asset`` — the insurable object (bien asegurable).

Storage is HYBRID (docs/v2-data-modeling-decisions.md §6): the ten underwriting
attributes common to every asset are real COLUMNS (so they are filterable and
comparable in SQL), and the type-specific tail lives in ``attributes`` JSON,
shaped by ``insurance_line.min_fields``.

``fire_protection`` and ``power_supply`` are promoted too, but they are
structured sub-objects in the source data, so they are JSON-typed columns.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.asset import AssetStatus

Name = Annotated[str, Field(min_length=1, max_length=255)]
ShortText = Annotated[str, Field(max_length=255)]
Commune = Annotated[str, Field(max_length=120)]
Area = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
DistanceKm = Annotated[Decimal, Field(ge=0, max_digits=8, decimal_places=2)]
Year = Annotated[int, Field(ge=1500, le=2200)]
Floors = Annotated[int, Field(ge=0, le=500)]


class AssetAttributes(BaseModel):
    """The ten promoted underwriting attributes. Shared by create/update/read."""

    built_area_m2: Area | None = None
    land_area_m2: Area | None = None
    construction_year: Year | None = None
    structure: str | None = None
    floors: Floors | None = None
    activity: str | None = None
    # e.g. {"wet_riser": true, "sprinklers": "partial", "extinguishers": 46}
    fire_protection: dict[str, Any] | None = None
    # e.g. {"service_kva": 1500, "generator_kva": 800}
    power_supply: dict[str, Any] | None = None
    fire_station_distance_km: DistanceKm | None = None
    seismic_zone: ShortText | None = None


class AssetCreate(AssetAttributes):
    """Create an asset under one of the broker's clients.

    ``client_id`` is optional in the body only because the nested route
    ``POST /clients/{client_id}/assets`` supplies it from the path. On the flat
    ``POST /assets`` route it is required.
    """

    model_config = ConfigDict(extra="forbid")

    client_id: int | None = None
    # Open vocabulary (industrial_plant, distribution_centre, clinic, fleet...):
    # a new asset type must never require a migration.
    asset_type: Annotated[str, Field(min_length=1, max_length=64)]
    name: Name
    address: ShortText | None = None
    commune: Commune | None = None
    region: Commune | None = None
    status: AssetStatus = AssetStatus.ACTIVE
    # The type-specific tail, shaped by insurance_line.min_fields.
    attributes: dict[str, Any] | None = None


class AssetUpdate(AssetAttributes):
    """Patch an asset. Every field optional; ``client_id`` is not re-assignable."""

    model_config = ConfigDict(extra="forbid")

    asset_type: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    name: Name | None = None
    address: ShortText | None = None
    commune: Commune | None = None
    region: Commune | None = None
    status: AssetStatus | None = None
    attributes: dict[str, Any] | None = None


class AssetClientSummary(BaseModel):
    """Enough of the owning client to render a row without a second request."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    legal_name: str | None = None
    rut: str | None = None


class AssetListItem(BaseModel):
    """One row of the assets list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    client_id: int
    asset_type: str
    name: str
    address: str | None = None
    commune: str | None = None
    region: str | None = None
    status: AssetStatus
    built_area_m2: Decimal | None = None
    land_area_m2: Decimal | None = None
    construction_year: int | None = None
    activity: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    client: AssetClientSummary | None = None
    placements_count: int = 0
    active_placements_count: int = 0


class AssetRead(AssetListItem):
    """Full asset detail: the promoted columns plus the JSON tail."""

    structure: str | None = None
    floors: int | None = None
    fire_protection: dict[str, Any] | None = None
    power_supply: dict[str, Any] | None = None
    fire_station_distance_km: Decimal | None = None
    seismic_zone: str | None = None
    attributes: dict[str, Any] | None = None
    inspections_count: int = 0


class AssetPage(BaseModel):
    items: list[AssetListItem]
    total: int
    page: int
    page_size: int
    pages: int


class AssetSummary(BaseModel):
    """Aggregate counters for the assets list view."""

    total: int
    by_status: dict[str, int]
    by_type: dict[str, int]
    without_placements: int


__all__ = [
    "AssetAttributes",
    "AssetCreate",
    "AssetUpdate",
    "AssetClientSummary",
    "AssetListItem",
    "AssetRead",
    "AssetPage",
    "AssetSummary",
]
