"""``line_record_schema`` (v6) — the admin-maintained per-ramo antecedentes schema.

One row describes the shape of the antecedentes expediente for one
``insurance_line`` (ramo): the sections and fields the broker gathers, validates
and completes before registering an account's expediente. Radal ships a **global
seed** (``broker_id IS NULL``, mirroring ``insurance_line`` and the CMF catalogue)
and a broker may author its own version that takes precedence.

Resolution (service layer): ``or_(broker_id == caller, broker_id.is_(None))``,
preferring the broker's own active row over the global seed. Every write is
``broker_id``-scoped (rule 2); the global seed is Radal-maintained.

The ``definition`` is JSON, not child tables: it is authored/edited as one
document, versioned as a whole and never filtered on in SQL. Its **keys are
English identifiers** (rule 1); ``label``/``description``/``options`` are
broker-authored display data where Spanish is allowed.

``definition`` shape::

    {
      "sections": [
        {
          "key": "cover",                 # English identifier
          "label": "Datos de la cuenta",  # display, Spanish allowed
          "fields": [
            {
              "key": "razon_social",      # English identifier
              "label": "Razón social",
              "type": "text",             # see the type union below
              "required": true,
              "description": "...",        # optional
              "options": ["A", "B"],       # select only
              "unit": "UF",                # optional display unit
              "repeatable": false,         # list/table fields
              "fields": [ ... ]            # nested (list / group only)
            }
          ]
        }
      ]
    }

Field ``type`` is one of ``text | number | integer | boolean | date | money_uf |
percent | select | list | group``. ``list`` and ``group`` carry nested
``fields`` (a repeatable table / an inline object respectively).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.types import JSONType

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.insurance_line import InsuranceLine
    from app.models.user import User


class LineRecordSchema(Base, TimestampMixin):
    """The per-ramo antecedentes schema (global seed or broker override)."""

    __tablename__ = "line_record_schema"
    __table_args__ = (
        # v7 relaxed the per-ramo version uniqueness: a broker may author SEVERAL
        # lines for one ramo ("Incendio — Agro", "Incendio — Retail"), so the old
        # ``(broker_id, insurance_line_id, version)`` unique is gone. A line is
        # instead identified by its NAME within the owner; ``broker_id`` NULL (the
        # global templates) is exempt because SQLite/MySQL treat NULLs as distinct.
        UniqueConstraint(
            "broker_id",
            "name",
            name="uq_line_record_schema_broker_name",
        ),
        Index(
            "ix_line_record_schema_broker_line_active",
            "broker_id",
            "insurance_line_id",
            "is_active",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # NULL = global Radal seed (mirrors ``insurance_line``); otherwise the
    # broker that owns this override. CASCADE with the broker (rule 2).
    broker_id: Mapped[int | None] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), index=True
    )
    insurance_line_id: Mapped[int] = mapped_column(
        ForeignKey("insurance_line.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # A **template** is a Radal-recommended global definition (``broker_id IS
    # NULL`` + ``is_template=True``) a broker adopts and then freely edits. A
    # broker's own row (``broker_id`` set) is a **line**; ``is_template`` stays
    # False for it. Added by the v7 migrate pass (NOT NULL, defaults False).
    is_template: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # The sectioned field definition (see the module docstring for the shape).
    definition: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    # --- Relationships (read-only, for serialization) -----------------------
    broker: Mapped["Broker | None"] = relationship()
    insurance_line: Mapped["InsuranceLine"] = relationship(
        foreign_keys=[insurance_line_id]
    )
    created_by: Mapped["User | None"] = relationship(foreign_keys=[created_by_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<LineRecordSchema id={self.id} line={self.insurance_line_id} "
            f"broker={self.broker_id} v{self.version}>"
        )


__all__ = ["LineRecordSchema"]
