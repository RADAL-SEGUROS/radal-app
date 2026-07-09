"""Shared API dependencies: current user, role guards, tenant scoping."""
from __future__ import annotations

from collections.abc import Callable, Iterable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import ACCESS_TOKEN_TYPE, decode_token
from app.db.session import get_db
from app.models.usuario import RolEnum, Usuario

bearer_scheme = HTTPBearer(auto_error=False)

_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="No autenticado",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Usuario:
    """Decode the Bearer access token and return the matching active usuario."""
    if credentials is None or not credentials.credentials:
        raise _CREDENTIALS_EXC

    payload = decode_token(credentials.credentials)
    if payload is None or payload.get("type") != ACCESS_TOKEN_TYPE:
        raise _CREDENTIALS_EXC

    sub = payload.get("sub")
    if sub is None:
        raise _CREDENTIALS_EXC

    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        raise _CREDENTIALS_EXC

    usuario = db.get(Usuario, user_id)
    if usuario is None or not usuario.activo:
        raise _CREDENTIALS_EXC
    return usuario


def get_current_corredora_id(
    current_user: Usuario = Depends(get_current_user),
) -> int:
    """Tenant scoping helper: the corredora_id every query must filter by."""
    return current_user.corredora_id


def require_roles(*roles: RolEnum | str) -> Callable[[Usuario], Usuario]:
    """Dependency factory: only allow users whose rol is in `roles`."""
    allowed: set[str] = {r.value if isinstance(r, RolEnum) else str(r) for r in roles}

    def _guard(current_user: Usuario = Depends(get_current_user)) -> Usuario:
        rol_value = current_user.rol.value if isinstance(current_user.rol, RolEnum) else str(
            current_user.rol
        )
        if rol_value not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tiene permisos para esta acción",
            )
        return current_user

    return _guard


def scope_to_tenant(query, model, corredora_id: int):
    """Apply the tenant filter to a Select if the model carries corredora_id."""
    if hasattr(model, "corredora_id"):
        return query.where(model.corredora_id == corredora_id)
    return query


# Convenience role guards used across routers.
require_admin_corredora = require_roles(RolEnum.admin_corredora)
require_corredora_staff = require_roles(
    RolEnum.admin_corredora, RolEnum.ejecutivo_corredora
)


__all__ = [
    "get_current_user",
    "get_current_corredora_id",
    "require_roles",
    "require_admin_corredora",
    "require_corredora_staff",
    "scope_to_tenant",
    "get_db",
    "select",
    "Iterable",
]
