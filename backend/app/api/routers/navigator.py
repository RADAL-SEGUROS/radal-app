"""``/navigator`` — the main sidebar's data in one round trip (spec §4.2).

A router of its own, not a ``/account-groups/navigator`` sub-path: a literal
segment on a collection that also owns ``/{id}`` is a route-order trap, and the
navigator is a *view*, not a group resource.

The gate is ``CaseFiles.View``: the rail is a list of folders, and every count
in it is already narrowed to the cases the caller may see (a ``partial`` grant
narrows the SELECT, it does not hide the endpoint).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.models.user import User
from app.schemas.navigator import NavigatorResponse
from app.services import navigator as navigator_service

router = APIRouter(prefix="/navigator", tags=["navigator"])


@router.get("", response_model=NavigatorResponse)
def get_navigator(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "View")),
) -> NavigatorResponse:
    """Groups + vigencias + the most recent folders + the ANTECEDENTES mapping."""
    return navigator_service.build_navigator(db, current_user, broker_id)
