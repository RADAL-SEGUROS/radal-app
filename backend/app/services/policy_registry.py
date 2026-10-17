"""Policies as the main view (v12, docs/v12/02-backend-api.md §2).

Four jobs, all broker-scoped, all flush-only (the caller commits):

* **Colocación on demand** (team R1 / 2b.6) — :func:`attach_or_create_account`.
  Every policy belongs to an account folder (``case_file(kind=account)``). A
  policy without one is attached to an OPEN account of the same client and
  subtipo (or ramo) whose vigencia contains its start date; failing that a new
  folder is opened with ``origin=new`` (the v3 axis) and
  ``entry_origin`` = the door (``policy_upload`` / ``manual_entry``), its
  ``route`` from the door too (upload → ``digital``, manual → ``manual``; the
  subtipo's ``suggested_route`` is for the full journey only — team spec,
  decision 2026-10-17), and its timeline written as
  *omitida* for every stage before ``policy_issued`` that never happened, then
  ``policy_issued`` → ``active``. An existing folder still in progress makes
  the same jump (R34: registering the policy closes the colocación).
* **Parties** — :func:`ensure_default_parties`: insured from the client;
  policyholder = the account's contratante when it differs, else the insured;
  payer = the policyholder (team rule). Only missing roles are added.
* **"Falta: X"** — :func:`compute_policy_missing` / :func:`policy_operability`:
  every feature declares the data it needs and is enabled only with it (R38).
  Reasons are Spanish: the UI shows them raw.
* **Cobranza line** — :func:`collection_summary` over the policy's cuotas,
  through the phase-1 state machine (``services.collection.status``) and the
  level resolver (``services.collection.resolver``).
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.account_client import AccountClient, AccountClientRole
from app.models.account_group import AccountGroup, AccountGroupStatus
from app.models.activity import Activity
from app.models.case_file import CaseFile
from app.models.catalog import InsuranceSubtype
from app.models.client import Client
from app.models.collection import CollectionInstallment
from app.models.enums import (
    CaseEntryOrigin,
    CaseFileKind,
    CaseFileStatus,
    CaseOrigin,
    EntityType,
    PartyRole,
    PlacementRoute,
)
from app.models.insurance_line import InsuranceLine
from app.models.insured import Insured
from app.models.policy import Policy, PolicyStatus
from app.models.policy_party import PolicyParty
from app.models.user import User
from app.services import case_files as machine

__all__ = [
    "DUE_SOON_DAYS",
    "PLACEHOLDER_NUMBER_PREFIX",
    "MISSING_KEYS",
    "AccountAttachment",
    "attach_or_create_account",
    "resolve_account_for",
    "route_for_entry",
    "linked_insured_recipients",
    "notify_policy_linked_insured",
    "find_account_for",
    "ensure_client_group",
    "ensure_default_parties",
    "compute_policy_missing",
    "policy_operability",
    "collection_summary",
    "computed_status",
    "computed_status_clause",
    "policy_journey",
    "possible_duplicates",
    "is_placeholder_number",
    "provisional_number",
    "final_placeholder_number",
    "today_santiago",
]

#: A cuota due within this many days (today included) is "due soon" — the same
#: window as Cobranza's ``upcoming`` bucket (02 §4).
DUE_SOON_DAYS = 30

#: ``policy.policy_number`` is NOT NULL + unique per broker; a manual policy
#: without its number ("vacío solo en vía Manual") stores this placeholder and
#: reports ``policy_number`` as missing until "Completar" fills it.
PLACEHOLDER_NUMBER_PREFIX = "SIN-N-"

#: Every ``missing`` key, in display order.
MISSING_KEYS: tuple[str, ...] = (
    "source_document",
    "policy_number",
    "plan",
    "gross_premium",
    "payer_contact",
    "premiums",
    "commission",
)

_LATE = frozenset({"overdue", "termination_risk", "promised"})
#: Public name of the late cuota states (vencida, riesgo de término, en acuerdo).
LATE_STATES = _LATE
_NEXT = frozenset({"upcoming", "due_today"})


def today_santiago() -> date:
    """Business dates are America/Santiago (00 §3)."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/Santiago")).date()
    except Exception:  # pragma: no cover - tzdata missing
        return datetime.now(timezone.utc).date()


# =============================================================================
# Policy number placeholder
# =============================================================================

