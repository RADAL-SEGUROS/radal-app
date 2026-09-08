"""The navigator — the data behind the two sidebars (spec §4.2).

Two builders, both read-only:

* :func:`build_navigator` — ``GET /navigator``: the main rail. Up to 8 groups
  ordered by their latest ``period_start`` (desc), each with up to 3 vigencias,
  the 5 most recently updated visible account/renewal folders, the count of
  clients that belong to no group, and ``record_folders`` — the SERVER-OWNED
  ANTECEDENTES mapping (``app.schemas.navigator.RECORD_FOLDERS``). The UI never
  hardcodes that mapping.
* :func:`build_group_tree` — ``GET /account-groups/{id}/tree``: the contextual
  rail. vigencia -> ramo -> account / policies (with post-sale children by kind)
  / renewal.

Three rules shape every query here:

1. **Visibility is the case-file router's**, not a copy: both builders start
   from ``app.api.routers.case_files._visible()`` (imported lazily to keep the
   service -> router direction one-way at import time), so a ``partial``
   ``CaseFiles.View`` grant — today the inspector's inspection subquery,
   tomorrow B5's ``CASE_VIEW_SCOPE`` — narrows the navigator for free. Post-sale
   child nodes are additionally filtered by the caller's
   ``Endorsements|Collections|Claims`` **View** grants.
2. **Portability.** Every aggregate is a grouped query whose non-aggregated
   columns all appear in ``GROUP BY`` (``ONLY_FULL_GROUP_BY``-safe) and no
   statement uses ``NULLS LAST``; ordering that must place NULLs last is done in
   Python over the already-bounded result.
3. **Vigencia is a label over per-account full dates** (spec §1): the tree
   buckets accounts by ``period_label`` and every ramo/policy node still carries
   its own ``period_start``/``period_end``.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import date
from typing import Any, Iterable

from sqlalchemy import Select, case as sql_case, func, or_, select
from sqlalchemy.orm import Session

from app.core.permissions import is_partial, resolve_role, user_has_permission
from app.models.account_client import AccountClient
from app.models.account_group import AccountGroup
from app.models.activity import Note
from app.models.case_file import CaseFile, CaseFileStageEvent, CasePack
from app.models.client import Client
from app.models.document import Document
from app.models.endorsement import Endorsement, EndorsementKind, EndorsementStatus
from app.models.enums import CaseFileKind, CaseFileStatus, CaseStage, EntityType
from app.models.insurance_line import InsuranceLine
from app.models.insured import Insured
from app.models.insurer import Insurer
from app.models.placement import Placement
from app.models.policy import Policy
from app.models.proposal import Proposal
from app.models.quote import QuoteRequest
from app.models.user import User
from app.schemas.navigator import (
    RECORD_FOLDER_BY_CATEGORY,
    RECORD_FOLDERS,
    GroupTree,
    NavigatorGroup,
    NavigatorPeriod,
    NavigatorRecentCase,
    NavigatorResponse,
    TreeAccountNode,
    TreeClient,
    TreeGroup,
    TreeLineNode,
    TreePeriodNode,
    TreePolicyChildren,
    TreePolicyNode,
    TreePostSaleNode,
    TreeRenewal,
)

__all__ = [
    "GroupNotFound",
    "ACCOUNT_KINDS",
    "POST_SALE_KINDS",
    "POST_SALE_MODULE",
    "RENEWABLE_STAGES",
    "MAX_NAVIGATOR_GROUPS",
    "MAX_NAVIGATOR_PERIODS",
    "MAX_NAVIGATOR_RECENT",
    "UNSCHEDULED_PERIOD_LABEL",
    "visible_cases",
    "visible_group_ids",
    "period_label_for",
    "as_date",
    "slugify_group_name",
    "SLUG_MAX",
    "build_navigator",
    "build_group_tree",
]


class GroupNotFound(LookupError):
    """The group does not exist inside the caller's broker (router -> 404)."""


