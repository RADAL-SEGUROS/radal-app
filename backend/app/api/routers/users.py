"""User administration, scoped to the caller's organization.

Scoping rule (never bypassed)
-----------------------------
- ``platform`` users are NOT broker-scoped: they see every user and may filter
  with ``?broker_id=``.
- ``broker`` users see only users of their own broker (the tenant boundary).
- ``insurer`` / ``insured`` admins see only users of their own organization.

A user outside the caller's scope answers **404**, not 403 — the caller must not
be able to probe for the existence of another tenant's accounts.

Invitation
----------
There is no outbound email in this pass. ``POST /users`` creates the account and,
when no password was supplied, returns a generated ``temporary_password`` ONCE so
the administrator can hand it over. The invitee changes it via
``POST /auth/change-password``. Nothing about this flow is a dead end.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.core import roles_config
from app.core.permissions import resolve_user_type
from app.core.security import hash_password
from app.models.activity import Activity
from app.models.broker import Broker
from app.models.enums import EntityType, UserType
from app.models.insured import Insured
from app.models.insurer import Insurer
from app.models.user import User
from app.schemas.user import (
    AssignableRole,
    AssignableRolesResponse,
    UserCreate,
    UserInviteResponse,
    UserListResponse,
    UserRead,
    UserRoleUpdate,
    UserStatusUpdate,
    UserUpdate,
)

router = APIRouter(prefix="/users", tags=["users"])

# The role that must never disappear from an organization: deactivating or
# demoting the last active one would lock everybody out.
ADMIN_ROLE_BY_USER_TYPE: dict[str, str] = {
    "platform": "platform_admin",
    "broker": "broker_admin",
    "insurer": "insurer_admin",
    "insured": "insured_admin",
}

_ORG_FK_BY_USER_TYPE: dict[str, str] = {
    "broker": "broker_id",
    "insurer": "insurer_id",
    "insured": "insured_id",
}

_ORG_MODEL_BY_USER_TYPE: dict[str, type] = {
    "broker": Broker,
    "insurer": Insurer,
    "insured": Insured,
}

_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
)


# --- Scope helpers -----------------------------------------------------------


def _actor_user_type(current_user: User) -> str:
    user_type = resolve_user_type(current_user)
    if not user_type:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has no actor family assigned",
        )
    return user_type


def _scope_conditions(current_user: User) -> list[ColumnElement[bool]]:
    """The WHERE clauses that restrict a user query to the caller's org.

    Empty list == platform user (deliberately unscoped, see the module docstring).
    """
    user_type = _actor_user_type(current_user)
    if user_type == "platform":
        return []

    column_name = _ORG_FK_BY_USER_TYPE[user_type]
    org_id = getattr(current_user, column_name, None)
    if org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This user is not attached to an organization",
        )
    return [getattr(User, column_name) == org_id]


def _get_scoped_user(db: Session, user_id: int, current_user: User) -> User:
    """Load a user inside the caller's scope, or 404."""
    stmt = select(User).where(User.id == user_id, *_scope_conditions(current_user))
    user = db.scalar(stmt)
    if user is None:
        raise _NOT_FOUND
    return user


def _assignable_roles(current_user: User) -> list[str]:
    """Roles the caller may grant.

    A platform admin may grant any role; every other actor may only grant roles
    inside its own family — no privilege escalation across actor families.
    """
    user_type = _actor_user_type(current_user)
    if user_type == "platform":
        return list(roles_config.ROLES)
    return roles_config.roles_for_user_type(user_type)


def _count_active_admins(
    db: Session, conditions: list[ColumnElement[bool]], admin_role: str, exclude_id: int
) -> int:
    stmt = (
        select(func.count())
        .select_from(User)
        .where(
            User.role == admin_role,
            User.is_active.is_(True),
            User.id != exclude_id,
            *conditions,
        )
    )
    return int(db.scalar(stmt) or 0)


