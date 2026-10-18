"""``/meta`` — product feature flags (v12 copilot pass)."""
from __future__ import annotations

from pydantic import BaseModel


class FeatureFlag(BaseModel):
    enabled: bool
    #: Spanish, shown verbatim; ``None`` when the feature is on.
    reason: str | None = None


class FeaturesRead(BaseModel):
    portal_search: FeatureFlag
