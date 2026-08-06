"""Declarative base + shared timestamp mixin."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String, func
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# --- MySQL portability -------------------------------------------------------
# SQLite accepts unbounded VARCHAR; MySQL requires a length. Models are written
# without lengths for local SQLite ergonomics, so default any length-less String
# to VARCHAR(255) on MySQL/MariaDB. Columns needing more use Text explicitly.
@compiles(String, "mysql")
@compiles(String, "mariadb")
def _mysql_varchar_default_length(element, compiler, **kw):  # noqa: ANN001
    if element.length is None:
        return "VARCHAR(255)"
    return compiler.visit_VARCHAR(element, **kw)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Root declarative base for all Radal models."""


class TimestampMixin:
    """created_at / updated_at (UTC) helpers."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )
