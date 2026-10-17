"""``/payments`` — native checkout intents and per-insurer capability (v12, 02 §5).

Owner: lane **B3** (payments / Forpay).

* ``POST /payments/intents`` (Collections.Edit) — "Generar link de pago": one
  intent = one Forpay ONEPAY link over native-level cuotas
  (:mod:`app.services.payments.checkout`; 409 with a Spanish reason otherwise).
* ``GET /payments/intents`` · ``GET /payments/intents/{id}`` (Collections.View),
  ``POST /payments/intents/{id}/cancel`` (Collections.Edit).
* ``GET /payments/insurers`` (Collections.View) — per insurer: the level
  available, live channels, the merged instructions (RADAL profile ⊕ broker
  override) and the override. **Never** the onboarding status or ETA.
* ``PUT /payments/insurers/{insurer_id}/override`` (Settings.Manage).

Tenant scope: every row is filtered by ``get_current_broker_id``; another
broker's intent or share link is a 404. Insurers are canonical and visible by
the same rule as ``/insurers`` (native, global, or created by this broker).
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.api.routers.clients import record_activity
from app.models.base_class import utcnow
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.collection_settings import CollectionSettings
from app.models.enums import EntityType, IntentStatus
from app.models.insurer import Insurer
from app.models.insurer_collection import BrokerInsurerCollection
from app.models.payment import PaymentIntent, PaymentIntentItem
from app.models.policy import Policy
from app.models.share_link import ShareLink
from app.models.user import User
from app.schemas.payments import (
    InsurerCapability,
    InsurerOverrideUpdate,
    IntentCreate,
    IntentCreated,
    IntentItemRead,
    IntentPage,
    IntentRead,
)
from app.services.payments.checkout import (
    CheckoutError,
    CheckoutProviderError,
    cancel_intent,
    create_intent_for_installments,
    insurer_label,
)
from app.services.payments.insurers import broker_override, clean_instructions, insurer_capability
from app.services.payments.intents import provider_paid_item_ids

router = APIRouter(prefix="/payments", tags=["payments"])

_NOT_FOUND = "Link de pago no encontrado"


# --- projections ---------------------------------------------------------------

def intent_read(db: Session, intent: PaymentIntent) -> IntentRead:
    paid = provider_paid_item_ids(db, intent)
    items: list[IntentItemRead] = []
    total = Decimal("0")
    for item in intent.items:
        inst = item.installment
        policy = inst.collection_plan.policy if inst is not None else None
        insurer = db.get(Insurer, item.insurer_id) if item.insurer_id else None
        total += Decimal(str(item.amount_uf))
        items.append(
            IntentItemRead(
                id=item.id,
                installment_id=item.installment_id,
                installment_number=inst.number if inst is not None else None,
                due_date=inst.due_date if inst is not None else None,
                policy_id=policy.id if policy is not None else None,
                policy_number=policy.policy_number if policy is not None else None,
                insurer_id=item.insurer_id,
                insurer_name=insurer_label(insurer) if insurer is not None else None,
                amount_uf=item.amount_uf,
                store_commerce_code=item.store_commerce_code,
                external_installment_id=item.external_installment_id,
                paid=item.id in paid,
            )
        )
    return IntentRead(
        id=intent.id,
        external_id=intent.external_id,
        status=intent.status,
        checkout_url=intent.checkout_url,
        provider_account_id=intent.provider_account_id,
        share_link_id=intent.share_link_id,
        payer_party_id=intent.payer_party_id,
        total_uf=total,
        expires_at=intent.expires_at,
        created_by_user_id=intent.created_by_user_id,
        created_at=intent.created_at,
        updated_at=intent.updated_at,
        items=items,
    )


def _intent_or_404(db: Session, intent_id: int, broker_id: int) -> PaymentIntent:
    intent = db.scalar(
        select(PaymentIntent).where(PaymentIntent.id == intent_id, PaymentIntent.broker_id == broker_id)
    )
    if intent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return intent


def _http(exc: CheckoutError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


# --- intents --------------------------------------------------------------------

@router.post("/intents", response_model=IntentCreated, status_code=status.HTTP_201_CREATED)
def create_intent(
    payload: IntentCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> IntentCreated:
    """"Generar link de pago" over one or more native-level cuotas."""
    share_link = None
    if payload.share_link_id is not None:
        share_link = db.scalar(
            select(ShareLink).where(
                ShareLink.id == payload.share_link_id, ShareLink.broker_id == broker_id
            )
        )
        if share_link is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link no encontrado")
    try:
        result = create_intent_for_installments(
            db,
            broker_id=broker_id,
            installment_ids=payload.installment_ids,
            user=current_user,
            share_link=share_link,
            now=utcnow(),
        )
    except CheckoutProviderError as exc:
        db.commit()  # keep the failed intent (and any superseded link) on record
        raise _http(exc) from exc
    except CheckoutError as exc:
        db.rollback()
        raise _http(exc) from exc
    intent = db.get(PaymentIntent, result.intent_id)
    if not result.reused:
        record_activity(
            db,
            broker_id=broker_id,
            user=current_user,
            action="payment_intent.created",
            entity_type=EntityType.PAYMENT_INTENT,
            entity_id=intent.id,
            description="Link de pago generado",
            meta={
                "installment_ids": [item.installment_id for item in intent.items],
                "external_id": intent.external_id,
            },
        )
    db.commit()
    db.refresh(intent)
    return IntentCreated(intent=intent_read(db, intent), checkout_url=result.checkout_url, reused=result.reused)


@router.get("/intents", response_model=IntentPage)
def list_intents(
    installment_id: int | None = Query(default=None),
    policy_id: int | None = Query(default=None),
    status_filter: list[IntentStatus] | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> IntentPage:
    stmt = select(PaymentIntent).where(PaymentIntent.broker_id == broker_id)
    if installment_id is not None or policy_id is not None:
        sub = select(PaymentIntentItem.intent_id).where(PaymentIntentItem.broker_id == broker_id)
        if installment_id is not None:
            sub = sub.where(PaymentIntentItem.installment_id == installment_id)
        if policy_id is not None:
            sub = (
                sub.join(CollectionInstallment, CollectionInstallment.id == PaymentIntentItem.installment_id)
                .join(CollectionPlan, CollectionPlan.id == CollectionInstallment.collection_plan_id)
                .where(CollectionPlan.policy_id == policy_id, CollectionPlan.broker_id == broker_id)
            )
        stmt = stmt.where(PaymentIntent.id.in_(sub))
    if status_filter:
        stmt = stmt.where(PaymentIntent.status.in_(status_filter))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(PaymentIntent.id.desc()).offset(offset).limit(limit)).all()
    return IntentPage(items=[intent_read(db, r) for r in rows], total=int(total), limit=limit, offset=offset)


@router.get("/intents/{intent_id}", response_model=IntentRead)
def get_intent(
    intent_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> IntentRead:
    return intent_read(db, _intent_or_404(db, intent_id, broker_id))


@router.post("/intents/{intent_id}/cancel", response_model=IntentRead)
def cancel_one_intent(
    intent_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> IntentRead:
    """Annul the link at the provider and cancel it (409 once paid or closed)."""
    intent = _intent_or_404(db, intent_id, broker_id)
    try:
        cancel_intent(db, intent, now=utcnow())
    except CheckoutError as exc:
        db.rollback()
        raise _http(exc) from exc
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="payment_intent.cancelled",
        entity_type=EntityType.PAYMENT_INTENT,
        entity_id=intent.id,
        description="Link de pago anulado",
    )
    db.commit()
    db.refresh(intent)
    return intent_read(db, intent)


# --- insurers -------------------------------------------------------------------

def _visible_insurers(broker_id: int):  # noqa: ANN202
    return select(Insurer).where(
        or_(
            Insurer.is_native.is_(True),
            Insurer.created_by_broker_id.is_(None),
            Insurer.created_by_broker_id == broker_id,
        )
    )


def _visible_insurer_or_404(db: Session, insurer_id: int, broker_id: int) -> Insurer:
    insurer = db.scalar(_visible_insurers(broker_id).where(Insurer.id == insurer_id))
    if insurer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compañía no encontrada")
    return insurer


def _settings(db: Session, broker_id: int) -> CollectionSettings | None:
    return db.scalar(select(CollectionSettings).where(CollectionSettings.broker_id == broker_id))


@router.get("/insurers", response_model=list[InsurerCapability])
def list_insurer_capabilities(
    scope: str = Query(default="mine", pattern="^(mine|all)$"),
    insurer_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> list[InsurerCapability]:
    """``scope=mine`` (default): insurers of the broker's policies or overrides; ``all``: every visible insurer."""
    stmt = _visible_insurers(broker_id)
    if scope == "mine":
        used = select(Policy.insurer_id).where(Policy.broker_id == broker_id)
        overridden = select(BrokerInsurerCollection.insurer_id).where(
            BrokerInsurerCollection.broker_id == broker_id
        )
        stmt = stmt.where(or_(Insurer.id.in_(used), Insurer.id.in_(overridden)))
    if insurer_id is not None:
        stmt = stmt.where(Insurer.id == insurer_id)
    settings_row = _settings(db, broker_id)
    rows = db.scalars(stmt.order_by(Insurer.legal_name.asc())).all()
    return [
        InsurerCapability.model_validate(
            insurer_capability(db, broker_id=broker_id, insurer=insurer, settings_row=settings_row)
        )
        for insurer in rows
    ]