def is_placeholder_number(number: str | None) -> bool:
    return not number or str(number).startswith(PLACEHOLDER_NUMBER_PREFIX)


def provisional_number() -> str:
    """A unique stand-in for the first flush (the id is not known yet)."""
    return f"{PLACEHOLDER_NUMBER_PREFIX}TMP-{secrets.token_hex(6)}"


def final_placeholder_number(policy: Policy) -> str:
    return f"{PLACEHOLDER_NUMBER_PREFIX}{policy.id}"


# =============================================================================
# Computed status (team regla de cálculo 3 — "se calcula, nunca se elige")
# =============================================================================

def computed_status(policy: Policy, today: date | None = None) -> str:
    """``upcoming`` | ``active`` | ``expired`` | ``cancelled`` from dates.

    A cancellation (status or an effective ``terminated_on`` reached) wins; a
    draft is not issued yet, so it reads ``upcoming`` until its end passes.
    Without dates the stored status decides.
    """
    today = today or today_santiago()
    if policy.status == PolicyStatus.CANCELLED or (
        policy.terminated_on is not None and policy.terminated_on <= today
    ):
        return "cancelled"
    ended = policy.end_date is not None and policy.end_date < today
    if policy.status == PolicyStatus.DRAFT:
        return "expired" if ended else "upcoming"
    if policy.start_date is not None and policy.start_date > today:
        return "upcoming"
    if ended:
        return "expired"
    if policy.end_date is None and policy.status in (PolicyStatus.EXPIRED, PolicyStatus.RENEWED):
        return "expired"
    return "active"


def computed_status_clause(state: str, today: date):
    """The SQL twin of :func:`computed_status` — ``Policy`` predicate for one
    computed state, so lists and cards filter in the database instead of
    loading every policy. ``tests/test_v12_policies.py`` pins the parity."""
    from sqlalchemy import and_, not_

    ended = and_(Policy.end_date.is_not(None), Policy.end_date < today)
    not_ended = or_(Policy.end_date.is_(None), Policy.end_date >= today)
    starts_later = and_(Policy.start_date.is_not(None), Policy.start_date > today)
    started = or_(Policy.start_date.is_(None), Policy.start_date <= today)
    cancelled = or_(
        Policy.status == PolicyStatus.CANCELLED,
        and_(Policy.terminated_on.is_not(None), Policy.terminated_on <= today),
    )
    live = not_(cancelled)
    draft = Policy.status == PolicyStatus.DRAFT
    issued = Policy.status != PolicyStatus.DRAFT
    stale = and_(
        Policy.end_date.is_(None),
        Policy.status.in_([PolicyStatus.EXPIRED, PolicyStatus.RENEWED]),
    )
    if state == "cancelled":
        return cancelled
    if state == "upcoming":
        return and_(live, or_(and_(draft, not_ended), and_(issued, starts_later)))
    if state == "expired":
        return and_(
            live, or_(and_(draft, ended), and_(issued, started, or_(ended, stale)))
        )
    if state == "active":
        return and_(live, issued, started, not_ended, not_(stale))
    raise ValueError(f"unknown computed status {state!r}")


# =============================================================================
# Groups & folders
# =============================================================================

def _insured_of(db: Session, client: Client) -> Insured | None:
    return db.get(Insured, client.insured_id) if client is not None else None


def ensure_client_group(db: Session, *, client: Client, user: User | None) -> AccountGroup:
    """The client's group; when it has none, one named after the client.

    Reuses the account-groups router's slug + orphan-adoption helpers so a group
    born here is indistinguishable from one created in the UI.
    """
    if client.account_group_id is not None:
        group = db.get(AccountGroup, client.account_group_id)
        if group is not None and group.broker_id == client.broker_id:
            return group
    from app.api.routers.account_groups import _adopt_client_case_files, _unique_slug

    insured = _insured_of(db, client)
    name = (insured.legal_name if insured else None) or f"Cliente {client.id}"
    group = AccountGroup(
        broker_id=client.broker_id,
        name=name[:255],
        slug=_unique_slug(db, client.broker_id, name),
        status=AccountGroupStatus.ACTIVE,
    )
    db.add(group)
    db.flush()
    client.account_group_id = group.id
    _adopt_client_case_files(db, client=client, group=group)
    db.add(
        Activity(
            broker_id=client.broker_id,
            user_id=user.id if user else None,
            action="account_group.created",
            entity_type=EntityType.ACCOUNT_GROUP,
            entity_id=group.id,
            description=f"{group.name} (creado al registrar una póliza)",
            meta={"client_id": client.id, "auto": True},
        )
    )
    return group


