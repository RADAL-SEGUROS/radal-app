"""``/share-links`` — create / list / revoke public links (authenticated) (v12, 02 §7).

Owner: lane **B5** (share links / public). Built on ``app.services.share_links``.

The gate is ``View`` on the TARGET's module (a link only shows what the broker
can already see): ``policy`` -> Policies; ``collection_plan`` / ``installment`` /
``status_inquiry`` -> Collections; ``case_file`` -> CaseFiles, narrowed by the
same ``CASE_VIEW_SCOPE`` the case-file list applies (rule 8). The module depends
on the request body, so the check runs inside the handler with the same
``has_permission`` the ``require_permission`` dependency uses.

Tenant scope: the target is resolved with the caller's ``broker_id``; another
tenant's row — and a case the caller's scope cannot see — is a **404**. The
link itself is broker-scoped too (list, revoke).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_current_user, get_db
from app.api.routers.clients import record_activity
from app.core.case_scope import scope_case_query
from app.core.permissions import has_permission, resolve_role
from app.models.base_class import utcnow
from app.models.broker import Broker
from app.models.case_file import CaseFile
from app.models.enums import EntityType, ShareEntity
from app.models.share_link import ShareLink
from app.models.user import User
from app.schemas.share import ShareLinkCreate, ShareLinkList, ShareLinkRead
from app.services.share_links import (
    ShareAudienceNotAllowed,
    ShareTargetNotFound,
    audience_refusal,
    create_share_link,
    is_link_live,
    link_permissions,
    module_for_entity,
    resolve_share_entity,
    resolve_share_scope,
    revoke_share_link,
    share_message,
    share_url,
    whatsapp_url,
)

router = APIRouter(prefix="/share-links", tags=["share-links"])

_NOT_FOUND = "Elemento no encontrado"


def _require_view(user: User, entity_type: ShareEntity) -> None:
    """403 unless the caller may ``View`` the module owning ``entity_type``."""
    module = module_for_entity(entity_type)
    if not has_permission(resolve_role(user), module, "View"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not permitted to View on {module}",
        )


def _target_or_404(
    db: Session, *, broker_id: int, user: User, entity_type: ShareEntity, entity_id: int
):
    """The broker's row (and, for a case, one the caller's scope can see)."""
    row = resolve_share_entity(db, broker_id=broker_id, entity_type=entity_type, entity_id=entity_id)
    if row is not None and entity_type is ShareEntity.CASE_FILE:
        visible = db.scalars(
            scope_case_query(
                select(CaseFile.id).where(CaseFile.broker_id == broker_id, CaseFile.id == entity_id),
                broker_id=broker_id,
                user=user,
            )
        ).first()
        row = row if visible is not None else None
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return row


def _read(db: Session, link: ShareLink) -> ShareLinkRead:
    url = share_url(link.token)
    broker = db.get(Broker, link.broker_id)
    scope = resolve_share_scope(db, link)
    policy = scope.policies[0] if scope is not None and scope.policies else None
    insurer = getattr(policy, "insurer", None) if policy is not None else None
    text = share_message(
        link,
        url=url,
        broker_name=(broker.trade_name or broker.legal_name) if broker else None,
        policy_number=policy.policy_number if policy is not None else None,
        insurer_name=(insurer.trade_name or insurer.legal_name) if insurer is not None else None,
    )
    return ShareLinkRead(
        id=link.id,
        entity_type=link.entity_type,
        entity_id=link.entity_id,
        audience=link.audience,
        permissions=link_permissions(link),
        label=link.label,
        token=link.token,
        url=url,
        whatsapp_url=whatsapp_url(text),
        whatsapp_text=text,
        is_live=is_link_live(link, now=utcnow()),
        expires_at=link.expires_at,
        revoked_at=link.revoked_at,
        view_count=link.view_count or 0,
        last_viewed_at=link.last_viewed_at,
        created_by_user_id=link.created_by_user_id,
        created_at=link.created_at,
    )


@router.post("", response_model=ShareLinkRead, status_code=status.HTTP_201_CREATED)
def create_link(
    payload: ShareLinkCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(get_current_user),
) -> ShareLinkRead:
    """Mint a public link to one of the broker's rows (WhatsApp-ready)."""
    _require_view(current_user, payload.entity_type)
    _target_or_404(
        db,
        broker_id=broker_id,
        user=current_user,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
    )
    # A case spans several insurers (insured only); an inquiry is for the insurer.
    refusal = audience_refusal(payload.entity_type, payload.audience)
    if refusal is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=refusal)
    try:
        link = create_share_link(
            db,
            broker_id=broker_id,
            user=current_user,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            audience=payload.audience,
            permissions=payload.permissions.model_dump(exclude_none=True)
            if payload.permissions
            else None,
            expires_in_days=payload.expires_in_days,
            label=payload.label,
        )
    except ShareTargetNotFound as exc:  # pragma: no cover - checked just above
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND) from exc
    except ShareAudienceNotAllowed as exc:  # pragma: no cover - checked just above
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Vigencia del enlace inválida (entre 1 y 365 días).",
        ) from exc
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="share_link.created",
        entity_type=EntityType.SHARE_LINK,
        entity_id=link.id,
        description="Enlace público creado",
        meta={
            "entity_type": link.entity_type.value,
            "entity_id": link.entity_id,
            "audience": link.audience.value,
        },
    )
    db.commit()
    db.refresh(link)
    return _read(db, link)


@router.get("", response_model=ShareLinkList)
def list_links(
    entity_type: ShareEntity = Query(...),
    entity_id: int = Query(..., gt=0),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(get_current_user),
) -> ShareLinkList:
    """The links minted for one entity, newest first (revoked/expired included)."""
    _require_view(current_user, entity_type)
    _target_or_404(
        db, broker_id=broker_id, user=current_user, entity_type=entity_type, entity_id=entity_id
    )
    rows = db.scalars(
        select(ShareLink)
        .where(
            ShareLink.broker_id == broker_id,
            ShareLink.entity_type == entity_type,
            ShareLink.entity_id == entity_id,
        )
        .order_by(ShareLink.id.desc())
    ).all()
    items = [_read(db, row) for row in rows]
    return ShareLinkList(items=items, total=len(items))


@router.post("/{link_id}/revoke", response_model=ShareLinkRead)
def revoke_link(
    link_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(get_current_user),
) -> ShareLinkRead:
    """Revoke a link (idempotent). The public side answers 404 from then on."""
    link = db.scalar(
        select(ShareLink).where(ShareLink.id == link_id, ShareLink.broker_id == broker_id)
    )
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enlace no encontrado")
    _require_view(current_user, link.entity_type)
    if link.entity_type is ShareEntity.CASE_FILE:
        # Same visibility as create/list: a case outside the caller's scope is a 404.
        try:
            _target_or_404(
                db,
                broker_id=broker_id,
                user=current_user,
                entity_type=link.entity_type,
                entity_id=link.entity_id,
            )
        except HTTPException as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Enlace no encontrado"
            ) from exc
    already = link.revoked_at is not None
    revoke_share_link(link)
    if not already:
        record_activity(
            db,
            broker_id=broker_id,
            user=current_user,
            action="share_link.revoked",
            entity_type=EntityType.SHARE_LINK,
            entity_id=link.id,
            description="Enlace público revocado",
            meta={"entity_type": link.entity_type.value, "entity_id": link.entity_id},
        )
    db.commit()
    db.refresh(link)
    return _read(db, link)


__all__ = ["router"]
