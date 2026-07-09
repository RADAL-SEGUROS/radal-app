"""Ramos config router — working list + CRUD. /api/v1/ramos."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_corredora_id,
    get_current_user,
    require_admin_corredora,
)
from app.db.session import get_db
from app.models.ramo import Ramo
from app.models.usuario import Usuario

router = APIRouter(prefix="/ramos", tags=["ramos"])


class RamoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    requiere_inspeccion: bool
    campos_min: dict[str, Any] | None = None
    reglas_presuscripcion: dict[str, Any] | None = None
    campos_comparador: dict[str, Any] | None = None


class RamoCreate(BaseModel):
    nombre: str
    requiere_inspeccion: bool = False
    campos_min: dict[str, Any] = Field(default_factory=dict)
    reglas_presuscripcion: dict[str, Any] = Field(default_factory=dict)
    campos_comparador: dict[str, Any] = Field(default_factory=dict)


class RamoUpdate(BaseModel):
    nombre: str | None = None
    requiere_inspeccion: bool | None = None
    campos_min: dict[str, Any] | None = None
    reglas_presuscripcion: dict[str, Any] | None = None
    campos_comparador: dict[str, Any] | None = None


@router.get("", response_model=list[RamoOut])
def list_ramos(
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(get_current_user),
) -> list[Ramo]:
    rows = db.execute(
        select(Ramo).where(Ramo.corredora_id == corredora_id).order_by(Ramo.nombre)
    ).scalars().all()
    return list(rows)


@router.get("/{ramo_id}", response_model=RamoOut)
def get_ramo(
    ramo_id: int,
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(get_current_user),
) -> Ramo:
    row = db.get(Ramo, ramo_id)
    if row is None or row.corredora_id != corredora_id:
        raise HTTPException(status_code=404, detail="Ramo no encontrado")
    return row


@router.post("", response_model=RamoOut, status_code=status.HTTP_201_CREATED)
def create_ramo(
    payload: RamoCreate,
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(require_admin_corredora),
) -> Ramo:
    row = Ramo(corredora_id=corredora_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{ramo_id}", response_model=RamoOut)
def update_ramo(
    ramo_id: int,
    payload: RamoUpdate,
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(require_admin_corredora),
) -> Ramo:
    row = db.get(Ramo, ramo_id)
    if row is None or row.corredora_id != corredora_id:
        raise HTTPException(status_code=404, detail="Ramo no encontrado")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{ramo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ramo(
    ramo_id: int,
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(require_admin_corredora),
) -> Response:
    row = db.get(Ramo, ramo_id)
    if row is None or row.corredora_id != corredora_id:
        raise HTTPException(status_code=404, detail="Ramo no encontrado")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