#: The folder kinds that ARE an account (one line x one period x N RUTs).
ACCOUNT_KINDS: tuple[CaseFileKind, ...] = (CaseFileKind.ACCOUNT, CaseFileKind.RENEWAL)

#: The post-sale children rendered under a policy node.
POST_SALE_KINDS: tuple[CaseFileKind, ...] = (
    CaseFileKind.ENDORSEMENT,
    CaseFileKind.COLLECTION,
    CaseFileKind.CLAIM,
)

#: Which module's ``View`` grant gates each post-sale child node (spec §4.2).
POST_SALE_MODULE: dict[CaseFileKind, str] = {
    CaseFileKind.ENDORSEMENT: "Endorsements",
    CaseFileKind.COLLECTION: "Collections",
    CaseFileKind.CLAIM: "Claims",
}

#: A renewal may only be started from a finished vigencia (spec §4.3).
RENEWABLE_STAGES: frozenset[CaseStage] = frozenset(
    {CaseStage.ACTIVE, CaseStage.MIRROR_VALIDATION, CaseStage.CLOSED}
)

#: Stages that still allow a period edit — beyond these the folder is locked
#: (rule 1). Mirrors what ``/case-files/{id}/reperiod`` enforces (WP B2).
_UNLOCKED_STAGES: frozenset[CaseStage] = frozenset({CaseStage.LEAD, CaseStage.INTAKE})

MAX_NAVIGATOR_GROUPS = 8
MAX_NAVIGATOR_PERIODS = 3
MAX_NAVIGATOR_RECENT = 5

#: Bucket for folders that carry neither a label nor dates. Deliberately
#: punctuation, not a word: the label is display text, and Spanish copy belongs
#: in the locale files (rule 1).
UNSCHEDULED_PERIOD_LABEL = "—"


# =============================================================================
# Visibility
# =============================================================================

def visible_cases(db: Session, broker_id: int, user: User) -> Select:
    """The case-file router's ``_visible()`` SELECT, imported not copied.

    Lazy import: ``app.api.routers.case_files`` imports services, so importing
    it at module scope here would make the dependency circular. Doing it inside
    the function also means B5's ``CASE_VIEW_SCOPE`` lands in the navigator the
    moment it lands in the router, with no edit on this file.
    """
    from app.api.routers.case_files import _visible  # noqa: PLC0415

    return _visible(db, broker_id, user)


def _post_sale_kinds_for(user: User) -> tuple[CaseFileKind, ...]:
    """The post-sale kinds this caller may see under a policy node."""
    return tuple(
        kind
        for kind in POST_SALE_KINDS
        if user_has_permission(user, POST_SALE_MODULE[kind], "View")
    )


def visible_group_ids(db: Session, broker_id: int, user: User) -> set[int] | None:
    """Group ids the caller may list, or ``None`` when every group is visible.

    ``Groups.View = partial`` (the inspector) means "groups containing a case
    you can see" — the same narrowing the case-file router applies, one level up.
    """
    if not is_partial(resolve_role(user), "Groups", "View"):
        return None
    base = visible_cases(db, broker_id, user).subquery()
    rows = db.execute(
        select(base.c.account_group_id)
        .where(base.c.account_group_id.is_not(None))
        .group_by(base.c.account_group_id)
    ).all()
    return {int(row[0]) for row in rows}


# =============================================================================
# Small helpers
# =============================================================================

#: ``account_group.slug`` is String(80); the slug is the importer/backfill key.
SLUG_MAX = 80


def slugify_group_name(value: str) -> str:
    """The group get-or-create key — MUST stay identical everywhere.

    Fold accents (NFD, drop combining marks), lowercase, every run of
    non-alphanumerics becomes one ``-``, trim to 80. Byte-identical to
    ``scripts/backfill_groups.slugify`` on purpose: a backfilled RDS and a
    re-imported local database must agree on group identity
    (``GRUPO VIÑA INDÓMITA`` -> ``grupo-vina-indomita``). The importers (WP C1,
    C2) import THIS function; ``scripts/`` is not app code and must not be the
    source of truth.
    """
    folded = "".join(
        ch
        for ch in unicodedata.normalize("NFD", value or "")
        if unicodedata.category(ch) != "Mn"
    )
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug[:SLUG_MAX].strip("-") or "grupo"


