"""``currency_rate`` — the daily UF / USD observed values (v12). PLATFORM table.

Loaded by the ``fx.daily`` job from ``settings.FX_SOURCE_URL`` (mindicador.cl).
A CLP payment is converted to UF with the row of its ``paid_on`` date; the rate
used is copied onto ``payment.uf_rate`` so a later correction of this table
never rewrites a registered payment.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_class import Base, TimestampMixin
from app.models.types import UF


class CurrencyRate(Base, TimestampMixin):
    """One business date's UF and USD (dólar observado) in CLP. No broker_id."""

    __tablename__ = "currency_rate"

    id: Mapped[int] = mapped_column(primary_key=True)
    rate_date: Mapped[date] = mapped_column(Date, unique=True, index=True, nullable=False)
    # CLP per 1 UF.
    uf: Mapped[Decimal | None] = mapped_column(UF)
    # CLP per 1 USD (dólar observado).
    usd_observed: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    source: Mapped[str | None] = mapped_column(String(32))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CurrencyRate {self.rate_date} uf={self.uf} usd={self.usd_observed}>"


__all__ = ["CurrencyRate"]
