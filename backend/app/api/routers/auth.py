"""Auth router: login, refresh, logout, me. Mounted at /api/v1/auth."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import (
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.db.session import get_db
from app.models.corredora import Corredora
from app.models.usuario import Usuario
from app.schemas.common import (
    CorredoraSummary,
    LoginRequest,
    MeResponse,
    RefreshRequest,
    Token,
    TokenPair,
    UsuarioOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _authenticate(db: Session, email: str, password: str) -> Usuario:
    usuario = db.execute(
        select(Usuario).where(Usuario.email == email)
    ).scalar_one_or_none()
    if usuario is None or not verify_password(password, usuario.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
        )
    if not usuario.activo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Usuario inactivo"
        )
    return usuario


def _issue_tokens(usuario: Usuario) -> tuple[str, str]:
    extra = {"corredora_id": usuario.corredora_id, "rol": usuario.rol.value}
    access = create_access_token(usuario.id, extra)
    refresh = create_refresh_token(usuario.id, {"corredora_id": usuario.corredora_id})
    return access, refresh


@router.post("/login", response_model=Token)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> Token:
    """JSON login: email + password -> access + refresh + usuario."""
    usuario = _authenticate(db, payload.email, payload.password)
    access, refresh = _issue_tokens(usuario)
    return Token(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        usuario=UsuarioOut.model_validate(usuario),
    )


@router.post("/login/token", response_model=Token)
def login_oauth_form(
    form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
) -> Token:
    """OAuth2 form login (username=email). Convenience for tooling / Swagger."""
    usuario = _authenticate(db, form_data.username, form_data.password)
    access, refresh = _issue_tokens(usuario)
    return Token(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        usuario=UsuarioOut.model_validate(usuario),
    )


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenPair:
    data = decode_token(payload.refresh_token)
    if data is None or data.get("type") != REFRESH_TOKEN_TYPE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token inválido"
        )
    sub = data.get("sub")
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token inválido"
        )
    usuario = db.get(Usuario, user_id)
    if usuario is None or not usuario.activo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no válido"
        )
    access, new_refresh = _issue_tokens(usuario)
    return TokenPair(access_token=access, refresh_token=new_refresh, token_type="bearer")


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: RefreshRequest) -> Response:
    """Stateless logout: client discards tokens. 204 no content."""
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=MeResponse)
def me(
    current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)
) -> MeResponse:
    corredora = db.get(Corredora, current_user.corredora_id)
    base = UsuarioOut.model_validate(current_user).model_dump()
    return MeResponse(
        **base,
        corredora=CorredoraSummary.model_validate(corredora) if corredora else None,
    )
