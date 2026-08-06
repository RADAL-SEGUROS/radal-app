"""RBAC enforcement layer for Radal v2.

This module is the ENFORCEMENT surface on top of the config-driven matrix in
``app.core.roles_config`` (the single source of truth for grants). It exposes:

- ``has_permission(role, module, action)`` — pure boolean check.
- ``require_permission(module, action)`` — FastAPI dependency factory that
  returns the current user when allowed and raises ``403`` when denied.
- ``effective_matrix(role)`` / ``permissions_response(user)`` — the user's full
  effective permission matrix, for the frontend to gate the UI.

Design notes
------------
- The matrix lives in ``roles_config.ROLES``. Swapping in a revised matrix is a
  one-file data change there; nothing here needs to change.
- Grants are ``"yes" | "no" | "partial"``. ``has_permission`` is the coarse gate:
  ``"yes"`` and (by config) ``"partial"`` pass, ``"no"`` fails. The router or
  service applies the fine-grained restriction for ``"partial"`` — restrict the
  visible scope, the editable fields, or the required status.
- Fail-closed: unknown role / module / action -> denied.
- This layer says nothing about MULTI-TENANCY. Scoping every query to the
  authenticated user's ``broker_id`` is a separate, always-applied concern.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import Depends, HTTPException, status

from app.core import roles_config
from app.core.roles_config import (
    ACTIONS,
    GRANT_NO,
    GRANT_PARTIAL,
    GRANT_YES,
    MODULES,
)

if TYPE_CHECKING:  # avoid an import cycle (deps imports permissions)
    from app.models.user import User


# --- Role resolution ---------------------------------------------------------

def resolve_role(user: "User") -> str | None:
    """Return the effective RBAC role key for a user.

    Accepts either a plain string or an Enum on ``user.role``.
    """
    role = getattr(user, "role", None)
    if role is None:
        return None
    return role.value if hasattr(role, "value") else str(role)


def resolve_user_type(user: "User") -> str | None:
    """Return the actor family (platform | broker | insurer | insured).

    Prefers the explicit ``user.user_type`` column; falls back to deriving it
    from the role when the column is absent or empty.
    """
    user_type = getattr(user, "user_type", None)
    if user_type is not None:
        return user_type.value if hasattr(user_type, "value") else str(user_type)
    role = resolve_role(user)
    return roles_config.user_type_for(role) if role else None


# --- Core checks -------------------------------------------------------------

def has_permission(role: str | None, module: str, action: str) -> bool:
    """True when ``role`` may perform ``action`` on ``module``.

    Coarse gate: ``"yes"`` -> True; ``"partial"`` -> ``PARTIAL_IS_ALLOWED``;
    ``"no"`` / unknown -> False.
    """
    if not role:
        return False
    return roles_config.is_allowed(role, module, action)


def user_has_permission(user: "User", module: str, action: str) -> bool:
    """Convenience: resolve the user's role and check the permission."""
    return has_permission(resolve_role(user), module, action)


def is_partial(role: str | None, module: str, action: str) -> bool:
    """True when the grant is "partial" — the caller must narrow the scope."""
    if not role:
        return False
    return roles_config.is_partial(role, module, action)


# --- Effective matrix (for the frontend to gate UI) --------------------------

def effective_matrix(role: str | None) -> dict[str, dict[str, str]]:
    """Return ``{module: {action: grant}}`` for a role (raw grants).

    Grants are the raw strings (``"yes" | "no" | "partial"``) so the frontend can
    distinguish full from partial access. Unknown role -> everything ``"no"``.
    """
    return {
        module: {
            action: roles_config.grant_for(role, module, action) if role else GRANT_NO
            for action in ACTIONS
        }
        for module in MODULES
    }


def allowed_matrix(role: str | None) -> dict[str, dict[str, bool]]:
    """Return ``{module: {action: bool}}`` — the coarse-gate booleans."""
    return {
        module: {action: has_permission(role, module, action) for action in ACTIONS}
        for module in MODULES
    }


def permissions_response(user: "User") -> dict[str, Any]:
    """Build the payload for ``GET /auth/permissions``."""
    role = resolve_role(user)
    return {
        "user_id": user.id,
        "user_type": resolve_user_type(user),
        "role": role,
        "modules": MODULES,
        "actions": ACTIONS,
        "grants": {"yes": GRANT_YES, "no": GRANT_NO, "partial": GRANT_PARTIAL},
        # Raw grants ("yes"/"no"/"partial") — lets the UI show a partial state.
        "matrix": effective_matrix(role),
        # Coarse booleans — lets the UI cheaply gate (show/disable) a control.
        "allowed": allowed_matrix(role),
    }


# --- FastAPI dependency factory ---------------------------------------------

def require_permission(module: str, action: str):
    """Dependency factory: 403 unless the current user may do ``action`` on
    ``module``.

    Usage::

        @router.post("/clients")
        def create_client(user = Depends(require_permission("Clients", "Create"))):
            ...

    Returns the authenticated ``User`` on success (so the endpoint can keep using
    it). For ``"partial"`` grants the gate passes and the router/service is
    responsible for the fine-grained restriction — check ``is_partial()``.
    """
    # Imported here to avoid a circular import (deps imports this module).
    from app.api.deps import get_current_user

    if module not in MODULES:
        raise ValueError(f"require_permission: unknown module {module!r}")
    if action not in ACTIONS:
        raise ValueError(f"require_permission: unknown action {action!r}")

    def _guard(current_user: "User" = Depends(get_current_user)) -> "User":
        role = resolve_role(current_user)
        if not has_permission(role, module, action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Not permitted to {action} on {module}",
            )
        return current_user

    return _guard


__all__ = [
    "resolve_role",
    "resolve_user_type",
    "has_permission",
    "user_has_permission",
    "is_partial",
    "effective_matrix",
    "allowed_matrix",
    "permissions_response",
    "require_permission",
]