def _guard_last_admin(db: Session, target: User, current_user: User) -> None:
    """409 when the change would leave the organization with no active admin."""
    target_type = (
        target.user_type.value
        if hasattr(target.user_type, "value")
        else str(target.user_type)
    )
    admin_role = ADMIN_ROLE_BY_USER_TYPE.get(target_type)
    if admin_role is None or target.role != admin_role or not target.is_active:
        return

    column_name = _ORG_FK_BY_USER_TYPE.get(target_type)
    conditions: list[ColumnElement[bool]] = []
    if column_name is not None:
        org_id = getattr(target, column_name, None)
        conditions.append(getattr(User, column_name) == org_id)

    if _count_active_admins(db, conditions, admin_role, target.id) == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This is the last active administrator of the organization",
        )


def _log_activity(db: Session, actor: User, action: str, target: User) -> None:
    """Audit the change when it happens inside a broker workspace.

    ``activity`` is broker-scoped by design, so platform/insurer/insured-side
    changes are simply not recorded here.
    """
    broker_id = target.broker_id or actor.broker_id
    if broker_id is None:
        return
    db.add(
        Activity(
            broker_id=broker_id,
            user_id=actor.id,
            action=action,
            entity_type=EntityType.USER,
            entity_id=target.id,
            description=f"{target.full_name} <{target.email}>",
            meta={"role": target.role, "is_active": target.is_active},
        )
    )


# --- Endpoints ---------------------------------------------------------------


@router.get("/roles", response_model=AssignableRolesResponse)
def list_assignable_roles(
    current_user: User = Depends(require_permission("Users", "View")),
) -> AssignableRolesResponse:
    """Roles the caller may grant — powers the invite form (no invalid options)."""
    return AssignableRolesResponse(
        items=[
            AssignableRole(role=role, user_type=roles_config.user_type_for(role) or "")
            for role in _assignable_roles(current_user)
        ]
    )


@router.get("", response_model=UserListResponse)
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("Users", "View")),
    search: str | None = Query(default=None, max_length=255),
    role: str | None = Query(default=None, max_length=64),
    user_type: str | None = Query(default=None, max_length=32),
    is_active: bool | None = Query(default=None),
    broker_id: int | None = Query(
        default=None, description="Platform users only: restrict to one broker"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> UserListResponse:
    """List the users of the caller's organization."""
    conditions = _scope_conditions(current_user)

    if broker_id is not None:
        if _actor_user_type(current_user) != "platform":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only platform users may filter across brokers",
            )
        conditions.append(User.broker_id == broker_id)

    if role is not None:
        conditions.append(User.role == role)
    if user_type is not None:
        if user_type not in roles_config.USER_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown user_type {user_type!r}",
            )
        conditions.append(User.user_type == UserType(user_type))
    if is_active is not None:
        conditions.append(User.is_active.is_(is_active))
    if search:
        pattern = f"%{search.strip().lower()}%"
        conditions.append(
            or_(
                func.lower(User.full_name).like(pattern),
                func.lower(User.email).like(pattern),
            )
        )

    total = int(
        db.scalar(select(func.count()).select_from(User).where(*conditions)) or 0
    )
    rows = db.scalars(
        select(User)
        .where(*conditions)
        .order_by(User.full_name.asc(), User.id.asc())
        .limit(limit)
        .offset(offset)
    ).all()

    return UserListResponse(
        items=[UserRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=UserInviteResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("Users", "Create")),
) -> UserInviteResponse:
    """Invite / create a user inside the caller's organization.

    409 when the email already exists, 403 when the requested role belongs to
    another actor family, 404 when a platform user names a missing organization.
    """
    actor_type = _actor_user_type(current_user)
    target_type = roles_config.user_type_for(payload.role)
    if target_type is None:  # defensive: the schema already validated the role
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown role {payload.role!r}",
        )

    if payload.role not in _assignable_roles(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You may not grant the role {payload.role!r}",
        )

    org_ids: dict[str, int | None] = {
        "broker_id": None,
        "insurer_id": None,
        "insured_id": None,
    }
    column_name = _ORG_FK_BY_USER_TYPE.get(target_type)

    if actor_type == "platform":
        # Platform admins create users anywhere, but must name the organization.
        if column_name is not None:
            requested = getattr(payload, column_name, None)
            if requested is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"{column_name} is required for a {target_type} user",
                )
            org_model = _ORG_MODEL_BY_USER_TYPE[target_type]
            if db.get(org_model, requested) is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"{target_type} {requested} not found",
                )
            org_ids[column_name] = requested
    else:
        # Everyone else is pinned to their own organization; an explicit
        # mismatch is a scope violation, not a silent override.
        assert column_name is not None  # actor_type == target_type here
        own_id = getattr(current_user, column_name)
        requested = getattr(payload, column_name, None)
        if requested is not None and requested != own_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You may only create users inside your own organization",
            )
        org_ids[column_name] = own_id

    email = payload.email.lower()
    existing = db.scalar(select(User.id).where(func.lower(User.email) == email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    generated_password = None if payload.password else secrets.token_urlsafe(9)
    raw_password = payload.password or generated_password

    user = User(
        email=email,
        hashed_password=hash_password(raw_password),
        full_name=payload.full_name.strip(),
        job_title=payload.job_title,
        phone=payload.phone,
        user_type=UserType(target_type),
        role=payload.role,
        is_active=payload.is_active,
        **org_ids,
    )
    db.add(user)
    db.flush()
    _log_activity(db, current_user, "user.created", user)
    db.commit()
    db.refresh(user)

    return UserInviteResponse(
        user=UserRead.model_validate(user),
        temporary_password=generated_password,
    )


@router.get("/{user_id}", response_model=UserRead)
def read_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("Users", "View")),
) -> UserRead:
    """One user of the caller's organization."""
    return UserRead.model_validate(_get_scoped_user(db, user_id, current_user))