def _member_clause(broker_id: int, client_id: int):
    return or_(
        CaseFile.client_id == client_id,
        CaseFile.id.in_(
            select(AccountClient.case_file_id).where(
                AccountClient.broker_id == broker_id,
                AccountClient.client_id == client_id,
            )
        ),
    )


def find_account_for(
    db: Session,
    *,
    broker_id: int,
    client_id: int,
    insurance_subtype_id: int | None,
    insurance_line_id: int | None,
    on: date | None,
) -> CaseFile | None:
    """An OPEN account of this client whose vigencia contains ``on``: same
    subtipo first, then the same ramo when either side carries no subtipo."""
    if on is None:
        return None
    base = (
        select(CaseFile)
        .where(
            CaseFile.broker_id == broker_id,
            CaseFile.kind.in_(machine.ACCOUNT_KINDS),
            CaseFile.status == CaseFileStatus.OPEN,
            _member_clause(broker_id, client_id),
            CaseFile.period_start.is_not(None),
            CaseFile.period_end.is_not(None),
            CaseFile.period_start <= on,
            CaseFile.period_end > on,
        )
        .order_by(CaseFile.period_start.desc(), CaseFile.id.desc())
    )
    if insurance_subtype_id is not None:
        hit = db.scalars(base.where(CaseFile.insurance_subtype_id == insurance_subtype_id)).first()
        if hit is not None:
            return hit
    if insurance_line_id is not None:
        stmt = base.where(CaseFile.insurance_line_id == insurance_line_id)
        if insurance_subtype_id is not None:
            stmt = stmt.where(CaseFile.insurance_subtype_id.is_(None))
        hit = db.scalars(stmt).first()
        if hit is not None:
            return hit
    return None


def route_for_entry(
    entry_origin: CaseEntryOrigin, subtype: InsuranceSubtype | None = None
) -> PlacementRoute:
    """The vía of a colocación opened on demand (team spec, decision 2026-10-17).

    The door decides, never the subtipo: a policy uploaded with no folder (2b)
    is ``digital``; a hand-typed one (vía Manual) is ``manual``. Only the full
    journey (``POST /case-files``) takes the subtipo's ``suggested_route``; any
    other door falls back to it, then to ``digital``.
    """
    if entry_origin == CaseEntryOrigin.POLICY_UPLOAD:
        return PlacementRoute.DIGITAL
    if entry_origin == CaseEntryOrigin.MANUAL_ENTRY:
        return PlacementRoute.MANUAL
    if subtype is not None and subtype.suggested_route is not None:
        return subtype.suggested_route
    return PlacementRoute.DIGITAL


def _create_account(
    db: Session,
    *,
    broker_id: int,
    client: Client,
    subtype: InsuranceSubtype | None,
    insurance_line_id: int | None,
    period_start: date,
    period_end: date,
    entry_origin: CaseEntryOrigin,
    group: AccountGroup,
    user: User | None,
) -> CaseFile:
    insured = _insured_of(db, client)
    who = insured.legal_name if insured else f"Cliente {client.id}"
    what = subtype.name if subtype is not None else None
    if what is None and insurance_line_id is not None:
        line = db.get(InsuranceLine, insurance_line_id)
        what = line.name if line else None
    route = route_for_entry(entry_origin, subtype)
    case = CaseFile(
        broker_id=broker_id,
        client_id=client.id,
        insurance_line_id=insurance_line_id,
        insurance_subtype_id=subtype.id if subtype is not None else None,
        kind=CaseFileKind.ACCOUNT,
        # Born at the entry point: the first stage is written as *omitida* by
        # :func:`machine.advance_to_active` (``fresh=True``).
        stage=machine.initial_stage_for(CaseFileKind.ACCOUNT),
        status=CaseFileStatus.OPEN,
        reference=machine.build_reference(db, broker_id=broker_id, year=period_start.year),
        title=(f"{who} · {what}" if what else f"{who} · account")[:255],
        sequence_no=1,
        version=1,
        owner_user_id=user.id if user else None,
        account_group_id=group.id,
        period_start=period_start,
        period_end=period_end,
        period_label=machine.period_label_for(period_start, period_end),
        origin=CaseOrigin.NEW,
        entry_origin=entry_origin,
        route=route,
    )
    db.add(case)
    db.flush()
    db.add(
        AccountClient(
            broker_id=broker_id,
            case_file_id=case.id,
            client_id=client.id,
            role=AccountClientRole.POLICYHOLDER,
            is_primary=True,
        )
    )
    db.add(
        Activity(
            broker_id=broker_id,
            user_id=user.id if user else None,
            action="case_file.created",
            entity_type=EntityType.CASE_FILE,
            entity_id=case.id,
            description=f"Expediente {case.reference} · {case.title} (creado con la póliza)",
            meta={
                "kind": case.kind.value,
                "entry_origin": entry_origin.value,
                "route": route.value,
                "on_demand": True,
            },
        )
    )
    return case