def period_label_for(start: date | None, end: date | None) -> str | None:
    """``2026-2027`` from the folder's own dates (spec §2.3)."""
    if start is None or end is None:
        return None
    return f"{start.year}-{end.year}"


def _label_of(case: CaseFile) -> str:
    return (
        case.period_label
        or period_label_for(case.period_start, case.period_end)
        or UNSCHEDULED_PERIOD_LABEL
    )


def _sort_key_desc(value: date | None) -> tuple[int, str]:
    """Order by a nullable date descending with NULLs last, in Python.

    ``NULLS LAST`` is MySQL-only and ``col.is_(None)`` ordering would need a
    second ORDER BY term on every query; both result sets here are bounded, so
    the sort is done after the fetch.
    """
    if value is None:
        return (1, "")
    # Descending over an ascending sort: invert by using the ordinal.
    return (0, f"{9999 - value.year:04d}{12 - value.month:02d}{31 - value.day:02d}")


def _counts_by_key(rows: Iterable[Any]) -> dict[int, int]:
    return {int(key): int(count) for key, count in rows if key is not None}


# =============================================================================
# GET /navigator
# =============================================================================

def build_navigator(db: Session, user: User, broker_id: int) -> NavigatorResponse:
    """The main rail: groups + vigencias + the most recent folders."""
    allowed = visible_group_ids(db, broker_id, user)

    group_stmt = select(AccountGroup).where(AccountGroup.broker_id == broker_id)
    if allowed is not None:
        if not allowed:
            groups: list[AccountGroup] = []
        else:
            group_stmt = group_stmt.where(AccountGroup.id.in_(allowed))
            groups = list(db.scalars(group_stmt).all())
    else:
        groups = list(db.scalars(group_stmt).all())

    base = visible_cases(db, broker_id, user).subquery()

    # One grouped query for every (group, vigencia) pair: counts + the vigencia's
    # latest start. Every non-aggregated column is in GROUP BY.
    period_rows = db.execute(
        select(
            base.c.account_group_id,
            base.c.period_label,
            func.count(base.c.id),
            func.sum(sql_case((base.c.status == CaseFileStatus.OPEN, 1), else_=0)),
            func.max(base.c.period_start),
        )
        .where(
            base.c.account_group_id.is_not(None),
            base.c.kind.in_(list(ACCOUNT_KINDS)),
        )
        .group_by(base.c.account_group_id, base.c.period_label)
    ).all()

    per_group: dict[int, list[dict[str, Any]]] = defaultdict(list)
    open_counts: dict[int, int] = defaultdict(int)
    for group_id, label, count, open_count, latest_start in period_rows:
        per_group[int(group_id)].append(
            {
                "label": label or UNSCHEDULED_PERIOD_LABEL,
                "accounts_count": int(count),
                "latest_start": latest_start,
            }
        )
        open_counts[int(group_id)] += int(open_count or 0)

    payload_groups: list[NavigatorGroup] = []
    for group in groups:
        periods = sorted(
            per_group.get(group.id, []),
            key=lambda item: _sort_key_desc(as_date(item["latest_start"])),
        )
        latest = periods[0] if periods else None
        payload_groups.append(
            NavigatorGroup(
                id=group.id,
                name=group.name,
                slug=group.slug,
                accounts_count=sum(item["accounts_count"] for item in periods),
                open_count=open_counts.get(group.id, 0),
                latest_period_label=latest["label"] if latest else None,
                latest_period_start=as_date(latest["latest_start"]) if latest else None,
                periods=[
                    NavigatorPeriod(
                        label=item["label"], accounts_count=item["accounts_count"]
                    )
                    for item in periods[:MAX_NAVIGATOR_PERIODS]
                ],
            )
        )

    payload_groups.sort(
        key=lambda item: (_sort_key_desc(item.latest_period_start), item.name.lower())
    )
    payload_groups = payload_groups[:MAX_NAVIGATOR_GROUPS]

    recent_rows = db.execute(
        select(
            base.c.id,
            base.c.reference,
            base.c.title,
            base.c.account_group_id,
            base.c.period_label,
            base.c.period_start,
            base.c.period_end,
            base.c.stage,
            base.c.origin,
            InsuranceLine.name,
        )
        .join(InsuranceLine, InsuranceLine.id == base.c.insurance_line_id, isouter=True)
        .where(base.c.kind.in_(list(ACCOUNT_KINDS)))
        .order_by(base.c.updated_at.desc(), base.c.id.desc())
        .limit(MAX_NAVIGATOR_RECENT)
    ).all()

    recent = [
        NavigatorRecentCase(
            case_file_id=int(row[0]),
            reference=row[1],
            title=row[2],
            group_id=int(row[3]) if row[3] is not None else None,
            period_label=row[4]
            or period_label_for(as_date(row[5]), as_date(row[6])),
            line_name=row[9],
            stage=row[7],
            origin=row[8],
        )
        for row in recent_rows
    ]

    ungrouped = int(
        db.scalar(
            select(func.count(Client.id)).where(
                Client.broker_id == broker_id, Client.account_group_id.is_(None)
            )
        )
        or 0
    )

    return NavigatorResponse(
        groups=payload_groups,
        recent=recent,
        ungrouped_clients_count=ungrouped,
        record_folders=list(RECORD_FOLDERS),
    )


