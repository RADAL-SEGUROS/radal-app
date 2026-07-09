"""usuario — a person who logs in (role-based access)."""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum as SQLEnum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.corredora import Corredora


class RolEnum(str, enum.Enum):
    admin_corredora = "admin_corredora"
    ejecutivo_corredora = "ejecutivo_corredora"
    inspector = "inspector"
    admin_asegurado = "admin_asegurado"
    ejecutivo_asegurado = "ejecutivo_asegurado"
    admin_aseguradora = "admin_aseguradora"
    ejecutivo_aseguradora = "ejecutivo_aseguradora"


class Usuario(Base):
    __tablename__ = "usuario"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    corredora_id: Mapped[int] = mapped_column(
        ForeignKey("corredora.id"), nullable=False, index=True
    )
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    cargo: Mapped[str] = mapped_column(String, nullable=False)
    rol: Mapped[RolEnum] = mapped_column(
        SQLEnum(RolEnum, native_enum=False, length=32, validate_strings=True),
        nullable=False,
    )
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    corredora: Mapped["Corredora"] = relationship(back_populates="usuarios")
