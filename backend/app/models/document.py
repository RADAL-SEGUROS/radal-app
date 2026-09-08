"""``document`` — THE ONLY PLACE AN S3 KEY LIVES.

Rule 8 of the rebuild: domain entities never store a raw S3 key string; where a
file matters they hold a FK to ``document.id``
(``proposal.source_document_id`` NOT NULL, ``inspection.report_document_id``,
``placement.brief_document_id``, ``offering.pdf_document_id``).

Why: the file's metadata — who uploaded it, when, mime, size, category — must
live in exactly one row. A bare string drifts, cannot be joined, and leaves no
audit trail; a FK gives referential integrity and one lifecycle for S3 cleanup.

The key is DERIVED, never free-form:
``documents/{entity_type}/{entity_id}/{category}-{n}.{ext}``.

(The ``media/`` prefix is a different, deterministic namespace for derived
512x512 WEBP logos/avatars — those live on ``broker.logo_key`` etc. and are not
documents.)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import CaseSection, EntityType, StrEnum, sql_enum

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.case_file import CaseFile
    from app.models.user import User


class DocumentCategory(StrEnum):
    """Every document kind the platform knows how to file and, where a schema
    exists, extract.

    The first block is the original v2 set (kept byte-identical). The second is
    the case-file registry merged from both analyst taxonomies — one canonical
    English slug per document type, deduped across the two vocabularies.

    ``PROPOSAL`` is KEPT AND DEPRECATED: it is what the current AI upload flow
    writes for an insurer quotation, so the existing route and its tests keep
    working. The registry maps it to the same spec as ``INSURER_QUOTATION``;
    new code writes ``INSURER_QUOTATION``.
    """

    # --- Original v2 set ----------------------------------------------------
    CMF_CERTIFICATE = "cmf_certificate"
    APPOINTMENT = "appointment"
    LOGO = "logo"
    ASSET_SHEET = "asset_sheet"
    ASSET_PHOTO = "asset_photo"
    VALUATION = "valuation"
    INSURED_AMOUNTS = "insured_amounts"
    EVIDENCE = "evidence"
    INSPECTION_REPORT = "inspection_report"
    TECHNICAL_BRIEF = "technical_brief"
    CLAIMS_HISTORY = "claims_history"
    PROPOSAL = "proposal"  # deprecated alias of INSURER_QUOTATION
    POWER_OF_ATTORNEY = "power_of_attorney"
    OFFERING = "offering"
    OTHER = "other"

    # --- root_prospect ------------------------------------------------------
    PROSPECT_REQUEST = "prospect_request"  # 00A

    # --- submission (broker -> insurers) ------------------------------------
    BUSINESS_QUESTIONNAIRE = "business_questionnaire"  # 00B
    INSURED_VALUES_SCHEDULE = "insured_values_schedule"  # 00C
    LOSS_HISTORY = "loss_history"  # 00D
    SUBMISSION_LETTER = "submission_letter"  # 02
    RISK_ENGINEERING_PLAN = "risk_engineering_plan"  # 03E
    RESUBMISSION_LETTER = "resubmission_letter"  # 03F

    # --- insurer_quotes (insurers -> broker) --------------------------------
    INSURER_QUOTATION = "insurer_quotation"  # 03 / 04 / 05 / 05B
    DECLINATION = "declination"  # 03A / 03B / 03C
    CONDITIONAL_PRONOUNCEMENT = "conditional_pronouncement"  # 03D
    QUOTE_COMPARISON = "quote_comparison"  # 06

    # --- broker_proposal ----------------------------------------------------
    ISSUANCE_PROPOSAL = "issuance_proposal"  # 07
    TECHNICAL_RECOMMENDATION = "technical_recommendation"  # 07R

    # --- policy_file --------------------------------------------------------
    POLICY = "policy"  # 08

    # --- collection ---------------------------------------------------------
    PAYMENT_PLAN = "payment_plan"  # 09 plan de pago
    COLLECTION_STATUS = "collection_status"  # 09 / 10 estado de cobranza

    # --- endorsement --------------------------------------------------------
    ENDORSEMENT_PROPOSAL = "endorsement_proposal"  # 07A / 07B / 09C
    ENDORSEMENT = "endorsement"  # 08A / 08B / 09B / 09D
    COMPLIANCE_NOTICE = "compliance_notice"  # 09A

    # --- claim --------------------------------------------------------------
    CLAIM_NOTICE = "claim_notice"  # 10 / 11 denuncio
    CLAIM_PRELIMINARY_REPORT = "claim_preliminary_report"  # 11 / 12 pre-informe
    CLAIM_FINAL_REPORT = "claim_final_report"  # 12 / 13 informe final
    BROKER_CLOSING_NOTE = "broker_closing_note"  # 14 nota de cierre

    # --- generated packs (never extracted) ----------------------------------
    SUBMISSION_PACK = "submission_pack"
    COMPARISON_PACK = "comparison_pack"
    PROPOSAL_PACK = "proposal_pack"
    # The ZIP of a whole group / one vigencia, stored as a ``document`` row with
    # ``entity_type=account_group`` (rule 8: the S3 key lives only here).
    ARCHIVE_PACK = "archive_pack"  # 12 < 25: no widening
    # The branded antecedentes expediente PDF (v6), one per account
    # (``entity_type=case_file``); its id lives in ``record_expediente.pdf_document_id``.
    ANTECEDENTES_PACK = "antecedentes_pack"  # 17 < 25: no widening


class Document(Base, TimestampMixin):
    """One stored file, attached polymorphically to a domain entity."""

    __tablename__ = "document"
    __table_args__ = (
        Index("ix_document_entity", "entity_type", "entity_id"),
        Index("ix_document_broker_category", "broker_id", "category"),
        Index("ix_document_case_section", "case_file_id", "section"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Polymorphic attachment. Deliberately NOT a FK: one table serves every
    # entity, and the pair is always resolved inside the broker's own scope.
    entity_type: Mapped[EntityType] = mapped_column(sql_enum(EntityType), nullable=False)
    entity_id: Mapped[int] = mapped_column(nullable=False)

    # The single source of truth for where the bytes are.
    s3_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    bucket: Mapped[str] = mapped_column(String(128), nullable=False)

    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum: Mapped[str | None] = mapped_column(String(128))

    category: Mapped[DocumentCategory] = mapped_column(
        sql_enum(DocumentCategory), default=DocumentCategory.OTHER, nullable=False
    )
    # Workflow phase label the file was captured in (free text, UI grouping only).
    phase: Mapped[str | None] = mapped_column(String(120))

    # --- Case-file filing ---------------------------------------------------
    # ``use_alter``: document -> case_file -> placement -> document is a genuine
    # FK cycle. Emitting this one constraint with ALTER TABLE keeps MySQL's
    # create ordering solvable; SQLite (supports_alter=False) keeps it inline,
    # which it accepts because forward references are legal there.
    case_file_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "case_file.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_document_case_file",
        ),
        index=True,
    )
    # Which sub-expediente the file belongs to.
    section: Mapped[CaseSection | None] = mapped_column(sql_enum(CaseSection))
    # The corpus code hint: "00A", "07R", "09B".
    document_code: Mapped[str | None] = mapped_column(String(8))

    uploaded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    broker: Mapped["Broker"] = relationship(back_populates="documents")
    uploaded_by: Mapped["User | None"] = relationship(foreign_keys=[uploaded_by_id])
    case_file: Mapped["CaseFile | None"] = relationship(foreign_keys=[case_file_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Document id={self.id} key={self.s3_key!r}>"


__all__ = ["Document", "DocumentCategory"]