def as_date(value: Any) -> date | None:
    """A ``date`` from whatever the driver returned.

    Both engines hand a Date column back as ``date``, but a value selected out
    of a SUBQUERY can arrive as an ISO string on SQLite — and every aggregate
    here reads through one.
    """
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:  # pragma: no cover - defensive
        return None


# =============================================================================
# GET /account-groups/{id}/tree
# =============================================================================

def build_group_tree(
    db: Session, user: User, group_id: int, *, broker_id: int
) -> GroupTree:
    """The contextual rail. Raises :class:`GroupNotFound` outside the tenant."""
    group = db.scalars(
        select(AccountGroup).where(
            AccountGroup.id == group_id, AccountGroup.broker_id == broker_id
        )
    ).first()
    if group is None:
        raise GroupNotFound(group_id)

    allowed = visible_group_ids(db, broker_id, user)
    if allowed is not None and group.id not in allowed:
        raise GroupNotFound(group_id)

    client_rows = db.execute(
        select(Client.id, Insured.rut, Insured.legal_name)
        .join(Insured, Insured.id == Client.insured_id)
        .where(Client.broker_id == broker_id, Client.account_group_id == group.id)
        .order_by(Insured.legal_name.asc(), Client.id.asc())
    ).all()

    base = visible_cases(db, broker_id, user)
    accounts = list(
        db.scalars(
            base.where(
                CaseFile.account_group_id == group.id,
                CaseFile.kind.in_(ACCOUNT_KINDS),
            ).order_by(CaseFile.id.asc())
        ).all()
    )
    account_ids = [case.id for case in accounts]

    placements = _placements_of(db, broker_id, accounts)
    members = _members_of(db, account_ids)
    documents, record_counts = _document_counts(db, account_ids)
    quotes_count, quote_ids = _quote_counts(db, broker_id, accounts, placements)
    proposals_count = _proposal_counts(db, broker_id, quote_ids)
    packs_count = _counts_by_key(
        db.execute(
            select(CasePack.case_file_id, func.count(CasePack.id))
            .where(CasePack.case_file_id.in_(account_ids or [0]))
            .group_by(CasePack.case_file_id)
        ).all()
    )
    notes_count = _counts_by_key(
        db.execute(
            select(Note.entity_id, func.count(Note.id))
            .where(
                Note.broker_id == broker_id,
                Note.entity_type == EntityType.CASE_FILE,
                Note.entity_id.in_(account_ids or [0]),
            )
            .group_by(Note.entity_id)
        ).all()
    )
    locked = _locked_case_ids(db, account_ids)
    renewed_by = _renewed_by(db, broker_id, user, account_ids)

    policies_by_case, policy_ids = _policies_of(db, broker_id, accounts, placements)
    children_by_policy = _post_sale_children(db, broker_id, user, policy_ids)
    extended = _extended_policies(db, broker_id, policy_ids)

    lines = _line_names(db, broker_id, accounts)

    # --- Assemble -----------------------------------------------------------
    by_label: dict[str, list[CaseFile]] = defaultdict(list)
    for case in accounts:
        by_label[_label_of(case)].append(case)

    period_nodes: list[TreePeriodNode] = []
    for label, cases in by_label.items():
        starts = [c.period_start for c in cases if c.period_start is not None]
        ends = [c.period_end for c in cases if c.period_end is not None]
        line_nodes: list[TreeLineNode] = []
        for case in sorted(
            cases,
            key=lambda c: (
                (lines.get(c.insurance_line_id) or {}).get("name") or "",
                c.id,
            ),
        ):
            line = lines.get(case.insurance_line_id) or {}
            account_node = TreeAccountNode(
                case_file_id=case.id,
                reference=case.reference,
                kind=case.kind,
                stage=case.stage,
                status=case.status,
                origin=case.origin,
                origin_case_file_id=case.origin_case_file_id,
                renewed_by_case_file_id=renewed_by.get(case.id),
                period_start=case.period_start,
                period_end=case.period_end,
                period_locked=case.id in locked,
                client_ids=members.get(case.id, []) or _fallback_members(case, placements),
                placement_ids=[p.id for p in placements.get(case.id, [])],
                documents_count=documents.get(case.id, 0),
                record_counts=record_counts.get(
                    case.id, {folder.key: 0 for folder in RECORD_FOLDERS}
                ),
                quotes_count=quotes_count.get(case.id, 0),
                proposals_count=proposals_count.get(case.id, 0),
                packs_count=packs_count.get(case.id, 0),
                notes_count=notes_count.get(case.id, 0),
            )
            policy_nodes = [
                TreePolicyNode(
                    id=policy.id,
                    policy_number=policy.policy_number,
                    insurer_name=insurer_name,
                    status=policy.status,
                    start_date=policy.start_date,
                    end_date=policy.end_date,
                    extended_end_date=policy.end_date if policy.id in extended else None,
                    children=children_by_policy.get(policy.id, TreePolicyChildren()),
                )
                for policy, insurer_name in policies_by_case.get(case.id, [])
            ]
            line_nodes.append(
                TreeLineNode(
                    insurance_line_id=case.insurance_line_id or 0,
                    name=line.get("name") or "—",
                    requires_inspection=bool(line.get("requires_inspection")),
                    account=account_node,
                    policies=policy_nodes,
                    renewal=_renewal_node(
                        db,
                        case=case,
                        renewed_by=renewed_by.get(case.id),
                        siblings=accounts,
                    ),
                )
            )
        period_nodes.append(
            TreePeriodNode(
                label=label,
                start=min(starts) if starts else None,
                end=max(ends) if ends else None,
                is_latest=False,
                lines=line_nodes,
            )
        )

    period_nodes.sort(key=lambda node: _sort_key_desc(node.start))
    if period_nodes:
        period_nodes[0].is_latest = True

    return GroupTree(
        group=TreeGroup(
            id=group.id,
            name=group.name,
            status=group.status,
            clients=[
                TreeClient(id=int(cid), rut=rut, legal_name=legal_name)
                for cid, rut, legal_name in client_rows
            ],
        ),
        periods=period_nodes,
    )


