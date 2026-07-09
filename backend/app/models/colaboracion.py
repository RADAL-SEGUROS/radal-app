"""Polymorphic collaboration tables: documento, observacion, actividad."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.usuario import Usuario


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Documento(Base):
    __tablename__ = "documento"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    corredora_id: Mapped[int] = mapped_column(
        ForeignKey("corredora.id"), nullable=False, index=True
    )
    entidad_tipo: Mapped[str] = mapped_column(String, nullable=False, index=True)
    entidad_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    tipo: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    autor_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    compartido_con: Mapped[list[Any] | None] = mapped_column(JSON, default=list, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False
    )

    autor: Mapped["Usuario"] = relationship("Usuario")


class Observacion(Base):
    __tablename__ = "observacion"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    corredora_id: Mapped[int] = mapped_column(
        ForeignKey("corredora.id"), nullable=False, index=True
    )
    entidad_tipo: Mapped[str] = mapped_column(String, nullable=False, index=True)
    entidad_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    autor_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    texto: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False
    )

    autor: Mapped["Usuario"] = relationship("Usuario")


class Actividad(Base):
    __tablename__ = "actividad"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    corredora_id: Mapped[int] = mapped_column(
        ForeignKey("corredora.id"), nullable=False, index=True
    )
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id"), nullable=True, index=True
    )
    accion: Mapped[str] = mapped_column(String, nullable=False)
    entidad_tipo: Mapped[str] = mapped_column(String, nullable=False, index=True)
    entidad_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    descripcion: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False
    )

    usuario: Mapped["Usuario | None"] = relationship("Usuario")