@router.patch("/{user_id}", response_model=UserRead)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("Users", "Edit")),
) -> UserRead:
    """Update editable profile fields. Role and status have dedicated endpoints."""
    user = _get_scoped_user(db, user_id, current_user)
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(user, field, value.strip() if isinstance(value, str) else value)
    if data:
        _log_activity(db, current_user, "user.updated", user)
    db.commit()
    db.refresh(user)
    return UserRead.model_validate(user)


@router.patch("/{user_id}/role", response_model=UserRead)
def update_user_role(
    user_id: int,
    payload: UserRoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("Users", "Manage")),
) -> UserRead:
    """Change a user's RBAC role.

    The new role must belong to the target's own actor family (422) and to the
    set the caller may grant (403). Changing your own role, or demoting the last
    active administrator, is rejected with 409.
    """
    user = _get_scoped_user(db, user_id, current_user)

    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot change your own role",
        )

    target_family = (
        user.user_type.value
        if hasattr(user.user_type, "value")
        else str(user.user_type)
    )
    new_family = roles_config.user_type_for(payload.role)
    if new_family != target_family:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Role {payload.role!r} belongs to the {new_family!r} actor "
                f"family; this user is {target_family!r}"
            ),
        )
    if payload.role not in _assignable_roles(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You may not grant the role {payload.role!r}",
        )

    if payload.role != user.role:
        _guard_last_admin(db, user, current_user)
        user.role = payload.role
        _log_activity(db, current_user, "user.role_changed", user)
    db.commit()
    db.refresh(user)
    return UserRead.model_validate(user)


@router.patch("/{user_id}/status", response_model=UserRead)
def update_user_status(
    user_id: int,
    payload: UserStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("Users", "Manage")),
) -> UserRead:
    """Activate or deactivate a user.

    Users are never hard-deleted — they are referenced by activity, documents,
    proposals and policies. Deactivating yourself, or the last active
    administrator, is rejected with 409.
    """
    user = _get_scoped_user(db, user_id, current_user)

    if user.id == current_user.id and not payload.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot deactivate your own account",
        )

    if user.is_active != payload.is_active:
        if not payload.is_active:
            _guard_last_admin(db, user, current_user)
        user.is_active = payload.is_active
        action = "user.activated" if payload.is_active else "user.deactivated"
        _log_activity(db, current_user, action, user)
    db.commit()
    db.refresh(user)
    return UserRead.model_validate(user)


__all__ = ["router"]
