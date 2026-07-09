"""Radal backend app factory: FastAPI, CORS, create_all, routers, health, search."""
from __future__ import annotations

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_corredora_id, get_current_user
from app.api.routers import (
    aseguradoras,
    auth,
    clientes,
    cotizaciones,
    dashboard,
    inspecciones,
    polizas,
    ramos,
    renovaciones,
    siniestros,
)
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine, get_db
from app.models.activo import Activo
from app.models.cliente import Cliente
from app.models.poliza import Poliza
from app.models.usuario import Usuario


def create_app() -> FastAPI:
    app = FastAPI(title=settings.PROJECT_NAME, version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def _on_startup() -> None:
        # Create all tables if they don't exist (no alembic this pass).
        Base.metadata.create_all(bind=engine)

    prefix = settings.API_V1_PREFIX

    # Auth + module routers, all under /api/v1
    app.include_router(auth.router, prefix=prefix)
    app.include_router(dashboard.router, prefix=prefix)
    app.include_router(clientes.router, prefix=prefix)
    app.include_router(polizas.router, prefix=prefix)
    app.include_router(renovaciones.router, prefix=prefix)
    app.include_router(cotizaciones.router, prefix=prefix)
    app.include_router(siniestros.router, prefix=prefix)
    app.include_router(inspecciones.router, prefix=prefix)
    app.include_router(aseguradoras.router, prefix=prefix)
    app.include_router(ramos.router, prefix=prefix)

    @app.get(f"{prefix}/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "radal-backend"}

    @app.get(f"{prefix}/search", tags=["search"])
    def search(
        q: str = Query("", description="término de búsqueda"),
        categorias: str | None = Query(
            None, description="clientes,polizas,activos"
        ),
        db: Session = Depends(get_db),
        corredora_id: int = Depends(get_current_corredora_id),
        _: Usuario = Depends(get_current_user),
    ) -> dict[str, list[dict]]:
        """Real-time global search, categorized and clickable to detail."""
        term = (q or "").strip()
        wanted = (
            {c.strip() for c in categorias.split(",") if c.strip()}
            if categorias
            else {"clientes", "polizas", "activos"}
        )
        result: dict[str, list[dict]] = {"clientes": [], "polizas": [], "activos": []}
        if not term:
            return result

        like = f"%{term}%"

        if "clientes" in wanted:
            rows = db.execute(
                select(Cliente)
                .where(
                    Cliente.corredora_id == corredora_id,
                    (Cliente.nombre.ilike(like)) | (Cliente.rut.ilike(like)),
                )
                .limit(10)
            ).scalars().all()
            result["clientes"] = [
                {"id": c.id, "nombre": c.nombre, "url": f"/clientes/{c.id}"} for c in rows
            ]

        if "polizas" in wanted:
            rows = db.execute(
                select(Poliza)
                .where(
                    Poliza.corredora_id == corredora_id,
                    Poliza.numero_poliza.ilike(like),
                )
                .limit(10)
            ).scalars().all()
            result["polizas"] = [
                {
                    "id": p.id,
                    "numero_poliza": p.numero_poliza,
                    "cliente": p.cliente.nombre if p.cliente else None,
                    "url": f"/polizas/{p.id}",
                }
                for p in rows
            ]

        if "activos" in wanted:
            rows = db.execute(
                select(Activo)
                .where(
                    Activo.corredora_id == corredora_id,
                    (Activo.nombre.ilike(like)) | (Activo.direccion.ilike(like)),
                )
                .limit(10)
            ).scalars().all()
            result["activos"] = [
                {
                    "id": a.id,
                    "nombre": a.nombre,
                    "cliente": a.cliente.nombre if a.cliente else None,
                    "url": f"/clientes/{a.cliente_id}",
                }
                for a in rows
            ]

        return result

    return app


app = create_app()
