"""``/catalog`` — the closed ramo/subtipo catalogue (v12, 02 §1).

Owner: lane **B1**. Read-only and GLOBAL: any authenticated user (broker or
platform) may read it; nobody writes it through the API. The rows are seeded by
``app.db.seed_v12.seed_catalog`` from the team CSV.

    GET /catalog/lines            global active ramos + their active subtipos, ordered
    GET /catalog/subtypes/{id}    one active subtipo (404 for unknown / retired)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.insurance_line import InsuranceLine
from app.models.user import User
from app.schemas.catalog import CatalogLineRead, SubtypeDetail, SubtypeRead
from app.services import catalog as catalog_service

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/lines", response_model=list[CatalogLineRead])
def list_catalog_lines(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[CatalogLineRead]:
    """The 18 ramos with their subtipos, in the catalogue's order (the cascade)."""
    out: list[CatalogLineRead] = []
    for line in catalog_service.catalog_lines(db):
        subtypes = sorted(
            (s for s in line.subtypes if s.is_active),
            key=lambda s: (s.sort_order, s.id),
        )
        out.append(
            CatalogLineRead(
                id=line.id,
                code=line.code,
                name=line.name,
                sort_order=line.sort_order,
                subtypes=[SubtypeRead.model_validate(s) for s in subtypes],
            )
        )
    return out


@router.get("/subtypes/{subtype_id}", response_model=SubtypeDetail)
def get_catalog_subtype(
    subtype_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> SubtypeDetail:
    row = catalog_service.get_subtype(db, subtype_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Subtipo no encontrado"
        )
    line = db.get(InsuranceLine, row.insurance_line_id)
    return SubtypeDetail(
        **SubtypeRead.model_validate(row).model_dump(),
        insurance_line_id=row.insurance_line_id,
        line_code=line.code if line else None,
        line_name=line.name if line else None,
        is_active=row.is_active,
    )


__all__ = ["router"]
