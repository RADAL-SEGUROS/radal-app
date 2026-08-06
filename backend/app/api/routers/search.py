"""``/search`` — global search for the topbar.

One query across the broker's book: clients (by name or RUT), assets, quotes and
proposals. Results are grouped by kind so the UI can render a categorized
dropdown, and each hit carries the frontend ``url`` to navigate to.

Tenant scoping is absolute: every branch filters on the authenticated user's
``broker_id``. The canonical ``insured`` table is only reached through the
broker's own ``client`` rows, so one broker can never surface another's book.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.models.asset import Asset
from app.models.client import Client
from app.models.insured import Insured
from app.models.proposal import Proposal
from app.models.quote import QuoteRequest
from app.models.user import User
from app.services.identifiers import normalize_rut

router = APIRouter(prefix="/search", tags=["search"])

# Per-kind cap. The dropdown shows a handful per group; anything more belongs in
# the module's own list view with its filters.
_PER_KIND = 5


class SearchHit(BaseModel):
    id: int
    label: str
    sublabel: str | None = None
    url: str


class SearchResults(BaseModel):
    clients: list[SearchHit] = []
    assets: list[SearchHit] = []
    quotes: list[SearchHit] = []
    proposals: list[SearchHit] = []


def _rut_variants(term: str) -> list[str]:
    """A RUT may be typed with dots/dash or without; match either form."""
    variants = [term]
    try:
        normalized = normalize_rut(term)
        if normalized != term:
            variants.append(normalized)
    except Exception:  # noqa: BLE001 — not a RUT, plain text search is fine
        pass
    return variants


@router.get("", response_model=SearchResults)
def global_search(
    q: str = Query(min_length=2, max_length=120),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _: User = Depends(require_permission("Dashboard", "View")),
) -> SearchResults:
    """Search the broker's clients, assets, quotes and proposals."""
    term = q.strip()
    if not term:
        return SearchResults()
    like = f"%{term}%"

    # --- clients (by insured name/trade name, or RUT in either notation) -----
    rut_clauses = [Insured.rut.ilike(f"%{v}%") for v in _rut_variants(term)]
    clients = db.execute(
        select(Client)
        .join(Insured, Client.insured_id == Insured.id)
        .options(selectinload(Client.insured))
        .where(
            Client.broker_id == broker_id,
            or_(
                Insured.legal_name.ilike(like),
                Insured.trade_name.ilike(like),
                *rut_clauses,
            ),
        )
        .limit(_PER_KIND)
    ).scalars().all()

    # --- assets -------------------------------------------------------------
    assets = db.execute(
        select(Asset)
        .where(
            Asset.broker_id == broker_id,
            or_(Asset.name.ilike(like), Asset.address.ilike(like)),
        )
        .limit(_PER_KIND)
    ).scalars().all()

    # --- quotes -------------------------------------------------------------
    quotes = db.execute(
        select(QuoteRequest)
        .where(
            QuoteRequest.broker_id == broker_id,
            QuoteRequest.insured_object.ilike(like),
        )
        .limit(_PER_KIND)
    ).scalars().all()

    # --- proposals (by the issuing insurer's name) --------------------------
    proposals = db.execute(
        select(Proposal)
        .options(selectinload(Proposal.insurer))
        .where(Proposal.broker_id == broker_id)
        .limit(50)
    ).scalars().all()
    proposal_hits = [
        SearchHit(
            id=p.id,
            label=(p.insurer.legal_name if p.insurer else f"#{p.id}"),
            sublabel=(f"UF {p.total_premium_uf:,.2f}" if p.total_premium_uf else None),
            url=f"/proposals/{p.id}",
        )
        for p in proposals
        if p.insurer and term.lower() in (p.insurer.legal_name or "").lower()
    ][:_PER_KIND]

    return SearchResults(
        clients=[
            SearchHit(
                id=c.id,
                label=(c.insured.legal_name if c.insured else f"#{c.id}"),
                sublabel=(c.insured.rut if c.insured else None),
                url=f"/clients/{c.id}",
            )
            for c in clients
        ],
        assets=[
            SearchHit(id=a.id, label=a.name, sublabel=a.address, url=f"/assets/{a.id}")
            for a in assets
        ],
        quotes=[
            SearchHit(
                id=qr.id,
                label=qr.insured_object or f"#{qr.id}",
                sublabel=(
                    f"UF {qr.declared_value_uf:,.2f}" if qr.declared_value_uf else None
                ),
                url=f"/quotes/{qr.id}",
            )
            for qr in quotes
        ],
        proposals=proposal_hits,
    )