# --- Tree sub-queries ---------------------------------------------------------

def _placements_of(
    db: Session, broker_id: int, accounts: list[CaseFile]
) -> dict[int, list[Placement]]:
    """Placements of each folder: ``placement.case_file_id`` is the authority
    (one folder -> N placements, spec §2.5)."""
    ids = [case.id for case in accounts]
    if not ids:
        return {}
    rows = db.scalars(
        select(Placement)
        .where(Placement.broker_id == broker_id, Placement.case_file_id.in_(ids))
        .order_by(Placement.id.asc())
    ).all()
    grouped: dict[int, list[Placement]] = defaultdict(list)
    for placement in rows:
        grouped[int(placement.case_file_id)].append(placement)
    # The legacy single-placement link stays authoritative when it is not
    # mirrored on the placement row yet (pre-backfill databases).
    for case in accounts:
        if case.placement_id is not None and not any(
            p.id == case.placement_id for p in grouped.get(case.id, [])
        ):
            placement = db.get(Placement, case.placement_id)
            if placement is not None and placement.broker_id == broker_id:
                grouped[case.id].append(placement)
    return grouped


def _members_of(db: Session, account_ids: list[int]) -> dict[int, list[int]]:
    if not account_ids:
        return {}
    rows = db.execute(
        select(AccountClient.case_file_id, AccountClient.client_id)
        .where(AccountClient.case_file_id.in_(account_ids))
        .order_by(AccountClient.is_primary.desc(), AccountClient.id.asc())
    ).all()
    grouped: dict[int, list[int]] = defaultdict(list)
    for case_id, client_id in rows:
        if int(client_id) not in grouped[int(case_id)]:
            grouped[int(case_id)].append(int(client_id))
    return grouped


