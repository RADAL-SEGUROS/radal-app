"""Request/response models for the v6 antecedentes expediente.

The ``line_record_schema.definition`` travels as a :class:`RamoSchema` (English
``key`` identifiers per rule 1; ``label``/``description``/``options`` are
broker-authored display data where Spanish is allowed). The antecedentes payload
is a free ``dict`` mirroring that sectioned shape — it is validated on the server
against the dynamically-built Pydantic model, never against a fixed schema here.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.ai import ExtractionRead
from app.schemas.document import DocumentDownload


class ApiModel(BaseModel):
    """ORM-friendly base that trims incidental whitespace on strings."""

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


# --- The per-ramo schema definition (line_record_schema.definition) ----------


class RamoField(ApiModel):
    """One field of a section. ``list``/``group`` carry nested ``fields``."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    key: str
    label: str
    type: str = "text"
    required: bool = False
    description: Optional[str] = None
    options: Optional[List[str]] = None
    unit: Optional[str] = None
    repeatable: Optional[bool] = None
    fields: Optional[List["RamoField"]] = None


class RamoSection(ApiModel):
    key: str
    label: str
    fields: List[RamoField] = Field(default_factory=list)


class RamoSchema(ApiModel):
    """The whole sectioned definition — the API view of ``definition``."""

    sections: List[RamoSection] = Field(default_factory=list)


RamoField.model_rebuild()


# --- Ramo-schema maintainer CRUD ---------------------------------------------


class RecommendedFile(ApiModel):
    """One recommended antecedentes document for a ramo (the corrected v9 shape).

    This is the FIRST-CLASS content of a ramo — the recommended files a broker
    should gather, each with human ``label`` + ``description`` (context) +
    ``required`` and an expected ``format``. Advisory: it drives the uploader's
    "recommended files" checklist; it never gates the consolidation or the PDF.

    ``category`` / ``doc_type`` both name the document type — ``category`` maps to
    a :class:`DocumentCategory` value where one fits; ``doc_type`` is the free
    label when it does not. ``format`` is the expected upload format ("pdf",
    "word", "pdf/word", "image")."""

    model_config = ConfigDict(from_attributes=True, extra="allow", str_strip_whitespace=True)

    #: Stable English identifier for the recommended file (rule 1).
    key: Optional[str] = None
    label: Optional[str] = None
    #: Context/guidance for this file (the AI-primer + the uploader hint).
    description: Optional[str] = None
    #: A :class:`DocumentCategory` value, when the type maps to one.
    category: Optional[str] = None
    #: The document type as a free label ("slip de términos y condiciones").
    doc_type: Optional[str] = None
    #: The expected upload format: "pdf" | "word" | "pdf/word" | "image".
    format: Optional[str] = None
    required: Optional[bool] = None
    #: DEPRECATED alias of ``description`` (older seeds authored ``explanation``).
    explanation: Optional[str] = None


class LineRecordSchemaCreate(ApiModel):
    insurance_line_id: Optional[int] = None
    name: str = Field(min_length=1, max_length=160)
    #: DEPRECATED — a ramo is no longer a field/section schema. Accepted for
    #: backward compatibility but never required: a ramo is valid with just a
    #: name + recommended files.
    definition: Optional[RamoSchema] = None
    #: The first-class content of a ramo: the recommended antecedentes documents.
    recommended_files: Optional[List[RecommendedFile]] = None
    #: Ramo-level prose/context, shown in the picker and used to prime the AI.
    explanation: Optional[str] = None
    version: Optional[int] = None
    is_active: bool = True
    #: Clone this template's recommended files (and ramo, when not overridden)
    #: into the new broker line. A template is a global row (``broker_id`` NULL) —
    #: visible to every broker; the clone is broker-owned and freely editable.
    from_template_id: Optional[int] = None


class LineRecordSchemaUpdate(ApiModel):
    name: Optional[str] = Field(default=None, max_length=160)
    definition: Optional[RamoSchema] = None
    recommended_files: Optional[List[RecommendedFile]] = None
    explanation: Optional[str] = None
    version: Optional[int] = None
    is_active: Optional[bool] = None


class LineUsage(ApiModel):
    """How many of the broker's accounts (and distinct groups) resolve to a line."""

    accounts: int = 0
    groups: int = 0


class LineRecordSchemaRead(ApiModel):
    id: int
    broker_id: Optional[int] = None
    #: NULLable (v9): a fully custom ramo need not map to any CMF line.
    insurance_line_id: Optional[int] = None
    insurance_line_name: Optional[str] = None
    name: str
    version: int
    is_active: bool
    #: ``True`` when this is a global Radal template (``broker_id`` is null).
    is_global: bool = False
    #: ``True`` for a Radal-recommended global TEMPLATE (global + is_template).
    is_template: bool = False
    #: How many of the broker's accounts / groups resolve to this line. Zero on
    #: reads that do not compute usage (e.g. the single-row resolve endpoint).
    usage: LineUsage = Field(default_factory=LineUsage)
    #: The FIRST-CLASS content of a ramo: the recommended antecedentes documents
    #: and a ramo-level prose blurb. Advisory — they guide the uploader + AI.
    recommended_files: List[RecommendedFile] = Field(default_factory=list)
    explanation: Optional[str] = None
    #: DEPRECATED — the old sectioned schema. Empty (``{"sections": []}``) for a
    #: v9 ramo authored with recommended files only.
    definition: RamoSchema = Field(default_factory=RamoSchema)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# --- Antecedentes expediente -------------------------------------------------