def resolve_account_for(
    db: Session,
    *,
    broker_id: int,
    client: Client,
    subtype: InsuranceSubtype | None,
    insurance_line_id: int | None,
    period_start: date | None,
    period_end: date | None,
    entry_origin: CaseEntryOrigin,
    user: User | None,
) -> tuple[CaseFile, bool]:
    """Find the colocación a policy hangs off, or open it. ``(case, created)``.

    1. an open account of the client + subtipo/ramo covering ``period_start``;
    2. rule 5 (no hybrid states): an open folder of the client's group with the
       same ramo and EXACT vigencia — the client joins it as a member;
    3. a new folder (``origin=new``, ``entry_origin`` = the door).

    Raises :class:`ValueError` (Spanish) when the vigencia is incomplete.
    """
    line_id = insurance_line_id or (subtype.insurance_line_id if subtype is not None else None)
    found = find_account_for(
        db,
        broker_id=broker_id,
        client_id=client.id,
        insurance_subtype_id=subtype.id if subtype is not None else None,
        insurance_line_id=line_id,
        on=period_start,
    )
    if found is not None:
        return found, False
    if period_start is None or period_end is None or period_end <= period_start:
        raise ValueError(
            "Falta la vigencia (desde y hasta) para ubicar o crear la colocación de la póliza."
        )
    group = ensure_client_group(db, client=client, user=user)
    clash = machine.find_open_folder(
        db,
        broker_id=broker_id,
        account_group_id=group.id,
        insurance_line_id=line_id,
        period_start=period_start,
        period_end=period_end,
    )
    if clash is not None:
        exists = db.scalar(
            select(AccountClient.id).where(
                AccountClient.case_file_id == clash.id, AccountClient.client_id == client.id
            )
        )
        if exists is None and clash.client_id != client.id:
            db.add(
                AccountClient(
                    broker_id=broker_id,
                    case_file_id=clash.id,
                    client_id=client.id,
                    role=AccountClientRole.INSURED,
                )
            )
        return clash, False
    case = _create_account(
        db,
        broker_id=broker_id,
        client=client,
        subtype=subtype,
        insurance_line_id=line_id,
        period_start=period_start,
        period_end=period_end,
        entry_origin=entry_origin,
        group=group,
        user=user,
    )
    return case, True


@dataclass
class AccountAttachment:
    """What :func:`attach_or_create_account` did."""

    case: CaseFile
    created: bool = False
    advanced: bool = False
    warnings: list[str] = field(default_factory=list)


def _has_events(db: Session, case: CaseFile) -> bool:
    from app.models.case_file import CaseFileStageEvent

    return (
        db.scalar(
            select(CaseFileStageEvent.id).where(CaseFileStageEvent.case_file_id == case.id).limit(1)
        )
        is not None
    )


