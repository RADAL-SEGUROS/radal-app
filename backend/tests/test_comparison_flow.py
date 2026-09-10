"""The v8 broker-journey redesign: comparison → promote → propuesta → ratify.

Hermetic throughout. ``extract_budget_proposal`` reads through a FAKED chat model
(``tests.test_ai_streaming._FakeChatModel``); alignment falls back to the
deterministic slug-match (no provider is reached). The acceptance invariants:

* a wrong-file column is a VISIBLE rejection (``is_wrong_file``), never a 422;
* promoting a column's fixed money core mints a real inbound ``proposal`` through
  the shared ``reconcile_money`` path;
* a propuesta is built SOLELY from an aligned comparison + a promoted winner;
* every query is tenant-scoped: a foreign comparison is a 404, never a 403.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.broker_proposal import BrokerProposal, BrokerProposalStatus
from app.models.comparison import Comparison, ComparisonSource, ComparisonStatus
from app.models.document import Document, DocumentCategory
from app.models.enums import CaseStage, EntityType
from app.models.proposal import Proposal
from app.services import ai as ai_service
from tests.conftest import API, make_case_file
from tests.test_ai_streaming import _FakeChatModel, tool_call_double


# --- Builders ----------------------------------------------------------------

_GOOD_OFFER = {
    "document_type": "budget_proposal",
    "document_type_confidence": 95,
    "insurer_name": "HDI Seguros S.A.",
    "insurer_rut": "99301000-6",
    "insurer_cmf_code": "CMF-HDI-001",
    "taxable_premium_uf": 100,
    "exempt_premium_uf": 0,
    "net_premium_uf": 100,
    "vat_uf": 19,
    "total_premium_uf": 119,
    "facets": [
        {"key": "sismo", "group": "deductible", "label": "Sismo", "present": True},
        {"key": "incendio", "group": "coverage", "label": "Incendio", "present": True},
    ],
}

_WRONG_FILE = {
    "document_type": "not_a_proposal",
    "document_type_confidence": 90,
    "rejection_reason": "Es un correo, no una cotización con prima ni coberturas.",
    "facets": [],
}

# A real cotización states only the INSURED's RUT — the insurer's identity is
# absent, so the reviewer must supply it at promote (find_or_create_insurer
# refuses name-only resolution).
_GOOD_OFFER_NO_INSURER = {
    "document_type": "budget_proposal",
    "document_type_confidence": 95,
    "insurer_name": "HDI Seguros S.A.",
    "insurer_rut": None,
    "insurer_cmf_code": None,
    "taxable_premium_uf": 100,
    "exempt_premium_uf": 0,
    "net_premium_uf": 100,
    "vat_uf": 19,
    "total_premium_uf": 119,
    "facets": [
        {"key": "sismo", "group": "deductible", "label": "Sismo", "present": True},
        {"key": "incendio", "group": "coverage", "label": "Incendio", "present": True},
    ],
}


def _readable_document(db, world, tmp_path, *, name: str) -> Document:
    """A document whose bytes really exist on disk, so the loader succeeds."""
    path = tmp_path / name
    path.write_text("Cotización de prueba. " * 8, encoding="utf-8")
    document = Document(
        broker_id=world.a.broker_id,
        entity_type=EntityType.QUOTE_REQUEST,
        entity_id=world.a.quote.id,
        s3_key=str(path),
        bucket="radal-test-bucket",
        original_name=name,
        mime_type="text/plain",
        category=DocumentCategory.BUDGET_PROPOSAL,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


@pytest.fixture()
def account_case(db, world):
    """An account case of broker A parked at COMPARISON, over its placement."""
    case = make_case_file(db, world.a, stage=CaseStage.COMPARISON)
    db.commit()
    db.refresh(case)
    return case


# --- The full happy path -----------------------------------------------------

def test_full_comparison_to_ratified_propuesta(
    client, headers_a, world, db, account_case, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    fake = _FakeChatModel(content=json.dumps(_GOOD_OFFER, ensure_ascii=False))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    monkeypatch.setattr(ai_service, "tool_call", tool_call_double(fake))

    # 1. Create the comparison.
    resp = client.post(
        f"{API}/comparisons",
        json={"case_file_id": account_case.id},
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    comparison_id = resp.json()["id"]
    assert resp.json()["status"] == ComparisonStatus.DRAFT.value

    # A repeat call returns the SAME live comparison, not a duplicate.
    again = client.post(
        f"{API}/comparisons",
        json={"case_file_id": account_case.id},
        headers=headers_a,
    )
    assert again.status_code == 201
    assert again.json()["id"] == comparison_id

    # 2a. Add a GOOD column.
    doc_ok = _readable_document(db, world, tmp_path, name="oferta-hdi.txt")
    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries",
        json={"document_id": doc_ok.id},
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["is_wrong_file"] is False
    assert body["document_type"] == "budget_proposal"
    good_entry_id = body["entry"]["id"]
    assert body["entry"]["facets"], "the compact facets were cached on the column"

    # 2b. Add a WRONG-FILE column — a visible rejection, never a 422.
    fake._content = json.dumps(_WRONG_FILE, ensure_ascii=False)
    doc_bad = _readable_document(db, world, tmp_path, name="correo.txt")
    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries",
        json={"document_id": doc_bad.id},
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["is_wrong_file"] is True
    assert body["document_type"] == "not_a_proposal"
    assert "correo" in (body["rejection_reason"] or "").lower()
    bad_entry_id = body["entry"]["id"]

    # 3. Align — deterministic slug-match (no provider reached).
    resp = client.post(f"{API}/comparisons/{comparison_id}/align", headers=headers_a)
    assert resp.status_code == 200, resp.text
    aligned = resp.json()
    assert aligned["comparison"]["status"] == ComparisonStatus.ALIGNED.value
    assert aligned["comparison"]["dictionary"], "a canonical dictionary was snapshotted"

    # A wrong-file column cannot be promoted.
    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{bad_entry_id}/promote",
        headers=headers_a,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "wrong_file"

    # 4. Promote the good column into a real inbound proposal.
    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{good_entry_id}/promote",
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    promoted = resp.json()
    proposal_id = promoted["proposal_id"]
    assert promoted["insurer_id"] == world.insurer.id  # matched on rut/cmf, native
    assert promoted["entry"]["proposal_id"] == proposal_id

    proposal = db.get(Proposal, proposal_id)
    assert proposal is not None
    assert str(proposal.total_premium_uf) == "119.0000"
    assert proposal.source_document_id == doc_ok.id

    # The cached source now carries the proposal id (linked back).
    source = db.get(ComparisonSource, promoted["entry"]["comparison_source_id"])
    assert source.proposal_id == proposal_id

    # A second promote of the same column is a conflict.
    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{good_entry_id}/promote",
        headers=headers_a,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "already_promoted"

    # 5. Mint the propuesta SOLELY from the comparison + the promoted winner.
    resp = client.post(
        f"{API}/broker-proposals",
        json={
            "comparison_id": comparison_id,
            "winning_proposal_id": proposal_id,
            "winner_note": "Mejor cobertura de sismo por prima.",
        },
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    bp = resp.json()
    assert bp["status"] == BrokerProposalStatus.DRAFT.value
    assert bp["content_hash"], "a stable content hash was computed"
    assert bp["comparison_id"] == comparison_id
    assert bp["winning_proposal_id"] == proposal_id
    bp_id = bp["id"]

    # The account was advanced past COMPARISON toward PROPOSAL_ISSUED.
    db.refresh(account_case)
    assert account_case.stage != CaseStage.COMPARISON

    # 6. Ratify — freeze it, stamp who, keep the hash.
    resp = client.post(
        f"{API}/broker-proposals/{bp_id}/ratify",
        json={"note": "Ratificado por el cliente."},
        headers=headers_a,
    )
    assert resp.status_code == 200, resp.text
    ratified = resp.json()
    assert ratified["is_ratified"] is True
    assert ratified["ratified_at"] is not None
    assert ratified["ratified_by_id"] == world.a.admin.id
    assert ratified["status"] == BrokerProposalStatus.RATIFIED.value

    # A second ratify is a conflict.
    resp = client.post(f"{API}/broker-proposals/{bp_id}/ratify", headers=headers_a)
    assert resp.status_code == 409


def test_mint_requires_winner_promoted_from_the_comparison(
    client, headers_a, world, db, account_case, tmp_path, monkeypatch
):
    """A propuesta cannot name a winner that is not a promoted column (422)."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    fake = _FakeChatModel(content=json.dumps(_GOOD_OFFER, ensure_ascii=False))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    monkeypatch.setattr(ai_service, "tool_call", tool_call_double(fake))

    comparison_id = client.post(
        f"{API}/comparisons",
        json={"case_file_id": account_case.id},
        headers=headers_a,
    ).json()["id"]
    doc = _readable_document(db, world, tmp_path, name="oferta.txt")
    client.post(
        f"{API}/comparisons/{comparison_id}/entries",
        json={"document_id": doc.id},
        headers=headers_a,
    )
    client.post(f"{API}/comparisons/{comparison_id}/align", headers=headers_a)

    # An unrelated proposal (never a column of this comparison).
    other = Proposal(
        broker_id=world.a.broker_id,
        quote_request_id=world.a.quote.id,
        insurer_id=world.insurer.id,
        source_document_id=world.a.document.id,
    )
    db.add(other)
    db.commit()
    db.refresh(other)

    resp = client.post(
        f"{API}/broker-proposals",
        json={"comparison_id": comparison_id, "winning_proposal_id": other.id},
        headers=headers_a,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "winner_not_in_comparison"


def test_mint_refuses_unaligned_comparison(
    client, headers_a, world, db, account_case, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    fake = _FakeChatModel(content=json.dumps(_GOOD_OFFER, ensure_ascii=False))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    monkeypatch.setattr(ai_service, "tool_call", tool_call_double(fake))

    comparison_id = client.post(
        f"{API}/comparisons",
        json={"case_file_id": account_case.id},
        headers=headers_a,
    ).json()["id"]
    doc = _readable_document(db, world, tmp_path, name="oferta.txt")
    good = client.post(
        f"{API}/comparisons/{comparison_id}/entries",
        json={"document_id": doc.id},
        headers=headers_a,
    ).json()
    promoted = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{good['entry']['id']}/promote",
        headers=headers_a,
    ).json()

    # Never aligned → mint is refused.
    resp = client.post(
        f"{API}/broker-proposals",
        json={
            "comparison_id": comparison_id,
            "winning_proposal_id": promoted["proposal_id"],
        },
        headers=headers_a,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "comparison_not_aligned"


# --- Promote: reviewer-supplied insurer identity -----------------------------

def _comparison_with_column(client, headers, db, world, tmp_path, account_case, *, name):
    """Open a comparison and add one good column extracted through the fake."""
    comparison_id = client.post(
        f"{API}/comparisons", json={"case_file_id": account_case.id}, headers=headers
    ).json()["id"]
    doc = _readable_document(db, world, tmp_path, name=name)
    good = client.post(
        f"{API}/comparisons/{comparison_id}/entries",
        json={"document_id": doc.id},
        headers=headers,
    ).json()
    return comparison_id, good["entry"]["id"], doc


def test_promote_with_supplied_cmf_resolves_and_mints(
    client, headers_a, world, db, account_case, tmp_path, monkeypatch
):
    """A cotización with no insurer identity promotes once the reviewer supplies
    the CMF code: it resolves the insurer by normalised code and mints a
    money-reconciled proposal."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    fake = _FakeChatModel(content=json.dumps(_GOOD_OFFER_NO_INSURER, ensure_ascii=False))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    monkeypatch.setattr(ai_service, "tool_call", tool_call_double(fake))

    comparison_id, entry_id, doc = _comparison_with_column(
        client, headers_a, db, world, tmp_path, account_case, name="oferta-sin-rut.txt"
    )

    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{entry_id}/promote",
        json={"insurer_cmf_code": "cmf-hdi-001"},  # lower-case: normalised to match
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    promoted = resp.json()
    assert promoted["insurer_id"] == world.insurer.id  # matched on cmf, not name
    proposal = db.get(Proposal, promoted["proposal_id"])
    assert proposal is not None
    assert str(proposal.total_premium_uf) == "119.0000"  # money reconciled
    assert proposal.source_document_id == doc.id


def test_promote_with_supplied_rut_resolves(
    client, headers_a, world, db, account_case, tmp_path, monkeypatch
):
    """A reviewer-supplied RUT (mod-11 validated) resolves the insurer too."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    fake = _FakeChatModel(content=json.dumps(_GOOD_OFFER_NO_INSURER, ensure_ascii=False))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    monkeypatch.setattr(ai_service, "tool_call", tool_call_double(fake))

    comparison_id, entry_id, _doc = _comparison_with_column(
        client, headers_a, db, world, tmp_path, account_case, name="oferta-rut.txt"
    )

    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{entry_id}/promote",
        json={"insurer_rut": "99301000-6"},
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["insurer_id"] == world.insurer.id


def test_promote_succeeds_with_non_additive_comprehensive_rate(
    client, headers_a, world, db, account_case, tmp_path, monkeypatch
):
    """HDI-shaped: premium reconciles but comprehensive rate is NOT the per-peril
    sum. The rate is soft now, so the promote SUCCEEDS (no money_inconsistent),
    the value is stored as-is, and the soft rate note rides along in warnings."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    hdi_offer = {
        "document_type": "budget_proposal",
        "document_type_confidence": 95,
        "insurer_name": "HDI Seguros S.A.",
        "insurer_rut": "99301000-6",
        "insurer_cmf_code": "CMF-HDI-001",
        "taxable_premium_uf": 100,
        "exempt_premium_uf": 0,
        "net_premium_uf": 100,
        "vat_uf": 19,
        "total_premium_uf": 119,
        "taxable_rate_permille": 1.2,
        "exempt_rate_permille": 0.8,
        "comprehensive_rate_permille": 2.357,  # weighted tasa media, not 2.0
        "facets": [],
    }
    fake = _FakeChatModel(content=json.dumps(hdi_offer, ensure_ascii=False))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    monkeypatch.setattr(ai_service, "tool_call", tool_call_double(fake))

    comparison_id, entry_id, _doc = _comparison_with_column(
        client, headers_a, db, world, tmp_path, account_case, name="oferta-hdi-rate.txt"
    )

    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{entry_id}/promote",
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    proposal = db.get(Proposal, body["proposal_id"])
    assert str(proposal.comprehensive_rate_permille) == "2.3570"  # stored independently
    # The soft rate note is surfaced, not swallowed, and not a 422.
    assert any("comprehensive_rate" in w for w in body["warnings"])


def test_promote_without_any_insurer_identity_is_422(
    client, headers_a, world, db, account_case, tmp_path, monkeypatch
):
    """No insurer identity anywhere → a clean 422 telling the reviewer to supply
    the RUT or CMF code, never a name match, never a 500."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    fake = _FakeChatModel(content=json.dumps(_GOOD_OFFER_NO_INSURER, ensure_ascii=False))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    monkeypatch.setattr(ai_service, "tool_call", tool_call_double(fake))

    comparison_id, entry_id, _doc = _comparison_with_column(
        client, headers_a, db, world, tmp_path, account_case, name="oferta-anon.txt"
    )

    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{entry_id}/promote",
        headers=headers_a,
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "insurer_identity_incomplete"
    assert "RUT" in detail["message"] or "CMF" in detail["message"]
    # No proposal was minted, and the column stays un-promoted.
    entry_resp = client.get(f"{API}/comparisons/{comparison_id}", headers=headers_a)
    entry = next(e for e in entry_resp.json()["entries"] if e["id"] == entry_id)
    assert entry["proposal_id"] is None


def test_promote_supplied_identity_wins_over_parsed(
    client, headers_a, world, db, account_case, tmp_path, monkeypatch
):
    """A valid supplied identity takes precedence over the parsed one."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    # The parse carries a DIFFERENT (external) identity; the reviewer overrides it.
    parsed = dict(_GOOD_OFFER_NO_INSURER)
    parsed["insurer_rut"] = "76000000-0"
    parsed["insurer_cmf_code"] = "CMF-OTHER-9"
    fake = _FakeChatModel(content=json.dumps(parsed, ensure_ascii=False))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    monkeypatch.setattr(ai_service, "tool_call", tool_call_double(fake))

    comparison_id, entry_id, _doc = _comparison_with_column(
        client, headers_a, db, world, tmp_path, account_case, name="oferta-override.txt"
    )

    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{entry_id}/promote",
        json={"insurer_cmf_code": "CMF-HDI-001"},
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["insurer_id"] == world.insurer.id  # the supplied CMF won


# --- Align: holistic submit_comparison (v9) ----------------------------------

def test_align_surfaces_recommendation_and_dictionary(
    client, headers_a, world, db, account_case, tmp_path, monkeypatch
):
    """The holistic align snapshots a standardized table + an AI recommendation."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    fake = _FakeChatModel(content=json.dumps(_GOOD_OFFER, ensure_ascii=False))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    monkeypatch.setattr(ai_service, "tool_call", tool_call_double(fake))

    comparison_id, _entry_id, _doc = _comparison_with_column(
        client, headers_a, db, world, tmp_path, account_case, name="oferta-align.txt"
    )

    resp = client.post(f"{API}/comparisons/{comparison_id}/align", headers=headers_a)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["used_ai"] is True
    assert body["batched"] is False
    assert body["comparison"]["status"] == ComparisonStatus.ALIGNED.value
    assert body["comparison"]["dictionary"], "a canonical dictionary was snapshotted"
    # The recommendation is surfaced both on the align result and the read.
    assert body["recommendation"] is not None
    assert body["recommendation"]["rationale"]
    assert body["comparison"]["recommendation"]["rationale"]

    # GET exposes the recommendation too (Phase 3 reads it here).
    got = client.get(f"{API}/comparisons/{comparison_id}", headers=headers_a)
    assert got.status_code == 200
    assert got.json()["recommendation"]["rationale"]


def test_align_blocks_when_provider_unreachable(
    client, headers_a, world, db, account_case, tmp_path, monkeypatch
):
    """BLOCK-ON-DOWN: an AIError from ``submit_comparison`` is a clean provider
    error (never a deterministic half-baked view, never a 500)."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    fake = _FakeChatModel(content=json.dumps(_GOOD_OFFER, ensure_ascii=False))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    monkeypatch.setattr(ai_service, "tool_call", tool_call_double(fake))

    comparison_id, _entry_id, _doc = _comparison_with_column(
        client, headers_a, db, world, tmp_path, account_case, name="oferta-outage.txt"
    )

    # The provider goes down at align time: submit_comparison raises, align blocks.
    def _down(**kwargs):
        if (kwargs.get("tool_schema") or {}).get("name") == "submit_comparison":
            raise ai_service.AIProviderError("provider unreachable")
        return tool_call_double(fake)(**kwargs)

    monkeypatch.setattr(ai_service, "tool_call", _down)

    resp = client.post(f"{API}/comparisons/{comparison_id}/align", headers=headers_a)
    assert resp.status_code == 502, resp.text
    assert resp.headers.get("X-Radal-AI-Error") == "ai_provider"
    # The comparison was NOT flipped to ALIGNED — the step is blocked.
    db.refresh(db.get(Comparison, comparison_id))
    got = client.get(f"{API}/comparisons/{comparison_id}", headers=headers_a).json()
    assert got["status"] != ComparisonStatus.ALIGNED.value


# --- Tenant isolation --------------------------------------------------------

def test_foreign_comparison_is_404(client, headers_b, world, db, account_case):
    """Broker B cannot see broker A's comparison — a 404 by every path."""
    comparison = Comparison(
        broker_id=world.a.broker_id,
        case_file_id=account_case.id,
        status=ComparisonStatus.DRAFT,
    )
    db.add(comparison)
    db.commit()
    db.refresh(comparison)

    assert (
        client.get(f"{API}/comparisons/{comparison.id}", headers=headers_b).status_code
        == 404
    )
    assert (
        client.post(
            f"{API}/comparisons/{comparison.id}/entries",
            json={"document_id": world.b.document.id},
            headers=headers_b,
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{API}/comparisons/{comparison.id}/align", headers=headers_b
        ).status_code
        == 404
    )


def test_foreign_broker_proposal_is_404(client, headers_b, world, db, account_case):
    comparison = Comparison(
        broker_id=world.a.broker_id,
        case_file_id=account_case.id,
        status=ComparisonStatus.ALIGNED,
    )
    db.add(comparison)
    db.flush()
    bp = BrokerProposal(
        broker_id=world.a.broker_id,
        case_file_id=account_case.id,
        comparison_id=comparison.id,
        status=BrokerProposalStatus.DRAFT,
    )
    db.add(bp)
    db.commit()
    db.refresh(bp)

    assert client.get(f"{API}/broker-proposals/{bp.id}", headers=headers_b).status_code == 404
    assert (
        client.post(f"{API}/broker-proposals/{bp.id}/ratify", headers=headers_b).status_code
        == 404
    )


# --- Pending actions ---------------------------------------------------------

def test_pending_actions_lists_gaps(
    client, headers_a, world, db, account_case, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    fake = _FakeChatModel(content=json.dumps(_GOOD_OFFER, ensure_ascii=False))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    monkeypatch.setattr(ai_service, "tool_call", tool_call_double(fake))

    # A comparison with an un-aligned column.
    comparison_id = client.post(
        f"{API}/comparisons",
        json={"case_file_id": account_case.id},
        headers=headers_a,
    ).json()["id"]
    doc = _readable_document(db, world, tmp_path, name="oferta.txt")
    client.post(
        f"{API}/comparisons/{comparison_id}/entries",
        json={"document_id": doc.id},
        headers=headers_a,
    )

    resp = client.get(
        f"{API}/case-files/{account_case.id}/pending-actions", headers=headers_a
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["case_file_id"] == account_case.id
    codes = {a["code"] for a in body["actions"]}
    # No antecedentes record yet, and the fresh column is not aligned.
    assert "antecedentes_missing" in codes
    assert "comparison_unaligned" in codes
    for action in body["actions"]:
        assert action["severity"] in {"info", "warning", "blocker"}
        assert action["tab"]
        assert action["reason"]


def test_pending_actions_foreign_case_is_404(client, headers_b, world, account_case):
    resp = client.get(
        f"{API}/case-files/{account_case.id}/pending-actions", headers=headers_b
    )
    assert resp.status_code == 404


# --- List broker proposals by case -------------------------------------------

def test_list_broker_proposals_by_case(
    client, headers_a, headers_b, world, db, account_case
):
    """The list-by-case endpoint returns the account's propuestas newest-first,
    and 404s a foreign case (never a 403)."""
    comparison = Comparison(
        broker_id=world.a.broker_id,
        case_file_id=account_case.id,
        status=ComparisonStatus.ALIGNED,
    )
    db.add(comparison)
    db.flush()
    older = BrokerProposal(
        broker_id=world.a.broker_id,
        case_file_id=account_case.id,
        comparison_id=comparison.id,
        status=BrokerProposalStatus.DRAFT,
    )
    db.add(older)
    db.flush()
    newer = BrokerProposal(
        broker_id=world.a.broker_id,
        case_file_id=account_case.id,
        comparison_id=comparison.id,
        status=BrokerProposalStatus.RATIFIED,
    )
    db.add(newer)
    db.commit()
    db.refresh(older)
    db.refresh(newer)

    resp = client.get(
        f"{API}/broker-proposals", params={"case_file_id": account_case.id}, headers=headers_a
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [bp["id"] for bp in body] == [newer.id, older.id]  # newest-first
    assert body[0]["case_file_id"] == account_case.id

    # A foreign broker sees the account (and thus its propuestas) as a 404.
    resp = client.get(
        f"{API}/broker-proposals", params={"case_file_id": account_case.id}, headers=headers_b
    )
    assert resp.status_code == 404


def test_list_broker_proposals_minted_flow(
    client, headers_a, world, db, account_case, tmp_path, monkeypatch
):
    """A propuesta minted through the real flow is returned by the list endpoint."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    fake = _FakeChatModel(content=json.dumps(_GOOD_OFFER, ensure_ascii=False))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    monkeypatch.setattr(ai_service, "tool_call", tool_call_double(fake))

    comparison_id = client.post(
        f"{API}/comparisons", json={"case_file_id": account_case.id}, headers=headers_a
    ).json()["id"]
    doc = _readable_document(db, world, tmp_path, name="oferta.txt")
    good = client.post(
        f"{API}/comparisons/{comparison_id}/entries",
        json={"document_id": doc.id},
        headers=headers_a,
    ).json()
    client.post(f"{API}/comparisons/{comparison_id}/align", headers=headers_a)
    proposal_id = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{good['entry']['id']}/promote",
        headers=headers_a,
    ).json()["proposal_id"]
    bp_id = client.post(
        f"{API}/broker-proposals",
        json={"comparison_id": comparison_id, "winning_proposal_id": proposal_id},
        headers=headers_a,
    ).json()["id"]

    resp = client.get(
        f"{API}/broker-proposals", params={"case_file_id": account_case.id}, headers=headers_a
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [bp["id"] for bp in body] == [bp_id]
    assert body[0]["comparison_id"] == comparison_id
    assert body[0]["winning_proposal_id"] == proposal_id


# --- Premium core surfaced per column pre-promotion --------------------------

def test_comparison_read_surfaces_premium_core(
    client, headers_a, world, db, account_case, tmp_path, monkeypatch
):
    """Each column exposes its extracted money core before promotion; a
    wrong-file column carries a null premium block."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    fake = _FakeChatModel(content=json.dumps(_GOOD_OFFER, ensure_ascii=False))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    monkeypatch.setattr(ai_service, "tool_call", tool_call_double(fake))

    comparison_id = client.post(
        f"{API}/comparisons", json={"case_file_id": account_case.id}, headers=headers_a
    ).json()["id"]

    doc_ok = _readable_document(db, world, tmp_path, name="oferta-hdi.txt")
    good = client.post(
        f"{API}/comparisons/{comparison_id}/entries",
        json={"document_id": doc_ok.id},
        headers=headers_a,
    ).json()
    good_entry_id = good["entry"]["id"]
    # The premium block is already present on the add-entry response (pre-promote).
    assert good["entry"]["premium"] is not None
    assert good["entry"]["premium"]["total_premium_uf"] is not None

    fake._content = json.dumps(_WRONG_FILE, ensure_ascii=False)
    doc_bad = _readable_document(db, world, tmp_path, name="correo.txt")
    bad = client.post(
        f"{API}/comparisons/{comparison_id}/entries",
        json={"document_id": doc_bad.id},
        headers=headers_a,
    ).json()
    bad_entry_id = bad["entry"]["id"]

    resp = client.get(f"{API}/comparisons/{comparison_id}", headers=headers_a)
    assert resp.status_code == 200, resp.text
    by_id = {e["id"]: e for e in resp.json()["entries"]}

    good_premium = by_id[good_entry_id]["premium"]
    assert good_premium is not None
    assert good_premium["total_premium_uf"] is not None
    assert good_premium["net_premium_uf"] is not None
    assert good_premium["taxable_premium_uf"] is not None
    # Money invariants are NOT re-validated here — display only.

    # A wrong-file column has a null premium block.
    assert by_id[bad_entry_id]["is_wrong_file"] is True
    assert by_id[bad_entry_id]["premium"] is None
