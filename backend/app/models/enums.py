"""Shared enum vocabulary + the SQL enum factory used by every model module.

All enum *values* are English lowercase snake_case. They are persisted as
strings (``native_enum=False``) with an explicit VARCHAR length so the schema
compiles identically on SQLite (local) and MySQL (RDS).

Why ``values_callable``: by default SQLAlchemy stores the Python member *name*.
We always want the member *value*, so member names can stay uppercase (PEP 8)
while the database holds the lowercase English token.
"""
from __future__ import annotations

import enum
import re
from typing import Type, TypeVar

from sqlalchemy import Enum as SQLEnum

_E = TypeVar("_E", bound=enum.Enum)

# Extra head-room over the longest current value so a new member does not force
# a migration for a couple of characters.
_LENGTH_SLACK = 12


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def sql_enum(enum_cls: Type[_E], *, length: int | None = None, name: str | None = None) -> SQLEnum:
    """Build the canonical ``SQLEnum`` for a Python enum.

    ``native_enum=False`` -> portable VARCHAR + no server-side ENUM type.
    ``validate_strings=True`` -> a raw string that is not a member raises early.
    """
    values = [str(member.value) for member in enum_cls]
    computed = max(len(value) for value in values) + _LENGTH_SLACK
    return SQLEnum(
        enum_cls,
        name=name or _snake(enum_cls.__name__),
        native_enum=False,
        length=length or computed,
        validate_strings=True,
        values_callable=lambda e: [str(member.value) for member in e],
    )


class StrEnum(str, enum.Enum):
    """Base for every domain enum: comparable to, and JSON-serialisable as, str."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


# --- Cross-aggregate vocabularies -------------------------------------------


class EntityType(StrEnum):
    """Polymorphic target of ``document`` / ``activity`` / ``note`` / ``agent_thread``.

    Mirrors the S3 route ``documents/{entity_type}/{entity_id}/...``.
    """

    BROKER = "broker"
    USER = "user"
    CLIENT = "client"
    INSURED = "insured"
    INSURER = "insurer"
    ASSET = "asset"
    INSURANCE_LINE = "insurance_line"
    PLACEMENT = "placement"
    QUOTE_REQUEST = "quote_request"
    PROPOSAL = "proposal"
    INSPECTION_REQUEST = "inspection_request"
    INSPECTION = "inspection"
    POLICY = "policy"
    CLAIM = "claim"
    OFFERING = "offering"


class CoverageKind(StrEnum):
    """A coverage line item is either something covered or something excluded."""

    COVERAGE = "coverage"
    EXCLUSION = "exclusion"


class UserType(StrEnum):
    """Actor family. Mirrors ``USER_TYPES`` in ``app.core.roles_config``."""

    PLATFORM = "platform"
    BROKER = "broker"
    INSURER = "insurer"
    INSURED = "insured"


class PersonType(StrEnum):
    NATURAL = "natural"
    LEGAL = "legal"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


__all__ = [
    "sql_enum",
    "StrEnum",
    "EntityType",
    "CoverageKind",
    "UserType",
    "PersonType",
    "Priority",
]