def attach_or_create_account(
    db: Session,
    *,
    broker_id: int,
    policy: Policy,
    entry_origin: CaseEntryOrigin,
    user: User | None,
    now: datetime | None = None,
) -> AccountAttachment:
    """Colocación on demand (02 §2). Flushes; the caller commits.

    A policy already pointing at a folder of this broker keeps it; otherwise the
    folder is found or created by :func:`resolve_account_for`. Either way the
    folder then jumps to ``policy_issued`` → ``active`` (skipped events for the
    stages that never happened). Raises :class:`ValueError` (vigencia missing)
    and :class:`machine.CaseTransitionError` (a placement refuses the walk).
    """
    case: CaseFile | None = None
    created = False
    if policy.case_file_id is not None:
        case = db.scalars(
            select(CaseFile).where(
                CaseFile.id == policy.case_file_id, CaseFile.broker_id == broker_id
            )
        ).first()
    if case is None:
        client = db.scalars(
            select(Client).where(Client.id == policy.client_id, Client.broker_id == broker_id)
        ).first()
        if client is None:
            raise LookupError("client not found for this broker")
        subtype = (
            db.get(InsuranceSubtype, policy.insurance_subtype_id)
            if policy.insurance_subtype_id is not None
            else None
        )
        case, created = resolve_account_for(
            db,
            broker_id=broker_id,
            client=client,
            subtype=subtype,
            insurance_line_id=policy.insurance_line_id,
            period_start=policy.start_date,
            period_end=policy.end_date,
            entry_origin=entry_origin,
            user=user,
        )
    policy.case_file_id = case.id
    if case.kind in machine.ACCOUNT_KINDS and case.policy_id is None:
        case.policy_id = policy.id
    if case.insurance_subtype_id is None and policy.insurance_subtype_id is not None:
        # Stamp the folder's subtipo only when it is of the folder's ramo.
        subtype_line = db.scalar(
            select(InsuranceSubtype.insurance_line_id).where(
                InsuranceSubtype.id == policy.insurance_subtype_id
            )
        )
        if case.insurance_line_id is None or subtype_line == case.insurance_line_id:
            case.insurance_subtype_id = policy.insurance_subtype_id
            if case.insurance_line_id is None:
                case.insurance_line_id = subtype_line
    if policy.insurance_line_id is None and case.insurance_line_id is not None:
        policy.insurance_line_id = case.insurance_line_id
    db.flush()

    result = AccountAttachment(case=case, created=created)
    if case.kind not in machine.ACCOUNT_KINDS or case.status != CaseFileStatus.OPEN:
        return result
    fresh = created or not _has_events(db, case)
    events = machine.advance_to_active(db, case=case, user=user, fresh=fresh, now=now)
    result.advanced = bool(events)
    return result


# =============================================================================
# 2b.5 — "Póliza enlazada a un asegurado existente" (FYI)
# =============================================================================

#: The bell origin of the 2b.5 FYI. NOT ``policy``: the collection clock raises
#: ``plan_missing`` on ``("policy", id)`` and a second kind on the same origin
#: would overwrite that to-do in place (one open row per user + origin).
LINKED_INSURED_ORIGIN = "policy_link"


def _active_member(db: Session, broker_id: int, user_id: int | None) -> User | None:
    if user_id is None:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active or user.broker_id != broker_id:
        return None
    return user


def linked_insured_recipients(
    db: Session,
    *,
    broker_id: int,
    client: Client,
    case: CaseFile | None,
    case_created: bool,
    uploader: User | None,
) -> list[int]:
    """Who hears that a policy was attached to an insured already in the cartera.

    Team spec §Notificaciones: "al ejecutivo del asegurado; también al
    administrador si quien subió el PDF es de otra cartera". The executive is
    the client's ``account_manager``, else the owner of the colocación the
    policy joined when that folder already existed (a folder opened by this
    very upload is owned by the uploader and says nothing about the cartera).
    No executive at all → the broker admins. When the uploader is not that
    executive the admins are added too. The admin share never includes the
    uploader themself (who did the linking); the executive always hears it.
    """
    from app.services.notifications import broker_admin_ids

    owner = _active_member(db, broker_id, client.account_manager_id)
    if owner is None and case is not None and not case_created:
        owner = _active_member(db, broker_id, case.owner_user_id)
    uploader_id = uploader.id if uploader is not None else None
    # The admins hear it as a cross-cartera check — never the uploader themself.
    admins = [uid for uid in broker_admin_ids(db, broker_id) if uid != uploader_id]
    if owner is None:
        return admins
    out = [owner.id]
    if uploader_id != owner.id:
        out += [uid for uid in admins if uid not in out]
    return out


