"""``/inquiries`` — status inquiries, the evidence inbox and payer notices (v12, 02 §6).

Owner: lane **B4**.

**Path deviation (reported):** the contract places the evidence inbox at
``/collections/evidence`` and the notices at ``/collections/notices/*``. Those
prefixes belong to lane B2's ``routers/collections.py`` and ``main.py`` is
frozen, so they live on THIS router (prefix ``/inquiries``):

* ``POST /inquiries`` (Collections.Edit) — create + mint the insurer share
  link + send ``[RADAL-INQ-XXXXXX]``;
* ``GET /inquiries`` · ``GET /inquiries/{id}`` (Collections.View);
* ``POST /inquiries/{id}/resend`` · ``/inquiries/{id}/cancel`` (Collections.Edit);
* ``GET /inquiries/evidence`` (Collections.View) — "Respuestas por confirmar";
* ``POST /inquiries/evidence/{id}/confirm`` · ``/reject`` (Collections.Edit);
* ``POST /inquiries/notices/preview`` · ``/inquiries/notices/send``
  (Collections.Edit) — a payer reminder for one cuota now (WhatsApp -> 409
  "pronto");
* ``GET /inquiries/notices?installment_id=`` (Collections.View).

Static paths are declared BEFORE ``/{inquiry_id}``. Every row is scoped by the
caller's ``broker_id``: another tenant's inquiry, evidence, policy or cuota is a
404 on every path.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db
from app.api.routers.clients import record_activity
from app.core.permissions import require_permission
from app.models.base_class import utcnow
from app.models.collection import CollectionInstallment
from app.models.collection_settings import CollectionNotice
from app.models.enums import (
    EntityType,
    EvidenceStatus,
    InquiryStatus,
    NoticeChannel,
    PaymentStatus,
)
from app.models.insurer import Insurer
from app.models.mail import MailMessage, PaymentEvidence, StatusInquiry
from app.models.policy import Policy
from app.models.share_link import ShareLink
from app.models.user import User
from app.schemas.inquiries import (
    EvidenceConfirm,
    EvidenceInstallmentSummary,
    EvidencePaymentLine,
    EvidenceDecision,
    EvidencePage,
    EvidenceRead,
    EvidenceReject,
    InquiryCreate,
    InquiryDetail,
    InquiryInstallment,
    InquiryMail,
    InquiryPage,
    InquiryRead,
    InquiryResend,
    NoticePage,
    NoticePreview,
    NoticeRead,
    NoticeRequest,
)
from app.services.mail.evidence import (
    OPEN_EVIDENCE,
    EvidenceError,
    confirm_evidence,
    reject_evidence,
    suggested_amount_uf,
)
from app.services.mail.inquiries import (
    InquiryError,
    cancel_inquiry,
    create_inquiry,
    inquiry_installments,
    resend_inquiry,
)
from app.services.notices import (
    NoticeError,
    business_today,
    draft_notice,
    installment_state,
    insured_name,
    send_notice,
)
from app.services.identifiers import display_rut
from app.services.share_links import share_url

router = APIRouter(prefix="/inquiries", tags=["inquiries"])

INQUIRY_NOT_FOUND = "Consulta no encontrada"
EVIDENCE_NOT_FOUND = "Respuesta no encontrada"
POLICY_NOT_FOUND = "Póliza no encontrada"
INSTALLMENT_NOT_FOUND = "Cuota no encontrada"


def _val(value):  # noqa: ANN001, ANN202
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _http(exc: InquiryError | EvidenceError | NoticeError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _insurer_name(db: Session, insurer_id: int | None) -> str | None:
    insurer = db.get(Insurer, insurer_id) if insurer_id is not None else None
    if insurer is None:
        return None
    return insurer.trade_name or insurer.legal_name


# --- read builders ----------------------------------------------------------------

def _inquiry_read(db: Session, inquiry: StatusInquiry, *, cls=InquiryRead):  # noqa: ANN001
    policy = db.get(Policy, inquiry.policy_id)
    link = db.get(ShareLink, inquiry.share_link_id) if inquiry.share_link_id else None
    mail = db.get(MailMessage, inquiry.outbound_message_id) if inquiry.outbound_message_id else None
    open_count = db.scalar(
        select(func.count(PaymentEvidence.id)).where(
            PaymentEvidence.broker_id == inquiry.broker_id,
            PaymentEvidence.status_inquiry_id == inquiry.id,
            PaymentEvidence.status.in_(OPEN_EVIDENCE),
        )
    )
    today = business_today()
    rows = []
    for i in inquiry_installments(db, inquiry):
        state = installment_state(db, i, today=today)
        rows.append(
            InquiryInstallment(
                id=i.id,
                number=i.number,
                due_date=i.due_date,
                gross_amount_uf=i.gross_amount_uf,
                currency=_val(i.currency) or "UF",
                status=_val(i.status) or "",
                state=state.state,
                is_paid=state.state in ("paid", "voided"),
                balance_uf=state.balance_uf,
            )
        )
    client = getattr(policy, "client", None) if policy is not None else None
    insured = getattr(client, "insured", None) if client is not None else None
    return cls(
        id=inquiry.id,
        token=inquiry.token,
        status=inquiry.status,
        policy_id=inquiry.policy_id,
        policy_number=policy.policy_number if policy is not None else None,
        insured_name=insured_name(policy) if policy is not None else None,
        insured_rut=display_rut(getattr(insured, "rut", None)) or None,
        insurer_id=inquiry.insurer_id,
        insurer_name=_insurer_name(db, inquiry.insurer_id),
        to_address=inquiry.to_address,
        installment_ids=[int(i) for i in inquiry.installment_ids or []],
        installments=rows,
        share_link_id=inquiry.share_link_id,
        share_url=share_url(link.token) if (link is not None and link.revoked_at is None) else None,
        outbound_message_id=inquiry.outbound_message_id,
        mail_status=mail.status if mail is not None else None,
        sent_at=inquiry.sent_at,
        replied_at=inquiry.replied_at,
        resolved_at=inquiry.resolved_at,
        created_by_user_id=inquiry.created_by_user_id,
        created_by_job=inquiry.created_by_job,
        created_at=inquiry.created_at,
        evidence_open=int(open_count or 0),
        all_paid=bool(rows) and all(r.is_paid for r in rows),
    )


def _sender_verified(reasons) -> bool | None:  # noqa: ANN001
    if not isinstance(reasons, list):
        return None
    if "sender_unverified" in reasons:
        return False
    if "sender_matches_insurer" in reasons:
        return True
    return None


def _installment_summary(db: Session, inst: CollectionInstallment) -> EvidenceInstallmentSummary:
    state = installment_state(db, inst, today=business_today())
    return EvidenceInstallmentSummary(
        gross_uf=inst.gross_amount_uf,
        paid_uf=state.paid_uf,
        balance_uf=state.balance_uf,
        state=state.state,
        currency=_val(inst.currency) or "UF",
        payments=[
            EvidencePaymentLine(
                id=p.id,
                amount_uf=p.amount_uf,
                evidence_level=_val(p.evidence_level) or "declared",
                paid_on=p.paid_on,
                source=_val(p.source),
            )
            for p in sorted(inst.payments, key=lambda p: (p.paid_on or date.min, p.id or 0))
            if _val(p.status) == PaymentStatus.ACTIVE.value
        ],
    )


def _evidence_read(db: Session, ev: PaymentEvidence) -> EvidenceRead:
    inst = db.get(CollectionInstallment, ev.installment_id) if ev.installment_id else None
    policy: Policy | None = None
    if inst is not None:
        policy = inst.collection_plan.policy
    elif ev.status_inquiry is not None:
        policy = db.get(Policy, ev.status_inquiry.policy_id)
    mail = ev.mail_message
    return EvidenceRead(
        id=ev.id,
        status=ev.status,
        source=ev.source,
        installment_id=ev.installment_id,
        installment_number=inst.number if inst is not None else None,
        installment_due_date=inst.due_date if inst is not None else None,
        installment_amount_uf=inst.gross_amount_uf if inst is not None else None,
        policy_id=policy.id if policy is not None else None,
        policy_number=policy.policy_number if policy is not None else None,
        insurer_name=_insurer_name(db, policy.insurer_id) if policy is not None else None,
        status_inquiry_id=ev.status_inquiry_id,
        inquiry_token=ev.status_inquiry.token if ev.status_inquiry is not None else None,
        mail_message_id=ev.mail_message_id,
        mail_subject=mail.subject if mail is not None else None,
        mail_from=mail.from_address if mail is not None else None,
        document_id=ev.document_id,
        extraction_id=ev.extraction_id,
        reported_status=ev.reported_status,
        reported_amount=ev.reported_amount,
        reported_currency=ev.reported_currency,
        reported_paid_on=ev.reported_paid_on,
        score=ev.score,
        reasons=ev.reasons,
        sender_verified=_sender_verified(ev.reasons),
        note=ev.note,
        payment_id=ev.payment_id,
        decided_at=ev.decided_at,
        created_at=ev.created_at,
        installment_summary=_installment_summary(db, inst) if inst is not None else None,
        suggested_amount_uf=(
            suggested_amount_uf(db, ev, inst, today=business_today())
            if inst is not None and _val(ev.status) in {s.value for s in OPEN_EVIDENCE}
            else None
        ),
    )


def _get_inquiry_or_404(db: Session, inquiry_id: int, broker_id: int) -> StatusInquiry:
    row = db.scalar(
        select(StatusInquiry).where(StatusInquiry.id == inquiry_id, StatusInquiry.broker_id == broker_id)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=INQUIRY_NOT_FOUND)
    return row


def _get_evidence_or_404(db: Session, evidence_id: int, broker_id: int) -> PaymentEvidence:
    row = db.scalar(
        select(PaymentEvidence).where(
            PaymentEvidence.id == evidence_id, PaymentEvidence.broker_id == broker_id
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=EVIDENCE_NOT_FOUND)
    return row


def _get_installment_or_404(db: Session, installment_id: int, broker_id: int) -> CollectionInstallment:
    row = db.scalar(
        select(CollectionInstallment).where(
            CollectionInstallment.id == installment_id,
            CollectionInstallment.broker_id == broker_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=INSTALLMENT_NOT_FOUND)
    return row


# --- inquiries ---------------------------------------------------------------------------

@router.post("", response_model=InquiryRead, status_code=status.HTTP_201_CREATED)
def create_status_inquiry(
    payload: InquiryCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> InquiryRead:
    policy = db.scalar(
        select(Policy).where(Policy.id == payload.policy_id, Policy.broker_id == broker_id)
    )
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=POLICY_NOT_FOUND)
    try:
        inquiry, mail = create_inquiry(
            db,
            broker_id=broker_id,
            policy=policy,
            installment_ids=payload.installment_ids,
            to_address=payload.to_address,
            message=payload.message,
            send_via=payload.send_via,
            mail_account_id=payload.mail_account_id,
            user=current_user,
        )
    except InquiryError as exc:
        db.rollback()
        raise _http(exc) from None
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="status_inquiry.created",
        entity_type=EntityType.STATUS_INQUIRY,
        entity_id=inquiry.id,
        description=f"Consulta de estado {inquiry.token} a {inquiry.to_address}",
        meta={
            "policy_id": policy.id,
            "installment_ids": list(inquiry.installment_ids),
            "mail_status": _val(mail.status),
        },
    )
    db.commit()
    db.refresh(inquiry)
    return _inquiry_read(db, inquiry)


@router.get("", response_model=InquiryPage)
def list_status_inquiries(
    status_filter: InquiryStatus | None = Query(default=None, alias="status"),
    policy_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> InquiryPage:
    stmt = select(StatusInquiry).where(StatusInquiry.broker_id == broker_id)
    if status_filter is not None:
        stmt = stmt.where(StatusInquiry.status == status_filter)
    if policy_id is not None:
        stmt = stmt.where(StatusInquiry.policy_id == policy_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(StatusInquiry.created_at.desc(), StatusInquiry.id.desc()).offset(offset).limit(limit)
    ).all()
    return InquiryPage(
        items=[_inquiry_read(db, row) for row in rows], total=int(total), limit=limit, offset=offset
    )


# --- evidence inbox (static paths BEFORE /{inquiry_id}) --------------------------------------

@router.get("/evidence", response_model=EvidencePage)
def list_evidence(
    status_filter: list[EvidenceStatus] | None = Query(default=None, alias="status"),
    inquiry_id: int | None = Query(default=None),
    installment_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> EvidencePage:
    """Without ``status``: the open ones (``suggested`` + ``needs_review``)."""
    stmt = select(PaymentEvidence).where(PaymentEvidence.broker_id == broker_id)
    statuses = status_filter or list(OPEN_EVIDENCE)
    stmt = stmt.where(PaymentEvidence.status.in_(statuses))
    if inquiry_id is not None:
        stmt = stmt.where(PaymentEvidence.status_inquiry_id == inquiry_id)
    if installment_id is not None:
        stmt = stmt.where(PaymentEvidence.installment_id == installment_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(PaymentEvidence.created_at.desc(), PaymentEvidence.id.desc()).offset(offset).limit(limit)
    ).all()
    return EvidencePage(
        items=[_evidence_read(db, row) for row in rows], total=int(total), limit=limit, offset=offset
    )


@router.post("/evidence/{evidence_id}/confirm", response_model=EvidenceDecision)
def confirm_payment_evidence(
    evidence_id: int,
    payload: EvidenceConfirm,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> EvidenceDecision:
    evidence = _get_evidence_or_404(db, evidence_id, broker_id)
    if payload.installment_id is not None:
        _get_installment_or_404(db, payload.installment_id, broker_id)
    try:
        payment = confirm_evidence(
            db,
            evidence,
            user=current_user,
            installment_id=payload.installment_id,
            amount=payload.amount,
            currency=payload.currency,
            paid_on=payload.paid_on,
            confirmed_by_insurer=payload.confirmed_by_insurer,
            note=payload.note,
        )
    except EvidenceError as exc:
        db.rollback()
        raise _http(exc) from None
    inst = db.get(CollectionInstallment, evidence.installment_id) if evidence.installment_id else None
    upgraded = list(getattr(evidence, "_upgraded_payment_ids", []) or [])
    if upgraded and inst is not None:
        record_activity(
            db,
            broker_id=broker_id,
            user=current_user,
            action="payment.confirmed",
            entity_type=EntityType.COLLECTION_PLAN,
            entity_id=inst.collection_plan_id,
            description=(
                f"Cuota {inst.number}: la compañía confirmó "
                + ("el pago declarado" if len(upgraded) == 1 else f"{len(upgraded)} pagos declarados")
            ),
            meta={"installment_id": inst.id, "payment_ids": upgraded, "evidence_id": evidence.id},
        )
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="payment_evidence.confirmed",
        entity_type=EntityType.INSTALLMENT if inst is not None else EntityType.STATUS_INQUIRY,
        entity_id=inst.id if inst is not None else evidence.status_inquiry_id,
        description=(
            "Respuesta de la compañía confirmada"
            + (f": pago registrado en la cuota {inst.number}" if (payment and inst) else "")
        ),
        meta={
            "evidence_id": evidence.id,
            "payment_id": payment.id if payment is not None else None,
            "reported_status": _val(evidence.reported_status),
        },
    )
    db.commit()
    db.refresh(evidence)
    return EvidenceDecision(
        evidence=_evidence_read(db, evidence),
        payment_id=payment.id if payment is not None else None,
        installment_status=_val(inst.status) if inst is not None else None,
    )


@router.post("/evidence/{evidence_id}/reject", response_model=EvidenceDecision)
def reject_payment_evidence(
    evidence_id: int,
    payload: EvidenceReject | None = None,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> EvidenceDecision:
    evidence = _get_evidence_or_404(db, evidence_id, broker_id)
    try:
        reject_evidence(db, evidence, user=current_user, note=payload.note if payload else None)
    except EvidenceError as exc:
        db.rollback()
        raise _http(exc) from None
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="payment_evidence.rejected",
        entity_type=EntityType.INSTALLMENT if evidence.installment_id else EntityType.STATUS_INQUIRY,
        entity_id=evidence.installment_id or evidence.status_inquiry_id,
        description="Respuesta de la compañía descartada",
        meta={"evidence_id": evidence.id},
    )
    db.commit()
    db.refresh(evidence)
    return EvidenceDecision(evidence=_evidence_read(db, evidence))


# --- payer notices ----------------------------------------------------------------------------

@router.post("/notices/preview", response_model=NoticePreview)
def preview_notice(
    payload: NoticeRequest,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "Edit")),
) -> NoticePreview:
    inst = _get_installment_or_404(db, payload.installment_id, broker_id)
    try:
        draft = draft_notice(
            db,
            broker_id=broker_id,
            installment=inst,
            kind=payload.kind,
            channel=payload.channel,
            today=business_today(),
        )
    except NoticeError as exc:
        raise _http(exc) from None
    return NoticePreview(
        installment_id=inst.id,
        kind=payload.kind,
        channel=NoticeChannel(draft.channel),
        to_address=draft.to_address,
        subject=draft.subject,
        text=draft.text,
        html=draft.html,
        level=draft.mode.level,
        level_reason=draft.mode.reason,
        missing=list(draft.mode.missing),
        warnings=draft.warnings,
        template_source=draft.template_source,
        recipient_name=draft.recipient_name,
        amount_uf=draft.amount_uf,
        paid_uf=draft.paid_uf,
        gross_uf=draft.gross_uf,
    )


@router.post("/notices/send", response_model=NoticeRead, status_code=status.HTTP_201_CREATED)
def send_payer_notice(
    payload: NoticeRequest,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> NoticeRead:
    inst = _get_installment_or_404(db, payload.installment_id, broker_id)
    try:
        notice = send_notice(
            db,
            broker_id=broker_id,
            installment=inst,
            kind=payload.kind,
            channel=payload.channel,
            user=current_user,
            now=utcnow(),
        )
    except NoticeError as exc:
        db.rollback()
        raise _http(exc) from None
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="collection_notice.sent",
        entity_type=EntityType.INSTALLMENT,
        entity_id=inst.id,
        description=f"Aviso al pagador ({_val(payload.kind)}) a {notice.to_address}",
        meta={"notice_id": notice.id, "status": _val(notice.status)},
    )
    db.commit()
    db.refresh(notice)
    return NoticeRead.model_validate(notice)


@router.get("/notices", response_model=NoticePage)
def list_notices(
    installment_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> NoticePage:
    stmt = select(CollectionNotice).where(CollectionNotice.broker_id == broker_id)
    if installment_id is not None:
        stmt = stmt.where(CollectionNotice.installment_id == installment_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(CollectionNotice.created_at.desc(), CollectionNotice.id.desc()).offset(offset).limit(limit)
    ).all()
    return NoticePage(
        items=[NoticeRead.model_validate(row) for row in rows], total=int(total), limit=limit, offset=offset
    )


# --- one inquiry ---------------------------------------------------------------------------------

@router.get("/{inquiry_id}", response_model=InquiryDetail)
def get_status_inquiry(
    inquiry_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> InquiryDetail:
    inquiry = _get_inquiry_or_404(db, inquiry_id, broker_id)
    detail: InquiryDetail = _inquiry_read(db, inquiry, cls=InquiryDetail)
    messages = db.scalars(
        select(MailMessage)
        .where(MailMessage.broker_id == broker_id, MailMessage.thread_token == inquiry.token)
        .order_by(MailMessage.id)
    ).all()
    detail.messages = [
        InquiryMail(
            id=m.id,
            status=m.status,
            from_address=m.from_address,
            to_addresses=m.to_addresses,
            subject=m.subject,
            occurred_at=m.occurred_at,
            error=m.error,
        )
        for m in messages
    ]
    detail.evidence = [
        _evidence_read(db, ev)
        for ev in db.scalars(
            select(PaymentEvidence)
            .where(PaymentEvidence.broker_id == broker_id, PaymentEvidence.status_inquiry_id == inquiry.id)
            .order_by(PaymentEvidence.id)
        )
    ]
    return detail


@router.post("/{inquiry_id}/resend", response_model=InquiryRead)
def resend_status_inquiry(
    inquiry_id: int,
    payload: InquiryResend | None = None,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> InquiryRead:
    inquiry = _get_inquiry_or_404(db, inquiry_id, broker_id)
    body = payload or InquiryResend()
    try:
        mail = resend_inquiry(
            db,
            inquiry,
            message=body.message,
            send_via=body.send_via,
            mail_account_id=body.mail_account_id,
            user=current_user,
        )
    except InquiryError as exc:
        db.rollback()
        raise _http(exc) from None
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="status_inquiry.resent",
        entity_type=EntityType.STATUS_INQUIRY,
        entity_id=inquiry.id,
        description=f"Consulta {inquiry.token} reenviada",
        meta={"mail_status": _val(mail.status)},
    )
    db.commit()
    db.refresh(inquiry)
    return _inquiry_read(db, inquiry)


@router.post("/{inquiry_id}/cancel", response_model=InquiryRead)
def cancel_status_inquiry(
    inquiry_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> InquiryRead:
    inquiry = _get_inquiry_or_404(db, inquiry_id, broker_id)
    try:
        cancel_inquiry(db, inquiry)
    except InquiryError as exc:
        db.rollback()
        raise _http(exc) from None
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="status_inquiry.cancelled",
        entity_type=EntityType.STATUS_INQUIRY,
        entity_id=inquiry.id,
        description=f"Consulta {inquiry.token} cancelada",
    )
    db.commit()
    db.refresh(inquiry)
    return _inquiry_read(db, inquiry)


# =============================================================================
# Contract paths (02 §6): the same endpoints under /collections/*
# =============================================================================
# 02-backend-api §6 names the evidence inbox and the payer notices under
# ``/collections``; lane B4 built them under ``/inquiries`` (its own router).
# Both doors serve the SAME functions (same gates, shapes and errors), so a
# client may use either. ``/collections`` declares only ``{plan_id:int}``
# dynamic paths, so these static paths can never collide with it.
collections_router = APIRouter(prefix="/collections", tags=["inquiries"])
collections_router.add_api_route(
    "/evidence", list_evidence, methods=["GET"], response_model=EvidencePage
)
collections_router.add_api_route(
    "/evidence/{evidence_id}/confirm",
    confirm_payment_evidence,
    methods=["POST"],
    response_model=EvidenceDecision,
)
collections_router.add_api_route(
    "/evidence/{evidence_id}/reject",
    reject_payment_evidence,
    methods=["POST"],
    response_model=EvidenceDecision,
)
collections_router.add_api_route(
    "/notices/preview", preview_notice, methods=["POST"], response_model=NoticePreview
)
collections_router.add_api_route(
    "/notices/send",
    send_payer_notice,
    methods=["POST"],
    response_model=NoticeRead,
    status_code=status.HTTP_201_CREATED,
)
collections_router.add_api_route(
    "/notices", list_notices, methods=["GET"], response_model=NoticePage
)
