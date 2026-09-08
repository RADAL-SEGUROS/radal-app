"""Backfill the GROUPS & ACCOUNTS shape on a LIVE database — dry-run first.

Why this exists
---------------
``scripts/migrate_case_files.py`` adds the *columns*
(``account_group``/``account_client`` and the period/origin axes of
``case_file``); it never invents data. A database that will be re-imported
(``import_fixtures`` + ``import_expedientes``) gets its groups from the
importers. A database that will NOT be re-imported — the dev RDS with real
work in it — needs this script, which derives the groups pass from what is
already there and writes nothing that is not implied by existing rows
(docs/v3-groups-accounts-spec.md §3.1).

What it derives, per broker, in this order
------------------------------------------
1. **client -> group**: name from ``client.source`` when set, else
   ``insured.trade_name``, else ``insured.legal_name``. Get-or-create
   ``account_group`` by ``(broker_id, slug)`` — the same idempotency key the
   importers use — and set ``client.account_group_id``.
2. **account folder -> group**: ``case_file.meta["commercial_account"]`` when
   present (a genuine group name that may span RUTs, e.g. GRUPO VIÑA INDÓMITA),
   else the client's group.
3. **period**: ``period_start``/``period_end`` copied from the folder's primary
   placement (``case_file.placement_id``, else the first placement pointing at
   the folder), ``period_label = f"{start.year}-{end.year}"``.
4. **membership**: one ``account_client`` row for ``case_file.client_id``
   (``is_primary=True``, ``role=policyholder`` — the contratante).
5. **post-sale children** (``endorsement|collection|claim``) inherit
   ``account_group_id`` and the three period fields from their policy's account
   folder, so the tree never has to join through ``policy`` for counts.
6. **renewal folders with no placement** are rebuilt as §3.3 row 16 describes:
   ``origin=renewal``, ``origin_case_file_id=<source account>``,
   ``policy_id=NULL``, ``parent_case_file_id=NULL``, the source's placements
   cloned into the next period as ``draft`` and attached to the folder, and the
   source's membership copied.

Nothing is ever deleted, no group is merged, and no non-NULL value is
overwritten: every step only fills a hole, so a second run is a no-op. That is
also why the report reads as "N would change" rather than "N changed".

Modes
-----
* Default / ``--dry-run``: print the plan; exit 1 when work is pending (so it
  can gate CI), 0 when the database is already backfilled.
* ``--apply``: run it in ONE transaction, then re-derive and verify zero
  pending work.
* ``--broker-id N``: restrict to one tenant (default: every broker).

Usage (from backend/, venv active)
----------------------------------
    python -m scripts.backfill_groups                      # dry run vs DATABASE_URL
    python -m scripts.backfill_groups --apply
    python -m scripts.backfill_groups --url mysql+pymysql://user:pw@host/radal --apply

Run it AFTER ``scripts/migrate_case_files.py --apply`` (the columns must exist)
and never on a database that is about to be re-imported. No secrets are
printed: URLs are rendered with the password hidden.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, ".")  # allow `python -m scripts.backfill_groups` from backend/

from app.models.account_client import AccountClient, AccountClientRole  # noqa: E402
from app.models.account_group import AccountGroup  # noqa: E402
from app.models.case_file import CaseFile  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.enums import CaseFileKind, CaseOrigin  # noqa: E402
from app.models.insured import Insured  # noqa: E402
from app.models.placement import Placement, PlacementStatus  # noqa: E402
from app.models.policy import Policy  # noqa: E402

ACCOUNT_KINDS = (CaseFileKind.ACCOUNT, CaseFileKind.RENEWAL)
POST_SALE_KINDS = (CaseFileKind.ENDORSEMENT, CaseFileKind.COLLECTION, CaseFileKind.CLAIM)

# ``account_group.slug`` is String(80); the same budget the importers use.
SLUG_MAX = 80


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def slugify(value: str) -> str:
    """Accent-folded, lowercase, ``-``-joined — the get-or-create key.

    Must produce the SAME string as the importers for the same name, or a
    backfilled database and a re-imported one would disagree on group identity:
    fold accents (NFD, drop combining marks), lowercase, every run of
    non-alphanumerics becomes one ``-``, trim to 80.
    """
    folded = "".join(
        ch for ch in unicodedata.normalize("NFD", value or "") if unicodedata.category(ch) != "Mn"
    )
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug[:SLUG_MAX].strip("-") or "grupo"


def period_label_for(start: date | None, end: date | None) -> str | None:
    """``2026-2027``. A label only — the authoritative dates are the columns."""
    if start is None or end is None:
        return None
    return f"{start.year}-{end.year}"


def _plus_one_year(value: date) -> date:
    """Same day next year; 29-feb folds to 28-feb rather than raising."""
    try:
        return value.replace(year=value.year + 1)
    except ValueError:  # 29 February
        return value.replace(year=value.year + 1, day=28)


@dataclass
class Report:
    """What the run would change / did change, grouped for a readable plan."""

    groups_created: list[str] = field(default_factory=list)
    clients_linked: list[str] = field(default_factory=list)
    cases_linked: list[str] = field(default_factory=list)
    periods_filled: list[str] = field(default_factory=list)
    members_created: list[str] = field(default_factory=list)
    children_inherited: list[str] = field(default_factory=list)
    renewals_rebuilt: list[str] = field(default_factory=list)
    placements_created: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    SECTIONS = (
        ("groups_created", "account_group created"),
        ("clients_linked", "client.account_group_id set"),
        ("cases_linked", "case_file.account_group_id set"),
        ("periods_filled", "case_file period_start/end/label filled"),
        ("members_created", "account_client created"),
        ("children_inherited", "post-sale child inherited group + period"),
        ("renewals_rebuilt", "renewal folder rebuilt (origin/links)"),
        ("placements_created", "placement cloned for a renewal folder"),
    )

    @property
    def total(self) -> int:
        return sum(len(getattr(self, name)) for name, _ in self.SECTIONS)

    def print(self, *, applied: bool) -> None:
        verb = "applied" if applied else "would change"
        if not self.total:
            print("plan: nothing to do — the groups shape is already backfilled.")
        else:
            print(f"plan: {self.total} change(s) {verb}")
            for name, title in self.SECTIONS:
                rows = getattr(self, name)
                if not rows:
                    continue
                print(f"\n-- {title} ({len(rows)})")
                for row in rows:
                    print(f"   {row}")
        for note in self.notes:
            print(f"\nnote: {note}")


# ---------------------------------------------------------------------------
# The backfill itself — one function, flushes only; the caller owns the commit
# ---------------------------------------------------------------------------
def backfill(session: Session, *, broker_id: int | None = None) -> Report:
    """Derive the groups shape. Writes to the session; NEVER commits."""
    report = Report()

    broker_ids = session.scalars(
        select(Client.broker_id).distinct().union(select(CaseFile.broker_id).distinct())
    ).all()
    broker_ids = sorted({b for b in broker_ids if b is not None})
    if broker_id is not None:
        if broker_id not in broker_ids:
            report.notes.append(f"broker {broker_id} owns no clients or case files.")
            return report
        broker_ids = [broker_id]

    for current_broker in broker_ids:
        _backfill_broker(session, current_broker, report)
    return report


def _backfill_broker(session: Session, broker_id: int, report: Report) -> None:
    # Cache of (broker_id, slug) -> AccountGroup for this tenant. Groups are
    # broker-private, so the cache never crosses the tenant boundary.
    cache: dict[str, AccountGroup] = {
        group.slug: group
        for group in session.scalars(
            select(AccountGroup).where(AccountGroup.broker_id == broker_id)
        )
    }

    def get_or_create_group(name: str) -> AccountGroup:
        slug = slugify(name)
        existing = cache.get(slug)
        if existing is not None:
            return existing
        group = AccountGroup(broker_id=broker_id, name=name.strip(), slug=slug)
        session.add(group)
        session.flush()
        cache[slug] = group
        report.groups_created.append(f"broker {broker_id}: {group.name!r} (slug {slug})")
        return group

    # --- 1. client -> group -------------------------------------------------
    clients = session.scalars(
        select(Client).where(Client.broker_id == broker_id).order_by(Client.id)
    ).all()
    for client in clients:
        if client.account_group_id is not None:
            continue
        insured = session.get(Insured, client.insured_id)
        name = (
            (client.source or "").strip()
            or ((insured.trade_name or "").strip() if insured else "")
            or ((insured.legal_name or "").strip() if insured else "")
        )
        if not name:
            report.notes.append(
                f"client {client.id} (broker {broker_id}) has no source and no insured "
                "name — left ungrouped; name the group by hand."
            )
            continue
        group = get_or_create_group(name)
        client.account_group_id = group.id
        report.clients_linked.append(f"client {client.id} -> group {group.id} ({group.slug})")

    session.flush()
    client_by_id = {client.id: client for client in clients}

    cases = session.scalars(
        select(CaseFile).where(CaseFile.broker_id == broker_id).order_by(CaseFile.id)
    ).all()
    case_by_id = {case.id: case for case in cases}

    placements_by_case: dict[int, list[Placement]] = {}
    for placement in session.scalars(
        select(Placement).where(Placement.broker_id == broker_id).order_by(Placement.id)
    ):
        if placement.case_file_id is not None:
            placements_by_case.setdefault(placement.case_file_id, []).append(placement)

    members = {
        (member.case_file_id, member.client_id)
        for member in session.scalars(
            select(AccountClient).where(AccountClient.broker_id == broker_id)
        )
    }

    # --- 2/3/4. account + renewal folders ----------------------------------
    for case in cases:
        if case.kind not in ACCOUNT_KINDS:
            continue

        if case.account_group_id is None:
            name = ((case.meta or {}).get("commercial_account") or "").strip()
            group = None
            if name:
                group = get_or_create_group(name)
            else:
                client = client_by_id.get(case.client_id)
                if client is not None and client.account_group_id is not None:
                    group = session.get(AccountGroup, client.account_group_id)
            if group is None:
                report.notes.append(
                    f"case {case.id} ({case.reference}) has no commercial_account and its "
                    "client is ungrouped — left ungrouped."
                )
            else:
                case.account_group_id = group.id
                report.cases_linked.append(
                    f"case {case.id} ({case.reference}) -> group {group.id} ({group.slug})"
                )

        if case.period_start is None or case.period_end is None:
            source = _primary_placement(case, placements_by_case)
            if source is not None and (source.period_start or source.period_end):
                case.period_start = case.period_start or source.period_start
                case.period_end = case.period_end or source.period_end
                case.period_label = case.period_label or period_label_for(
                    case.period_start, case.period_end
                ) or source.period
                report.periods_filled.append(
                    f"case {case.id} ({case.reference}) -> "
                    f"{case.period_start}..{case.period_end} ({case.period_label})"
                )
        elif case.period_label is None:
            case.period_label = period_label_for(case.period_start, case.period_end)
            report.periods_filled.append(
                f"case {case.id} ({case.reference}) -> label {case.period_label}"
            )

        if case.origin is None:  # only possible on a hand-patched schema
            case.origin = CaseOrigin.NEW

        if (case.id, case.client_id) not in members:
            session.add(
                AccountClient(
                    broker_id=broker_id,
                    case_file_id=case.id,
                    client_id=case.client_id,
                    role=AccountClientRole.POLICYHOLDER,
                    is_primary=True,
                )
            )
            members.add((case.id, case.client_id))
            report.members_created.append(
                f"case {case.id} ({case.reference}) <- client {case.client_id} (primary)"
            )

    session.flush()

    # --- 5. post-sale children inherit from their policy's account ---------
    for case in cases:
        if case.kind not in POST_SALE_KINDS:
            continue
        parent = _account_of_child(session, case, case_by_id)
        if parent is None:
            report.notes.append(
                f"case {case.id} ({case.reference}) is a post-sale child with no resolvable "
                "account folder — left as is."
            )
            continue
        changed = []
        if case.account_group_id is None and parent.account_group_id is not None:
            case.account_group_id = parent.account_group_id
            changed.append(f"group {parent.account_group_id}")
        if case.period_start is None and parent.period_start is not None:
            case.period_start = parent.period_start
            changed.append("period_start")
        if case.period_end is None and parent.period_end is not None:
            case.period_end = parent.period_end
            changed.append("period_end")
        if case.period_label is None and parent.period_label is not None:
            case.period_label = parent.period_label
            changed.append("period_label")
        if changed:
            report.children_inherited.append(
                f"case {case.id} ({case.reference}) <- case {parent.id}: " + ", ".join(changed)
            )

    session.flush()

    # --- 6. renewal folders with no placement ------------------------------
    for case in cases:
        if case.kind is not CaseFileKind.RENEWAL:
            continue
        if placements_by_case.get(case.id):
            continue
        _rebuild_renewal(session, case, case_by_id, placements_by_case, members, report)

    session.flush()


def _primary_placement(
    case: CaseFile, placements_by_case: dict[int, list[Placement]]
) -> Placement | None:
    """``case_file.placement_id`` is the primary; else the lowest-id member."""
    attached = placements_by_case.get(case.id) or []
    if case.placement_id is not None:
        for placement in attached:
            if placement.id == case.placement_id:
                return placement
    return attached[0] if attached else None


def _account_of_child(
    session: Session, case: CaseFile, case_by_id: dict[int, CaseFile]
) -> CaseFile | None:
    """The account folder a post-sale child hangs off.

    ``parent_case_file_id`` is the authority; the policy's own
    ``case_file_id`` is the fallback for rows written before it was set.
    """
    parent = case_by_id.get(case.parent_case_file_id or 0)
    if parent is not None and parent.kind in ACCOUNT_KINDS:
        return parent
    if case.policy_id is not None:
        policy = session.get(Policy, case.policy_id)
        if policy is not None and policy.case_file_id is not None:
            candidate = case_by_id.get(policy.case_file_id)
            if candidate is not None and candidate.kind in ACCOUNT_KINDS:
                return candidate
    return None


def _rebuild_renewal(
    session: Session,
    case: CaseFile,
    case_by_id: dict[int, CaseFile],
    placements_by_case: dict[int, list[Placement]],
    members: set[tuple[int, int]],
    report: Report,
) -> None:
    """A renewal folder is a SIBLING of the prior vigencia, never its child.

    Pre-groups data modelled it as a post-sale child on the policy
    (``parent_case_file_id`` + ``policy_id`` set, no placement). §3.3 row 16
    restates it: origin axis instead of post-sale axis, its own placements in
    the next period.
    """
    source = _account_of_child(session, case, case_by_id)
    if source is None:
        report.notes.append(
            f"case {case.id} ({case.reference}) is a renewal with no resolvable source "
            "account — left as is."
        )
        return

    changed = []
    if case.origin is not CaseOrigin.RENEWAL:
        case.origin = CaseOrigin.RENEWAL
        changed.append("origin=renewal")
    if case.origin_case_file_id != source.id:
        case.origin_case_file_id = source.id
        changed.append(f"origin_case_file_id={source.id}")
    if case.parent_case_file_id is not None:
        case.parent_case_file_id = None
        changed.append("parent_case_file_id=NULL")
    if case.policy_id is not None:
        case.policy_id = None
        changed.append("policy_id=NULL")

    # The renewal runs the period AFTER the source's, unless it already states one.
    if case.period_start is None and source.period_end is not None:
        case.period_start = source.period_end
        case.period_end = _plus_one_year(source.period_end)
        case.period_label = period_label_for(case.period_start, case.period_end)
        changed.append(f"period={case.period_start}..{case.period_end}")

    if changed:
        report.renewals_rebuilt.append(
            f"case {case.id} ({case.reference}) <- source {source.id}: " + ", ".join(changed)
        )

    # Clone the source's placements into the new period, as drafts.
    label = case.period_label or period_label_for(case.period_start, case.period_end)
    for origin_placement in placements_by_case.get(source.id) or []:
        clone = Placement(
            broker_id=case.broker_id,
            client_id=origin_placement.client_id,
            asset_id=origin_placement.asset_id,
            insurance_line_id=origin_placement.insurance_line_id,
            period=label,
            period_start=case.period_start,
            period_end=case.period_end,
            status=PlacementStatus.DRAFT,
            case_file_id=case.id,
        )
        session.add(clone)
        session.flush()
        placements_by_case.setdefault(case.id, []).append(clone)
        if case.placement_id is None:
            case.placement_id = clone.id
        report.placements_created.append(
            f"placement {clone.id} (asset {clone.asset_id}, line {clone.insurance_line_id}) "
            f"-> case {case.id} ({case.reference})"
        )

    # Membership follows the folder, not the policy.
    for member_case_id, member_client_id in sorted(members):
        if member_case_id != source.id:
            continue
        if (case.id, member_client_id) in members:
            continue
        session.add(
            AccountClient(
                broker_id=case.broker_id,
                case_file_id=case.id,
                client_id=member_client_id,
                role=AccountClientRole.POLICYHOLDER
                if member_client_id == case.client_id
                else AccountClientRole.INSURED,
                is_primary=member_client_id == case.client_id,
            )
        )
        members.add((case.id, member_client_id))
        report.members_created.append(
            f"case {case.id} ({case.reference}) <- client {member_client_id} (copied from "
            f"case {source.id})"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def resolve_url(cli_url: str | None) -> str:
    if cli_url:
        return cli_url
    from app.db.session import DATABASE_URL

    return DATABASE_URL


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill account groups, account periods and account membership on a "
                    "live database (dry run by default; exits 1 when work is pending)."
    )
    parser.add_argument("--url", help="database URL (default: the app's DATABASE_URL)")
    parser.add_argument("--apply", action="store_true",
                        help="execute the backfill (default: print the plan and exit)")
    parser.add_argument("--dry-run", action="store_true",
                        help="explicit dry run (the default; cannot be combined with --apply)")
    parser.add_argument("--broker-id", type=int,
                        help="restrict to one tenant (default: every broker)")
    args = parser.parse_args()

    if args.apply and args.dry_run:
        print("error: --apply and --dry-run are mutually exclusive.")
        return 2

    url = resolve_url(args.url)
    engine = create_engine(url, future=True)
    print(f"database: {engine.url.render_as_string(hide_password=True)} "
          f"(dialect: {engine.dialect.name})")

    with Session(engine, future=True) as session:
        report = backfill(session, broker_id=args.broker_id)
        report.print(applied=False)

        if not report.total:
            session.rollback()
            return 0
        if not args.apply:
            session.rollback()
            print("\ndry run: nothing was written — re-run with --apply to execute the plan above.")
            return 1

        session.commit()
        print("\napply: done.")

    # Re-derive on a fresh session: the second run must be a no-op.
    with Session(engine, future=True) as session:
        verify = backfill(session, broker_id=args.broker_id)
        session.rollback()
        if verify.total:
            print(f"error: {verify.total} change(s) still pending after apply:")
            verify.print(applied=False)
            return 2
    print("verify: idempotent — a second run would change nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
