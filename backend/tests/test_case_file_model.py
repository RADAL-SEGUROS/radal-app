"""Schema-level guarantees for the case-file tables.

These tests are deliberately engine-agnostic and do not touch the API: they
protect the two invariants the whole build rests on —

  1. every table's DDL compiles on BOTH the sqlite (local) and mysql (RDS)
     dialects, with no unbounded VARCHAR reaching MySQL; and
  2. the new shared enums persist their ENGLISH lowercase *values*, round-trip
     through a real session, and reject a non-member string early.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db.base import Base
from app.models import (
    AccountClient,
    AccountClientRole,
    AccountGroup,
    AccountGroupStatus,
    Broker,
    CaseFile,
    CaseFileKind,
    CaseFileStageEvent,
    CaseFileStatus,
    CaseOrigin,
    CasePack,
    CaseSection,
    CaseStage,
    ClaimItemKind,
    ClaimRuling,
    Client,
    CollectionInstallment,
    CollectionPlan,
    CollectionPlanStatus,
    DocumentDirection,
    EndorsementKind,
    EndorsementStatus,
    EntityType,
    InstallmentStatus,
    Insured,
    Insurer,
    LeadStatus,
    PackKind,
    PackStatus,
    PaymentMode,
    Policy,
    ProposalOutcome,
    SalesLead,
    WarrantySource,
    WarrantyStatus,
)
from app.models.ai import ExtractionKind
from app.models.document import DocumentCategory

# Tables the case-file build adds. Named explicitly so a rename is a test
# failure rather than a silent drift.
NEW_TABLES = [
    "case_file",
    "case_file_stage_event",
    "case_pack",
    "sales_lead",
    "endorsement",
    "collection_plan",
    "collection_installment",
    "warranty",
    "claim_item",
    # v3 groups & accounts
    "account_group",
    "account_client",
]

# The v3 tables must be creatable by the migrator's plain CREATE TABLE path,
# which never emits a deferred ``AddConstraint`` — so no ``use_alter`` FK.
GROUPS_TABLES = ["account_group", "account_client"]

NEW_ENUMS = [
    CaseSection,
    CaseFileKind,
    CaseStage,
    CaseFileStatus,
    CaseOrigin,
    AccountGroupStatus,
    AccountClientRole,
    LeadStatus,
    PackKind,
    PackStatus,
    EndorsementKind,
    EndorsementStatus,
    PaymentMode,
    CollectionPlanStatus,
    InstallmentStatus,
    WarrantySource,
    WarrantyStatus,
    ClaimItemKind,
    ClaimRuling,
    ProposalOutcome,
    DocumentDirection,
]


# --- 1. Dialect compilation ---------------------------------------------------


@pytest.mark.parametrize("dialect_name", ["sqlite", "mysql"])
def test_every_table_compiles_on_both_dialects(dialect_name: str) -> None:
    dialect = sqlite.dialect() if dialect_name == "sqlite" else mysql.dialect()
    assert Base.metadata.tables, "metadata is empty — models were not imported"
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert ddl.strip().upper().startswith("CREATE TABLE")
        for index in table.indexes:
            str(CreateIndex(index).compile(dialect=dialect))


def test_no_unbounded_varchar_reaches_mysql() -> None:
    """The @compiles(String, "mysql") hook must cover every length-less String."""
    dialect = mysql.dialect()
    offenders = []
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        if re.search(r"VARCHAR(?!\()", ddl):
            offenders.append(table.name)
    assert offenders == []


def test_new_tables_are_registered() -> None:
    missing = [name for name in NEW_TABLES if name not in Base.metadata.tables]
    assert missing == []


def test_new_workspace_tables_are_broker_scoped() -> None:
    """Multi-tenancy rule 2: broker_id NOT NULL, indexed, ON DELETE CASCADE."""
    for name in NEW_TABLES:
        table = Base.metadata.tables[name]
        assert "broker_id" in table.c, f"{name} has no broker_id"
        column = table.c["broker_id"]
        assert not column.nullable, f"{name}.broker_id must be NOT NULL"
        fk = next(iter(column.foreign_keys))
        assert fk.column.table.name == "broker"
        assert fk.ondelete == "CASCADE", f"{name}.broker_id must CASCADE"
        indexed = column.index or any(
            list(index.columns)[0].name == "broker_id" for index in table.indexes
        )
        assert indexed, f"{name}.broker_id must be indexed"


def test_case_file_tables_are_not_mysql_reserved_words() -> None:
    """``lead`` / ``group`` are MySQL 8 reserved words — never bare table names."""
    reserved = mysql.base.RESERVED_WORDS_MYSQL
    for name in Base.metadata.tables:
        assert name not in reserved, f"table name {name!r} is a MySQL reserved word"
    assert "sales_lead" in Base.metadata.tables
    assert "lead" not in Base.metadata.tables
    assert "account_group" in Base.metadata.tables
    assert "group" not in Base.metadata.tables


def test_groups_tables_declare_no_use_alter_fk() -> None:
    """``CreateTable`` omits ``use_alter`` FKs and the migrator never adds them back."""
    for name in GROUPS_TABLES:
        table = Base.metadata.tables[name]
        for constraint in table.foreign_key_constraints:
            assert not constraint.use_alter, f"{name}.{constraint.name} uses use_alter"


def test_groups_tables_carry_the_spec_indexes() -> None:
    """Named ``Index`` objects, so the migrator's ``_index_sqls`` emits them."""
    group = Base.metadata.tables["account_group"]
    by_name = {index.name: index for index in group.indexes}
    assert by_name["ix_account_group_broker_slug"].unique is True
    assert [c.name for c in by_name["ix_account_group_broker_slug"].columns] == ["broker_id", "slug"]
    assert [c.name for c in by_name["ix_account_group_broker_status"].columns] == ["broker_id", "status"]
    assert "primary_client_id" not in group.c  # derived, never a column (spec §2.1)

    member = Base.metadata.tables["account_client"]
    by_name = {index.name: index for index in member.indexes}
    assert by_name["ix_account_client_case_client"].unique is True
    assert [c.name for c in by_name["ix_account_client_case_client"].columns] == ["case_file_id", "client_id"]
    assert next(iter(member.c["case_file_id"].foreign_keys)).ondelete == "CASCADE"
    assert next(iter(member.c["client_id"].foreign_keys)).ondelete == "RESTRICT"

    case = Base.metadata.tables["case_file"]
    by_name = {index.name: index for index in case.indexes}
    assert [c.name for c in by_name["ix_case_file_group_period"].columns] == [
        "broker_id",
        "account_group_id",
        "period_start",
    ]


