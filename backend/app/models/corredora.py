"""corredora — tenant / broker org (multi-tenant root)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.usuario import Usuario
    from app.models.ramo import Ramo
    from app.models.cliente import Cliente


class Corredora(Base):
    __tablename__ = "corredora"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    rut: Mapped[str] = mapped_column(String, nullable=False)
    logo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    usuarios: Mapped[list["Usuario"]] = relationship(back_populates="corredora")
    ramos: Mapped[list["Ramo"]] = relationship(back_populates="corredora")
    clientes: Mapped[list["Cliente"]] = relationship(back_populates="corredora")
