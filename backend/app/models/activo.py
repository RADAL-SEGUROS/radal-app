"""activo — NIVEL-2 insurable good."""
from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import Enum as SQLEnum, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.cliente import Cliente
    from app.models.proceso_ramo import ProcesoRamo


class ActivoEstadoEnum(str, enum.Enum):
    activo = "activo"
    en_evaluacion = "en_evaluacion"
    desactivado = "desactivado"
    archivado = "archivado"


class Activo(Base):
    __tablename__ = "activo"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    corredora_id: Mapped[int] = mapped_column(
        ForeignKey("corredora.id"), nullable=False, index=True
    )
    cliente_id: Mapped[int] = mapped_column(
        ForeignKey("cliente.id"), nullable=False, index=True
    )
    tipo_activo: Mapped[str] = mapped_column(String, nullable=False)
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    direccion: Mapped[str | None] = mapped_column(String, nullable=True)
    atributos: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict, nullable=True)
    estado: Mapped[ActivoEstadoEnum] = mapped_column(
        SQLEnum(ActivoEstadoEnum, native_enum=False, length=20, validate_strings=True),
        default=ActivoEstadoEnum.activo,
        nullable=False,
    )

    cliente: Mapped["Cliente"] = relationship(back_populates="activos")
    proceso_ramos: Mapped[list["ProcesoRamo"]] = relationship(back_populates="activo")
