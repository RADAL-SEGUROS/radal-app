"""Fast reading — the tiered POLICY read (docs/v12/11-fast-reading.md).

Hermetic: the provider is a fake over ``ai_service._chat_model`` (conftest blanks
the key; the ``ai_key`` fixture only flips the "configured" switch). Encodes:

- slim schemas: small hints, lenient row-by-row coercion, titles capped;
- the core runner: groups run IN PARALLEL with an explicit per-call timeout (READ_TIMEOUT_SECONDS) and
  per-group token budgets; the merge feeds the unchanged commit path; one failed
  core group fails the read with a typed error and a failed extraction row;
- the technical read: endpoints (404 foreign, 409 no PDF), a SUGGESTION until
  confirm (rule 6), per-section errors, confirm never overwrites, discard writes
  nothing;
- the audit's correctness fixes (core validation, mirror aliases);
- the agent's ``search_document_text`` read tool.
"""
from __future__ import annotations

import json
import threading
import time
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.activity import Activity
from app.models.ai import Extraction, ExtractionStatus
from app.models.document import Document, DocumentCategory
from app.models.enums import EntityType, PartyRole
from app.models.policy import Policy
from app.models.policy_party import PolicyParty
from app.models.warranty import Warranty
from app.schemas.extraction.policy_tiers import (
    CORE_GROUPS,
    TECHNICAL_GROUPS,
    CoreIdentityMoney,
    CorePlan,
    TechClauses,
    TechDeductibles,
    TechRisk,
)
from app.services import agent_tools
from app.services import ai as ai_service
from app.services import mirror
from app.services import policy_reading
from app.services.policy_validation import validate_policy_core
from tests.conftest import API, make_case_file, make_policy


# =============================================================================
# Provider double — answers per GROUP (the schema it is asked for)
# =============================================================================