@router.put("/insurers/{insurer_id}/override", response_model=InsurerCapability)
def put_insurer_override(
    insurer_id: int,
    payload: InsurerOverrideUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Settings", "Manage")),
) -> InsurerCapability:
    """Upsert the broker's override (contact, instructions field by field, native opt-out)."""
    insurer = _visible_insurer_or_404(db, insurer_id, broker_id)
    row = broker_override(db, broker_id=broker_id, insurer_id=insurer.id)
    created = row is None
    if row is None:
        row = BrokerInsurerCollection(broker_id=broker_id, insurer_id=insurer.id, native_enabled=True)
        db.add(row)
    fields = payload.model_dump(exclude_unset=True)
    before = {
        "collections_email": row.collections_email,
        "native_enabled": row.native_enabled,
    }
    if "collections_email" in fields:
        row.collections_email = str(fields["collections_email"]) if fields["collections_email"] else None
    if "contact_name" in fields:
        row.contact_name = (fields["contact_name"] or "").strip() or None
    if "payment_instructions" in fields:
        row.payment_instructions = clean_instructions(fields["payment_instructions"]) or None
    if "native_enabled" in fields and fields["native_enabled"] is not None:
        row.native_enabled = bool(fields["native_enabled"])
    if "notes" in fields:
        row.notes = (fields["notes"] or "").strip() or None
    db.flush()
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="insurer_collection.override_created" if created else "insurer_collection.override_updated",
        entity_type=EntityType.INSURER,
        entity_id=insurer.id,
        description="Configuración de cobranza de la compañía actualizada",
        meta={
            "fields": sorted(fields),
            "before": before,
            "after": {"collections_email": row.collections_email, "native_enabled": row.native_enabled},
        },
    )
    db.commit()
    return InsurerCapability.model_validate(
        insurer_capability(db, broker_id=broker_id, insurer=insurer, settings_row=_settings(db, broker_id))
    )
