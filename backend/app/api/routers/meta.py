"""``/meta`` — what the SPA needs to know about this deployment (v12 copilot pass).

``GET /meta/features`` is readable by ANY authenticated user — broker and
platform alike (it names no tenant data), so the rail and every "pronto" chip
can render the server's reason instead of a hardcoded one (CLAUDE.md rule 3).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.meta import FeaturesRead
from app.services.features import features_payload

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/features", response_model=FeaturesRead)
def get_features(_user: User = Depends(get_current_user)) -> FeaturesRead:
    return FeaturesRead(**features_payload())
