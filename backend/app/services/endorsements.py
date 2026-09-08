"""Endorsement services — the **prórroga** (period extension) fanned over N policies.

A prórroga is a manual multi-select (spec v3 §1 rule 3): the broker picks the
policies explicitly, the server writes ONE ``Endorsement`` per policy — each
still tied to exactly one policy (rule 2) — sharing a ``batch_key`` so the UI
can render the N rows as a single movement.

Two invariants make the batch cheap and safe:

* **Every premium delta is zero.** A prórroga moves a date, not money, so the
  collection ledger (``Σ instalments == policy gross + Σ ISSUED endorsement
  deltas``) is untouched and the re-pricing block of the single-issue path
  no-ops.
* **Only ``policy.end_date`` / ``policy.period_end_at`` move.** The account
  folder's vigencia is NOT touched — a date change on the folder opens a
  sibling through ``POST /case-files/{id}/reperiod`` (rule 1). That decision is
  isolated in :func:`apply_period_extension`, which is the single function to
  change if the team flips it (spec §9 question 3).
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, NamedTuple
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.case_file import CaseFile
from app.models.client import Client
from app.models.endorsement import Endorsement
from app.models.enums import (
    CaseFileKind,
    CaseFileStatus,
    EndorsementKind,
    EndorsementStatus,
)
from app.models.policy import Policy
from app.models.user import User
from app.services import case_files as machine

#: The Chilean contractual convention for a policy period boundary.
NOON = time(12, 0)

ZERO = Decimal("0")

#: The five premium deltas a prórroga pins to zero.
PREMIUM_DELTA_FIELDS = (
    "taxable_premium_delta_uf",
    "exempt_premium_delta_uf",
    "net_premium_delta_uf",
    "vat_delta_uf",
    "total_premium_delta_uf",
)


class EndorsementBatchError(ValueError):
    """A structured 422 the router hands back verbatim (spec §4)."""

    def __init__(self, code: str, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.extra = extra

    def as_detail(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, **self.extra}


class BatchMember(NamedTuple):
    """One policy's share of a batch."""

    policy: Policy
    endorsement: Endorsement
    case_file: CaseFile | None


# --- Resolving the policy's account and group ---------------------------------

def resolve_account_case(
    db: Session, policy: Policy, broker_id: int
) -> CaseFile | None:
    """The account folder a policy belongs to, or ``None``.

    Same order the case-files router uses when it defaults a post-sale parent:
    the policy's own ``case_file_id`` first, then the account case wrapping its
    placement. ``policy`` carries no ``account_group_id`` of its own (§2.6).
    """
    if policy.case_file_id is not None:
        case = db.get(CaseFile, policy.case_file_id)
        if case is not None and case.broker_id == broker_id:
            return case
    if policy.placement_id is not None:
        return db.scalars(
            select(CaseFile)
            .where(
                CaseFile.broker_id == broker_id,
                CaseFile.kind == CaseFileKind.ACCOUNT,
                CaseFile.placement_id == policy.placement_id,
            )
            .order_by(CaseFile.id)
            .limit(1)
        ).first()
    return None


def resolve_account_group_id(
    db: Session, policy: Policy, broker_id: int, *, account: CaseFile | None = None
) -> int | None:
    """The group a policy hangs under: its account folder's, else its client's.

    ``None`` is a value, not a hole: a set of policies that all resolve to
    ``None`` is one (ungrouped) selection and is allowed, while mixing ``None``
    with a real group is a span and is refused.
    """
    if account is None:
        account = resolve_account_case(db, policy, broker_id)
    if account is not None and account.account_group_id is not None:
        return account.account_group_id
    client = db.get(Client, policy.client_id)
    if client is not None and client.broker_id == broker_id:
        return client.account_group_id
    return None


def assert_single_group(
    db: Session, policies: list[Policy], broker_id: int
) -> int | None:
    """422 ``policies_span_groups`` unless every policy sits under one group."""
    by_policy = {
        policy.id: resolve_account_group_id(db, policy, broker_id)
        for policy in policies
    }
    distinct = set(by_policy.values())
    if len(distinct) > 1:
        raise EndorsementBatchError(
            "policies_span_groups",
            "Every policy of a prórroga must belong to the same account group",
            account_group_ids=sorted(
                (value for value in distinct if value is not None)
            ),
            policies={str(pid): gid for pid, gid in sorted(by_policy.items())},
        )
    return next(iter(distinct), None)


# --- Creating the batch --------------------------------------------------------

def next_endorsement_sequence(db: Session, *, broker_id: int, policy_id: int) -> int:
    highest = db.scalar(
        select(func.max(Endorsement.sequence_no)).where(
            Endorsement.broker_id == broker_id, Endorsement.policy_id == policy_id
        )
    )
    return int(highest or 0) + 1


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def create_period_extension_batch(
    db: Session,
    *,
    broker_id: int,
    user: User | None,
    policies: list[Policy],
    new_end_date: date,
    effective_at: datetime,
    issued_document_id: int | None = None,
    note: str | None = None,
) -> tuple[str, list[BatchMember]]:
    """One prórroga over N policies. Flushes only — the caller commits."""
    assert_single_group(db, policies, broker_id)

    batch_key = str(uuid4())
    members: list[BatchMember] = []
    for policy in policies:
        account = resolve_account_case(db, policy, broker_id)
        endorsement = Endorsement(
            broker_id=broker_id,
            policy_id=policy.id,
            sequence_no=next_endorsement_sequence(
                db, broker_id=broker_id, policy_id=policy.id
            ),
            kind=EndorsementKind.PERIOD_EXTENSION,
            status=EndorsementStatus.DRAFT,
            batch_key=batch_key,
            effective_at=effective_at,
            issued_document_id=issued_document_id,
            motive=note or "PRORROGA de vigencia",
            contractual_basis="period_extension",
            # The before/after table of this motive: one moved date.
            effect={
                "period_end": {
                    "before": _iso(policy.end_date),
                    "after": new_end_date.isoformat(),
                }
            },
            # A prórroga is a date, not money.
            insured_amount_delta_uf=None,
            taxable_premium_delta_uf=ZERO,
            exempt_premium_delta_uf=ZERO,
            net_premium_delta_uf=ZERO,
            vat_delta_uf=ZERO,
            total_premium_delta_uf=ZERO,
        )
        db.add(endorsement)
        db.flush()

        case = _open_endorsement_case(
            db,
            broker_id=broker_id,
            user=user,
            policy=policy,
            account=account,
            endorsement=endorsement,
            new_end_date=new_end_date,
            batch_key=batch_key,
        )
        endorsement.case_file_id = case.id
        members.append(BatchMember(policy=policy, endorsement=endorsement, case_file=case))
    return batch_key, members