class _Msg:
    def __init__(self, content: str, finish_reason: str = "stop"):
        self.content = content
        self.additional_kwargs: dict = {}
        self.response_metadata = {
            "model_name": "fake/tiered",
            "finish_reason": finish_reason,
            "token_usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        self.usage_metadata = {"input_tokens": 100, "output_tokens": 50}


class _GroupFake:
    """``_chat_model`` double. ``answers`` maps a schema class name to a dict
    (answered as JSON) or an exception (raised). Records every build's kwargs,
    the threads it ran on and the wall clock of each call."""

    def __init__(self, answers: dict, delay: float = 0.0):
        self.answers = answers
        self.delay = delay
        self.builds: list[dict] = []
        self.calls: list[tuple[str, str, float, float]] = []
        self.lock = threading.Lock()

    def __call__(self, **kwargs):
        with self.lock:
            self.builds.append(kwargs)
        return _GroupModel(self)


class _GroupModel:
    def __init__(self, owner: _GroupFake):
        self.owner = owner

    def with_structured_output(self, schema, method=None, include_raw=False):
        assert method == "json_mode" and include_raw
        return _GroupStructured(self.owner, schema)


class _GroupStructured:
    def __init__(self, owner: _GroupFake, schema):
        self.owner, self.schema = owner, schema

    def invoke(self, messages):
        started = time.monotonic()
        if self.owner.delay:
            time.sleep(self.owner.delay)
        answer = self.owner.answers.get(self.schema.__name__, {})
        with self.owner.lock:
            self.owner.calls.append(
                (self.schema.__name__, threading.current_thread().name, started, time.monotonic())
            )
        if isinstance(answer, BaseException):
            raise answer
        return {"raw": _Msg(json.dumps(answer, ensure_ascii=False)), "parsed": None,
                "parsing_error": None}


@pytest.fixture()
def ai_key(monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")


def _install(monkeypatch, answers: dict, delay: float = 0.0) -> _GroupFake:
    fake = _GroupFake(answers, delay=delay)
    monkeypatch.setattr(ai_service, "_chat_model", fake)
    return fake


_INSURER = {"legal_name": "HDI Seguros S.A.", "rut": "99301000-6", "cmf_code": "CMF-HDI-001"}

_IDENTITY = {
    "policy_number": "FR-001",
    "insurer": _INSURER,
    "insured": [
        {"legal_name": "Asegurado Alfa S.A.", "rut": "77.111.111-4"},
        {"legal_name": "Filial Alfa SpA", "rut": "76.086.428-5"},
    ],
    "beneficiaries": [
        {"name": "Banco Hipotecario S.A.", "rut": "97.004.000-5", "type": "Acreedor hipotecario",
         "amount_uf": "UF 5.000"},
    ],
    "broker": {"legal_name": "Corredora Alfa SpA"},
    "period_start_at": "2026-01-01T12:00",
    "period_end_at": "2027-01-01T12:00",
    "total_insured_amount_uf": "UF 17.920",
    "premium": {"taxable_premium_uf": 100, "exempt_premium_uf": 39, "vat_uf": 19,
                "total_premium_uf": 158},
    "commission_pct": "12,5",
    "confidence": 88,
    # A technical key leaking into the core is ignored (it is a suggestion).
    "deductibles": [{"peril": "incendio", "text": "10% de la pérdida"}],
}
_PLAN = {
    "payment_mode": "PAC",
    "installments": [
        {"number": 1, "due_date": "2026-02-05", "gross_amount_uf": 79},
        {"number": 2, "due_date": "2026-03-05", "gross_amount_uf": "79,0"},
    ],
}
_RISK = {
    "items": [{"type": "Ubicacion", "description": "Bodega central",
               "address_or_plate": "Av. Uno 123", "insured_amount_uf": "UF 10.000"}],
    "coverages": [{"code": "POL120130", "name": "Incendio", "limit_uf": 17920,
                   "sublimit_text": "Remoción de escombros 10%"}],
    "indemnity_limit": "UF 17.920 por evento y en el agregado anual",
    "aggregate_limit_uf": 17920,
    "valuation_basis": "Valor de reposición",
    "average_rate_permille": "1,32",
    "cmf_policy_code": "POL120130",
}
_DEDUCTIBLES = {
    "deductibles": [
        {"peril": "incendio", "pct": 10, "min_uf": 25, "text": "10% de la pérdida, mínimo UF 25"},
        {"peril": "sismo", "pct": 2, "min_uf": 50,
         "text": "2% del monto asegurado del ítem afectado, mínimo UF 50"},
    ],
    "warranties": [{"code": "R-1", "title": "Extintores", "requirement": "Mantener extintores",
                    "deadline_days": 30}],
}
_CLAUSES = {
    "exclusions": [{"number": "1", "title": "Guerra"}, "Terrorismo " + "x" * 300],
    "particular_clauses": [{"number": "3", "title": "Cláusula de 72 horas"}],
}


#: The sublimits table is its own parallel group (one row per extension).
_SUBLIMITS = {"sublimits": [{"name": "Incorporación automática de nuevos bienes", "limit_uf": 25000,
                             "sublimit_text": "10% del monto asegurado, máx UF 25.000"}]}


def _all_answers(**over) -> dict:
    answers = {
        "CoreIdentityMoney": _IDENTITY,
        "CorePlan": _PLAN,
        "TechRisk": _RISK,
        "TechSublimits": _SUBLIMITS,
        "TechDeductibles": _DEDUCTIBLES,
        "TechClauses": _CLAUSES,
    }
    answers.update(over)
    return answers


def _doc(db, tenant, tmp_path, *, case_id=None, name="poliza.txt", text=None):
    path = tmp_path / name
    path.write_text(
        text or "Póliza emitida por la aseguradora. Exclusión de guerra y terrorismo. " * 20,
        encoding="utf-8",
    )
    document = Document(
        broker_id=tenant.broker_id,
        entity_type=EntityType.CASE_FILE if case_id else EntityType.CLIENT,
        entity_id=case_id or tenant.client.id,
        case_file_id=case_id,
        s3_key=str(path),
        bucket="radal-test-bucket",
        original_name=name,
        mime_type="text/plain",
        category=DocumentCategory.POLICY,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def _upload(client, db, world, tmp_path, headers, monkeypatch, answers=None):
    case = make_case_file(db, world.a)
    db.commit()
    document = _doc(db, world.a, tmp_path, case_id=case.id)
    fake = _install(monkeypatch, answers or _all_answers())
    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "case_file_id": case.id},
        headers=headers,
    )
    return response, document, fake


# =============================================================================
# 1. Slim schemas
# =============================================================================


def test_slim_schema_hints_are_far_below_the_old_budget():
    sizes = {g.name: len(g.hint) for g in (*CORE_GROUPS, *TECHNICAL_GROUPS)}
    assert all(size < 1_200 for size in sizes.values()), sizes
    assert sum(sizes.values()) < ai_service.MAX_SCHEMA_CHARS // 4
    # No server-computed field and no dropped field is asked of the model.
    for name in ("inconsistencies", "issue_place", "mirror_issuance_statement", "premium_lines",
                 "drivers", "workers", "ap_plans", "requested_limit", "is_sublimit"):
        assert all(name not in g.hint for g in (*CORE_GROUPS, *TECHNICAL_GROUPS)), name
    assert {g.max_tokens for g in CORE_GROUPS} == {4000}
    assert {g.max_tokens for g in TECHNICAL_GROUPS} == {6000}


def test_slim_coercion_drops_bad_rows_one_by_one():
    plan = CorePlan.model_validate(
        {"installments": [{"number": 1, "gross_amount_uf": "UF 1.234,5"}, 7, None, "cuota 2"]}
    )
    # The int / null / prose rows are dropped one by one; the good row survives.
    assert plan.installments[0].gross_amount_uf == Decimal("1234.5")
    assert len(plan.installments) == 1

    core = CoreIdentityMoney.model_validate(
        {"insured": {"legal_name": "Solo"}, "beneficiaries": ["Banco X", {"name": "Leasing Co",
         "type": "LEASING financiero"}], "extraction_notes": ["a", "b", "c", "d", "e"]}
    )
    assert [p.legal_name for p in core.insured] == ["Solo"]
    assert core.beneficiaries[1].type == "leasing"
    assert len(core.extraction_notes) == 3

    clauses = TechClauses.model_validate(
        {"exclusions": ["Guerra " + "x" * 400, {"number": 2, "text": "Terrorismo"}]}
    )
    assert len(clauses.exclusions[0].title) <= 120
    assert clauses.exclusions[1].title == "Terrorismo" and clauses.exclusions[1].number == "2"
    assert set(clauses.exclusions[1].model_dump()) == {"number", "title"}

    risk = TechRisk.model_validate({"items": [{"type": "Camión", "description": "Tolva"}]})
    assert risk.items[0].type == "vehículo"
    assert TechDeductibles.model_validate({"deductibles": "5% de la pérdida"}).deductibles[0].text


def test_merge_core_maps_onto_the_committer_shape():
    merged = policy_reading.merge_core(
        CoreIdentityMoney.model_validate(_IDENTITY).model_dump(mode="json"),
        CorePlan.model_validate(_PLAN).model_dump(mode="json"),
    )
    assert merged["insured"] == {"legal_name": "Asegurado Alfa S.A.", "rut": "77.111.111-4"}
    assert len(merged["insured_parties"]) == 2
    assert merged["beneficiaries"][0]["type"] == "hipotecario"
    assert "deductibles" not in merged  # a technical key never rides on the core
    assert len(merged["installments"]) == 2
    assert merged["payment_mode"] == "PAC"
    # Nothing empty survives.
    assert "renews_policy_number" not in merged and "policyholder" not in merged


# =============================================================================
# 2. The parallel runner
# =============================================================================


def test_run_tier_runs_groups_in_parallel_with_explicit_budgets(ai_key, monkeypatch):
    fake = _install(monkeypatch, _all_answers(), delay=0.4)
    base = [("system", "reglas"), ("human", "documento")]
    started = time.monotonic()
    outcomes = policy_reading.run_tier(TECHNICAL_GROUPS, base)
    wall = time.monotonic() - started

    assert all(o.ok for o in outcomes.values()), outcomes
    assert wall < 1.0, f"{len(TECHNICAL_GROUPS)} x 0.4 s groups took {wall:.2f}s — not parallel"
    assert len({thread for _n, thread, _s, _e in fake.calls}) == len(TECHNICAL_GROUPS)
    # Explicit per-call timeout and per-group output budgets.
    assert {b["timeout"] for b in fake.builds} == {float(policy_reading.READ_TIMEOUT_SECONDS)}
    assert sorted(b["max_tokens"] for b in fake.builds) == [6000] * len(TECHNICAL_GROUPS)
    reads = {name: o.value for name, o in outcomes.items()}
    assert reads["tech_clauses"].finish_reason == "stop"
    assert reads["tech_risk"].completion_tokens == 50


def test_run_tier_reports_each_failure_without_raising(ai_key, monkeypatch):
    _install(monkeypatch, _all_answers(TechRisk=TimeoutError("slow")))
    outcomes = policy_reading.run_tier(TECHNICAL_GROUPS, [("human", "x")])
    assert not outcomes["tech_risk"].ok and outcomes["tech_deductibles"].ok
    assert isinstance(policy_reading._typed(outcomes["tech_risk"].error), ai_service.AITimeout)


# =============================================================================
# 3. POST /policies/upload — the core tier
# =============================================================================


def test_upload_runs_only_the_core_and_announces_the_technical_read(
    client, world, db, tmp_path, ai_key, monkeypatch, headers_a
):
    response, document, fake = _upload(client, db, world, tmp_path, headers_a, monkeypatch)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["technical_read"] == {"status": "pending"}
    assert body["is_core_valid"] is True
    # Only the two core groups ran — in parallel threads, 4 000 tokens each.
    assert sorted(name for name, *_ in fake.calls) == ["CoreIdentityMoney", "CorePlan"]
    assert [b["max_tokens"] for b in fake.builds] == [4000, 4000]

    policy = db.get(Policy, body["policy"]["id"])
    assert policy.policy_number == "FR-001"
    assert policy.total_premium_uf == Decimal("158.0000")
    assert policy.commission_pct == Decimal("12.5")
    assert policy.deductibles is None  # the leaked technical key was NOT committed
    assert body["policy"]["payload"]["beneficiaries"][0]["type"] == "hipotecario"
    # Σ cuotas = gross -> the plan committed.
    assert any("Plan de pago registrado" in w for w in body["warnings"])

    row = db.get(Extraction, body["extraction_id"])
    assert row.prompt_version == "policy-core-v1"
    assert row.status == ExtractionStatus.SUCCEEDED
    assert row.category == DocumentCategory.POLICY
    assert set(row.raw_output["groups"]) == {"core_identity_money", "core_plan"}
    for telemetry in row.raw_output["groups"].values():
        assert {"seconds", "completion_tokens", "finish_reason"} <= set(telemetry)
    assert row.completion_tokens == 100 and row.confidence == Decimal("88.000")

    parties = {(p.role, p.name): p for p in db.scalars(
        select(PolicyParty).where(PolicyParty.policy_id == policy.id)).all()}
    beneficiary = parties[(PartyRole.BENEFICIARY, "Banco Hipotecario S.A.")]
    assert beneficiary.beneficiary_kind == "hipotecario" and beneficiary.rut == "97004000-5"
    assert (PartyRole.ADDITIONAL_INSURED, "Filial Alfa SpA") in parties


def test_one_failed_core_group_fails_the_read_honestly(
    client, world, db, tmp_path, ai_key, monkeypatch, headers_a
):
    answers = _all_answers(CorePlan=TimeoutError("read timed out"))
    response, document, _fake = _upload(client, db, world, tmp_path, headers_a, monkeypatch, answers)
    assert response.status_code == 504, response.text
    assert response.headers["X-Radal-AI-Error"] == "ai_timeout"
    assert "tardó más de lo esperado" in response.json()["detail"]

    row = db.scalars(select(Extraction).where(Extraction.document_id == document.id)).one()
    assert row.status == ExtractionStatus.FAILED
    assert row.prompt_version == "policy-core-v1"
    assert "core_plan" in row.error
    assert row.raw_output["groups"]["core_plan"]["error_code"] == "ai_timeout"
    assert db.scalars(select(Policy).where(Policy.policy_number == "FR-001")).first() is None


def test_a_core_parse_failure_is_a_typed_422(client, world, db, tmp_path, ai_key, monkeypatch, headers_a):
    fake = _install(monkeypatch, _all_answers())

    def _bad_invoke(self, messages):  # the identity group answers prose
        if self.schema is CoreIdentityMoney:
            return {"raw": _Msg("no es json"), "parsed": None, "parsing_error": None}
        return {"raw": _Msg(json.dumps(_PLAN)), "parsed": None, "parsing_error": None}

    monkeypatch.setattr(_GroupStructured, "invoke", _bad_invoke)
    case = make_case_file(db, world.a)
    db.commit()
    document = _doc(db, world.a, tmp_path, case_id=case.id)
    response = client.post(
        f"{API}/policies/upload", json={"document_id": document.id, "case_file_id": case.id},
        headers=headers_a,
    )
    assert response.status_code == 422, response.text
    assert response.headers["X-Radal-AI-Error"] == "ai_parse"
    assert fake.builds


# =============================================================================
# 4. The technical read
# =============================================================================


def _uploaded_policy(client, world, db, tmp_path, headers_a, monkeypatch, answers=None):
    response, document, _ = _upload(client, db, world, tmp_path, headers_a, monkeypatch, answers)
    assert response.status_code == 201, response.text
    return response.json()["policy"]["id"], document


def test_technical_read_is_a_suggestion_until_confirmed(
    client, world, db, tmp_path, ai_key, monkeypatch, headers_a
):
    policy_id, document = _uploaded_policy(client, world, db, tmp_path, headers_a, monkeypatch)
    url = f"{API}/policies/{policy_id}/technical-read"
    assert client.get(url, headers=headers_a).json()["status"] == "none"

    fake = _install(monkeypatch, _all_answers())
    first = client.post(url, headers=headers_a)
    assert first.status_code == 200, first.text
    read = first.json()
    assert read["status"] == "ready"
    assert sorted(name for name, *_ in fake.calls) == ["TechClauses", "TechDeductibles", "TechRisk", "TechSublimits"]
    assert read["counts"] == {"items": 1, "coverages": 2, "deductibles": 2, "warranties": 1,  # principal + sublimits
                              "exclusions": 2, "particular_clauses": 1}
    assert read["sections"]["items"][0]["type"] == "ubicación"
    assert read["sections"]["limits"]["valuation_basis"] == "Valor de reposición"
    assert len(read["sections"]["exclusions"][1]["title"]) <= 120
    assert read["section_errors"] == {}

    row = db.get(Extraction, read["extraction_id"])
    assert row.prompt_version == "policy-technical-v1" and row.category == DocumentCategory.POLICY
    assert set(row.raw_output["groups"]) == {"tech_risk", "tech_sublimits", "tech_deductibles", "tech_clauses"}

    # SUGGEST only: nothing reached the policy.
    db.expire_all()
    policy = db.get(Policy, policy_id)
    assert policy.deductibles is None and policy.insured_amount_semantics is None
    assert not db.scalars(select(Warranty).where(Warranty.policy_id == policy_id)).all()

    # Idempotent-ish: the unconfirmed read is returned again; ?force=true re-reads.
    again = client.post(url, headers=headers_a).json()
    assert again["extraction_id"] == read["extraction_id"]
    forced = client.post(url, params={"force": "true"}, headers=headers_a).json()
    assert forced["extraction_id"] != read["extraction_id"]
    assert client.get(url, headers=headers_a).json()["extraction_id"] == forced["extraction_id"]


def test_confirm_writes_only_the_listed_columns_and_never_overwrites(
    client, world, db, tmp_path, ai_key, monkeypatch, headers_a
):
    policy_id, _ = _uploaded_policy(client, world, db, tmp_path, headers_a, monkeypatch)
    # A value the policy already holds (a confirmed / corrected one).
    policy = db.get(Policy, policy_id)
    policy.cmf_policy_code = "POL999999"
    db.commit()

    _install(monkeypatch, _all_answers())
    read = client.post(f"{API}/policies/{policy_id}/technical-read", headers=headers_a).json()
    confirm = client.post(
        f"{API}/policies/{policy_id}/technical-read/{read['extraction_id']}/confirm",
        json={}, headers=headers_a,
    )
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()
    assert body["status"] == "confirmed"
    assert set(body["confirmed_sections"]) == {
        "items", "coverages", "deductibles", "warranties", "exclusions", "particular_clauses",
        "limits"}
    assert any("código CMF" in w and "POL999999" in w for w in body["warnings"])

    db.expire_all()
    policy = db.get(Policy, policy_id)
    assert policy.cmf_policy_code == "POL999999"  # never overwritten
    assert policy.insured_amount_semantics == "Valor de reposición"
    assert policy.aggregate_limit_uf == Decimal("17920.0000")
    assert policy.average_rate_permille == Decimal("1.3200")
    assert policy.indemnity_limit.startswith("UF 17.920")
    assert policy.deductibles["sismo"]["min_uf"] in ("50", 50, "50.0")
    assert policy.deductibles["incendio"]["text"] == "10% de la pérdida, mínimo UF 25"
    assert policy.payload["exclusions"][0]["title"] == "Guerra"
    assert policy.payload["items"][0]["description"] == "Bodega central"
    assert policy.payload["technical_read"]["extraction_id"] == read["extraction_id"]
    # Core values on the payload are untouched.
    assert policy.payload["policy_number"] == "FR-001"
    warranty = db.scalars(select(Warranty).where(Warranty.policy_id == policy_id)).one()
    assert warranty.code == "R-1" and warranty.deadline_days == 30
    assert db.scalars(select(Activity).where(
        Activity.action == "policy.technical_confirmed", Activity.entity_id == policy_id)).one()

    row = db.get(Extraction, read["extraction_id"])
    assert row.raw_output["confirmation"]["target"] == "policy.technical"
    assert client.get(f"{API}/policies/{policy_id}/technical-read",
                      headers=headers_a).json()["status"] == "confirmed"
    # A second confirm is a conflict, not a duplicate write.
    twice = client.post(
        f"{API}/policies/{policy_id}/technical-read/{read['extraction_id']}/confirm",
        json={}, headers=headers_a,
    )
    assert twice.status_code == 409


def test_confirm_a_subset_of_sections(client, world, db, tmp_path, ai_key, monkeypatch, headers_a):
    policy_id, _ = _uploaded_policy(client, world, db, tmp_path, headers_a, monkeypatch)
    _install(monkeypatch, _all_answers())
    read = client.post(f"{API}/policies/{policy_id}/technical-read", headers=headers_a).json()
    body = client.post(
        f"{API}/policies/{policy_id}/technical-read/{read['extraction_id']}/confirm",
        json={"sections": ["exclusions"]}, headers=headers_a,
    ).json()
    assert body["confirmed_sections"] == ["exclusions"]
    db.expire_all()
    policy = db.get(Policy, policy_id)
    assert policy.deductibles is None and policy.insured_amount_semantics is None
    assert policy.payload["exclusions"][0]["title"] == "Guerra"
    assert not db.scalars(select(Warranty).where(Warranty.policy_id == policy_id)).all()


def test_a_failed_group_empties_only_its_sections(
    client, world, db, tmp_path, ai_key, monkeypatch, headers_a
):
    policy_id, _ = _uploaded_policy(client, world, db, tmp_path, headers_a, monkeypatch)
    _install(monkeypatch, _all_answers(TechDeductibles=TimeoutError("slow")))
    read = client.post(f"{API}/policies/{policy_id}/technical-read", headers=headers_a).json()
    assert read["status"] == "ready"
    assert set(read["section_errors"]) == {"deductibles", "warranties"}
    assert "tardó más de lo esperado" in read["section_errors"]["deductibles"]
    assert read["counts"]["deductibles"] == 0 and read["counts"]["exclusions"] == 2
    assert read["message"]

    body = client.post(
        f"{API}/policies/{policy_id}/technical-read/{read['extraction_id']}/confirm",
        json={}, headers=headers_a,
    ).json()
    assert "deductibles" not in body["confirmed_sections"]
    assert any("deducibles no se pudo leer" in w for w in body["warnings"])


def test_every_group_failing_is_a_failed_read_with_a_code(
    client, world, db, tmp_path, ai_key, monkeypatch, headers_a
):
    policy_id, document = _uploaded_policy(client, world, db, tmp_path, headers_a, monkeypatch)
    _install(monkeypatch, _all_answers(TechRisk=TimeoutError("a"), TechDeductibles=TimeoutError("b"),
                                       TechClauses=ConnectionError("c"),
                                       TechSublimits=TimeoutError("d")))
    read = client.post(f"{API}/policies/{policy_id}/technical-read", headers=headers_a)
    assert read.status_code == 200
    body = read.json()
    assert body["status"] == "failed" and body["error_code"] == "ai_timeout"
    assert body["message"].startswith("La lectura tardó")
    row = db.get(Extraction, body["extraction_id"])
    assert row.status == ExtractionStatus.FAILED and row.prompt_version == "policy-technical-v1"
    # A failed read is retried by a plain POST (no force needed).
    _install(monkeypatch, _all_answers())
    retry = client.post(f"{API}/policies/{policy_id}/technical-read", headers=headers_a).json()
    assert retry["status"] == "ready" and retry["extraction_id"] != body["extraction_id"]


def test_discard_writes_nothing(client, world, db, tmp_path, ai_key, monkeypatch, headers_a):
    policy_id, _ = _uploaded_policy(client, world, db, tmp_path, headers_a, monkeypatch)
    _install(monkeypatch, _all_answers())
    read = client.post(f"{API}/policies/{policy_id}/technical-read", headers=headers_a).json()
    before = dict(db.get(Policy, policy_id).payload)
    discard = client.post(
        f"{API}/policies/{policy_id}/technical-read/{read['extraction_id']}/discard",
        headers=headers_a,
    )
    assert discard.status_code == 200 and discard.json()["status"] == "none"
    db.expire_all()
    policy = db.get(Policy, policy_id)
    assert policy.payload == before and policy.deductibles is None
    assert client.get(f"{API}/policies/{policy_id}/technical-read",
                      headers=headers_a).json()["status"] == "none"
    # A discarded read cannot be confirmed.
    refused = client.post(
        f"{API}/policies/{policy_id}/technical-read/{read['extraction_id']}/confirm",
        json={}, headers=headers_a,
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == {"code": "technical_read_dismissed",
                                        "message": "Esta lectura fue descartada."}


def test_technical_read_tenant_scope_and_no_pdf(
    client, world, db, tmp_path, ai_key, monkeypatch, headers_a, headers_b
):
    policy_id, _ = _uploaded_policy(client, world, db, tmp_path, headers_a, monkeypatch)
    _install(monkeypatch, _all_answers())
    read = client.post(f"{API}/policies/{policy_id}/technical-read", headers=headers_a).json()

    base = f"{API}/policies/{policy_id}/technical-read"
    foreign = client.get(base, headers=headers_b)
    assert foreign.status_code == 404
    assert foreign.json()["detail"]["code"] == "not_found"  # structured, Spanish
    assert foreign.json()["detail"]["message"]
    assert client.post(base, headers=headers_b).status_code == 404
    assert client.post(f"{base}/{read['extraction_id']}/confirm", json={},
                       headers=headers_b).status_code == 404
    assert client.post(f"{base}/{read['extraction_id']}/discard",
                       headers=headers_b).status_code == 404

    # A policy with no PDF: 409 no_source_document; GET is just "none".
    bare = make_policy(db, world.a, world.insurer, policy_number="NO-PDF-1")
    db.commit()
    conflict = client.post(f"{API}/policies/{bare.id}/technical-read", headers=headers_a)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "no_source_document"
    assert "PDF" in conflict.json()["detail"]["message"]
    assert client.get(f"{API}/policies/{bare.id}/technical-read",
                      headers=headers_a).json()["status"] == "none"
    # An extraction of ANOTHER policy's document is a 404 through this policy.
    assert client.post(f"{API}/policies/{bare.id}/technical-read/{read['extraction_id']}/confirm",
                       json={}, headers=headers_a).status_code == 404


def test_technical_read_without_a_key_is_a_clean_503(
    client, world, db, tmp_path, ai_key, monkeypatch, headers_a
):
    policy_id, _ = _uploaded_policy(client, world, db, tmp_path, headers_a, monkeypatch)
    monkeypatch.setattr(settings, "AI_API_KEY", "")
    monkeypatch.setattr(ai_service, "_chat_model", _raise_not_configured)
    response = client.post(f"{API}/policies/{policy_id}/technical-read", headers=headers_a)
    assert response.status_code == 503, response.text
    assert response.headers["X-Radal-AI-Error"] == "ai_not_configured"
    assert response.json()["detail"]["code"] == "ai_unavailable"
    assert response.json()["detail"]["message"]


def _raise_not_configured(**_kwargs):
    raise ai_service.AINotConfigured(ai_service.AI_UNAVAILABLE_ES)


# =============================================================================
# 5. Correctness fixes from the audit
# =============================================================================


def test_core_validation_no_longer_passes_on_all_null_parties():
    payload = {
        "broker": {"legal_name": None, "rut": None},
        "insured": {"legal_name": None},
        "period_start_at": "2026-01-01T12:00:00",
        "period_end_at": "2027-01-01T12:00:00",
        "premium": {"taxable_premium_uf": 100, "exempt_premium_uf": 0},
    }
    verdict = validate_policy_core(payload)
    assert "corredor" in verdict["missing"] and "asegurado" in verdict["missing"]
    payload["broker"]["legal_name"] = "Corredora Alfa SpA"
    payload["insured"]["legal_name"] = "Asegurado Alfa S.A."
    assert validate_policy_core(payload)["is_core_valid"] is True


def test_core_validation_prima_reads_the_premium_block_not_an_installment():
    payload = {
        "broker": {"legal_name": "Corredora"},
        "insured": {"legal_name": "Asegurado"},
        "period_start_at": "2026-01-01",
        "period_end_at": "2027-01-01",
        "premium": {},
        "installments": [{"number": 1, "net_premium_uf": 10, "gross_amount_uf": 11.9}],
    }
    verdict = validate_policy_core(payload)
    assert "prima" in verdict["missing"] and verdict["detail"]["prima_present"] is False
    payload["premium"] = {"net_premium_uf": 10}
    assert validate_policy_core(payload)["detail"]["prima_present"] is True
    # The flat (proposal-style) top level still counts.
    flat = {k: v for k, v in payload.items() if k != "premium"}
    flat["total_premium_uf"] = 11.9
    assert validate_policy_core(flat)["detail"]["prima_present"] is True


def test_mirror_aligns_the_registry_premium_names_and_commission_block():
    proposal = {
        "premium": {"taxable_premium_uf": 100, "exempt_premium_uf": 20, "net_premium_uf": 120,
                    "vat_uf": 19, "total_premium_uf": 139},
        "commission": {"pct": 15},
        "confidence": 95,
    }
    policy_payload = {
        "premium": {"taxable_premium_uf": 110, "exempt_premium_uf": 20, "net_premium_uf": 130,
                    "vat_uf": 20.9, "total_premium_uf": 150.9},
        "commission_pct": 12,
        "confidence": 60,
    }
    rows = {row.path: row for row in mirror.diff_payloads(proposal, policy_payload)}
    assert rows["premium.net_taxable_uf"].expected == 100
    assert rows["premium.net_total_uf"].found == 130
    assert rows["premium.gross_uf"].found == 150.9
    assert rows["commission_pct"].expected == 15
    assert "premium.net_exempt_uf" not in rows  # equal
    assert "confidence" not in rows  # a quality signal is never a contract delta


def test_mirror_reads_core_plus_technical_and_skips_unread_collections(
    client, world, db, tmp_path, ai_key, monkeypatch, headers_a
):
    policy_id, document = _uploaded_policy(client, world, db, tmp_path, headers_a, monkeypatch)
    policy = db.get(Policy, policy_id)
    db.add(Extraction(
        broker_id=world.a.broker_id, document_id=world.a.document.id,
        case_file_id=policy.case_file_id, category=DocumentCategory.ISSUANCE_PROPOSAL,
        model="fake", prompt_version="issuance_proposal-v1", status=ExtractionStatus.SUCCEEDED,
        parsed={"deductibles_to_issue": [{"peril": "incendio", "text": "5% de la pérdida"}],
                "premium": {"taxable_premium_uf": 100, "exempt_premium_uf": 39}},
    ))
    db.commit()

    sources = mirror.load_sources(db, policy=policy)
    assert sources.policy_source and sources.policy_collections_read is False
    rows = mirror.diff_payloads(sources.proposal_payload, sources.policy_payload,
                                include_collections=sources.policy_collections_read)
    assert not [r for r in rows if r.path.startswith("deductibles")]

    _install(monkeypatch, _all_answers())
    client.post(f"{API}/policies/{policy_id}/technical-read", headers=headers_a)
    sources = mirror.load_sources(db, policy=policy)
    assert sources.policy_collections_read is True
    assert sources.policy_extraction_id != sources.policy_technical_extraction_id
    rows = mirror.diff_payloads(sources.proposal_payload, sources.policy_payload)
    deductible_rows = {r.path for r in rows if r.path.startswith("deductibles")}
    assert "deductibles[incendio]" in deductible_rows


# =============================================================================
# 6. Agent: search_document_text
# =============================================================================


def _run_tool(db, tenant, name, args):
    spec = agent_tools.TOOL_REGISTRY[name]
    return spec.execute(db, broker_id=tenant.broker_id, user=tenant.admin,
                        args=spec.args_schema.model_validate(args))


def test_search_document_text_quotes_ranked_paragraphs(db, world, tmp_path):
    text = (
        "CONDICIONES PARTICULARES\n\n"
        "Artículo 1. Cobertura de incendio y rayo.\n\n"
        "Artículo 7. EXCLUSIÓN DE TERRORISMO: no se cubren los daños causados por actos de "
        "terrorismo, salvo la contra-excepción de la cláusula 72 horas.\n\n"
        "Artículo 8. Guerra civil o internacional.\n\n"
        "Artículo 9. Deducible sismo: 2% del monto asegurado, mínimo UF 50."
    )
    document = _doc(db, world.a, tmp_path, name="wording.txt", text=text)
    result = _run_tool(db, world.a, "search_document_text",
                       {"document_id": document.id, "query": "exclusion terrorismo"})
    assert result["terms"] == ["exclusion", "terrorismo"]
    top = result["hits"][0]
    assert "EXCLUSIÓN DE TERRORISMO" in top["text"]  # verbatim, accents intact
    assert top["before"].startswith("Artículo 1.") and top["after"].startswith("Artículo 8.")
    assert top["page"] is None  # not a PDF
    assert "s3_key" not in json.dumps(result)

    none = _run_tool(db, world.a, "search_document_text",
                     {"document_id": document.id, "query": "inundación"})
    assert none["hits"] == [] and none["total_matches"] == 0


def test_search_document_text_reports_pdf_pages(db, world, tmp_path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    path = tmp_path / "poliza.pdf"
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.drawString(72, 800, "Poliza de incendio. Coberturas generales del contrato.")
    pdf.showPage()
    pdf.drawString(72, 800, "Clausula de terremoto: deducible del 2% del monto asegurado.")
    pdf.showPage()
    pdf.save()
    document = Document(
        broker_id=world.a.broker_id, entity_type=EntityType.CLIENT, entity_id=world.a.client.id,
        s3_key=str(path), bucket="radal-test-bucket", original_name="poliza.pdf",
        mime_type="application/pdf", category=DocumentCategory.POLICY,
    )
    db.add(document)
    db.commit()
    result = _run_tool(db, world.a, "search_document_text",
                       {"document_id": document.id, "query": "TERREMOTO deducible", "max_hits": 1})
    assert len(result["hits"]) == 1 and result["hits"][0]["page"] == 2


def test_search_document_text_is_broker_scoped_and_registered(db, world, tmp_path):
    foreign = _doc(db, world.b, tmp_path, name="ajena.txt")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        _run_tool(db, world.a, "search_document_text", {"document_id": foreign.id, "query": "guerra"})
    assert caught.value.status_code == 404

    spec = agent_tools.TOOL_REGISTRY["search_document_text"]
    assert spec.kind == "read" and (spec.module, spec.action) == ("Documents", "View")
    from app.services.agent_skills import SKILLS

    assert "search_document_text" in SKILLS["policy_intake"].tools
    assert "search_document_text" in SKILLS["file_check"].tools