class RequiredField(ApiModel):
    """One mandatory field of the resolved line, addressed by section + key.

    Used for BOTH the uploader checklist (``required_fields`` — the whole
    mandatory list) and the gap list (``missing_required`` — the subset still
    empty in the payload). ``label`` is the human display label (Spanish ok)."""

    section: str
    field: str
    label: str


class DocumentExtraction(ApiModel):
    """The pieces of data extracted from ONE antecedentes document (the review
    UI's "Extracción" subtab renders one card per file).

    ``payload`` is that document's own sectioned extraction (``None`` when the
    file still needs vision extraction). ``needs_vision`` flags an image/scanned
    file whose text could not be read — a vision-model fallback is not yet
    implemented, so the file is surfaced here rather than silently dropped."""

    document_id: int
    document_name: Optional[str] = None
    category: Optional[str] = None
    section: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    confidence: Optional[str] = None
    needs_vision: bool = False
    warnings: List[str] = Field(default_factory=list)


class AntecedentesRead(ApiModel):
    """The registered / in-progress antecedentes (Bases Técnicas) of one account.

    ``schema`` is the resolved per-ramo LINE definition (or ``None`` when the
    account has no line yet — the CTA is then disabled with a reason).
    ``payload`` is the staged (review) or human-validated (registered) sectioned
    data. ``required_fields`` is the line's mandatory checklist and
    ``missing_required`` the subset still empty; ``complete`` is True when nothing
    mandatory is missing — the frontend gates the Descargar Bases Técnicas button
    on it and ``GET /pdf`` returns 409 when it is False.
    """

    case_file_id: int
    status: str
    insurance_line_id: Optional[int] = None
    schema_id: Optional[int] = None
    schema_version: Optional[int] = None
    #: The assigned line's display name (``line_record_schema.name``), for the
    #: Bases-Técnicas header and the account/overview cards. None when unresolved.
    line_name: Optional[str] = None
    schema_: Optional[RamoSchema] = Field(default=None, alias="schema")
    payload: Optional[dict[str, Any]] = None
    confidence: Optional[Decimal] = None
    extraction_id: Optional[int] = None
    # The full persisted AI job (audit trail), embedded so the review UI can
    # render the provenance card without a second round-trip. None until a
    # SUGGEST has run and staged a source extraction.
    extraction: Optional[ExtractionRead] = None
    registered_at: Optional[datetime] = None
    registered_by_id: Optional[int] = None
    pdf_available: bool = False
    #: The line's whole mandatory field list (the uploader checklist).
    required_fields: List[RequiredField] = Field(default_factory=list)
    #: The subset of ``required_fields`` still empty in the payload.
    missing_required: List[RequiredField] = Field(default_factory=list)
    #: True when no mandatory field is missing — gates the PDF button.
    complete: bool = False
    #: Per-document extraction results (the "Extracción" subtab): one entry per
    #: source antecedentes file, keyed by document. Empty until a SUGGEST runs.
    document_extractions: List[DocumentExtraction] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    model_config = ConfigDict(
        from_attributes=True, str_strip_whitespace=True, populate_by_name=True
    )


class AntecedentesRegisterRequest(ApiModel):
    """The human-completed payload to validate and register."""

    payload: dict[str, Any] = Field(default_factory=dict)


class LineAssignmentRequest(ApiModel):
    """Assign a broker line (or an adopted template) to an account."""

    line_record_schema_id: int


class AntecedentesPdfResponse(DocumentDownload):
    """The generated Bases Técnicas download plus the advisory completeness flags.

    v8 warn-not-block: the PDF ALWAYS renders (DRAFT/REVIEW/REGISTERED). When the
    payload is not yet a complete, registered record it renders a BORRADOR /
    INCOMPLETO cover — ``incomplete`` says so and ``missing_required`` names the
    still-empty mandatory fields, so the UI can flag it without a hard block.
    """

    incomplete: bool = False
    missing_required: List[RequiredField] = Field(default_factory=list)


__all__ = [
    "RamoField",
    "RamoSection",
    "RamoSchema",
    "LineRecordSchemaCreate",
    "LineRecordSchemaUpdate",
    "LineRecordSchemaRead",
    "LineUsage",
    "RecommendedFile",
    "RequiredField",
    "DocumentExtraction",
    "AntecedentesRead",
    "AntecedentesRegisterRequest",
    "LineAssignmentRequest",
    "AntecedentesPdfResponse",
]