def _fallback_members(
    case: CaseFile, placements: dict[int, list[Placement]]
) -> list[int]:
    """Membership = ``account_client`` ∪ placements' clients ∪ the contratante."""
    ids: list[int] = []
    if case.client_id is not None:
        ids.append(case.client_id)
    for placement in placements.get(case.id, []):
        if placement.client_id not in ids:
            ids.append(placement.client_id)
    return ids


def _document_counts(
    db: Session, account_ids: list[int]
) -> tuple[dict[int, int], dict[int, dict[str, int]]]:
    empty = {folder.key: 0 for folder in RECORD_FOLDERS}
    if not account_ids:
        return {}, {}
    rows = db.execute(
        select(Document.case_file_id, Document.category, func.count(Document.id))
        .where(Document.case_file_id.in_(account_ids))
        .group_by(Document.case_file_id, Document.category)
    ).all()
    totals: dict[int, int] = defaultdict(int)
    folders: dict[int, dict[str, int]] = {
        case_id: dict(empty) for case_id in account_ids
    }
    for case_id, category, count in rows:
        totals[int(case_id)] += int(count)
        key = RECORD_FOLDER_BY_CATEGORY.get(category)
        if key is not None:
            folders[int(case_id)][key] += int(count)
    return dict(totals), folders


def _quote_counts(
    db: Session,
    broker_id: int,
    accounts: list[CaseFile],
    placements: dict[int, list[Placement]],
) -> tuple[dict[int, int], dict[int, list[int]]]:
    """Quotes of a folder: by ``case_file_id`` or through its placements.

    Counted in Python because a row can match on either column — a single
    ``GROUP BY`` would have to pick one and would drop the other.
    """
    if not accounts:
        return {}, {}
    placement_to_case = {
        placement.id: case.id
        for case in accounts
        for placement in placements.get(case.id, [])
    }
    account_ids = [case.id for case in accounts]
    rows = db.execute(
        select(QuoteRequest.id, QuoteRequest.case_file_id, QuoteRequest.placement_id)
        .where(
            QuoteRequest.broker_id == broker_id,
            or_(
                QuoteRequest.case_file_id.in_(account_ids),
                QuoteRequest.placement_id.in_(list(placement_to_case) or [0]),
            ),
        )
    ).all()
    counts: dict[int, int] = defaultdict(int)
    quote_ids: dict[int, list[int]] = defaultdict(list)
    for quote_id, case_file_id, placement_id in rows:
        case_id = (
            int(case_file_id)
            if case_file_id is not None and int(case_file_id) in set(account_ids)
            else placement_to_case.get(int(placement_id) if placement_id else 0)
        )
        if case_id is None:
            continue
        counts[case_id] += 1
        quote_ids[case_id].append(int(quote_id))
    return dict(counts), dict(quote_ids)