def notify_policy_linked_insured(
    db: Session,
    *,
    broker_id: int,
    policy: Policy,
    client: Client,
    case: CaseFile | None,
    case_created: bool,
    uploader: User | None,
    now: datetime | None = None,
) -> list:
    """Raise the 2b.5 FYI (``policy_linked_insured``, tray ``fyi``). Flushes only.

    Call it ONLY when a policy registered with no folder (upload or vía Manual,
    colocación on demand) hangs off a client that EXISTED before the request —
    a client created by the same request is not "linked". Closes on open (fyi).
    """
    from app.models.enums import NotificationKind, NotificationTray
    from app.services.notifications import notify

    recipients = linked_insured_recipients(
        db,
        broker_id=broker_id,
        client=client,
        case=case,
        case_created=case_created,
        uploader=uploader,
    )
    if not recipients:
        return []
    insured = _insured_of(db, client)
    return notify(
        db,
        broker_id=broker_id,
        user_ids=recipients,
        kind=NotificationKind.POLICY_LINKED_INSURED,
        tray=NotificationTray.FYI,
        origin_type=LINKED_INSURED_ORIGIN,
        origin_id=policy.id,
        params={
            "policy_number": policy.policy_number,
            "insured_name": insured.legal_name if insured is not None else None,
            "insured_rut": insured.rut if insured is not None else None,
            "client_id": client.id,
            "uploaded_by": uploader.full_name if uploader is not None else None,
        },
        link_path=f"/policies/{policy.id}",
        now=now,
    )


# =============================================================================
# Parties
# =============================================================================

def _contact_of(db: Session, client: Client) -> dict[str, Any]:
    insured = _insured_of(db, client)
    return {
        "client_id": client.id,
        "name": (insured.legal_name if insured else None) or f"Cliente {client.id}",
        "rut": insured.rut if insured else None,
        "email": client.contact_email or (insured.email if insured else None),
        "phone": client.contact_phone or (insured.phone if insured else None),
    }


def _client_by_rut(db: Session, broker_id: int, rut: str | None) -> Client | None:
    if not rut:
        return None
    return db.scalars(
        select(Client)
        .join(Insured, Insured.id == Client.insured_id)
        .where(Client.broker_id == broker_id, Insured.rut == rut)
    ).first()


def _account_contratante(db: Session, policy: Policy) -> Client | None:
    """The account folder's contratante, when it is NOT the policy's client."""
    if policy.case_file_id is None:
        return None
    case = db.get(CaseFile, policy.case_file_id)
    if case is None or case.broker_id != policy.broker_id:
        return None
    holder_id = db.scalar(
        select(AccountClient.client_id).where(
            AccountClient.case_file_id == case.id,
            AccountClient.role == AccountClientRole.POLICYHOLDER,
        ).order_by(AccountClient.is_primary.desc(), AccountClient.id.asc())
    ) or case.client_id
    if holder_id is None or holder_id == policy.client_id:
        return None
    client = db.get(Client, holder_id)
    return client if client is not None and client.broker_id == policy.broker_id else None


def ensure_default_parties(
    db: Session,
    policy: Policy,
    *,
    policyholder: dict[str, Any] | None = None,
    payer: dict[str, Any] | None = None,
) -> list[PolicyParty]:
    """Add the roles the policy is missing; never touches existing rows.

    insured ← the policy's client (its insured); policyholder ← ``policyholder``
    when given, else the account's contratante, else the insured; payer ←
    ``payer`` when given, else a copy of the policyholder. Emails/phones are
    copied from the client's CRM contact, then the canonical insured.
    """
    existing = list(
        db.scalars(
            select(PolicyParty).where(
                PolicyParty.broker_id == policy.broker_id, PolicyParty.policy_id == policy.id
            )
        ).all()
    )
    roles = {row.role for row in existing}
    created: list[PolicyParty] = []

    def _add(role: PartyRole, data: dict[str, Any], *, primary: bool = False) -> PolicyParty:
        client_id = data.get("client_id")
        if client_id is None:
            match = _client_by_rut(db, policy.broker_id, data.get("rut"))
            client_id = match.id if match is not None else None
        row = PolicyParty(
            broker_id=policy.broker_id,
            policy_id=policy.id,
            role=role,
            client_id=client_id,
            name=str(data.get("name") or "—")[:255],
            rut=data.get("rut"),
            email=data.get("email"),
            phone=data.get("phone"),
            is_primary=primary,
        )
        db.add(row)
        created.append(row)
        return row

    client = db.get(Client, policy.client_id)
    insured_data = _contact_of(db, client) if client is not None else {"name": "—"}
    if PartyRole.INSURED not in roles:
        _add(PartyRole.INSURED, insured_data, primary=True)

    holder_data: dict[str, Any] | None = None
    if policyholder:
        holder_data = dict(policyholder)
    else:
        contratante = _account_contratante(db, policy)
        holder_data = _contact_of(db, contratante) if contratante is not None else insured_data
    if PartyRole.POLICYHOLDER not in roles:
        _add(PartyRole.POLICYHOLDER, holder_data, primary=True)
    else:
        holder = next(row for row in existing if row.role == PartyRole.POLICYHOLDER)
        holder_data = {
            "client_id": holder.client_id,
            "name": holder.name,
            "rut": holder.rut,
            "email": holder.email,
            "phone": holder.phone,
        }

    if PartyRole.PAYER not in roles:
        _add(PartyRole.PAYER, dict(payer) if payer else holder_data, primary=True)
    db.flush()
    if created:
        db.refresh(policy)
    return created