def test_groups_columns_are_addable_without_a_rewrite() -> None:
    """Every added column is nullable or has a scalar default (spec §2.3).

    ``create_all`` never ALTERs, so the migrator ``ADD COLUMN``s these; a NOT
    NULL column without a scalar default would fail on existing rows.
    """
    added = {
        "client": ["account_group_id"],
        "case_file": [
            "account_group_id",
            "period_start",
            "period_end",
            "period_label",
            "origin",
            "origin_case_file_id",
        ],
        "endorsement": ["batch_key"],
        "sales_lead": ["account_group_id", "period_start", "period_end"],
    }
    for table_name, columns in added.items():
        table = Base.metadata.tables[table_name]
        for column_name in columns:
            column = table.c[column_name]
            if column.nullable:
                continue
            assert column.default is not None and column.default.is_scalar, (
                f"{table_name}.{column_name} is NOT NULL without a scalar default"
            )
    origin = Base.metadata.tables["case_file"].c["origin"]
    assert not origin.nullable
    assert origin.default.arg is CaseOrigin.NEW
    # Every group FK detaches (rule 7: archiving a group never cascades).
    for table_name in ("client", "case_file", "sales_lead"):
        fk = next(iter(Base.metadata.tables[table_name].c["account_group_id"].foreign_keys))
        assert fk.column.table.name == "account_group"
        assert fk.ondelete == "SET NULL", f"{table_name}.account_group_id must SET NULL"
    origin_fk = next(iter(Base.metadata.tables["case_file"].c["origin_case_file_id"].foreign_keys))
    assert origin_fk.column.table.name == "case_file"
    assert origin_fk.ondelete == "SET NULL"


