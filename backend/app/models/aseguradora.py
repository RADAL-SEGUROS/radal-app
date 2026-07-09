"""aseguradora — insurer catalog (tenant-scoped)."""
from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_class import Base


class Aseguradora(Base):
    __tablename__ = "aseguradora"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    corredora_id: Mapped[int] = mapped_column(
        ForeignKey("corredora.id"), nullable=False, index=True
    )
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    rut: Mapped[str | None] = mapped_column(String, nullable=True)
    sitio_pago_url: Mapped[str | None] = mapped_column(String, nullable=True)
