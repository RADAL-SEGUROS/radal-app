"""Registry-driven extraction schemas — one Pydantic shape per document category.

Import by full path (``from app.schemas.extraction.registry import spec_for``);
``app/schemas/__init__.py`` deliberately does not re-export this package, so the
25 category modules stay lazy for anyone who only needs the proposal flow.

The registry itself is the contract: :func:`spec_for` gives the AI service the
schema, the Spanish prompt guidance and the prompt version; the importer uses
:func:`category_for_code`; the UI renders any suggestion form from
``registry_as_json()``.
"""
from __future__ import annotations

from app.schemas.extraction.common import (
    ExtractionModel,
    RootExtraction,
    parse_pct,
    parse_uf,
)
from app.schemas.extraction.registry import (
    CATEGORY_REGISTRY,
    CODE_TO_CATEGORY,
    CategorySpec,
    UnknownCategory,
    category_for_code,
    has_spec,
    registry_as_json,
    spec_for,
)

__all__ = [
    "CATEGORY_REGISTRY",
    "CODE_TO_CATEGORY",
    "CategorySpec",
    "ExtractionModel",
    "RootExtraction",
    "UnknownCategory",
    "category_for_code",
    "has_spec",
    "parse_pct",
    "parse_uf",
    "registry_as_json",
    "spec_for",
]
