"""Status inquiries: Radal asks an insurer "¿está pagada la cuota…?" (v12, 03 §7, 04 §2).

Radal cannot read the bank comprobantes (the money lands in the INSURER's
account), so assisted collection ASKS. One ``status_inquiry`` covers 1..N cuotas
of ONE policy and carries a token ``INQ-XXXXXX`` that rides in the subject as
``[RADAL-INQ-XXXXXX]``; the reply is matched back by that token (or by
``In-Reply-To``) in :mod:`app.services.mail.inbound`. The insurer can also answer
without an account through an insurer share link (``can_report_status``).

Sent through Radal, the ``Reply-To`` is the Radal mailbox (which Radal polls)
and the reply is routed to the broker by token. Sent through a broker mailbox,
the reply arrives there (polled when ``receive_enabled``).

Functions flush only; the CALLER commits (and writes the activity row). The
job (:func:`run_collection_inquiries`) commits per policy.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.base_class import utcnow
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.collection_settings import CollectionSettings
from app.models.enums import (
    CollectionLevelPreference,
    InquiryStatus,
    MailMessageStatus,
    NoticeKind,
    ShareAudience,
    ShareEntity,
)
from app.models.insurer import Insurer
from app.models.mail import MailAccount, MailMessage, StatusInquiry
from app.models.policy import Policy, PolicyStatus
from app.models.share_link import ShareLink
from app.services.collection.resolver import insurer_contact_email, lead_insurer_id
from app.services.mail.sender import account_is_usable, send_mail
from app.services.notices import (
    build_context,
    business_today,
    effective_template,
    installment_state,
    render_html,
    render_text,
)
from app.services.share_links import create_share_link, revoke_share_link, share_url

logger = logging.getLogger(__name__)

TOKEN_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"  # RFC 4648 base32
TOKEN_PREFIX = "INQ-"
OPEN_STATUSES = (InquiryStatus.DRAFT, InquiryStatus.SENT, InquiryStatus.REPLIED)
INQUIRY_LINK_DAYS = 60

REASON_NO_INSTALLMENTS = "Indica al menos una cuota de la póliza para consultar"
REASON_FOREIGN_INSTALLMENT = "Las cuotas deben pertenecer a la póliza consultada"
REASON_NO_INSURER_EMAIL = (
    "No hay un correo de cobranza registrado para {insurer}: indica a quién enviar la consulta."
)
REASON_ACCOUNT_REQUIRED = "Elige la casilla de la corredora desde la que se enviará la consulta"
REASON_ACCOUNT_UNUSABLE = "La casilla elegida no está conectada o no tiene el envío habilitado"
REASON_NOT_RESENDABLE = "La consulta está {status}: no se puede reenviar"
REASON_NOT_CANCELLABLE = "La consulta está {status}: no se puede cancelar"

_STATUS_LABELS = {
    InquiryStatus.DRAFT.value: "en borrador",
    InquiryStatus.SENT.value: "enviada",
    InquiryStatus.REPLIED.value: "respondida",
    InquiryStatus.RESOLVED.value: "resuelta",
    InquiryStatus.FAILED.value: "fallida",
    InquiryStatus.CANCELLED.value: "cancelada",
}


REASON_DUPLICATE = (
    "Ya hay una consulta abierta ({token}) por la cuota {numbers}: revísala o reenvíala "
    "en vez de crear otra."
)
REASON_ALL_PAID = "Las cuotas de la consulta ya figuran pagadas o anuladas: no corresponde enviarla"
REASON_AUTO_CANCEL = "Cancelada automáticamente: todas sus cuotas ya figuran pagadas o anuladas"


class InquiryError(Exception):
    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def open_inquiry_for(
    db: Session, *, broker_id: int, policy_id: int, installment_ids: list[int]
) -> tuple[StatusInquiry, list[int]] | None:
    """The OPEN inquiry (draft / sent / replied) already asking about any of
    these cuotas, with the overlapping ids — ``None`` when there is none."""
    wanted = {int(i) for i in installment_ids}
    rows = db.scalars(
        select(StatusInquiry)
        .where(
            StatusInquiry.broker_id == broker_id,
            StatusInquiry.policy_id == policy_id,
            StatusInquiry.status.in_(OPEN_STATUSES),
        )
        .order_by(StatusInquiry.id.desc())
    ).all()
    for row in rows:
        overlap = sorted(wanted & {int(i) for i in (row.installment_ids or [])})
        if overlap:
            return row, overlap
    return None


def settled_ids(db: Session, installments: list[CollectionInstallment], *, today: date) -> set[int]:
    """Cuotas of the list that are paid or voided today."""
    return {
        inst.id
        for inst in installments
        if installment_state(db, inst, today=today).state in ("paid", "voided")
    }


def _val(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def new_inquiry_token(db: Session) -> str:
    """``INQ-`` + 6 base32 characters, unique."""
    for _ in range(20):
        token = TOKEN_PREFIX + "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(6))
        if db.scalar(select(StatusInquiry.id).where(StatusInquiry.token == token)) is None:
            return token
    raise RuntimeError("could not mint a unique inquiry token")  # pragma: no cover


def subject_marker(token: str) -> str:
    return f"[RADAL-{token}]"


def _insurer_label(db: Session, insurer_id: int | None) -> str:
    insurer = db.get(Insurer, insurer_id) if insurer_id is not None else None
    if insurer is None:
        return "la compañía"
    return insurer.trade_name or insurer.legal_name


def policy_installments(db: Session, policy: Policy) -> list[CollectionInstallment]:
    return list(
        db.scalars(
            select(CollectionInstallment)
            .join(CollectionPlan, CollectionPlan.id == CollectionInstallment.collection_plan_id)
            .where(
                CollectionPlan.policy_id == policy.id,
                CollectionInstallment.broker_id == policy.broker_id,
            )
            .order_by(CollectionInstallment.number, CollectionInstallment.id)
        )
    )


def inquiry_installments(db: Session, inquiry: StatusInquiry) -> list[CollectionInstallment]:
    ids = [int(i) for i in (inquiry.installment_ids or []) if str(i).isdigit() or isinstance(i, int)]
    if not ids:
        return []
    return list(
        db.scalars(
            select(CollectionInstallment)
            .where(
                CollectionInstallment.id.in_(ids),
                CollectionInstallment.broker_id == inquiry.broker_id,
            )
            .order_by(CollectionInstallment.number, CollectionInstallment.id)
        )
    )


def _may_send_from(account: MailAccount, user: Any) -> bool:
    """The mail-accounts visibility rule (02 §6, ``mail_accounts._visible_stmt``).

    A shared box (no owner) is the broker's; a PERSONAL box is its owner's,
    and only an admin (``Settings.Manage``) may use a colleague's. No user (a
    system path) never borrows a personal box.
    """
    if account.owner_user_id is None:
        return True
    if user is None:
        return False
    if account.owner_user_id == getattr(user, "id", None):
        return True
    from app.core.permissions import has_permission, resolve_role  # noqa: PLC0415

    return has_permission(resolve_role(user), "Settings", "Manage")


def _resolve_account(
    db: Session,
    *,
    broker_id: int,
    send_via: str | None,
    mail_account_id: int | None,
    user: Any = None,
) -> MailAccount | str | None:
    via = (send_via or "auto").lower()
    if via == "radal":
        return None
    if via == "account":
        if mail_account_id is None:
            raise InquiryError(422, REASON_ACCOUNT_REQUIRED)
        account = db.get(MailAccount, mail_account_id)
        if account is None or account.broker_id != broker_id or not _may_send_from(account, user):
            # A colleague's personal box is invisible (404), exactly as in /mail-accounts.
            raise InquiryError(404, "Casilla no encontrada")
        if not account_is_usable(account, broker_id):
            raise InquiryError(422, REASON_ACCOUNT_UNUSABLE)
        return account
    if via == "auto":
        return "auto"
    raise InquiryError(422, "send_via debe ser radal, account o auto")


@dataclass
class RenderedInquiry:
    subject: str
    text: str
    html: str


def render_inquiry(
    db: Session,
    inquiry: StatusInquiry,
    *,
    installments: list[CollectionInstallment],
    link_url: str,
    message: str | None,
    today: date,
) -> RenderedInquiry:
    template = effective_template(
        db, broker_id=inquiry.broker_id, kind=NoticeKind.INSURER_STATUS_INQUIRY
    )
    context = build_context(
        db,
        broker_id=inquiry.broker_id,
        installment=installments[0],
        today=today,
        share_link_url=link_url,
        inquiry_token=inquiry.token,
        installments=installments,
    )
    context["insurer_name"] = _insurer_label(db, inquiry.insurer_id)
    if template is None:  # pragma: no cover - the seed always carries one
        subject_t = "{{inquiry_token}} Consulta de estado de pago — póliza {{policy_number}}"
        body_t = "{{installments_table}}\n\n{{share_url}}"
    else:
        subject_t, body_t = template.subject, template.body
    subject = render_text(subject_t, context)
    marker = subject_marker(inquiry.token)
    if marker.replace(" ", "") not in subject.replace(" ", ""):
        subject = f"{marker} {subject}"
    if message and message.strip():
        extra = "\n\nMensaje de la corredora:\n" + message.strip()
        body_t = body_t + extra.replace("{{", "{ {")
    return RenderedInquiry(
        subject=subject[:500],
        text=render_text(body_t, context),
        html=render_html(body_t, context),
    )


def _deliver(
    db: Session,
    inquiry: StatusInquiry,
    *,
    account: MailAccount | str | None,
    message: str | None,
    now: datetime,
) -> MailMessage:
    installments = inquiry_installments(db, inquiry)
    link = db.get(ShareLink, inquiry.share_link_id) if inquiry.share_link_id else None
    rendered = render_inquiry(
        db,
        inquiry,
        installments=installments,
        link_url=share_url(link.token) if link is not None else "",
        message=message,
        today=business_today(now),
    )
    # Through Radal the reply must come back to a mailbox Radal polls.
    via_radal = not isinstance(account, MailAccount) and not (
        account == "auto" and _auto_is_account(db, inquiry.broker_id)
    )
    mail = send_mail(
        db,
        broker_id=inquiry.broker_id,
        kind=NoticeKind.INSURER_STATUS_INQUIRY.value,
        to=[inquiry.to_address] if inquiry.to_address else [],
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
        account=account,
        reply_to=settings.RADAL_MAIL_ADDRESS if via_radal else None,
        thread_token=inquiry.token,
        now=now,
    )
    inquiry.outbound_message_id = mail.id
    status_v = _val(mail.status)
    if status_v == MailMessageStatus.SENT.value:
        inquiry.status = InquiryStatus.SENT
        inquiry.sent_at = now
    elif status_v == MailMessageStatus.FAILED.value:
        inquiry.status = InquiryStatus.FAILED
    else:  # skipped (MAIL_ENABLED off): nothing left Radal; the link still works
        inquiry.status = InquiryStatus.DRAFT
    db.flush()
    return mail


def _auto_is_account(db: Session, broker_id: int) -> bool:
    from app.services.mail.sender import auto_account  # noqa: PLC0415

    return auto_account(db, broker_id) is not None


def create_inquiry(
    db: Session,
    *,
    broker_id: int,
    policy: Policy,
    installment_ids: list[int],
    to_address: str | None = None,
    message: str | None = None,
    send_via: str | None = "auto",
    mail_account_id: int | None = None,
    user: Any = None,
    created_by_job: bool = False,
    now: datetime | None = None,
) -> tuple[StatusInquiry, MailMessage]:
    """Create the inquiry, mint its insurer share link and send the mail."""
    moment = now or utcnow()
    if policy.broker_id != broker_id:
        raise InquiryError(404, "Póliza no encontrada")
    wanted = list(dict.fromkeys(int(i) for i in installment_ids or []))
    if not wanted:
        raise InquiryError(422, REASON_NO_INSTALLMENTS)
    own = {inst.id: inst for inst in policy_installments(db, policy)}
    if any(i not in own for i in wanted):
        raise InquiryError(422, REASON_FOREIGN_INSTALLMENT)
    insurer_id = lead_insurer_id(policy) or policy.insurer_id
    to = (to_address or "").strip() or insurer_contact_email(
        db, broker_id=broker_id, insurer_id=insurer_id
    )
    if not to:
        raise InquiryError(
            422, REASON_NO_INSURER_EMAIL.format(insurer=_insurer_label(db, insurer_id))
        )
    account = _resolve_account(
        db, broker_id=broker_id, send_via=send_via, mail_account_id=mail_account_id, user=user
    )
    # One OPEN inquiry per cuota (review v12 P3: duplicates were created silently).
    clash = open_inquiry_for(db, broker_id=broker_id, policy_id=policy.id, installment_ids=wanted)
    if clash is not None:
        existing, overlap = clash
        numbers = ", ".join(str(own[i].number) for i in overlap if i in own)
        raise InquiryError(
            409,
            {
                "code": "inquiry_exists",
                "message": REASON_DUPLICATE.format(token=existing.token, numbers=numbers),
                "existing_inquiry_id": existing.id,
                "existing_inquiry_token": existing.token,
            },
        )
    ordered = sorted(wanted, key=lambda i: (own[i].number, i))
    inquiry = StatusInquiry(
        broker_id=broker_id,
        insurer_id=insurer_id,
        policy_id=policy.id,
        token=new_inquiry_token(db),
        installment_ids=ordered,
        to_address=to[:255],
        status=InquiryStatus.DRAFT,
        created_by_user_id=getattr(user, "id", None),
        created_by_job=created_by_job,
    )
    db.add(inquiry)
    db.flush()
    link = create_share_link(
        db,
        broker_id=broker_id,
        user=user,
        entity_type=ShareEntity.STATUS_INQUIRY,
        entity_id=inquiry.id,
        audience=ShareAudience.INSURER,
        permissions={"can_report_status": True},
        expires_in_days=INQUIRY_LINK_DAYS,
        label=f"Consulta {inquiry.token}",
        now=moment,
    )
    inquiry.share_link_id = link.id
    db.flush()
    mail = _deliver(db, inquiry, account=account, message=message, now=moment)
    return inquiry, mail


def resend_inquiry(
    db: Session,
    inquiry: StatusInquiry,
    *,
    message: str | None = None,
    send_via: str | None = "auto",
    mail_account_id: int | None = None,
    user: Any = None,
    now: datetime | None = None,
) -> MailMessage:
    """Send the same inquiry (same token, same link) again."""
    status_v = _val(inquiry.status)
    if status_v in (InquiryStatus.CANCELLED.value, InquiryStatus.RESOLVED.value):
        raise InquiryError(
            409, REASON_NOT_RESENDABLE.format(status=_STATUS_LABELS.get(status_v, status_v))
        )
    moment = now or utcnow()
    installments = inquiry_installments(db, inquiry)
    if installments and len(settled_ids(db, installments, today=business_today(moment))) == len(
        installments
    ):
        raise InquiryError(409, REASON_ALL_PAID)
    account = _resolve_account(
        db,
        broker_id=inquiry.broker_id,
        send_via=send_via,
        mail_account_id=mail_account_id,
        user=user,
    )
    link = db.get(ShareLink, inquiry.share_link_id) if inquiry.share_link_id else None
    if link is None or link.revoked_at is not None:
        link = create_share_link(
            db,
            broker_id=inquiry.broker_id,
            user=None,
            entity_type=ShareEntity.STATUS_INQUIRY,
            entity_id=inquiry.id,
            audience=ShareAudience.INSURER,
            permissions={"can_report_status": True},
            expires_in_days=INQUIRY_LINK_DAYS,
            label=f"Consulta {inquiry.token}",
            now=moment,
        )
        inquiry.share_link_id = link.id
    previous = status_v
    mail = _deliver(db, inquiry, account=account, message=message, now=moment)
    if previous == InquiryStatus.REPLIED.value and _val(inquiry.status) == InquiryStatus.DRAFT.value:
        inquiry.status = InquiryStatus.REPLIED
    return mail


def cancel_moot_inquiries(db: Session, broker_id: int, *, now: datetime) -> list[StatusInquiry]:
    """Cancel the broker's not-yet-answered inquiries (draft / sent) whose cuotas
    are ALL paid or voided — the job never mails (or leaves pending) a question
    the ledger already answered. Records one activity row each (Spanish reason).
    """
    from app.models.activity import Activity  # noqa: PLC0415
    from app.models.enums import EntityType  # noqa: PLC0415

    today = business_today(now)
    cancelled: list[StatusInquiry] = []
    for inquiry in db.scalars(
        select(StatusInquiry)
        .where(
            StatusInquiry.broker_id == broker_id,
            StatusInquiry.status.in_((InquiryStatus.DRAFT, InquiryStatus.SENT)),
        )
        .order_by(StatusInquiry.id)
    ).all():
        installments = inquiry_installments(db, inquiry)
        if not installments or len(settled_ids(db, installments, today=today)) != len(installments):
            continue
        cancel_inquiry(db, inquiry, now=now)
        db.add(
            Activity(
                broker_id=broker_id,
                user_id=None,
                action="status_inquiry.cancelled",
                entity_type=EntityType.STATUS_INQUIRY,
                entity_id=inquiry.id,
                description=f"Consulta {inquiry.token}: {REASON_AUTO_CANCEL.lower()}",
                meta={"reason": "all_paid"},
            )
        )
        cancelled.append(inquiry)
    db.flush()
    return cancelled


def cancel_inquiry(db: Session, inquiry: StatusInquiry, *, now: datetime | None = None) -> None:
    status_v = _val(inquiry.status)
    if status_v in (InquiryStatus.CANCELLED.value, InquiryStatus.RESOLVED.value):
        raise InquiryError(
            409, REASON_NOT_CANCELLABLE.format(status=_STATUS_LABELS.get(status_v, status_v))
        )
    moment = now or utcnow()
    inquiry.status = InquiryStatus.CANCELLED
    inquiry.resolved_at = moment
    link = db.get(ShareLink, inquiry.share_link_id) if inquiry.share_link_id else None
    if link is not None:
        revoke_share_link(link, now=moment)
    db.flush()


# --- the job -------------------------------------------------------------------------

def _inquiry_step(
    db: Session,
    *,
    broker_id: int,
    policy_id: int,
    installment_ids: list[int],
    followup: int,
    today: date,
    now: datetime,
) -> list[str]:
    """Decide and (maybe) send the auto inquiry of ONE policy; returns stats keys."""
    policy = db.get(Policy, policy_id)
    if policy is None or policy.broker_id != broker_id:
        return []
    open_one = db.scalar(
        select(StatusInquiry.id).where(
            StatusInquiry.broker_id == broker_id,
            StatusInquiry.policy_id == policy_id,
            StatusInquiry.status.in_(OPEN_STATUSES),
        )
    )
    if open_one is not None:
        return ["open_exists"]
    last = db.scalar(
        select(StatusInquiry)
        .where(
            StatusInquiry.broker_id == broker_id,
            StatusInquiry.policy_id == policy_id,
            StatusInquiry.status != InquiryStatus.CANCELLED,
        )
        .order_by(StatusInquiry.created_at.desc(), StatusInquiry.id.desc())
    )
    if last is not None and followup:
        created = last.created_at
        if created is not None and business_today(created) > today - timedelta(days=followup):
            return ["recent"]
    try:
        inquiry, _mail = create_inquiry(
            db,
            broker_id=broker_id,
            policy=policy,
            installment_ids=installment_ids,
            send_via="auto",
            created_by_job=True,
            now=now,
        )
    except InquiryError as exc:
        return ["no_contact" if exc.status_code == 422 else "failed"]
    keys = ["created"]
    if _val(inquiry.status) == InquiryStatus.FAILED.value:
        keys.append("failed")
    return keys


def run_collection_inquiries(db: Session, now: datetime) -> dict[str, Any]:
    """``collection.inquiries``: auto-ask the insurer about overdue cuotas.

    Per broker with ``auto_status_inquiries``: cuotas at least
    ``inquiry_after_days`` past due (still overdue — not paid, voided or
    promised; plan not ``record``), grouped by policy; at most ONE open
    inquiry per policy, and no new one within ``followup_days`` of the last.

    Each policy is its own unit of work (SAVEPOINT + COMMIT right after its
    mail goes out): a later failure can never roll back an inquiry that was
    already mailed — which would re-mail the insurer under a NEW token and
    orphan its reply to the first one. A policy that raises is logged,
    counted in ``errors`` and retried on the next run.
    """
    today = business_today(now)
    stats = {
        "brokers": 0,
        "cancelled_paid": 0,
        "created": 0,
        "open_exists": 0,
        "recent": 0,
        "no_contact": 0,
        "failed": 0,
        "errors": 0,
    }
    # Every broker (auto inquiries on or off): an open, unanswered inquiry whose
    # cuotas are all paid is cancelled with a reason — never left "Sin enviar".
    sweep_brokers = sorted(
        set(
            db.scalars(
                select(StatusInquiry.broker_id).where(
                    StatusInquiry.status.in_((InquiryStatus.DRAFT, InquiryStatus.SENT))
                )
            )
        )
    )
    for broker_id in sweep_brokers:
        try:
            with db.begin_nested():
                stats["cancelled_paid"] += len(cancel_moot_inquiries(db, broker_id, now=now))
            db.commit()
        except Exception:  # noqa: BLE001 - one broker's sweep must not stop the rest
            db.rollback()
            logger.exception("inquiries: broker %s sweep failed", broker_id)
            stats["errors"] += 1
    rows = [
        (row.broker_id, int(row.inquiry_after_days or 0), int(row.followup_days or 0))
        for row in db.scalars(
            select(CollectionSettings)
            .where(CollectionSettings.auto_status_inquiries.is_(True))
            .order_by(CollectionSettings.broker_id)
        )
    ]
    for broker_id, after_days, followup_days in rows:
        stats["brokers"] += 1
        after = max(0, after_days)
        followup = max(0, followup_days)
        cutoff = today - timedelta(days=after)
        try:
            candidates = list(
                db.scalars(
                    select(CollectionInstallment)
                    .join(CollectionPlan, CollectionPlan.id == CollectionInstallment.collection_plan_id)
                    .join(Policy, Policy.id == CollectionPlan.policy_id)
                    .where(
                        CollectionInstallment.broker_id == broker_id,
                        CollectionPlan.broker_id == broker_id,
                        Policy.broker_id == broker_id,
                        CollectionInstallment.due_date.is_not(None),
                        CollectionInstallment.due_date <= cutoff,
                        CollectionPlan.level_preference != CollectionLevelPreference.RECORD,
                        Policy.status.not_in([PolicyStatus.CANCELLED, PolicyStatus.DRAFT]),
                    )
                    .order_by(Policy.id, CollectionInstallment.number)
                )
            )
            by_policy: dict[int, list[int]] = {}
            for inst in candidates:
                state = installment_state(db, inst, today=today)
                if state.state not in ("overdue", "termination_risk"):
                    continue
                by_policy.setdefault(inst.collection_plan.policy_id, []).append(inst.id)
        except Exception:  # noqa: BLE001 - one broker must not stop the others
            db.rollback()
            logger.exception("inquiries: broker %s could not be planned", broker_id)
            stats["errors"] += 1
            continue
        for policy_id, inst_ids in by_policy.items():
            try:
                with db.begin_nested():
                    keys = _inquiry_step(
                        db,
                        broker_id=broker_id,
                        policy_id=policy_id,
                        installment_ids=inst_ids,
                        followup=followup,
                        today=today,
                        now=now,
                    )
                db.commit()  # the mailed inquiry (token + link + mail row) is kept
            except Exception:  # noqa: BLE001 - one policy must not lose the others' sends
                db.rollback()
                logger.exception("inquiries: policy %s failed", policy_id)
                stats["errors"] += 1
                continue
            for key in keys:
                stats[key] += 1
    return stats


__all__ = [
    "InquiryError",
    "OPEN_STATUSES",
    "cancel_inquiry",
    "cancel_moot_inquiries",
    "create_inquiry",
    "open_inquiry_for",
    "settled_ids",
    "inquiry_installments",
    "new_inquiry_token",
    "policy_installments",
    "render_inquiry",
    "resend_inquiry",
    "run_collection_inquiries",
    "subject_marker",
]
