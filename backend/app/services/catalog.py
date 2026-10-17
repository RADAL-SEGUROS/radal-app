"""The closed ramo/subtipo catalogue (v12, 02 §1) — read helpers.

The catalogue is GLOBAL: 18 ``insurance_line`` rows with ``broker_id IS NULL``
and a ``code``, each with its ``insurance_subtype`` rows. No broker may create a
ramo any more (the product must be able to trace which ramos are processed
most); every broker path that used to mint one now answers 403 with
:data:`CLOSED_CATALOGUE_DETAIL`.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.catalog import InsuranceSubtype
from app.models.insurance_line import InsuranceLine

#: The 403 every broker ramo-create/edit path returns (02 §1, verbatim).
CLOSED_CATALOGUE_DETAIL = (
    "Los ramos son un catálogo cerrado de Radal. Pide uno nuevo a soporte."
)

#: 422 when a subtipo id is unknown or retired.
SUBTYPE_INVALID_DETAIL = "El subtipo indicado no existe o ya no está activo en el catálogo."


def catalog_lines(db: Session) -> list[InsuranceLine]:
    """The global, active ramos in catalogue order, subtipos eager-loaded."""
    return list(
        db.scalars(
            select(InsuranceLine)
            .where(
                InsuranceLine.broker_id.is_(None),
                InsuranceLine.code.is_not(None),
                InsuranceLine.is_active.is_(True),
            )
            .options(selectinload(InsuranceLine.subtypes))
            .order_by(InsuranceLine.sort_order.asc(), InsuranceLine.id.asc())
        ).all()
    )


def get_subtype(db: Session, subtype_id: int, *, active_only: bool = True) -> InsuranceSubtype | None:
    """A subtipo of a GLOBAL ramo, or ``None`` (unknown, retired or orphaned)."""
    row = db.get(InsuranceSubtype, subtype_id)
    if row is None:
        return None
    if active_only and not row.is_active:
        return None
    line = db.get(InsuranceLine, row.insurance_line_id)
    if line is None or line.broker_id is not None:
        return None
    if active_only and not line.is_active:
        return None
    return row


__all__ = [
    "CLOSED_CATALOGUE_DETAIL",
    "SUBTYPE_INVALID_DETAIL",
    "catalog_lines",
    "get_subtype",
]
