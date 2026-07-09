"""Aseguradoras catalog router — working list + CRUD. /api/v1/aseguradoras."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_corredora_id,
    get_current_user,
    require_admin_corredora,
)
from app.db.session import get_db
from app.models.aseguradora import Aseguradora
from app.models.usuario import Usuario

router = APIRouter(prefix="/aseguradoras", tags=["aseguradoras"])


class AseguradoraOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    rut: str | None = None
    sitio_pago_url: str | None = None


class AseguradoraCreate(BaseModel):
    nombre: str
    rut: str | None = None
    sitio_pago_url: str | None = None


class AseguradoraUpdate(BaseModel):
    nombre: str | None = None
    rut: str | None = None
    sitio_pago_url: str | None = None


@router.get("", response_model=list[AseguradoraOut])
def list_aseguradoras(
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(get_current_user),
) -> list[Aseguradora]:
    rows = db.execute(
        select(Aseguradora)
        .where(Aseguradora.corredora_id == corredora_id)
        .order_by(Aseguradora.nombre)
    ).scalars().all()
    return list(rows)


@router.get("/{aseguradora_id}", response_model=AseguradoraOut)
def get_aseguradora(
    aseguradora_id: int,
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(get_current_user),
) -> Aseguradora:
    row = db.get(Aseguradora, aseguradora_id)
    if row is None or row.corredora_id != corredora_id:
        raise HTTPException(status_code=404, detail="Aseguradora no encontrada")
    return row


@router.post("", response_model=AseguradoraOut, status_code=status.HTTP_201_CREATED)
def create_aseguradora(
    payload: AseguradoraCreate,
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(require_admin_corredora),
) -> Aseguradora:
    row = Aseguradora(corredora_id=corredora_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{aseguradora_id}", response_model=AseguradoraOut)
def update_aseguradora(
    aseguradora_id: int,
    payload: AseguradoraUpdate,
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(require_admin_corredora),
) -> Aseguradora:
    row = db.get(Aseguradora, aseguradora_id)
    if row is None or row.corredora_id != corredora_id:
        raise HTTPException(status_code=404, detail="Aseguradora no encontrada")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{aseguradora_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_aseguradora(
    aseguradora_id: int,
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(require_admin_corredora),
) -> Response:
    row = db.get(Aseguradora, aseguradora_id)
    if row is None or row.corredora_id != corredora_id:
        raise HTTPException(status_code=404, detail="Aseguradora no encontrada")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
