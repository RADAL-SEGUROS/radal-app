"""cliente — NIVEL-1 expediente (the asegurado as a client)."""
from __future__ import annotations

import enum
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Enum as SQLEnum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.corredora import Corredora
    from app.models.usuario import Usuario
    from app.models.activo import Activo
    from app.models.poliza import Poliza
    from app.models.asegurado_adicional import AseguradoAdicional


class ClienteEstadoEnum(str, enum.Enum):
    activo = "activo"
    onboarding = "onboarding"
    prospecto = "prospecto"
    suspendido = "suspendido"
    archivado = "archivado"


class Cliente(Base):
    __tablename__ = "cliente"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    corredora_id: Mapped[int] = mapped_column(
        ForeignKey("corredora.id"), nullable=False, index=True
    )
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    rut: Mapped[str] = mapped_column(String, nullable=False)
    sector: Mapped[str | None] = mapped_column(String, nullable=True)
    estado: Mapped[ClienteEstadoEnum] = mapped_column(
        SQLEnum(ClienteEstadoEnum, native_enum=False, length=20, validate_strings=True),
        default=ClienteEstadoEnum.prospecto,
        nullable=False,
    )
    contacto_principal: Mapped[str | None] = mapped_column(String, nullable=True)
    telefono: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    ejecutivo_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id"), nullable=True, index=True
    )
    fecha_alta: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    corredora: Mapped["Corredora"] = relationship(back_populates="clientes")
    ejecutivo: Mapped["Usuario | None"] = relationship("Usuario")
    activos: Mapped[list["Activo"]] = relationship(back_populates="cliente")
    polizas: Mapped[list["Poliza"]] = relationship(back_populates="cliente")
    asegurados_adicionales: Mapped[list["AseguradoAdicional"]] = relationship(
        back_populates="cliente"
    )
