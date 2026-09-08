"""``case_file`` (el expediente) + its timeline and generated packs.

The expediente is the folder the broker actually thinks in. ``placement`` stays
the operating record for one new-business cycle (asset x line x period);
``case_file`` is the narrative container around it — a stage on the journey, a
document tree grouped by SECTION, notes, packs and post-sale children.

Shape rules applied (docs/v2-data-modeling-decisions.md):
  * the stage timeline is a CHILD TABLE (``case_file_stage_event``) — we list it,
    order it and compute durations from it, so it is never JSON;
  * ``meta`` is JSON: the per-kind tail (renewal target date, declination tally)
    varies by kind and is never filtered on in SQL;
  * ``case_pack.recipients`` is a JSON SNAPSHOT, exactly like
    ``quote_request.recipient_insurer_ids`` — re-resolving contacts later must
    not rewrite history.

``sequence_no`` vs ``version``: endorsement E1 and E2 are two different cases on
the same policy (``sequence_no`` 1 and 2). Re-working E2 after the insurer
bounces it produces a NEW row with the same ``sequence_no``, ``version=2`` and
``supersedes_case_file_id`` pointing at v1 — the "past -> current with dates"
sub-funnel. The invariant is enforced in the service layer, not by a unique
constraint, because a NULL ``policy_id`` behaves differently per engine.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin, utcnow
from app.models.enums import (
    CaseFileKind,
    CaseFileStatus,
    CaseOrigin,
    CaseSection,
    CaseStage,
    PackKind,
    PackStatus,
    sql_enum,
)
from app.models.types import JSONType

if TYPE_CHECKING:
    from app.models.account_client import AccountClient
    from app.models.account_group import AccountGroup
    from app.models.broker import Broker
    from app.models.client import Client
    from app.models.document import Document
    from app.models.insurance_line import InsuranceLine
    from app.models.line_record_schema import LineRecordSchema
    from app.models.placement import Placement
    from app.models.policy import Policy
    from app.models.user import User


class CaseFile(Base, TimestampMixin):
    """One expediente: an account cycle, or a post-sale case on a policy."""

    __tablename__ = "case_file"
    __table_args__ = (
        UniqueConstraint("broker_id", "reference", name="uq_case_file_broker_reference"),
        Index("ix_case_file_broker_kind_stage", "broker_id", "kind", "stage"),
        Index(
            "ix_case_file_broker_policy_sequence",
            "broker_id",
            "policy_id",
            "kind",
            "sequence_no",
        ),
        Index("ix_case_file_broker_status_due", "broker_id", "status", "due_at"),
        # The group tree: folders of a group ordered by vigencia.
        Index("ix_case_file_group_period", "broker_id", "account_group_id", "period_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[int] = mapped_column(
        ForeignKey("client.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # NULL for post-sale-only cases; the account case wraps its placement 1:1.
    placement_id: Mapped[int | None] = mapped_column(
        ForeignKey("placement.id", ondelete="SET NULL"), index=True
    )
    # Set for endorsement / collection / claim / renewal cases.
    policy_id: Mapped[int | None] = mapped_column(
        ForeignKey("policy.id", ondelete="SET NULL"), index=True
    )
    parent_case_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("case_file.id", ondelete="SET NULL"), index=True
    )
    # The previous VERSION of this same case (rework after a bounce).
    supersedes_case_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("case_file.id", ondelete="SET NULL")
    )
    insurance_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("insurance_line.id", ondelete="SET NULL"), index=True
    )
    # --- Antecedentes line (v7) ---------------------------------------------
    # The broker-defined LINE assigned to this account (kind=account only): the
    # ramo plus the antecedentes data it requires. Drives what the uploader
    # requests, what the AI extracts/validates and the Bases Técnicas PDF. NULL
    # falls back to ``resolve_line_record_schema(insurance_line_id, broker_id)``
    # (broker line for the ramo → global template). SET NULL: deleting the line
    # detaches, never cascades the account away.
    line_record_schema_id: Mapped[int | None] = mapped_column(
        ForeignKey("line_record_schema.id", ondelete="SET NULL"), index=True
    )

    # --- Groups & accounts (v3) ---------------------------------------------
    # Denormalised from the client so tree counts never join ``client``.
    # Post-sale children inherit it from their policy's account at creation.
    # SET NULL: archiving/deleting a group detaches, never cascades (rule 7).
    account_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_group.id", ondelete="SET NULL"), index=True
    )
    # AUTHORITATIVE validity period of the folder (the vigencia) and the sort
    # key of the timeline. ``placement.period_*`` is the per-RUT operating
    # copy, locked together with the folder (rule 1: a date change opens a
    # sibling folder through ``/reperiod``, never an in-place edit).
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    # ``f"{start.year}-{end.year}"`` — a GROUPING LABEL only, never a range:
    # two ramos of one group may carry different full dates under one label.
    period_label: Mapped[str | None] = mapped_column(String(32))
    # The ORIGIN axis: how this folder came to exist. Exactly one value.
    origin: Mapped[CaseOrigin] = mapped_column(
        sql_enum(CaseOrigin), default=CaseOrigin.NEW, nullable=False
    )
    # The folder this one was renewed / re-perioded FROM — a sibling in time,
    # never a parent (that is ``parent_case_file_id``, post-sale only).
    origin_case_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("case_file.id", ondelete="SET NULL"), index=True
    )

    kind: Mapped[CaseFileKind] = mapped_column(sql_enum(CaseFileKind), nullable=False)
    stage: Mapped[CaseStage] = mapped_column(sql_enum(CaseStage), nullable=False)
    status: Mapped[CaseFileStatus] = mapped_column(
        sql_enum(CaseFileStatus), default=CaseFileStatus.OPEN, nullable=False
    )

    # Human code, e.g. "EXP-2026-0007". Unique per broker.
    reference: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # Ordinal within (policy_id, kind) — E1, E2, ...
    sequence_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Rework counter of THIS case.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Broker-written, or AI-suggested then human-confirmed.
    summary: Mapped[str | None] = mapped_column(Text)
    # Per-kind tail: renewal target date, declination tally, ...
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    # --- Relationships ------------------------------------------------------
    broker: Mapped["Broker"] = relationship()
    client: Mapped["Client"] = relationship(foreign_keys=[client_id])
    # NB: ``Placement.case_file`` is the mirror-image many-to-one over
    # ``placement.case_file_id`` (the convenience back-pointer), so these two
    # deliberately do NOT back_populate each other — they are different columns.
    placement: Mapped["Placement | None"] = relationship(foreign_keys=[placement_id])
    policy: Mapped["Policy | None"] = relationship(
        foreign_keys=[policy_id], back_populates="case_files"
    )
    insurance_line: Mapped["InsuranceLine | None"] = relationship(
        foreign_keys=[insurance_line_id]
    )
    line_record_schema: Mapped["LineRecordSchema | None"] = relationship(
        foreign_keys=[line_record_schema_id]
    )
    owner: Mapped["User | None"] = relationship(foreign_keys=[owner_user_id])

    parent: Mapped["CaseFile | None"] = relationship(
        "CaseFile",
        remote_side=[id],
        foreign_keys=[parent_case_file_id],
        back_populates="children",
    )
    children: Mapped[list["CaseFile"]] = relationship(
        "CaseFile",
        foreign_keys=[parent_case_file_id],
        back_populates="parent",
    )
    supersedes: Mapped["CaseFile | None"] = relationship(
        "CaseFile", remote_side=[id], foreign_keys=[supersedes_case_file_id]
    )
    # The ORIGIN axis (renewal / period change) — a sibling, not a parent.
    origin_case_file: Mapped["CaseFile | None"] = relationship(
        "CaseFile", remote_side=[id], foreign_keys=[origin_case_file_id]
    )

    account_group: Mapped["AccountGroup | None"] = relationship(
        back_populates="case_files", foreign_keys=[account_group_id]
    )
    # RUT membership of the account (union with ``placement.case_file_id``).
    account_clients: Mapped[list["AccountClient"]] = relationship(
        back_populates="case_file", cascade="all, delete-orphan"
    )

    stage_events: Mapped[list["CaseFileStageEvent"]] = relationship(
        back_populates="case_file",
        cascade="all, delete-orphan",
        order_by="CaseFileStageEvent.occurred_at",
    )
    packs: Mapped[list["CasePack"]] = relationship(
        back_populates="case_file", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CaseFile id={self.id} ref={self.reference!r} kind={self.kind} stage={self.stage}>"


class CaseFileStageEvent(Base, TimestampMixin):
    """One accepted stage transition of a case. The timeline, as rows."""

    __tablename__ = "case_file_stage_event"
    __table_args__ = (
        Index("ix_case_file_stage_event_case_time", "case_file_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_file_id: Mapped[int] = mapped_column(
        ForeignKey("case_file.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # NULL on the opening event.
    from_stage: Mapped[CaseStage | None] = mapped_column(sql_enum(CaseStage))
    to_stage: Mapped[CaseStage] = mapped_column(sql_enum(CaseStage), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    note: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    broker: Mapped["Broker"] = relationship()
    case_file: Mapped["CaseFile"] = relationship(back_populates="stage_events")
    user: Mapped["User | None"] = relationship(foreign_keys=[user_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CaseFileStageEvent case={self.case_file_id} to={self.to_stage}>"


class CasePack(Base, TimestampMixin):
    """A generated, downloadable bundle (PDF + optional ZIP) of an expediente.

    The bytes are stored through the normal document pipeline, so the two FKs
    point at ``document`` rows — rule 8 keeps every S3 key in ``document``.
    """

    __tablename__ = "case_pack"
    __table_args__ = (
        Index("ix_case_pack_case_kind", "case_file_id", "kind"),
        Index("ix_case_pack_broker_status", "broker_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_file_id: Mapped[int] = mapped_column(
        ForeignKey("case_file.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[PackKind] = mapped_column(sql_enum(PackKind), nullable=False)
    section: Mapped[CaseSection | None] = mapped_column(sql_enum(CaseSection))
    status: Mapped[PackStatus] = mapped_column(
        sql_enum(PackStatus), default=PackStatus.DRAFT, nullable=False
    )

    pdf_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )
    zip_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )

    # AI prose: suggest -> human edit -> confirm. Never auto-committed.
    summary: Mapped[str | None] = mapped_column(Text)
    summary_model: Mapped[str | None] = mapped_column(String(120))
    summary_prompt_version: Mapped[str | None] = mapped_column(String(64))
    is_summary_confirmed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # Send-time SNAPSHOT of the resolved insurer contacts.
    recipients: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JSONType)

    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    error: Mapped[str | None] = mapped_column(Text)

    broker: Mapped["Broker"] = relationship()
    case_file: Mapped["CaseFile"] = relationship(back_populates="packs")
    pdf_document: Mapped["Document | None"] = relationship(foreign_keys=[pdf_document_id])
    zip_document: Mapped["Document | None"] = relationship(foreign_keys=[zip_document_id])
    generated_by: Mapped["User | None"] = relationship(foreign_keys=[generated_by_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CasePack id={self.id} kind={self.kind} status={self.status}>"


__all__ = ["CaseFile", "CaseFileStageEvent", "CasePack"]
