"""Authentication endpoints: login, refresh, profile, effective permissions.

Design notes
------------
- JSON login (``{"email", "password"}``), not OAuth2 form-encoded — the SPA
  posts JSON everywhere and the CloudFront OAC path hashes the JSON body.
- Tokens are issued by ``app.core.security``; the access token carries a few
  non-authoritative claims (``role``, ``user_type``, ``broker_id``) for
  observability only. Authorization ALWAYS re-reads the user row — a claim is
  never trusted for tenant scoping (see ``app.api.deps``).
- ``/auth/refresh`` rotates: a valid refresh token yields a brand-new pair.
- Credential failures answer with one generic message so the endpoint cannot be
  used to enumerate registered emails.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.core.permissions import permissions_response, resolve_user_type
from app.core.security import (
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    MessageResponse,
    OrganizationSummary,
    PermissionsResponse,
    RefreshRequest,
    TokenPair,
)
from app.schemas.user import UserRead

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid email or password",
    headers={"WWW-Authenticate": "Bearer"},
)

_INVALID_REFRESH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired refresh token",
    headers={"WWW-Authenticate": "Bearer"},
)


# --- Helpers -----------------------------------------------------------------


def _token_claims(user: User) -> dict:
    """Non-authoritative claims, for logs/debugging only. Never used to scope."""
    return {
        "email": user.email,
        "role": user.role,
        "user_type": resolve_user_type(user),
        "broker_id": user.broker_id,
    }


def _issue_tokens(user: User) -> TokenPair:
    claims = _token_claims(user)
    return TokenPair(
        access_token=create_access_token(user.id, claims),
        refresh_token=create_refresh_token(user.id, claims),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def organization_summary(user: User) -> OrganizationSummary | None:
    """The user's home organization (broker | insurer | insured), or None.

    Platform users belong to Radal itself and have no organization row.
    """
    if user.broker_id is not None and user.broker is not None:
        org = user.broker
        return OrganizationSummary(
            type="broker",
            id=org.id,
            legal_name=org.legal_name,
            trade_name=org.trade_name,
            logo_key=org.logo_key,
            status=str(org.status) if org.status is not None else None,
        )
    if user.insurer_id is not None and user.insurer is not None:
        org = user.insurer
        return OrganizationSummary(
            type="insurer",
            id=org.id,
            legal_name=org.legal_name,
            trade_name=org.trade_name,
            logo_key=org.logo_key,
            status=str(org.status) if org.status is not None else None,
        )
    if user.insured_id is not None and user.insured is not None:
        org = user.insured
        return OrganizationSummary(
            type="insured",
            id=org.id,
            legal_name=org.legal_name,
            trade_name=org.trade_name,
            logo_key=org.logo_key,
        )
    return None


def _load_active_user(db: Session, user_id: int) -> User | None:
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


# --- Endpoints ---------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    """Exchange email + password for an access/refresh pair and the profile.

    401 on bad credentials, 403 when the account exists but was deactivated.
    """
    user = db.scalar(
        select(User).where(func.lower(User.email) == payload.email.lower())
    )
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise _INVALID_CREDENTIALS
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is deactivated. Contact your administrator.",
        )

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    tokens = _issue_tokens(user)
    return LoginResponse(
        **tokens.model_dump(),
        user=UserRead.model_validate(user),
        organization=organization_summary(user),
    )


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenPair:
    """Rotate a valid refresh token into a fresh access/refresh pair."""
    claims = decode_token(payload.refresh_token)
    if claims is None or claims.get("type") != REFRESH_TOKEN_TYPE:
        raise _INVALID_REFRESH

    sub = claims.get("sub")
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        raise _INVALID_REFRESH

    user = _load_active_user(db, user_id)
    if user is None:
        raise _INVALID_REFRESH
    return _issue_tokens(user)


@router.get("/me", response_model=MeResponse)
def read_me(current_user: User = Depends(get_current_user)) -> MeResponse:
    """The authenticated profile plus its home organization."""
    return MeResponse(
        user=UserRead.model_validate(current_user),
        organization=organization_summary(current_user),
    )


@router.get("/permissions", response_model=PermissionsResponse)
def read_permissions(
    current_user: User = Depends(get_current_user),
) -> PermissionsResponse:
    """The effective RBAC matrix for the current user, for UI gating.

    Returns both the raw grants (``matrix``: yes | no | partial) and the coarse
    booleans (``allowed``). The UI uses this to render a control enabled,
    visibly disabled ("pronto"), or hidden — never as a dead button.
    """
    return PermissionsResponse(**permissions_response(current_user))


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MessageResponse:
    """Self-service password change. 403 when the current password is wrong."""
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Current password is incorrect",
        )
    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The new password must differ from the current one",
        )
    current_user.hashed_password = hash_password(payload.new_password)
    db.commit()
    return MessageResponse(detail="Password updated")


__all__ = ["router", "organization_summary"]
