"""``asset`` — the insurable object (bien asegurable). HYBRID storage.

Evidence (docs/v2-data-modeling-decisions.md §6): across the three real assets,
**10 attribute keys are common to all of them** and drive underwriting,
filtering and comparison — those are promoted to COLUMNS. The remaining tail is
genuinely type-specific (a plant has ``refrigeration``, a distribution centre has
``rack_height_m``, a clinic has ``operating_rooms``) and stays in ``attributes``
JSON, shaped by ``insurance_line.min_fields``.

Two of the promoted ten (``fire_protection`` and ``power_supply``) are
structured sub-objects in the source data, so they are JSON-typed columns rather
than scalars — still first-class columns, still one per concept.
"""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import StrEnum, sql_enum
from app.models.types import AREA, DISTANCE_KM, JSONType

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.client import Client
    from app.models.inspection import Inspection, InspectionRequest
    from app.models.placement import Placement


class AssetStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class Asset(Base, TimestampMixin):
    """A physical (or intangible) object the broker places cover for."""

    __tablename__ = "asset"
    __table_args__ = (
        Index("ix_asset_broker_client", "broker_id", "client_id"),
        Index("ix_asset_broker_status", "broker_id", "status"),
        Index("ix_asset_broker_type", "broker_id", "asset_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[int] = mapped_column(
        ForeignKey("client.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Open vocabulary (industrial_plant, distribution_centre, clinic, fleet, ...)
    # deliberately NOT an enum: new asset types must not need a migration.
    asset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(String(255))
    commune: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[AssetStatus] = mapped_column(
        sql_enum(AssetStatus), default=AssetStatus.ACTIVE, nullable=False
    )

    # --- The 10 promoted underwriting attributes (filterable in SQL) --------
    built_area_m2: Mapped[Decimal | None] = mapped_column(AREA)
    land_area_m2: Mapped[Decimal | None] = mapped_column(AREA)
    construction_year: Mapped[int | None] = mapped_column(Integer, index=True)
    structure: Mapped[str | None] = mapped_column(Text)
    floors: Mapped[int | None] = mapped_column(Integer)
    activity: Mapped[str | None] = mapped_column(Text)
    # Structured: {"wet_riser": true, "sprinklers": "...", "extinguishers": 46, ...}
    fire_protection: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    # Structured: {"service_kva": 1500, "generator_kva": 800, ...}
    power_supply: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    fire_station_distance_km: Mapped[Decimal | None] = mapped_column(DISTANCE_KM)
    seismic_zone: Mapped[str | None] = mapped_column(String(255))

    # --- The type-specific tail --------------------------------------------
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    # --- Relationships ------------------------------------------------------
    broker: Mapped["Broker"] = relationship(back_populates="assets")
    client: Mapped["Client"] = relationship(back_populates="assets")
    placements: Mapped[list["Placement"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )
    inspection_requests: Mapped[list["InspectionRequest"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )
    inspections: Mapped[list["Inspection"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Asset id={self.id} name={self.name!r}>"
