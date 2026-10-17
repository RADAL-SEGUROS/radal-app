"""v12 cuota state machine (app.services.collection.status, 03 §1).

Two halves: the PURE ``compute_installment_state`` over plain value objects
(every state, every flag, tolerance, the rule order), then
``refresh_installment_cache`` against the real schema (the legacy status cache,
reversals, per-broker settings).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.enums import (
    CollectionPlanStatus,
    EvidenceLevel,
    InstallmentStatus,
    PaymentStatus,
)
from app.services.collection.status import (
    InstallmentState,
    compute_installment_state,
    legacy_status_for,
    refresh_installment_cache,
)
from tests.fakes_v12 import make_collection_settings, make_payment, make_policy_with_plan

CADENCE = [-15, -5, 0, 3, 10]
DUE = date(2026, 3, 10)


def inst(**fields):
    policy = SimpleNamespace(terminated_on=fields.pop("terminated_on", None))
    plan = SimpleNamespace(
        status=fields.pop("plan_status", CollectionPlanStatus.CURRENT),
        terminated_at=fields.pop("terminated_at", None),
        policy=policy,
    )
    base = {
        "gross_amount_uf": Decimal("10.0000"),
        "due_date": DUE,
        "status": InstallmentStatus.PENDING,
        "voided_by_endorsement_id": None,
        "direct_debit_rejected_at": None,
        "promise_until": None,
        "insurer_notice_sent_on": None,
        "paid_on": None,
        "days_late": None,
        "collection_plan": plan,
    }
    base.update(fields)
    return SimpleNamespace(**base)


_ids = iter(range(1, 10_000))


def pay(amount="10.0000", paid_on=DUE, **fields):
    base = {
        "id": next(_ids),
        "amount_uf": Decimal(str(amount)),
        "paid_on": paid_on,
        "status": PaymentStatus.ACTIVE,
        "evidence_level": EvidenceLevel.DECLARED,
        "confirmed_by_insurer": False,
        "receipt_document_id": None,
    }
    base.update(fields)
    return SimpleNamespace(**base)


def state(i, payments=(), today=DUE, cadence=CADENCE, **kw) -> InstallmentState:
    return compute_installment_state(i, list(payments), today=today, cadence_days=cadence, **kw)


# --- the seven states, in order ----------------------------------------------

def test_upcoming_before_due():
    s = state(inst(), today=date(2026, 3, 1))
    assert s.state == "upcoming"
    assert s.days_late == 0
    assert s.paid_uf == 0 and s.balance_uf == Decimal("10.0000")
    assert s.flags == frozenset()
    assert s.evidence is None


def test_due_today():
    assert state(inst()).state == "due_today"


def test_overdue_after_due_counts_days():
    s = state(inst(), today=date(2026, 3, 13))
    assert s.state == "overdue"
    assert s.days_late == 3


def test_termination_risk_after_last_cadence_step():
    # due + max(cadence) = Mar 20 -> still overdue ON the day, risk the day after.
    assert state(inst(), today=date(2026, 3, 20)).state == "overdue"
    s = state(inst(), today=date(2026, 3, 21))
    assert s.state == "termination_risk"
    assert s.days_late == 11


def test_termination_risk_follows_the_broker_cadence():
    assert state(inst(), today=date(2026, 3, 13), cadence=[-5, 0, 2]).state == "termination_risk"
    # An empty cadence falls back to the default one.
    assert state(inst(), today=date(2026, 3, 13), cadence=[]).state == "overdue"


def test_art528_notice_on_an_overdue_cuota_is_termination_risk():
    i = inst(insurer_notice_sent_on=date(2026, 3, 11))
    assert state(i, today=date(2026, 3, 12)).state == "termination_risk"
    # ...but not before the cuota is even due.
    assert state(i, today=date(2026, 3, 1)).state == "upcoming"


def test_promise_wins_over_overdue_and_risk():
    i = inst(promise_until=date(2026, 4, 30))
    s = state(i, today=date(2026, 4, 15))
    assert s.state == "promised"
    assert s.days_late == 36
    # Promise day itself still counts as promised.
    assert state(i, today=date(2026, 4, 30)).state == "promised"


def test_broken_promise_falls_back_to_the_clock():
    i = inst(promise_until=date(2026, 3, 12))
    assert state(i, today=date(2026, 3, 13)).state == "overdue"
    assert state(i, today=date(2026, 4, 30)).state == "termination_risk"


def test_paid_wins_over_promise_and_lateness():
    i = inst(promise_until=date(2026, 4, 30))
    s = state(i, [pay(paid_on=date(2026, 3, 1))], today=date(2026, 4, 15))
    assert s.state == "paid"
    assert s.balance_uf == 0
    assert s.days_late == 0
    assert s.paid_on == date(2026, 3, 1)


def test_voided_by_endorsement_wins_over_everything():
    i = inst(voided_by_endorsement_id=7, promise_until=date(2026, 4, 30))
    s = state(i, [pay()], today=date(2026, 5, 1))
    assert s.state == "voided"
    assert s.balance_uf == 0
    assert s.days_late == 0
    assert s.flags == frozenset()


def test_policy_termination_voids_only_later_cuotas():
    after = inst(terminated_on=date(2026, 3, 1))
    before = inst(terminated_on=date(2026, 3, 31))
    on_the_day = inst(terminated_on=DUE)
    assert state(after, today=date(2026, 4, 1)).state == "voided"
    assert state(before, today=date(2026, 3, 15)).state == "overdue"
    assert state(on_the_day, today=date(2026, 3, 15)).state == "overdue"


def test_terminated_plan_without_policy_date_uses_terminated_at():
    i = inst(
        plan_status=CollectionPlanStatus.TERMINATED,
        terminated_at=datetime(2026, 2, 1, 12, tzinfo=timezone.utc),
    )
    assert state(i, today=date(2026, 4, 1)).state == "voided"
    # terminated_at alone on a non-terminated plan (rehabilitated) does not void.
    j = inst(
        plan_status=CollectionPlanStatus.REHABILITATED,
        terminated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    assert state(j, today=date(2026, 3, 15)).state == "overdue"


def test_no_due_date_is_upcoming_forever():
    s = state(inst(due_date=None), today=date(2030, 1, 1))
    assert s.state == "upcoming"
    assert s.days_late == 0


# --- sums, tolerance, partial ------------------------------------------------

def test_within_tolerance_counts_as_paid():
    s = state(inst(), [pay("9.9950")])
    assert s.state == "paid"
    assert s.balance_uf == 0
    assert "partial" not in s.flags


def test_exactly_at_tolerance_boundary():
    assert state(inst(), [pay("9.9910")]).state == "paid"          # 10 - 0.009
    assert state(inst(), [pay("9.9909")]).state == "due_today"     # just below


def test_custom_tolerance():
    assert state(inst(), [pay("9.5")], tolerance_uf=Decimal("0.5")).state == "paid"
    assert state(inst(), [pay("9.5")], tolerance_uf=Decimal("0")).state != "paid"


def test_partial_payment_flag_and_balance():
    s = state(inst(), [pay("4.0000")], today=date(2026, 3, 15))
    assert s.state == "overdue"
    assert "partial" in s.flags
    assert s.paid_uf == Decimal("4.0000")
    assert s.balance_uf == Decimal("6.0000")


def test_partial_payments_add_up_and_paid_on_is_the_completing_one():
    payments = [
        pay("3", paid_on=date(2026, 3, 1)),
        pay("7", paid_on=date(2026, 3, 14)),
        pay("2", paid_on=date(2026, 3, 20)),  # overpayment after completion
    ]
    s = state(inst(), payments, today=date(2026, 4, 1))
    assert s.state == "paid"
    assert s.paid_on == date(2026, 3, 14)
    assert s.days_late == 4
    # Capped at the cuota's gross (review v12 #1): a summary never shows more
    # paid than the cuota costs — the panel, the plan page and the KPIs agree.
    assert s.paid_uf == Decimal("10")
    assert s.balance_uf == 0


def test_reversed_payments_do_not_count():
    s = state(
        inst(),
        [pay(status=PaymentStatus.REVERSED, evidence_level=EvidenceLevel.SETTLED)],
        today=date(2026, 3, 11),
    )
    assert s.state == "overdue"
    assert s.paid_uf == 0
    assert s.evidence is None
    assert "partial" not in s.flags


def test_overpayment_is_paid_with_zero_balance():
    s = state(inst(), [pay("15")])
    assert s.state == "paid" and s.balance_uf == 0


# --- flags -------------------------------------------------------------------

def test_paid_declared_without_receipt_is_unconfirmed_and_no_receipt():
    s = state(inst(), [pay()])
    assert s.flags == frozenset({"unconfirmed", "no_receipt"})
    assert s.evidence == "declared"


def test_insurer_confirmed_with_receipt_has_no_flags():
    s = state(inst(), [pay(evidence_level=EvidenceLevel.INSURER_CONFIRMED, receipt_document_id=3)])
    assert s.flags == frozenset()
    assert s.evidence == "insurer_confirmed"


def test_confirmed_by_insurer_boolean_lifts_evidence():
    s = state(inst(), [pay(confirmed_by_insurer=True, receipt_document_id=3)])
    assert s.evidence == "insurer_confirmed"
    assert "unconfirmed" not in s.flags


def test_best_evidence_is_the_strongest_active_one():
    payments = [
        pay("5", evidence_level=EvidenceLevel.REPORTED),
        pay("5", evidence_level=EvidenceLevel.PROVIDER_CONFIRMED),
        pay("5", evidence_level=EvidenceLevel.SETTLED, status=PaymentStatus.REVERSED),
    ]
    s = state(inst(), payments)
    assert s.evidence == "provider_confirmed"
    assert "unconfirmed" not in s.flags


def test_reported_evidence_is_still_unconfirmed():
    s = state(inst(), [pay(evidence_level=EvidenceLevel.REPORTED, receipt_document_id=9)])
    assert s.flags == frozenset({"unconfirmed"})


def test_receipt_on_any_active_payment_clears_no_receipt():
    payments = [pay("5"), pay("5", receipt_document_id=1)]
    assert "no_receipt" not in state(inst(), payments).flags


def test_direct_debit_rejected_after_last_payment():
    i = inst(direct_debit_rejected_at=date(2026, 3, 12))
    s = state(i, [pay("4", paid_on=date(2026, 3, 1))], today=date(2026, 3, 13))
    assert s.flags == frozenset({"partial", "direct_debit_rejected"})


def test_direct_debit_rejected_without_payments():
    i = inst(direct_debit_rejected_at=date(2026, 3, 10))
    assert state(i, today=date(2026, 3, 11)).flags == frozenset({"direct_debit_rejected"})


def test_direct_debit_rejection_superseded_by_a_later_payment():
    i = inst(direct_debit_rejected_at=date(2026, 3, 1))
    s = state(i, [pay(paid_on=date(2026, 3, 5), receipt_document_id=1,
                      evidence_level=EvidenceLevel.INSURER_CONFIRMED)])
    assert s.state == "paid"
    assert "direct_debit_rejected" not in s.flags


# --- legacy rows -------------------------------------------------------------

@pytest.mark.parametrize(
    "cached", [InstallmentStatus.PAID, InstallmentStatus.PAID_LATE, InstallmentStatus.CREDITED]
)
def test_legacy_paid_row_without_payments_reads_as_paid(cached):
    i = inst(status=cached, paid_on=date(2026, 3, 12))
    s = state(i, today=date(2026, 6, 1))
    assert s.state == "paid"
    assert s.legacy is True
    assert s.days_late == 2
    assert s.paid_uf == Decimal("10.0000")
    assert s.evidence == "declared"


def test_legacy_cancelled_row_reads_as_voided():
    s = state(inst(status=InstallmentStatus.CANCELLED), today=date(2026, 6, 1))
    assert s.state == "voided" and s.legacy is True


def test_any_payment_row_ends_the_legacy_reading():
    i = inst(status=InstallmentStatus.PAID, paid_on=DUE)
    s = state(i, [pay(status=PaymentStatus.REVERSED)], today=date(2026, 3, 11))
    assert s.state == "overdue"
    assert s.legacy is False


# --- the legacy cache mapping ------------------------------------------------

def _s(name, days_late=0, paid_on=None):
    return InstallmentState(
        state=name, flags=frozenset(), paid_uf=Decimal(0), balance_uf=Decimal(0),
        days_late=days_late, evidence=None, paid_on=paid_on,
    )


@pytest.mark.parametrize(
    ("derived", "legacy"),
    [
        (_s("voided"), InstallmentStatus.CANCELLED),
        (_s("paid", 0, DUE), InstallmentStatus.PAID),
        (_s("paid", 3, DUE), InstallmentStatus.PAID_LATE),
        (_s("promised", 5), InstallmentStatus.OVERDUE),
        (_s("overdue", 2), InstallmentStatus.OVERDUE),
        (_s("termination_risk", 40), InstallmentStatus.OVERDUE),
        (_s("due_today"), InstallmentStatus.DUE),
        (_s("upcoming"), InstallmentStatus.PENDING),
    ],
)
def test_legacy_status_mapping(derived, legacy):
    assert legacy_status_for(derived) == legacy


# --- refresh_installment_cache against the schema ----------------------------

def test_refresh_writes_the_cache_and_follows_reversals(db, world):
    _policy, plan = make_policy_with_plan(db, world.a, world.insurer, n_installments=2,
                                          start=date(2026, 3, 10))
    first = plan.installments[0]
    db.commit()

    s = refresh_installment_cache(db, first, today=date(2026, 3, 15))
    assert s.state == "overdue"
    assert first.status == InstallmentStatus.OVERDUE
    assert first.days_late == 5
    assert first.paid_on is None

    payment = make_payment(db, first, paid_on=date(2026, 3, 14))
    s = refresh_installment_cache(db, first, today=date(2026, 3, 15))
    assert s.state == "paid"
    assert first.status == InstallmentStatus.PAID_LATE
    assert first.paid_on == date(2026, 3, 14)
    assert first.days_late == 4
    db.commit()

    payment.status = PaymentStatus.REVERSED
    db.flush()
    db.refresh(first)
    s = refresh_installment_cache(db, first, today=date(2026, 3, 15))
    assert s.state == "overdue"
    assert first.status == InstallmentStatus.OVERDUE
    assert first.paid_on is None


def test_refresh_on_time_and_upcoming(db, world):
    _policy, plan = make_policy_with_plan(db, world.a, world.insurer, n_installments=2,
                                          start=date(2026, 3, 10))
    first, second = plan.installments
    make_payment(db, first, paid_on=date(2026, 3, 9))
    refresh_installment_cache(db, first, today=date(2026, 3, 9))
    refresh_installment_cache(db, second, today=date(2026, 3, 9))
    assert first.status == InstallmentStatus.PAID and first.days_late == 0
    assert second.status == InstallmentStatus.PENDING and second.days_late is None


def test_refresh_leaves_legacy_rows_untouched(db, world):
    _policy, plan = make_policy_with_plan(
        db, world.a, world.insurer, n_installments=1, start=date(2026, 3, 10),
        statuses=[InstallmentStatus.PAID_LATE],
    )
    only = plan.installments[0]
    only.paid_on = date(2026, 3, 20)
    only.days_late = 10
    db.commit()
    s = refresh_installment_cache(db, only, today=date(2026, 6, 1))
    assert s.legacy and s.state == "paid"
    assert only.status == InstallmentStatus.PAID_LATE
    assert only.paid_on == date(2026, 3, 20)


def test_refresh_reads_the_brokers_settings(db, world):
    make_collection_settings(
        db, world.a.broker_id, cadence_days=[-5, 0, 1], tolerance_uf=Decimal("1.0000")
    )
    _policy, plan = make_policy_with_plan(db, world.a, world.insurer, n_installments=2,
                                          start=date(2026, 3, 10))
    first = plan.installments[0]
    gross = Decimal(str(first.gross_amount_uf))
    make_payment(db, first, amount_uf=gross - Decimal("0.5"), paid_on=date(2026, 3, 10))
    assert refresh_installment_cache(db, first, today=date(2026, 3, 10)).state == "paid"

    second = plan.installments[1]  # due 2026-04-10; max cadence 1 -> risk on Apr 12
    assert refresh_installment_cache(db, second, today=date(2026, 4, 12)).state == "termination_risk"
    assert second.status == InstallmentStatus.OVERDUE
    # Broker B (no settings row) keeps the default cadence.
    _pb, plan_b = make_policy_with_plan(db, world.b, world.insurer, n_installments=2,
                                        start=date(2026, 3, 10))
    assert refresh_installment_cache(db, plan_b.installments[1],
                                     today=date(2026, 4, 12)).state == "overdue"
