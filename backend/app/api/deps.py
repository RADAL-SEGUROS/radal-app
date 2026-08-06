"""Shared API dependencies: current user, tenant scoping, RBAC re-exports.

Every router in this application composes three things from here:

1. ``get_current_user`` — decodes the JWT (from ``Authorization: Bearer`` OR the
   ``X-Radal-Token`` header) and loads the active user.
2. ``get_current_broker_id`` — the tenant scope. EVERY workspace query must be
   filtered by it; a "yes" in the RBAC matrix never means "across brokers".
3. ``require_permission(module, action)`` — the RBAC gate (re-exported from
   ``app.core.permissions`` so routers only import from one place).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.permissions import (
    has_permission,
    is_partial,
    require_permission,
    resolve_role,
    resolve_user_type,
)
from app.core.security import ACCESS_TOKEN_TYPE, decode_token
from app.db.session import get_db
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)

_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    x_radal_token: str | None = Header(default=None, alias="X-Radal-Token"),
    db: Session = Depends(get_db),
) -> User:
    """Decode the access token and return the matching active user.

    The token arrives in the ``Authorization: Bearer`` header for local/direct
    access, or in ``X-Radal-Token`` when served behind CloudFront — there the
    OAC reuses ``Authorization`` for its own SigV4 signature, so the app's JWT
    must ride in a separate header. Do not remove the fallback.

    A refresh token presented here is rejected: only ``type == "access"`` passes.
    """
    token = (credentials.credentials if credentials else None) or x_radal_token
    if not token:
        raise _CREDENTIALS_EXC

    payload = decode_token(token)
    if payload is None or payload.get("type") != ACCESS_TOKEN_TYPE:
        raise _CREDENTIALS_EXC

    sub = payload.get("sub")
    if sub is None:
        raise _CREDENTIALS_EXC

    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        raise _CREDENTIALS_EXC

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _CREDENTIALS_EXC
    return user


def get_current_broker_id(current_user: User = Depends(get_current_user)) -> int:
    """Tenant scoping helper: the broker_id every workspace query must filter by.

    Platform users carry no broker, so they cannot use broker-scoped endpoints
    without naming a broker explicitly.
    """
    broker_id = getattr(current_user, "broker_id", None)
    if broker_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This user is not attached to a broker workspace",
        )
    return broker_id


def get_current_broker_id_optional(
    current_user: User = Depends(get_current_user),
) -> int | None:
    """Like ``get_current_broker_id`` but ``None`` for platform users.

    Use it on endpoints that a platform user may legitimately call unscoped
    (e.g. cross-broker administration); the router must then decide the scope
    explicitly instead of silently querying every tenant.
    """
    return getattr(current_user, "broker_id", None)


def get_current_role(current_user: User = Depends(get_current_user)) -> str | None:
    """The RBAC role key of the current user."""
    return resolve_role(current_user)


def get_current_user_type(current_user: User = Depends(get_current_user)) -> str | None:
    """The actor family: platform | broker | insurer | insured."""
    return resolve_user_type(current_user)


def scope_to_broker(query, model, broker_id: int):
    """Apply the tenant filter to a Select if the model carries broker_id."""
    if hasattr(model, "broker_id"):
        return query.where(model.broker_id == broker_id)
    return query


# --- Annotated shorthands (optional sugar for routers) -----------------------

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentBrokerId = Annotated[int, Depends(get_current_broker_id)]


__all__ = [
    "CurrentBrokerId",
    "CurrentUser",
    "DbSession",
    "get_current_user",
    "get_current_broker_id",
    "get_current_broker_id_optional",
    "get_current_role",
    "get_current_user_type",
    "scope_to_broker",
    "require_permission",
    "has_permission",
    "is_partial",
    "resolve_role",
    "resolve_user_type",
    "get_db",
]