def _proposal_counts(
    db: Session, broker_id: int, quote_ids: dict[int, list[int]]
) -> dict[int, int]:
    flat = [qid for ids in quote_ids.values() for qid in ids]
    if not flat:
        return {}
    rows = db.execute(
        select(Proposal.quote_request_id, func.count(Proposal.id))
        .where(Proposal.broker_id == broker_id, Proposal.quote_request_id.in_(flat))
        .group_by(Proposal.quote_request_id)
    ).all()
    by_quote = _counts_by_key(rows)
    return {
        case_id: sum(by_quote.get(qid, 0) for qid in ids)
        for case_id, ids in quote_ids.items()
    }


def _locked_case_ids(db: Session, account_ids: list[int]) -> set[int]:
    """Folders with a stage event beyond ``intake`` — the period is a folder now.

    The same predicate ``POST /case-files/{id}/reperiod`` enforces (WP B2);
    the tree only *displays* it, so a local read is correct and keeps the
    navigator free of a cross-WP import.
    """
    if not account_ids:
        return set()
    rows = db.execute(
        select(CaseFileStageEvent.case_file_id)
        .where(
            CaseFileStageEvent.case_file_id.in_(account_ids),
            CaseFileStageEvent.to_stage.not_in(list(_UNLOCKED_STAGES)),
        )
        .group_by(CaseFileStageEvent.case_file_id)
    ).all()
    return {int(row[0]) for row in rows}


def _renewed_by(
    db: Session, broker_id: int, user: User, account_ids: list[int]
) -> dict[int, int]:
    """``origin_case_file_id -> the folder that renewed it``."""
    if not account_ids:
        return {}
    rows = db.execute(
        visible_cases(db, broker_id, user)
        .with_only_columns(CaseFile.origin_case_file_id, CaseFile.id)
        .where(
            CaseFile.origin_case_file_id.in_(account_ids),
            CaseFile.kind.in_(ACCOUNT_KINDS),
        )
        .order_by(CaseFile.id.asc())
    ).all()
    return {int(source): int(case_id) for source, case_id in rows}


def _policies_of(
    db: Session,
    broker_id: int,
    accounts: list[CaseFile],
    placements: dict[int, list[Placement]],
) -> tuple[dict[int, list[tuple[Policy, str | None]]], list[int]]:
    if not accounts:
        return {}, []
    account_ids = [case.id for case in accounts]
    placement_to_case = {
        placement.id: case.id
        for case in accounts
        for placement in placements.get(case.id, [])
    }
    rows = db.execute(
        select(Policy, Insurer.legal_name)
        .join(Insurer, Insurer.id == Policy.insurer_id, isouter=True)
        .where(
            Policy.broker_id == broker_id,
            or_(
                Policy.case_file_id.in_(account_ids),
                Policy.placement_id.in_(list(placement_to_case) or [0]),
            ),
        )
        .order_by(Policy.id.asc())
    ).all()
    grouped: dict[int, list[tuple[Policy, str | None]]] = defaultdict(list)
    ids: list[int] = []
    for policy, insurer_name in rows:
        case_id = (
            int(policy.case_file_id)
            if policy.case_file_id is not None
            and int(policy.case_file_id) in set(account_ids)
            else placement_to_case.get(policy.placement_id or 0)
        )
        if case_id is None:
            continue
        grouped[case_id].append((policy, insurer_name))
        ids.append(policy.id)
    return dict(grouped), ids


