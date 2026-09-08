"""``submission_letter`` (02) — Carta de remisión, broker -> insurers.

The dispatch. Its addressee list is what fills
``quote_request.recipient_insurer_ids`` and opens one pending proposal slot per
insurer.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    ExtractionModel,
    FlexDate,
    InsurerRefX,
    PartyRef,
    PersonRef,
    RootExtraction,
    Str,
)

__all__ = ["SubmissionLetterExtraction", "FolderItem"]


class FolderItem(ExtractionModel):
    """One entry of the "contenido de la carpeta" list."""

    code: Str = None
    title: Str = None
    note: Str = None


class SubmissionLetterExtraction(RootExtraction):
    letter_date: FlexDate = None
    letter_city: Str = None
    broker: PartyRef = Field(default_factory=PartyRef)
    broker_signer: PersonRef = Field(default_factory=PersonRef)

    addressee_insurers: list[InsurerRefX] = Field(
        default_factory=list,
        description="Every company the folder was sent to — resolve by rut/cmf_code",
    )
    addressee_department: Str = None

    insured: PartyRef = Field(default_factory=PartyRef)
    insurance_line: Str = None
    requested_inception_date: FlexDate = None
    placement_type: Str = Field(
        default=None, description="Nueva colocación / renovación / reemplazo"
    )

    folder_contents: list[FolderItem] = Field(default_factory=list)
    key_underwriting_points: list[Str] = Field(default_factory=list)
    quotation_requests: list[Str] = Field(default_factory=list)
    offer_deadline: FlexDate = None
    award_date_estimated: FlexDate = None
    site_visit_offer: Str = None
