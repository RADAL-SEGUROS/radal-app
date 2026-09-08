"""The document category registry and its 25 extraction schemas.

What is asserted here is the contract three other layers rely on:

* the registry covers every extractable category, and ``proposal`` is the SAME
  spec object as ``insurer_quotation`` (that alias is what keeps the existing
  proposal flow and its tests working);
* Chilean money parses correctly, and the premium invariant
  ``net = taxable + exempt`` / ``vat = 0.19 x taxable`` / ``total = net + vat``
  is reconciled without ever silently correcting a contradiction;
* prose survives verbatim, and contractual instants are datetimes, not dates;
* an insurer-quotation payload still projects onto the legacy
  ``ProposalSuggestion``.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.models.ai import ExtractionKind
from app.models.document import DocumentCategory
from app.models.enums import CaseSection, DocumentDirection
from app.schemas.extraction.common import (
    PremiumBlock,
    parse_event_datetime,
    parse_pct,
    parse_policy_datetime,
    parse_uf,
)
from app.schemas.extraction.registry import (
    CATEGORY_REGISTRY,
    CODE_TO_CATEGORY,
    UnknownCategory,
    category_for_code,
    registry_as_json,
    spec_for,
)
from app.services import ai as ai_service
from tests.conftest import API

# Every category that must have an extraction schema. The three pack categories
# are generated, never extracted, so they are deliberately absent.
GENERATED_ONLY = {
    DocumentCategory.SUBMISSION_PACK,
    DocumentCategory.COMPARISON_PACK,
    DocumentCategory.PROPOSAL_PACK,
}

EXPECTED_CATEGORIES = {
    DocumentCategory.PROSPECT_REQUEST,
    DocumentCategory.BUSINESS_QUESTIONNAIRE,
    DocumentCategory.INSURED_VALUES_SCHEDULE,
    DocumentCategory.LOSS_HISTORY,
    DocumentCategory.INSPECTION_REPORT,
    DocumentCategory.TECHNICAL_BRIEF,
    DocumentCategory.SUBMISSION_LETTER,
    DocumentCategory.RISK_ENGINEERING_PLAN,
    DocumentCategory.RESUBMISSION_LETTER,
    DocumentCategory.INSURER_QUOTATION,
    DocumentCategory.DECLINATION,
    DocumentCategory.CONDITIONAL_PRONOUNCEMENT,
    DocumentCategory.QUOTE_COMPARISON,
    DocumentCategory.ISSUANCE_PROPOSAL,
    DocumentCategory.TECHNICAL_RECOMMENDATION,
    DocumentCategory.POLICY,
    DocumentCategory.PAYMENT_PLAN,
    DocumentCategory.COLLECTION_STATUS,
    DocumentCategory.ENDORSEMENT_PROPOSAL,
    DocumentCategory.ENDORSEMENT,
    DocumentCategory.COMPLIANCE_NOTICE,
    DocumentCategory.CLAIM_NOTICE,
    DocumentCategory.CLAIM_PRELIMINARY_REPORT,
    DocumentCategory.CLAIM_FINAL_REPORT,
    DocumentCategory.BROKER_CLOSING_NOTE,
}


# --- Registry shape ----------------------------------------------------------


def test_registry_covers_every_extractable_category():
    missing = EXPECTED_CATEGORIES - set(CATEGORY_REGISTRY)
    assert not missing, f"categories with no schema: {sorted(c.value for c in missing)}"
    assert len(EXPECTED_CATEGORIES) == 25
    for category in GENERATED_ONLY:
        assert category not in CATEGORY_REGISTRY


def test_proposal_is_an_alias_of_insurer_quotation():
    """The deprecated alias must be the SAME spec object, not a copy."""
    assert spec_for(DocumentCategory.PROPOSAL) is spec_for(DocumentCategory.INSURER_QUOTATION)
    assert spec_for("proposal").category is DocumentCategory.INSURER_QUOTATION
    # And it keeps the legacy prompt version, so old and new rows compare.
    assert spec_for("proposal").prompt_version == ai_service.PROMPT_VERSION


def test_specs_are_well_formed():
    for category, spec in CATEGORY_REGISTRY.items():
        assert isinstance(spec.section, CaseSection)
        assert isinstance(spec.direction, DocumentDirection)
        assert isinstance(spec.extraction_kind, ExtractionKind)
        assert spec.module.startswith("app.schemas.extraction.")
        assert spec.prefill_target
        # extraction.prompt_version is String(32).
        assert 0 < len(spec.prompt_version) <= 32, category
        # Spanish guidance is required — it IS the prompt.
        assert len(spec.guidance) > 80, category


def test_guidance_is_spanish_prose():
    """The one place Spanish is allowed in code besides locale values."""
    joined = " ".join(spec.guidance for spec in set(CATEGORY_REGISTRY.values())).lower()
    for marker in ("extrae", "documento", "literal"):
        assert marker in joined


def test_every_schema_accepts_an_empty_object():
    """A model that answers ``{}`` must still produce a reviewable payload."""
    for category, spec in CATEGORY_REGISTRY.items():
        instance = spec.schema.model_validate({})
        assert instance.model_dump(mode="json") is not None, category
        spec.schema.model_json_schema()


def test_unknown_category_raises():
    with pytest.raises(UnknownCategory):
        spec_for("not_a_category")
    with pytest.raises(UnknownCategory):
        spec_for(None)
    with pytest.raises(UnknownCategory):
        spec_for(DocumentCategory.SUBMISSION_PACK)


def test_code_lookup():
    assert CODE_TO_CATEGORY["00A"] is DocumentCategory.PROSPECT_REQUEST
    assert CODE_TO_CATEGORY["07R"] is DocumentCategory.TECHNICAL_RECOMMENDATION
    assert CODE_TO_CATEGORY["09B"] is DocumentCategory.ENDORSEMENT
    for code in ("03", "04", "05", "05B"):
        assert CODE_TO_CATEGORY[code] is DocumentCategory.INSURER_QUOTATION
    for code in ("03A", "03B", "03C"):
        assert CODE_TO_CATEGORY[code] is DocumentCategory.DECLINATION
    # The same numeric code means different things per sub-expediente.
    assert category_for_code("11", CaseSection.CLAIM) is DocumentCategory.CLAIM_NOTICE
    assert (
        category_for_code("11", CaseSection.COLLECTION) is DocumentCategory.COLLECTION_STATUS
    )
    assert category_for_code(None) is None


def test_registry_as_json_is_renderable():
    rows = registry_as_json()
    assert len(rows) == len(CATEGORY_REGISTRY)
    row = next(r for r in rows if r["category"] == "insurer_quotation")
    assert row["section"] == "insurer_quotes"
    assert row["direction"] == "insurer_to_broker"
    names = {field["name"] for field in row["fields"]}
    assert {"quotation_number", "deductibles", "coverages", "total_premium_uf"} <= names


# --- Chilean number parsing --------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("UF 17.920", Decimal("17920")),
        ("UF 12,99", Decimal("12.99")),
        ("1.234.567,89", Decimal("1234567.89")),
        ("−1.234,56", Decimal("-1234.56")),  # U+2212 MINUS SIGN
        ("(1.234)", Decimal("-1234")),
        ("0,19", Decimal("0.19")),
        (17920, Decimal("17920")),
        ("no aplica", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_uf(raw, expected):
    assert parse_uf(raw) == expected


def test_parse_pct_handles_percent_and_permille():
    assert parse_pct("19%") == Decimal("19")
    assert parse_pct("1,32 por mil") == Decimal("1.32")


def test_contractual_datetimes():
    """Noon for cover periods, midnight for real-world events."""
    assert parse_policy_datetime("2026-01-01") == datetime(2026, 1, 1, 12, 0)
    assert parse_policy_datetime("01-01-2026 08:30") == datetime(2026, 1, 1, 8, 30)
    assert parse_policy_datetime("3 de marzo de 2026") == datetime(2026, 3, 3, 12, 0)
    assert parse_event_datetime("2026-04-17") == datetime(2026, 4, 17, 0, 0)
    assert parse_event_datetime("17/04/2026 03:20 hrs") == datetime(2026, 4, 17, 3, 20)


# --- Money invariants --------------------------------------------------------


def test_premium_block_derives_the_missing_components():
    block = PremiumBlock(taxable_premium_uf="UF 100", exempt_premium_uf="UF 20")
    assert block.net_premium_uf == Decimal("120")
    assert block.vat_uf == Decimal("19.00")  # 0.19 x TAXABLE, never on net
    assert block.total_premium_uf == Decimal("139.00")
    assert block.inconsistencies == []


def test_premium_block_reports_but_never_fixes_a_contradiction():
    block = PremiumBlock(
        taxable_premium_uf=100, exempt_premium_uf=20, net_premium_uf=130, vat_uf=19, total_premium_uf=149
    )
    assert block.net_premium_uf == Decimal("130")  # left exactly as the document said
    assert any("net" in problem for problem in block.inconsistencies)


def test_premium_block_accepts_an_all_zero_administrative_movement():
    block = PremiumBlock(taxable_premium_uf=0, exempt_premium_uf=0)
    assert block.net_premium_uf == Decimal("0")
    assert block.vat_uf == Decimal("0.00")
    assert block.total_premium_uf == Decimal("0.00")
    assert block.inconsistencies == []


# --- Prose and per-document fidelity ----------------------------------------


def test_prose_fields_survive_verbatim():
    from app.schemas.extraction.policy import PolicyExtraction

    wording = (
        "5% de la pérdida con un mínimo de UF 25; sismo 1% sobre el monto asegurado "
        "del ítem afectado"
    )
    payload = PolicyExtraction.model_validate(
        {
            "policy_number": "0020119904",
            "deductibles": [{"peril": "incendio", "text": wording, "min_uf": "UF 25"}],
            "exclusions": [
                {"text": "Daños por agua", "carve_back": "salvo rotura accidental de cañerías"}
            ],
            "pledge_creditor": [
                {"name": "Banco de Chile", "stipulation": "Cláusula de acreedor prendario N°3"}
            ],
        }
    )
    assert payload.deductibles[0].text == wording
    assert payload.deductibles[0].min_uf == Decimal("25")
    assert payload.exclusions[0].carve_back.startswith("salvo")
    assert payload.pledge_creditor[0].stipulation


def test_policy_period_is_a_datetime_not_a_date():
    from app.schemas.extraction.policy import PolicyExtraction

    payload = PolicyExtraction.model_validate(
        {"period_start_at": "2026-01-01", "period_end_at": "2027-01-01"}
    )
    assert isinstance(payload.period_start_at, datetime)
    assert payload.period_start_at.hour == 12


def test_claim_occurrence_is_a_datetime():
    from app.schemas.extraction.claim_notice import ClaimNoticeExtraction

    payload = ClaimNoticeExtraction.model_validate(
        {"occurrence_at": "2027-02-14 03:20", "notice_at": "2027-02-14 09:00"}
    )
    assert payload.occurrence_at == datetime(2027, 2, 14, 3, 20)


def test_endorsement_effect_is_a_datetime():
    from app.schemas.extraction.endorsement import EndorsementExtraction

    payload = EndorsementExtraction.model_validate(
        {"endorsement_number": "791237-4", "endorsement_effective_at": "15-06-2026"}
    )
    assert payload.endorsement_effective_at == datetime(2026, 6, 15, 12, 0)
    assert payload.endorsement_number == "791237-4"


def test_nothing_is_reconciled_across_documents():
    """The 00B VIN and the 07 VIN differ in the corpus; both are recorded as-is."""
    from app.schemas.extraction.business_questionnaire import BusinessQuestionnaireExtraction

    payload = BusinessQuestionnaireExtraction.model_validate(
        {"fleet": [{"plate": "KXYZ-11", "vin": "9BWZZZ377VT004251"}]}
    )
    assert payload.fleet[0].vin == "9BWZZZ377VT004251"


def test_unknown_keys_are_kept_not_dropped():
    from app.schemas.extraction.prospect_request import ProspectRequestExtraction

    payload = ProspectRequestExtraction.model_validate(
        {"letter_date": "2026-03-01", "una_columna_inesperada": "valor"}
    )
    assert payload.letter_date == date(2026, 3, 1)
    assert payload.model_dump()["una_columna_inesperada"] == "valor"


# --- The legacy proposal bridge ----------------------------------------------


def test_insurer_quotation_projects_onto_the_legacy_proposal_suggestion():
    spec = spec_for(DocumentCategory.INSURER_QUOTATION)
    payload = spec.schema.model_validate(
        {
            "quotation_number": "COT-2026-118",
            "insurer": {"legal_name": "HDI Seguros S.A.", "rut": "99301000-6", "cmf_code": "CMF-HDI-001"},
            "cover_mode": "todo riesgo",
            "net_premium_taxable_uf": "UF 100",
            "net_premium_exempt_uf": "UF 20",
            "gross_premium_uf": "UF 139",
            "vat_uf": "UF 19",
            "validity_days": 15,
            "period_start_at": "2026-01-01",
            "period_end_at": "2027-01-01",
            "deductibles": [
                {"peril": "fire", "basis": "loss", "pct": 5, "min_uf": "UF 25",
                 "offered": "5% de la pérdida, mínimo UF 25"},
                {"peril": "earthquake", "basis": "insured_amount", "pct": 1},
            ],
            "coverages": [{"number": "1", "name": "Incendio", "offered_condition": "100% del monto"}],
            "exclusions": [{"text": "Guerra y terrorismo"}],
            "warranties": [{"code": "G-1", "requirement": "Mantener red húmeda operativa"}],
        }
    )
    # The spec names map onto the legacy column names.
    assert payload.taxable_premium_uf == Decimal("100")
    assert payload.total_premium_uf == Decimal("139")
    assert payload.validity_business_days == 15
    assert payload.coverage_start == date(2026, 1, 1)  # date mirror of the datetime

    suggestion, warnings = ai_service.legacy_proposal_suggestion(payload)
    assert suggestion.insurer.cmf_code == "CMF-HDI-001"
    assert suggestion.taxable_premium_uf == Decimal("100")
    assert suggestion.deductibles["fire"].pct == Decimal("5")
    assert suggestion.deductibles["earthquake"].basis == "insured_amount"
    kinds = {coverage.kind for coverage in suggestion.coverages}
    assert kinds == {"coverage", "exclusion"}
    assert "red húmeda" in (suggestion.warranties or "")
    assert warnings == []

    # And the money still reconciles through the legacy helper.
    money = ai_service.derive_money(suggestion)
    assert money["net_premium_uf"] == Decimal("120.0000")
    assert money["vat_uf"] == Decimal("19.0000")


# --- The endpoint ------------------------------------------------------------


def test_categories_endpoint_serves_the_registry(client, world, headers_a):
    response = client.get(f"{API}/ai/categories", headers=headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == len(CATEGORY_REGISTRY)
    slugs = {item["category"] for item in body["items"]}
    assert "insurer_quotation" in slugs and "proposal" in slugs
    alias = next(item for item in body["items"] if item["category"] == "proposal")
    assert alias["is_alias"] is True
    assert alias["canonical_category"] == "insurer_quotation"


def test_categories_endpoint_requires_authentication(client, world):
    assert client.get(f"{API}/ai/categories").status_code == 401