# =============================================================================
# Cobranza line
# =============================================================================

def _q4(value: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.0001"))


def collection_summary(
    db: Session,
    policy: Policy,
    *,
    today: date | None = None,
    params_cache: dict[int, tuple[list[int], Decimal]] | None = None,
    with_level: bool = True,
) -> dict[str, Any]:
    """Next cuota, arrears and level of one policy (all its plans).

    ``with_level=False`` skips the collection-mode resolver (several queries):
    filters and cards that only need the state and the arrears pass it.

    ``state``: ``no_plan`` (no cuotas at all) · ``overdue`` (a late cuota —
    vencida, riesgo de término or en acuerdo) · ``due_soon`` (next unpaid cuota
    within :data:`DUE_SOON_DAYS`) · ``ok``.
    """
    from app.services.collection.resolver import resolve_collection_mode
    from app.services.collection.status import (
        broker_collection_params,
        compute_installment_state,
    )

    today = today or today_santiago()
    plans = sorted(policy.collection_plans or [], key=lambda p: p.id)
    installments: list[CollectionInstallment] = [
        inst for plan in plans for inst in (plan.installments or [])
    ]
    out: dict[str, Any] = {
        "plan_id": plans[0].id if plans else None,
        "state": "no_plan",
        "next_due_date": None,
        "next_due_amount_uf": None,
        "overdue_count": 0,
        "overdue_uf": Decimal("0"),
        "level": None,
        "level_reason": None,
        "installments_count": len(installments),
        "paid_count": 0,
    }
    if not installments:
        return out

    if params_cache is not None and policy.broker_id in params_cache:
        cadence, tolerance = params_cache[policy.broker_id]
    else:
        cadence, tolerance = broker_collection_params(db, policy.broker_id)
        if params_cache is not None:
            params_cache[policy.broker_id] = (cadence, tolerance)

    late: list[tuple[CollectionInstallment, Any]] = []
    upcoming: list[tuple[CollectionInstallment, Any]] = []
    for inst in installments:
        state = compute_installment_state(
            inst, list(inst.payments or []), today=today, cadence_days=cadence, tolerance_uf=tolerance
        )
        if state.state == "paid":
            out["paid_count"] += 1
        elif state.state in _LATE:
            late.append((inst, state))
        elif state.state in _NEXT:
            upcoming.append((inst, state))

    out["overdue_count"] = len(late)
    out["overdue_uf"] = _q4(sum((s.balance_uf for _, s in late), Decimal("0")))
    upcoming.sort(key=lambda pair: (pair[0].due_date or date.max, pair[0].number))
    if upcoming:
        inst, state = upcoming[0]
        out["next_due_date"] = inst.due_date
        out["next_due_amount_uf"] = _q4(state.balance_uf)

    if late:
        out["state"] = "overdue"
    elif (
        out["next_due_date"] is not None
        and out["next_due_date"] <= today + timedelta(days=DUE_SOON_DAYS)
    ):
        out["state"] = "due_soon"
    else:
        out["state"] = "ok"

    if not with_level:
        return out
    focus = (
        sorted(late, key=lambda pair: (pair[0].due_date or date.max, pair[0].number))[0][0]
        if late
        else (upcoming[0][0] if upcoming else installments[-1])
    )
    try:
        mode = resolve_collection_mode(db, focus, broker_id=policy.broker_id)
        out["level"] = mode.level
        out["level_reason"] = mode.reason
    except LookupError:  # pragma: no cover - tenant guard, cannot happen here
        pass
    return out


# =============================================================================
# "Falta: X" and operability (R38)
# =============================================================================

_REASONS: dict[str, str] = {
    "source_document": "Sin respaldo: sube el PDF de la póliza (Subir póliza).",
    "policy_number": "Falta el n° de póliza: Endosar lo pide antes de seguir.",
    "plan": "Falta plan de pago: sin Cobranza ni notificaciones.",
    "gross_premium": "Falta la prima bruta: sin Cobranza ni notificaciones.",
    "payer_contact": (
        "Falta el correo del contratante o del pagador: la cuota queda Sin canal y no "
        "sale aviso."
    ),
    "premiums": (
        "Faltan las primas afecta, exenta o neta: no suma a los reportes de prima."
    ),
    "commission": "Falta el % de comisión: no suma a la comisión estimada.",
}


def _has_installments(policy: Policy) -> bool:
    return any(plan.installments for plan in (policy.collection_plans or []))


def compute_policy_missing(db: Session, policy: Policy) -> list[str]:
    """The data the policy lacks, as stable keys (see :data:`MISSING_KEYS`)."""
    from app.services.collection.resolver import payer_contact_email

    missing: list[str] = []
    if not policy.has_source_document:
        missing.append("source_document")
    if is_placeholder_number(policy.policy_number):
        missing.append("policy_number")
    if not _has_installments(policy):
        missing.append("plan")
    if policy.total_premium_uf is None:
        missing.append("gross_premium")
    if not payer_contact_email(policy):
        missing.append("payer_contact")
    if policy.net_premium_uf is None or (
        policy.taxable_premium_uf is None and policy.exempt_premium_uf is None
    ):
        missing.append("premiums")
    if policy.commission_pct is None:
        missing.append("commission")
    return missing


#: feature -> the missing keys that switch it off.
_FEATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("collections", ("plan", "gross_premium")),
    ("payer_notices", ("plan", "gross_premium", "payer_contact")),
    ("endorsements", ("policy_number",)),
    ("claims", ()),
    ("premium_reports", ("premiums",)),
    ("commission", ("premiums", "commission")),
    ("source_document", ("source_document",)),
)