def _open_endorsement_case(
    db: Session,
    *,
    broker_id: int,
    user: User | None,
    policy: Policy,
    account: CaseFile | None,
    endorsement: Endorsement,
    new_end_date: date,
    batch_key: str,
) -> CaseFile:
    """The ``case_file(kind=endorsement)`` child of the policy's account.

    Group and period are INHERITED from the account (§2.3) so the group tree
    never has to join through ``policy`` to count a post-sale child.
    """
    sequence_no = machine.next_sequence_no(
        db, broker_id=broker_id, policy_id=policy.id, kind=CaseFileKind.ENDORSEMENT
    )
    stage = machine.initial_stage_for(CaseFileKind.ENDORSEMENT)
    case = CaseFile(
        broker_id=broker_id,
        client_id=account.client_id if account is not None else policy.client_id,
        policy_id=policy.id,
        parent_case_file_id=account.id if account is not None else None,
        insurance_line_id=(
            account.insurance_line_id if account is not None else None
        )
        or policy.insurance_line_id,
        account_group_id=resolve_account_group_id(
            db, policy, broker_id, account=account
        ),
        period_start=account.period_start if account is not None else None,
        period_end=account.period_end if account is not None else None,
        period_label=account.period_label if account is not None else None,
        kind=CaseFileKind.ENDORSEMENT,
        stage=stage,
        status=CaseFileStatus.OPEN,
        reference=machine.build_post_sale_reference(
            db,
            broker_id=broker_id,
            kind=CaseFileKind.ENDORSEMENT,
            sequence_no=sequence_no,
            parent=account,
        ),
        title=f"Prórroga · póliza {policy.policy_number}",
        sequence_no=sequence_no,
        version=1,
        owner_user_id=user.id if user is not None else None,
        meta={
            "batch_key": batch_key,
            "endorsement_id": endorsement.id,
            "period_end": {
                "before": _iso(policy.end_date),
                "after": new_end_date.isoformat(),
            },
        },
    )
    db.add(case)
    db.flush()
    machine.record_stage_event(
        db,
        case=case,
        from_stage=None,
        to_stage=stage,
        user=user,
        note="Prórroga en lote",
        meta={"batch_key": batch_key, "endorsement_id": endorsement.id},
    )
    return case


# --- Reading a batch -----------------------------------------------------------

def load_batch(db: Session, *, broker_id: int, batch_key: str) -> list[Endorsement]:
    """Every member of a batch, in policy order. Empty when the key is unknown."""
    return list(
        db.scalars(
            select(Endorsement)
            .where(
                Endorsement.broker_id == broker_id,
                Endorsement.batch_key == batch_key,
            )
            .order_by(Endorsement.policy_id.asc(), Endorsement.id.asc())
        ).all()
    )


# --- Applying the extension ----------------------------------------------------

def extension_target(endorsement: Endorsement) -> date | None:
    """The ``effect.period_end.after`` date, or ``None`` when it is unreadable."""
    effect = endorsement.effect
    if not isinstance(effect, dict):
        return None
    period_end = effect.get("period_end")
    if not isinstance(period_end, dict):
        return None
    after = period_end.get("after")
    if isinstance(after, date) and not isinstance(after, datetime):
        return after
    if isinstance(after, datetime):
        return after.date()
    if isinstance(after, str):
        try:
            return date.fromisoformat(after[:10])
        except ValueError:
            return None
    return None


def apply_period_extension(policy: Policy, endorsement: Endorsement) -> date | None:
    """Move the POLICY's end date — and nothing else (rule 3).

    The account folder's ``period_end`` is deliberately untouched: a vigencia
    change is a new folder, never an edit. This is the one place to change if
    the team decides a prórroga must move the account too.
    """
    if endorsement.kind is not EndorsementKind.PERIOD_EXTENSION:
        return policy.end_date
    new_end = extension_target(endorsement)
    if new_end is None:
        return policy.end_date
    previous = policy.period_end_at
    policy.end_date = new_end
    if previous is not None:
        policy.period_end_at = datetime.combine(new_end, previous.timetz())
    else:
        policy.period_end_at = datetime.combine(new_end, NOON, tzinfo=timezone.utc)
    if endorsement.ends_at is None:
        endorsement.ends_at = policy.period_end_at
    return new_end


__all__ = [
    "NOON",
    "PREMIUM_DELTA_FIELDS",
    "BatchMember",
    "EndorsementBatchError",
    "apply_period_extension",
    "assert_single_group",
    "create_period_extension_batch",
    "extension_target",
    "load_batch",
    "next_endorsement_sequence",
    "resolve_account_case",
    "resolve_account_group_id",
]