def test_create_all_succeeds_on_sqlite() -> None:
    """The FK cycles (case_file <-> policy/placement/document/...) must be solvable."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    engine.dispose()


# --- 2. Enum vocabulary -------------------------------------------------------


@pytest.mark.parametrize("enum_cls", NEW_ENUMS, ids=lambda e: e.__name__)
def test_enum_values_are_english_snake_case(enum_cls: type) -> None:
    for member in enum_cls:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", member.value), (
            f"{enum_cls.__name__}.{member.name} = {member.value!r} is not "
            "English lowercase snake_case"
        )


def test_endorsement_kind_has_the_fifteen_corpus_motives_plus_period_extension() -> None:
    assert len(list(EndorsementKind)) == 16
    assert EndorsementKind.DEDUCTIBLE_REDUCTION.value == "deductible_reduction"
    assert EndorsementKind.PERIOD_EXTENSION.value == "period_extension"
    # 16 chars < ``aggregate_reinstatement`` (23): no MODIFY COLUMN needed.
    longest = max(len(member.value) for member in EndorsementKind)
    assert longest == len("aggregate_reinstatement")


def test_entity_type_gained_the_case_members_without_widening() -> None:
    for value in (
        "case_file",
        "sales_lead",
        "endorsement",
        "collection_plan",
        "warranty",
        "account_group",
    ):
        assert value in {member.value for member in EntityType}
    # The longest value stays ``inspection_request`` (18) so the computed
    # VARCHAR length is unchanged — no migration for these members.
    longest = max(len(member.value) for member in EntityType)
    assert longest == len("inspection_request")


def test_case_origin_has_exactly_the_three_origins() -> None:
    assert [member.value for member in CaseOrigin] == ["new", "renewal", "period_change"]
    assert [member.value for member in AccountGroupStatus] == ["active", "archived"]
    assert [member.value for member in AccountClientRole] == ["policyholder", "insured"]


def test_document_category_keeps_the_deprecated_proposal_alias() -> None:
    """The current AI upload flow writes ``proposal``; it must keep working."""
    assert DocumentCategory.PROPOSAL.value == "proposal"
    assert DocumentCategory.INSURER_QUOTATION.value == "insurer_quotation"


def test_document_category_covers_the_case_registry() -> None:
    values = {member.value for member in DocumentCategory}
    for expected in (
        "prospect_request",
        "business_questionnaire",
        "insured_values_schedule",
        "loss_history",
        "submission_letter",
        "risk_engineering_plan",
        "resubmission_letter",
        "insurer_quotation",
        "declination",
        "conditional_pronouncement",
        "quote_comparison",
        "issuance_proposal",
        "technical_recommendation",
        "policy",
        "payment_plan",
        "collection_status",
        "endorsement_proposal",
        "endorsement",
        "compliance_notice",
        "claim_notice",
        "claim_preliminary_report",
        "claim_final_report",
        "broker_closing_note",
        "submission_pack",
        "comparison_pack",
        "proposal_pack",
        "archive_pack",
    ):
        assert expected in values, f"DocumentCategory is missing {expected!r}"
    # ``archive_pack`` (12) is far below the longest category (25): no widening.
    longest = max(len(member.value) for member in DocumentCategory)
    assert longest == len("conditional_pronouncement")


def test_extraction_kind_gained_case_document_and_summary() -> None:
    values = {member.value for member in ExtractionKind}
    assert {"case_document", "summary"} <= values


def test_case_stage_covers_every_chain() -> None:
    values = {member.value for member in CaseStage}
    # The seven account hitos, in order, plus the post-sale chains.
    assert {
        "lead",
        "intake",
        "pre_underwriting",
        "technical_basis",
        "market_submission",
        "quotes_received",
        "comparison",
        "insured_decision",
        "proposal_issued",
        "ratified",
        "policy_issued",
        "mirror_validation",
        "active",
        "renewal_review",
        "closed",
    } <= values
    assert {
        "endorsement_requested",
        "endorsement_proposed",
        "endorsement_issued",
        "endorsement_applied",
    } <= values
    assert {
        "collection_scheduled",
        "collection_in_progress",
        "collection_overdue",
        "collection_settled",
        "collection_suspended",
    } <= values
    assert {
        "claim_reported",
        "claim_adjusting",
        "claim_preliminary",
        "claim_final",
        "claim_settled",
    } <= values


def test_enum_column_rejects_a_non_member_string() -> None:
    """``validate_strings=True`` must catch a bad token before it reaches SQL."""
    column_type = CaseFile.__table__.c["stage"].type
    with pytest.raises((LookupError, ValueError)):
        column_type._object_value_for_elem("not_a_stage")


# --- 3. Round-trip through a real session ------------------------------------


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def _seed_client(db: Session) -> Client:
    broker = Broker(legal_name="Corredora Demo", rut="76.000.000-0")
    db.add(broker)
    db.flush()
    insured = Insured(legal_name="Asegurado Demo", rut="76.827.029-5")
    db.add(insured)
    db.flush()
    client = Client(broker_id=broker.id, insured_id=insured.id)
    db.add(client)
    db.flush()
    return client


def test_case_file_round_trips_with_its_timeline_and_pack(session: Session) -> None:
    client = _seed_client(session)
    case = CaseFile(
        broker_id=client.broker_id,
        client_id=client.id,
        kind=CaseFileKind.ACCOUNT,
        stage=CaseStage.INTAKE,
        status=CaseFileStatus.OPEN,
        reference="EXP-2026-0007",
        title="COCCOLINO · MULTIRRIESGO",
    )
    session.add(case)
    session.flush()

    session.add(
        CaseFileStageEvent(
            broker_id=case.broker_id,
            case_file_id=case.id,
            from_stage=CaseStage.LEAD,
            to_stage=CaseStage.INTAKE,
            occurred_at=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
        )
    )
    session.add(
        CasePack(
            broker_id=case.broker_id,
            case_file_id=case.id,
            kind=PackKind.SUBMISSION,
            section=CaseSection.SUBMISSION,
            status=PackStatus.DRAFT,
            recipients=[{"insurer_id": 1, "resolution_level": "broker_line"}],
        )
    )
    session.commit()
    session.expire_all()

    stored = session.execute(select(CaseFile)).scalar_one()
    assert stored.kind is CaseFileKind.ACCOUNT
    assert stored.stage is CaseStage.INTAKE
    # Defaults from the spec: E1, v1.
    assert stored.sequence_no == 1
    assert stored.version == 1
    assert len(stored.stage_events) == 1
    assert stored.stage_events[0].to_stage is CaseStage.INTAKE
    assert len(stored.packs) == 1
    assert stored.packs[0].recipients[0]["resolution_level"] == "broker_line"
    assert stored.packs[0].is_summary_confirmed is False


def test_enum_values_are_persisted_not_member_names(session: Session) -> None:
    """``values_callable`` must store ``on_hold``, never ``ON_HOLD``."""
    client = _seed_client(session)
    case = CaseFile(
        broker_id=client.broker_id,
        client_id=client.id,
        kind=CaseFileKind.ENDORSEMENT,
        stage=CaseStage.ENDORSEMENT_REQUESTED,
        status=CaseFileStatus.ON_HOLD,
        title="Endoso E1",
    )
    session.add(case)
    session.commit()

    raw = session.connection().exec_driver_sql(
        "SELECT kind, stage, status FROM case_file"
    ).one()
    assert raw == ("endorsement", "endorsement_requested", "on_hold")


def test_sales_lead_tolerates_a_missing_rut(session: Session) -> None:
    """A broker is NEVER blocked tracking a prospect whose RUT is unknown."""
    client = _seed_client(session)
    lead = SalesLead(
        broker_id=client.broker_id,
        name="JO PASTELERIA",
        status=LeadStatus.NEW,
        follow_up_on=date(2026, 4, 15),
    )
    session.add(lead)
    session.commit()
    session.expire_all()

    stored = session.execute(select(SalesLead)).scalar_one()
    assert stored.rut is None
    assert stored.status is LeadStatus.NEW
    assert stored.converted_case_file_id is None
    assert stored.account_group_id is None
    assert stored.period_start is None


def test_account_group_round_trips_with_clients_and_folders(session: Session) -> None:
    """A group over N RUTs, an account folder with members, and the ORIGIN axis."""
    client = _seed_client(session)
    other_insured = Insured(legal_name="Viña Indómita SpA", rut="99568600-7")
    session.add(other_insured)
    session.flush()
    other_client = Client(broker_id=client.broker_id, insured_id=other_insured.id)
    session.add(other_client)
    session.flush()

    group = AccountGroup(broker_id=client.broker_id, name="GRUPO VIÑA INDÓMITA", slug="grupo-vina-indomita")
    session.add(group)
    session.flush()
    client.account_group_id = group.id
    other_client.account_group_id = group.id

    prior = CaseFile(
        broker_id=client.broker_id,
        client_id=other_client.id,
        account_group_id=group.id,
        kind=CaseFileKind.ACCOUNT,
        stage=CaseStage.ACTIVE,
        reference="EXP-2025-0001",
        title="Viña Indómita · Incendio",
        period_start=date(2025, 10, 15),
        period_end=date(2026, 10, 15),
        period_label="2025-2026",
    )
    session.add(prior)
    session.flush()
    renewal = CaseFile(
        broker_id=client.broker_id,
        client_id=client.id,
        account_group_id=group.id,
        kind=CaseFileKind.RENEWAL,
        stage=CaseStage.RENEWAL_REVIEW,
        reference="EXP-2025-0001-RN1",
        title="Viña Indómita · Incendio · renovación",
        period_start=date(2026, 10, 15),
        period_end=date(2027, 10, 15),
        period_label="2026-2027",
        origin=CaseOrigin.RENEWAL,
        origin_case_file_id=prior.id,
    )
    session.add(renewal)
    session.flush()
    session.add_all(
        [
            AccountClient(
                broker_id=client.broker_id,
                case_file_id=renewal.id,
                client_id=client.id,
                role=AccountClientRole.POLICYHOLDER,
                is_primary=True,
            ),
            AccountClient(broker_id=client.broker_id, case_file_id=renewal.id, client_id=other_client.id),
        ]
    )
    session.commit()
    session.expire_all()

    stored = session.execute(select(AccountGroup)).scalar_one()
    assert stored.status is AccountGroupStatus.ACTIVE
    assert {c.id for c in stored.clients} == {client.id, other_client.id}
    assert {c.reference for c in stored.case_files} == {"EXP-2025-0001", "EXP-2025-0001-RN1"}

    folder = session.get(CaseFile, renewal.id)
    assert folder.origin is CaseOrigin.RENEWAL
    assert folder.origin_case_file.reference == "EXP-2025-0001"
    # The origin axis is NOT the post-sale axis.
    assert folder.parent_case_file_id is None and folder.policy_id is None
    assert folder.account_group.slug == "grupo-vina-indomita"
    members = sorted(folder.account_clients, key=lambda m: m.client_id)
    assert [(m.client_id, m.is_primary, m.role) for m in members] == [
        (client.id, True, AccountClientRole.POLICYHOLDER),
        (other_client.id, False, AccountClientRole.INSURED),
    ]
    # The prior folder took the scalar default: origin=new, nothing to point at.
    assert session.get(CaseFile, prior.id).origin is CaseOrigin.NEW
    raw = session.connection().exec_driver_sql(
        "SELECT origin, role FROM case_file JOIN account_client ON account_client.case_file_id = case_file.id "
        "WHERE account_client.is_primary = 1"
    ).one()
    assert raw == ("renewal", "policyholder")


def test_account_client_is_unique_per_case_and_client(session: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    client = _seed_client(session)
    case = CaseFile(
        broker_id=client.broker_id,
        client_id=client.id,
        kind=CaseFileKind.ACCOUNT,
        stage=CaseStage.INTAKE,
        title="Cuenta",
    )
    session.add(case)
    session.flush()
    session.add(AccountClient(broker_id=client.broker_id, case_file_id=case.id, client_id=client.id))
    session.flush()
    session.add(AccountClient(broker_id=client.broker_id, case_file_id=case.id, client_id=client.id))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_deleting_a_group_detaches_and_never_cascades(session: Session) -> None:
    """Rule 7: SET NULL on client / case_file / sales_lead, rows survive."""
    client = _seed_client(session)
    group = AccountGroup(broker_id=client.broker_id, name="JO PASTELERÍA", slug="jo-pasteleria")
    session.add(group)
    session.flush()
    client.account_group_id = group.id
    case = CaseFile(
        broker_id=client.broker_id,
        client_id=client.id,
        account_group_id=group.id,
        kind=CaseFileKind.ACCOUNT,
        stage=CaseStage.INTAKE,
        title="Cuenta",
    )
    lead = SalesLead(broker_id=client.broker_id, name="JO", account_group_id=group.id)
    session.add_all([case, lead])
    session.commit()

    session.connection().exec_driver_sql("PRAGMA foreign_keys = ON")
    session.connection().exec_driver_sql("DELETE FROM account_group")
    session.commit()
    session.expire_all()

    assert session.get(Client, client.id).account_group_id is None
    assert session.get(CaseFile, case.id).account_group_id is None
    assert session.get(SalesLead, lead.id).account_group_id is None


def test_collection_installments_sum_to_the_plan_total(session: Session) -> None:
    """The corpus invariant: Σ gross_amount_uf == the plan's total premium."""
    client = _seed_client(session)
    insurer = Insurer(
        rut="99.999.999-9", cmf_code="C-999", legal_name="Compañía Demo S.A."
    )
    session.add(insurer)
    session.flush()
    policy = Policy(
        broker_id=client.broker_id,
        client_id=client.id,
        insurer_id=insurer.id,
        policy_number="0020119904",
    )
    session.add(policy)
    session.flush()

    plan = CollectionPlan(
        broker_id=client.broker_id,
        policy_id=policy.id,
        payment_mode=PaymentMode.COUPON_BOOK,
        installment_count=2,
        total_premium_uf=Decimal("100.0000"),
        status=CollectionPlanStatus.CURRENT,
    )
    session.add(plan)
    session.flush()
    for number, amount in ((1, "60.0000"), (2, "40.0000")):
        session.add(
            CollectionInstallment(
                broker_id=client.broker_id,
                collection_plan_id=plan.id,
                number=number,
                gross_amount_uf=Decimal(amount),
                status=InstallmentStatus.PENDING,
            )
        )
    session.commit()
    session.expire_all()

    stored = session.execute(select(CollectionPlan)).scalar_one()
    assert sum(i.gross_amount_uf for i in stored.installments) == stored.total_premium_uf
    assert [i.number for i in stored.installments] == [1, 2]


def test_endorsement_status_and_kind_are_english(session: Session) -> None:
    assert EndorsementStatus.PROPOSED.value == "proposed"
    assert WarrantyStatus.MET_AFTER_CLAIM.value == "met_after_claim"
    assert WarrantySource.ENGINEERING_MEASURE.value == "engineering_measure"
    assert ClaimItemKind.BUSINESS_INTERRUPTION.value == "business_interruption"
    assert ClaimRuling.PARTIALLY_COVERED.value == "partially_covered"
    assert ProposalOutcome.NO_RESPONSE.value == "no_response"
    assert DocumentDirection.ADJUSTER_TO_PARTIES.value == "adjuster_to_parties"
