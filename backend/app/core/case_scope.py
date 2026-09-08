"""Case-file visibility scopes — the ONE mechanism behind every ``partial``
``CaseFiles.View`` grant (spec v3 §6, binary rule 8).

Why this module exists
----------------------
Before the v3 pass the narrowing lived inline in
``app.api.routers.case_files._visible()`` as a single ``if inspector`` branch,
and ``get_case_or_404()`` applied **nothing** — so the packs and documents
routers, which fetch a case by id through that helper, would happily serve a
case the caller can never see in its own list. One mechanism, applied in both
places, closes that hole and makes adding a process profile a data change.

``CASE_VIEW_SCOPE`` maps a role key to a callable that narrows a
``Select`` over ``CaseFile``. It is consulted **only** when the role's
``CaseFiles.View`` grant is ``partial`` — a ``yes`` sees the whole tenant and a
``no`` never reaches a router at all. A role that is ``partial`` but registers
no scope **fails closed** (sees nothing), matching the fail-closed posture of
``roles_config.grant_for``: a half-configured profile must not silently widen.

Multi-tenancy is NOT expressed here. ``broker_id`` filtering is always applied
on top by the caller — a scope narrows *within* one tenant, never across.
"""
from __future__ import annotations

from typing import Callable, TypeAlias

from sqlalchemy import Select, false, or_, select
from sqlalchemy.orm import aliased

from app.core.roles_config import is_partial
from app.models.case_file import CaseFile
from app.models.enums import CaseFileKind
from app.models.inspection import Inspection

#: A narrowing callable: ``(stmt, broker_id) -> stmt``.
CaseScope: TypeAlias = Callable[[Select, int], Select]


def _inspection_scope(stmt: Select, broker_id: int) -> Select:
    """``broker_inspector``: only cases that have an inspection attached.

    Unchanged from the case-files pass — this is the reference implementation
    of a ``partial`` grant narrowed in a router.
    """
    return stmt.where(
        CaseFile.id.in_(
            select(Inspection.case_file_id).where(
                Inspection.broker_id == broker_id,
                Inspection.case_file_id.is_not(None),
            )
        )
    )


def _kind_scope(kind: CaseFileKind) -> CaseScope:
    """Build a scope for a post-sale process profile.

    The profile sees its own post-sale cases **plus the account folders those
    cases hang off** — without the parent the collections desk could open a
    cobranza but never read the policy's expediente it belongs to, which makes
    the folder unusable. Nothing else of the tenant is visible.
    """

    def _scope(stmt: Select, broker_id: int) -> Select:
        # Alias the self-join: an un-aliased inner SELECT over the same table
        # would auto-correlate and lose its FROM.
        child = aliased(CaseFile)
        parents = select(child.parent_case_file_id).where(
            child.broker_id == broker_id,
            child.kind == kind,
            child.parent_case_file_id.is_not(None),
        )
        return stmt.where(or_(CaseFile.kind == kind, CaseFile.id.in_(parents)))

    return _scope


def _deny_all(stmt: Select, broker_id: int) -> Select:
    """Fail-closed fallback for a ``partial`` role with no registered scope."""
    return stmt.where(false())


#: role -> narrowing callable. Consulted only for a ``partial`` CaseFiles.View.
CASE_VIEW_SCOPE: dict[str, CaseScope] = {
    "broker_inspector": _inspection_scope,
    "broker_collections": _kind_scope(CaseFileKind.COLLECTION),
    "broker_claims": _kind_scope(CaseFileKind.CLAIM),
}


def case_view_scope(role: str | None) -> CaseScope | None:
    """The scope for ``role``, or ``None`` when it sees the whole tenant.

    ``None`` means "no narrowing" (grant is ``yes``, or the role cannot reach
    the module at all and was already refused by ``require_permission``).
    """
    if not role:
        return None
    if not is_partial(role, "CaseFiles", "View"):
        return None
    return CASE_VIEW_SCOPE.get(role, _deny_all)


def apply_case_view_scope(stmt: Select, *, broker_id: int, role: str | None) -> Select:
    """Narrow a ``Select`` over ``CaseFile`` for the caller's role."""
    scope = case_view_scope(role)
    return stmt if scope is None else scope(stmt, broker_id)


def scope_case_query(stmt: Select, *, broker_id: int, user) -> Select:
    """``apply_case_view_scope`` taking the ``User`` row instead of the role."""
    from app.core.permissions import resolve_role  # noqa: PLC0415  (import cycle)

    if user is None:
        return stmt
    return apply_case_view_scope(stmt, broker_id=broker_id, role=resolve_role(user))


__all__ = [
    "CaseScope",
    "CASE_VIEW_SCOPE",
    "case_view_scope",
    "apply_case_view_scope",
    "scope_case_query",
]
