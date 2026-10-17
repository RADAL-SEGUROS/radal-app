"""v12 collection-level resolver (app.services.collection.resolver, 03 §2).

record (plan preference) -> native (every precondition) -> assisted (always).
Lead insurer = coinsurance leader, else policy.insurer_id. Brokers never see
onboarding status.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.base_class import utcnow
from app.models.enums import (
    ChannelStatus,
    CollectionLevelPreference,
    MailSender,
    OnboardingStatus,
    PartyRole,
    PaymentChannel,
    ProviderAccountStatus,
)
from app.models.insurer import Insurer
from app.models.policy import CoinsuranceShare
from app.services.collection.resolver import (
    REASON_RECORD,
    lead_insurer_id,
    payer_contact_email,
    resolve_collection_mode,
)
from tests.fakes_v12 import (
    make_broker_override,
    make_collection_settings,
    make_insurer_profile,
    make_native_channel,
    make_party,
    make_policy_with_plan,
    make_provider_account,
)


def _cuota(db, world, **kw):
    policy, plan = make_policy_with_plan(db, world.a, world.insurer, n_installments=2,
                                         start=date(2026, 3, 10), **kw)
    db.commit()
    return policy, plan, plan.installments[0]


def _go_native(db, insurer, *, account_status=ProviderAccountStatus.ACTIVE,
               channel_status=ChannelStatus.ACTIVE, channel=PaymentChannel.WEBPAY):
    account = make_provider_account(db, status=account_status)
    make_native_channel(db, insurer, account, status=channel_status, channel=channel)
    db.commit()
    return account


def _mode(db, world, inst):
    db.refresh(inst)
    return resolve_collection_mode(db, inst, broker_id=world.a.broker_id)


# --- assisted is the floor ----------------------------------------------------

def test_default_is_assisted_with_what_is_missing(db, world):
    _p, _plan, inst = _cuota(db, world)
    mode = _mode(db, world, inst)
    assert mode.level == "assisted"
    assert mode.reason == "Cobro asistido: Radal recuerda al pagador y consulta a HDI Seguros S.A."
    assert mode.channels == ()
    assert set(mode.missing) == {"payer_contact", "insurer_contact", "templates_unapproved"}
    assert mode.insurer_id == world.insurer.id


def test_assisted_with_everything_in_place_misses_nothing(db, world):
    policy, _plan, inst = _cuota(db, world)
    make_party(db, policy, role=PartyRole.PAYER, email="paga@cliente.cl")
    make_insurer_profile(db, world.insurer, collections_email="cobranza@hdi.cl")
    make_collection_settings(db, world.a.broker_id, notices_approved_at=utcnow())
    db.commit()
    mode = _mode(db, world, inst)
    assert mode.level == "assisted"
    assert mode.missing == ()


def test_broker_override_email_counts_as_insurer_contact(db, world):
    _p, _plan, inst = _cuota(db, world)
    make_broker_override(db, world.a.broker_id, world.insurer, collections_email="x@hdi.cl")
    db.commit()
    assert "insurer_contact" not in _mode(db, world, inst).missing


def test_payer_contact_resolution(db, world):
    policy, _plan, inst = _cuota(db, world)
    # No parties: the client (insured) contact stands in for the policyholder.
    world.a.client.contact_email = "contacto@alfa.cl"
    db.commit()
    db.refresh(policy)
    assert payer_contact_email(policy) == "contacto@alfa.cl"
    # A payer party without e-mail: the fallback no longer applies.
    make_party(db, policy, role=PartyRole.PAYER, email=None)
    assert payer_contact_email(policy) is None
    # A policyholder with e-mail fills in.
    make_party(db, policy, role=PartyRole.POLICYHOLDER, name="Tomador", email="tomador@alfa.cl")
    assert payer_contact_email(policy) == "tomador@alfa.cl"
    # A payer with e-mail wins over the policyholder.
    make_party(db, policy, role=PartyRole.PAYER, name="Pagador 2", email="paga@alfa.cl")
    assert payer_contact_email(policy) == "paga@alfa.cl"
    db.commit()
    assert "payer_contact" not in _mode(db, world, inst).missing


def test_party_opted_out_of_collections_is_not_a_contact(db, world):
    policy, _plan, _inst = _cuota(db, world)
    make_party(db, policy, role=PartyRole.PAYER, email="no@alfa.cl", notify_collections=False)
    assert payer_contact_email(policy) is None


def test_mail_sender_missing_when_broker_account_is_not_connected(db, world):
    _p, _plan, inst = _cuota(db, world)
    make_collection_settings(db, world.a.broker_id, default_sender=MailSender.BROKER_ACCOUNT)
    db.commit()
    assert "mail_sender" in _mode(db, world, inst).missing


# --- record ------------------------------------------------------------------

def test_record_preference_wins_even_when_native_is_available(db, world):
    _p, _plan, inst = _cuota(db, world, level_preference=CollectionLevelPreference.RECORD)
    _go_native(db, world.insurer)
    mode = _mode(db, world, inst)
    assert mode.level == "record"
    assert mode.reason == REASON_RECORD == "Cobro solo de registro (elegido por la corredora)"
    assert mode.channels == () and mode.missing == ()


# --- native ------------------------------------------------------------------

def test_native_when_the_lead_insurer_has_an_active_channel(db, world):
    _p, _plan, inst = _cuota(db, world)
    _go_native(db, world.insurer)
    mode = _mode(db, world, inst)
    assert mode.level == "native"
    assert mode.reason == "HDI Seguros S.A. cobra en línea con Radal"
    assert mode.channels == ("webpay",)


def test_native_lists_every_active_channel_once(db, world):
    _p, _plan, inst = _cuota(db, world)
    account = _go_native(db, world.insurer)
    make_native_channel(db, world.insurer, account, channel=PaymentChannel.ONECLICK)
    make_native_channel(db, world.insurer, account, channel=PaymentChannel.BANK_TRANSFER,
                        status=ChannelStatus.SANDBOX)
    db.commit()
    assert _mode(db, world, inst).channels == ("webpay", "oneclick")


def test_assisted_preference_stays_assisted(db, world):
    _p, _plan, inst = _cuota(db, world, level_preference=CollectionLevelPreference.ASSISTED)
    _go_native(db, world.insurer)
    assert _mode(db, world, inst).level == "assisted"


def test_broker_override_opt_out_blocks_native(db, world):
    _p, _plan, inst = _cuota(db, world)
    _go_native(db, world.insurer)
    make_broker_override(db, world.a.broker_id, world.insurer, native_enabled=False)
    db.commit()
    assert _mode(db, world, inst).level == "assisted"
    # Broker B's opt-out does not affect broker A.
    _p2, plan_b = make_policy_with_plan(db, world.b, world.insurer, n_installments=1)
    db.commit()
    other = plan_b.installments[0]
    assert resolve_collection_mode(db, other, broker_id=world.b.broker_id).level == "native"


def test_broker_master_switch_off_blocks_native(db, world):
    _p, _plan, inst = _cuota(db, world)
    _go_native(db, world.insurer)
    make_collection_settings(db, world.a.broker_id, native_collection_enabled=False)
    db.commit()
    assert _mode(db, world, inst).level == "assisted"


@pytest.mark.parametrize(
    ("account_status", "channel_status"),
    [
        (ProviderAccountStatus.ACTIVE, ChannelStatus.PENDING),
        (ProviderAccountStatus.ACTIVE, ChannelStatus.SANDBOX),
        (ProviderAccountStatus.ACTIVE, ChannelStatus.SUSPENDED),
        (ProviderAccountStatus.DRAFT, ChannelStatus.ACTIVE),
        (ProviderAccountStatus.DISABLED, ChannelStatus.ACTIVE),
    ],
)
def test_inactive_channel_or_account_is_not_native(db, world, account_status, channel_status):
    _p, _plan, inst = _cuota(db, world)
    _go_native(db, world.insurer, account_status=account_status, channel_status=channel_status)
    assert _mode(db, world, inst).level == "assisted"


def test_channel_without_provider_account_is_not_native(db, world):
    _p, _plan, inst = _cuota(db, world)
    make_native_channel(db, world.insurer, None)
    db.commit()
    assert _mode(db, world, inst).level == "assisted"


def test_onboarding_status_is_irrelevant_and_never_exposed(db, world):
    _p, _plan, inst = _cuota(db, world)
    make_insurer_profile(db, world.insurer, onboarding_status=OnboardingStatus.ONBOARDING,
                         expected_live_on=date(2026, 12, 1), status_note="interno")
    db.commit()
    mode = _mode(db, world, inst)
    assert mode.level == "assisted"
    text = repr(mode)
    assert "onboarding" not in text and "2026-12-01" not in text and "interno" not in text


# --- coinsurance leader (R18) -------------------------------------------------

def _second_insurer(db):
    other = Insurer(legal_name="Otra Compañía S.A.", trade_name="Otra", rut="5000001-K",
                    cmf_code="CMF-OTRA-1", is_native=True)
    db.add(other)
    db.flush()
    return other


def test_coinsurance_leader_decides_the_level(db, world):
    policy, _plan, inst = _cuota(db, world)
    leader = _second_insurer(db)
    db.add_all([
        CoinsuranceShare(policy_id=policy.id, insurer_id=world.insurer.id, is_leader=False,
                         percentage=Decimal("40")),
        CoinsuranceShare(policy_id=policy.id, insurer_id=leader.id, is_leader=True,
                         percentage=Decimal("60")),
    ])
    db.commit()
    db.refresh(policy)
    assert lead_insurer_id(policy) == leader.id

    # The policy's own insurer is native, but the LEADER is not -> assisted.
    _go_native(db, world.insurer)
    mode = _mode(db, world, inst)
    assert mode.level == "assisted"
    assert mode.insurer_id == leader.id
    assert "Otra" in mode.reason

    # Once the leader is native, the cuota is native.
    _go_native(db, leader)
    mode = _mode(db, world, inst)
    assert mode.level == "native"
    assert mode.reason == "Otra cobra en línea con Radal"


def test_without_a_flagged_leader_the_policy_insurer_leads(db, world):
    policy, _plan, _inst = _cuota(db, world)
    other = _second_insurer(db)
    db.add(CoinsuranceShare(policy_id=policy.id, insurer_id=other.id, is_leader=False,
                            percentage=Decimal("50")))
    db.commit()
    db.refresh(policy)
    assert lead_insurer_id(policy) == world.insurer.id


# --- tenancy -----------------------------------------------------------------

def test_another_brokers_installment_is_refused(db, world):
    _p, _plan, inst = _cuota(db, world)
    with pytest.raises(LookupError):
        resolve_collection_mode(db, inst, broker_id=world.b.broker_id)