def policy_operability(
    db: Session, policy: Policy, missing: list[str] | None = None
) -> list[dict[str, Any]]:
    """Each feature, whether it works with the data on file, and why not."""
    missing = missing if missing is not None else compute_policy_missing(db, policy)
    rows: list[dict[str, Any]] = []
    for feature, needs in _FEATURES:
        lacking = [key for key in needs if key in missing]
        reason = _REASONS[lacking[0]] if lacking else None
        if feature == "claims" and "policy_number" in missing:
            reason = "Se registra igual, con la marca Sin n° de póliza."
        rows.append(
            {"feature": feature, "enabled": not lacking, "reason": reason, "missing": lacking}
        )
    return rows


# =============================================================================
# Journey (the policy's colocación, as a viewer)
# =============================================================================

def policy_journey(db: Session, policy: Policy) -> dict[str, Any] | None:
    if policy.case_file_id is None:
        return None
    case = db.scalars(
        select(CaseFile).where(
            CaseFile.id == policy.case_file_id, CaseFile.broker_id == policy.broker_id
        )
    ).first()
    if case is None:
        return None
    states = machine.stage_states(db, case)
    return {
        "case_file_id": case.id,
        "group_id": case.account_group_id,
        "stage": case.stage,
        "entered_stages": [row["stage"] for row in states if row["state"] in ("done", "current")],
        "skipped_stages": [row["stage"] for row in states if row["state"] == "skipped"],
    }


def possible_duplicates(
    db: Session,
    *,
    broker_id: int,
    client_id: int,
    insurer_id: int,
    insurance_subtype_id: int | None,
    start_date: date | None,
    end_date: date | None,
    exclude_id: int | None = None,
) -> int:
    """R5 without a number: same RUT, compañía, subtipo and vigencia → warn."""
    stmt = select(func.count(Policy.id)).where(
        Policy.broker_id == broker_id,
        Policy.client_id == client_id,
        Policy.insurer_id == insurer_id,
        Policy.start_date == start_date,
        Policy.end_date == end_date,
    )
    if insurance_subtype_id is None:
        stmt = stmt.where(Policy.insurance_subtype_id.is_(None))
    else:
        stmt = stmt.where(Policy.insurance_subtype_id == insurance_subtype_id)
    if exclude_id is not None:
        stmt = stmt.where(Policy.id != exclude_id)
    return int(db.scalar(stmt) or 0)