def _post_sale_children(
    db: Session, broker_id: int, user: User, policy_ids: list[int]
) -> dict[int, TreePolicyChildren]:
    """The ENDOSO / COBRANZA / SINIESTROS leaves, gated per module."""
    kinds = _post_sale_kinds_for(user)
    if not policy_ids or not kinds:
        return {}
    cases = list(
        db.scalars(
            visible_cases(db, broker_id, user)
            .where(CaseFile.policy_id.in_(policy_ids), CaseFile.kind.in_(kinds))
            .order_by(
                CaseFile.sequence_no.asc(), CaseFile.version.asc(), CaseFile.id.asc()
            )
        ).all()
    )
    if not cases:
        return {}

    meta_rows = db.execute(
        select(Endorsement.case_file_id, Endorsement.effective_at, Endorsement.batch_key)
        .where(
            Endorsement.broker_id == broker_id,
            Endorsement.case_file_id.in_([case.id for case in cases]),
        )
    ).all()
    meta = {
        int(case_id): (effective_at, batch_key)
        for case_id, effective_at, batch_key in meta_rows
        if case_id is not None
    }

    grouped: dict[int, TreePolicyChildren] = {}
    for case in cases:
        children = grouped.setdefault(int(case.policy_id), TreePolicyChildren())
        effective_at, batch_key = meta.get(case.id, (None, None))
        node = TreePostSaleNode(
            case_file_id=case.id,
            reference=case.reference,
            sequence_no=case.sequence_no,
            version=case.version,
            stage=case.stage,
            status=case.status,
            effective_at=effective_at,
            batch_key=batch_key,
        )
        getattr(children, case.kind.value).append(node)
    return grouped


def _extended_policies(db: Session, broker_id: int, policy_ids: list[int]) -> set[int]:
    """Policies whose ``end_date`` was moved by a prórroga ("prorrogada hasta …")."""
    if not policy_ids:
        return set()
    rows = db.execute(
        select(Endorsement.policy_id)
        .where(
            Endorsement.broker_id == broker_id,
            Endorsement.policy_id.in_(policy_ids),
            Endorsement.kind == EndorsementKind.PERIOD_EXTENSION,
            Endorsement.status.in_(
                [EndorsementStatus.ISSUED, EndorsementStatus.APPLIED]
            ),
        )
        .group_by(Endorsement.policy_id)
    ).all()
    return {int(row[0]) for row in rows}


def _line_names(
    db: Session, broker_id: int, accounts: list[CaseFile]
) -> dict[int, dict[str, Any]]:
    ids = {case.insurance_line_id for case in accounts if case.insurance_line_id}
    if not ids:
        return {}
    rows = db.execute(
        select(
            InsuranceLine.id, InsuranceLine.name, InsuranceLine.requires_inspection
        ).where(InsuranceLine.id.in_(ids))
    ).all()
    return {
        int(line_id): {"name": name, "requires_inspection": bool(requires)}
        for line_id, name, requires in rows
    }


def _renewal_node(
    db: Session, *, case: CaseFile, renewed_by: int | None, siblings: list[CaseFile]
) -> TreeRenewal:
    """Whether ``POST /case-files/{id}/renew`` would be accepted, and why not.

    The three reasons are exhaustive and checked in this order:
      ``already_renewed`` — a folder already points here through
      ``origin_case_file_id``; ``account_not_active`` — the vigencia has not
      reached a renewable stage; ``period_open`` — a later vigencia of the same
      ramo is already open, so the renewal exists under another origin.
    """
    if renewed_by is not None:
        return TreeRenewal(case_file_id=renewed_by, allowed=False, reason="already_renewed")
    if case.stage not in RENEWABLE_STAGES:
        return TreeRenewal(allowed=False, reason="account_not_active")
    if case.period_start is not None and any(
        other.id != case.id
        and other.insurance_line_id == case.insurance_line_id
        and other.status == CaseFileStatus.OPEN
        and other.period_start is not None
        and other.period_start > case.period_start
        for other in siblings
    ):
        return TreeRenewal(allowed=False, reason="period_open")
    return TreeRenewal(allowed=True, reason=None)
