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
    async def _on_startup() -> None:
        # Create all tables if they don't exist (no alembic this pass).
        Base.metadata.create_all(bind=engine)
        # Warm up the shared headless Chromium for HTML->PDF. Best-effort: the
        # app must still boot where Chromium isn't installed (tests/dev), so a
        # failure here is swallowed and the first PDF render lazy-launches (or
        # surfaces a clean PDFGenerationError -> 502). Skipped under pytest so
        # the (function-scoped, context-managed) test client never spins up a
        # browser per test — a real render in tests is mocked anyway.
        import sys

        if "pytest" not in sys.modules:
            try:
                from app.services.pdf import ensure_browser

                await ensure_browser()
            except Exception:  # noqa: BLE001 - never block boot on the PDF engine
                pass

    @app.on_event("shutdown")
    async def _on_shutdown() -> None:
        from app.services.pdf import close_browser

        await close_browser()

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
    from app.api.routers import broker_proposals, comparisons, proposals, quotes

    app.include_router(quotes.router, prefix=prefix)
    app.include_router(proposals.router, prefix=prefix)
    # v8 broker-journey redesign: the incremental comparison worktable and the
    # outbound broker propuesta built solely from it.
    app.include_router(comparisons.router, prefix=prefix)
    app.include_router(broker_proposals.router, prefix=prefix)

    # --- AI pipeline: extraction (suggest -> confirm) + agent chat -----------
    from app.api.routers import ai as ai_router

    app.include_router(ai_router.router, prefix=prefix)

    # --- Antecedentes expediente (v6): consolidate -> register -> PDF --------
    from app.api.routers import antecedentes

    app.include_router(antecedentes.router, prefix=prefix)
    app.include_router(antecedentes.schemas_router, prefix=prefix)

    # --- Inspections + documents (S3) + offerings ---------------------------
    from app.api.routers import documents, inspections, offerings

    app.include_router(inspections.requests_router, prefix=prefix)
    app.include_router(inspections.router, prefix=prefix)
    app.include_router(documents.router, prefix=prefix)
    app.include_router(offerings.router, prefix=prefix)
    # Unauthenticated share link — the offering's share_token IS the credential.
    app.include_router(offerings.public_router, prefix=prefix)

    # --- Expedientes: case files, leads, notes/activity, packs --------------
    from app.api.routers import case_files, leads, notes, packs

    app.include_router(case_files.router, prefix=prefix)
    app.include_router(leads.router, prefix=prefix)
    app.include_router(notes.router, prefix=prefix)
    app.include_router(notes.activities_router, prefix=prefix)
    app.include_router(packs.router, prefix=prefix)
    app.include_router(packs.packs_router, prefix=prefix)

    # --- Groups & the navigator (v3): the folder above the expediente -------
    from app.api.routers import account_groups, navigator

    app.include_router(account_groups.router, prefix=prefix)
    # A router of its own: a literal path on a collection that also owns
    # ``/{id}`` is a route-order trap.
    app.include_router(navigator.router, prefix=prefix)

    # --- Post-sale: policies, endorsements, collections, claims -------------
    from app.api.routers import claims, collections, endorsements, policies

    app.include_router(policies.router, prefix=prefix)
    app.include_router(policies.warranties_router, prefix=prefix)
    # The policy's post-sale sub-funnel is a case-file view under /policies.
    app.include_router(case_files.policy_cases_router, prefix=prefix)
    app.include_router(endorsements.router, prefix=prefix)
    app.include_router(collections.router, prefix=prefix)
    app.include_router(claims.router, prefix=prefix)

    return app


app = create_app()
