"""Radal backend app factory: FastAPI, CORS, create_all, health.

v2 skeleton. Routers are mounted by the API pass — each one registers itself in
``app.api.routers`` and is included below. Keep this module import-clean: it must
never depend on a model or router that does not exist yet.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import auth, insurers, search, users
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine


def create_app() -> FastAPI:
    app = FastAPI(title=settings.PROJECT_NAME, version="2.0.0")

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

    @app.get(f"{prefix}/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "radal-backend", "version": "2.0.0"}

    # Routers are added here by the API pass, e.g.:
    #     app.include_router(auth.router, prefix=prefix)

    # --- Broker workspace: clients + assets + placements --------------------
    from app.api.routers import assets, clients, placements

    app.include_router(clients.router, prefix=prefix)
    app.include_router(assets.router, prefix=prefix)
    app.include_router(assets.client_assets_router, prefix=prefix)
    app.include_router(placements.router, prefix=prefix)
    app.include_router(auth.router, prefix=prefix)
    app.include_router(users.router, prefix=prefix)
    app.include_router(insurers.router, prefix=prefix)
    app.include_router(search.router, prefix=prefix)

    # --- Quotes + proposals (the core) --------------------------------------
    from app.api.routers import proposals, quotes

    app.include_router(quotes.router, prefix=prefix)
    app.include_router(proposals.router, prefix=prefix)

    # --- AI pipeline: extraction (suggest -> confirm) + agent chat -----------
    from app.api.routers import ai as ai_router

    app.include_router(ai_router.router, prefix=prefix)

    # --- Inspections + documents (S3) + offerings ---------------------------
    from app.api.routers import documents, inspections, offerings

    app.include_router(inspections.requests_router, prefix=prefix)
    app.include_router(inspections.router, prefix=prefix)
    app.include_router(documents.router, prefix=prefix)
    app.include_router(offerings.router, prefix=prefix)
    # Unauthenticated share link — the offering's share_token IS the credential.
    app.include_router(offerings.public_router, prefix=prefix)

    return app


app = create_app()
