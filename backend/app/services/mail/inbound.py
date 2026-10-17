"""Inbound mail: poll, PREFILTER, keep, classify, suggest (v12, 04-mail §4).

``mail.poll`` (:func:`run_mail_poll`) reads the Radal system mailbox (the
NULL-broker ``mail_account`` row, created lazily, which only stores the IMAP
cursor) and every broker mailbox with ``receive_enabled`` and
``status=connected``.

**Privacy first (Ley 21.719).** :func:`prefilter` runs BEFORE anything about a
message is stored. A message is kept only when it is

(a) ours by thread — a ``RADAL-INQ-XXXXXX`` token in subject/body, or an
    ``In-Reply-To`` / ``References`` id equal to one of our outbound
    ``message_id_header``s;
(b) from an insurer — the sender's domain is in some insurer's
    ``sender_domains`` AND the mailbox is a BROKER account (never the shared
    Radal box); or
(c) a bounce (``mailer-daemon`` / ``postmaster`` / a DSN report) of one of our
    outbound messages.

Anything else is recorded as ``{provider_uid, occurred_at, status=ignored}`` —
no sender, no subject, no body — only so the same UID is never re-read.
``provider_uid`` is ``{uidvalidity}:{uid}`` (:func:`uid_key`): a UIDVALIDITY
reset renumbers from 1, and a bare UID would make new mail look "seen". The
2-day re-read after such a reset is deduplicated by ``Message-ID`` (a kept
message is never processed twice).

**Sender check.** A token is not a secret (it rides in a subject that gets
forwarded), so evidence scoring asks :func:`sender_verified`: the From must be
the inquiry's / replied outbound's recipient (same address, or same domain
unless it is a public mailbox provider) or in the insurer's
``sender_domains``. Otherwise the reason ``sender_unverified`` is added and
the score is capped below :data:`SUGGEST_THRESHOLD` (always ``needs_review``). On a
broker mailbox a match is always scoped to THAT broker (another tenant's token
arriving there is ignored), so a mailbox can never route mail across tenants.

A kept message: ``.eml`` saved as a ``document(category=email_message)`` on
the inquiry (or the policy it names, else the mailbox) through the documents
router's storage helper (rule 8), classified + read by
:mod:`app.services.mail.classifier` (always an ``extraction`` row), then for
``insurer_status_reply | insurer_statement | cancellation_notice`` one
``payment_evidence`` per referenced cuota (score + reasons), the inquiry moves
to ``replied`` and the bell rings (``inquiry_reply`` / ``evidence_pending``).
A bounce marks the ``collection_notice`` ``bounced`` (and a bounced inquiry
``failed``). A mailbox that cannot be read goes ``error`` and raises a
``mail_account_error`` to-do.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.base_class import utcnow
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.collection_settings import CollectionNotice
from app.models.document import DocumentCategory
from app.models.enums import (
    EntityType,
    EvidenceSource,
    EvidenceStatus,
    InboundClass,
    InquiryStatus,
    MailAccountStatus,
    MailDirection,
    MailMessageStatus,
    MailProvider,
    NoticeStatus,
    NotificationKind,
    NotificationTray,
    PaymentCurrency,
    ReportedStatus,
)
from app.models.insurer_collection import InsurerCollectionProfile
from app.models.mail import MailAccount, MailMessage, PaymentEvidence, StatusInquiry
from app.models.policy import Policy
from app.models.user import User
from app.services.mail.classifier import MailReading, classify_mail
from app.services.mail.inquiries import inquiry_installments, policy_installments
from app.services.mail.sender import account_imap_config
from app.services.mail.transport import (
    FetchedMail,
    MailTransportError,
    get_transport,
    radal_imap_config,
)
from app.services.notifications import broker_admin_ids, notify, recipients_for_policy

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"RADAL-(INQ-[A-Z2-7]{6})", re.IGNORECASE)
MSGID_RE = re.compile(r"<[^<>\s]+@[^<>\s]+>")
_BOUNCE_LOCALS = ("mailer-daemon", "postmaster", "mail-daemon")
EVIDENCE_CLASSES = (
    InboundClass.INSURER_STATUS_REPLY.value,
    InboundClass.INSURER_STATEMENT.value,
    InboundClass.CANCELLATION_NOTICE.value,
)
SUGGEST_THRESHOLD = Decimal("70")
FETCH_LIMIT = 200


def _val(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def uid_key(fetched: FetchedMail) -> str:
    """``provider_uid`` = ``{uidvalidity}:{uid}``.

    A UID is only unique inside its UIDVALIDITY epoch: after a reset the
    server renumbers from 1, so a bare UID would collide with an old message
    and the new mail would be skipped as "already seen".
    """
    return f"{fetched.uidvalidity or '0'}:{fetched.uid}"[:64]


# --- parsing helpers ------------------------------------------------------------------

def body_text(msg: EmailMessage) -> str:
    """The best plain-text rendering of a message (HTML stripped as a fallback)."""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except Exception:  # noqa: BLE001
        part = None
    if part is None:
        if msg.is_multipart():
            return ""
        try:
            return str(msg.get_content())
        except Exception:  # noqa: BLE001
            payload = msg.get_payload(decode=True)
            return payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""
    try:
        content = str(part.get_content())
    except Exception:  # noqa: BLE001
        payload = part.get_payload(decode=True)
        content = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""
    if part.get_content_subtype() == "html":
        content = re.sub(r"(?is)<(script|style).*?</\1>", " ", content)
        content = re.sub(r"(?i)<br\s*/?>|</p>", "\n", content)
        content = re.sub(r"<[^>]+>", " ", content)
        content = re.sub(r"[ \t]+", " ", content)
    return content.strip()


def _header(msg: EmailMessage, name: str) -> str:
    try:
        return str(msg.get(name, "") or "")
    except Exception:  # noqa: BLE001 - a malformed header is just absent
        return ""


def reference_ids(msg: EmailMessage) -> list[str]:
    ids: list[str] = []
    for name in ("In-Reply-To", "References"):
        for found in MSGID_RE.findall(_header(msg, name)):
            if found not in ids:
                ids.append(found)
    return ids


def sender_address(msg: EmailMessage) -> str:
    return parseaddr(_header(msg, "From"))[1].strip().lower()


def sender_domain(address: str) -> str:
    return address.rpartition("@")[2].strip().lower()


def occurred_at(msg: EmailMessage, *, now: datetime) -> datetime:
    raw = _header(msg, "Date")
    if raw:
        try:
            value = parsedate_to_datetime(raw)
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value
        except (TypeError, ValueError, IndexError):
            pass
    return now


def is_bounce(msg: EmailMessage) -> bool:
    local = sender_address(msg).partition("@")[0]
    if any(local.startswith(prefix) for prefix in _BOUNCE_LOCALS):
        return True
    if msg.get_content_type() == "multipart/report":
        return "delivery-status" in (msg.get_param("report-type") or "").lower()
    return False


def bounced_message_ids(msg: EmailMessage, raw: bytes) -> list[str]:
    """Message-IDs of the ORIGINAL mail a DSN reports on."""
    ids = list(reference_ids(msg))
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype in ("message/rfc822", "text/rfc822-headers", "message/rfc822-headers"):
            payload = part.get_payload()
            candidates: list[Any] = payload if isinstance(payload, list) else [payload]
            for item in candidates:
                text = item.as_string() if hasattr(item, "as_string") else str(item or "")
                for line in text.splitlines():
                    if line.lower().startswith("message-id:"):
                        for found in MSGID_RE.findall(line):
                            if found not in ids:
                                ids.append(found)
    if not ids:
        text = raw.decode("utf-8", "replace")
        for match in re.finditer(r"(?im)^message-id:\s*(<[^<>\s]+@[^<>\s]+>)", text):
            if match.group(1) not in ids:
                ids.append(match.group(1))
    return ids


# --- the prefilter -------------------------------------------------------------------------

@dataclass
class Match:
    reason: str  # token | reply | insurer_domain | bounce
    broker_id: int | None
    inquiry: StatusInquiry | None = None
    outbound: MailMessage | None = None
    insurer_id: int | None = None
    reasons: list[str] = field(default_factory=list)


def _outbound_by_ids(db: Session, ids: list[str], broker_id: int | None) -> MailMessage | None:
    if not ids:
        return None
    stmt = select(MailMessage).where(
        MailMessage.direction == MailDirection.OUTBOUND,
        MailMessage.message_id_header.in_(ids),
    )
    if broker_id is not None:
        stmt = stmt.where(MailMessage.broker_id == broker_id)
    return db.scalar(stmt.order_by(MailMessage.id.desc()))


def _inquiry_for_outbound(db: Session, outbound: MailMessage) -> StatusInquiry | None:
    inquiry = db.scalar(
        select(StatusInquiry).where(StatusInquiry.outbound_message_id == outbound.id)
    )
    if inquiry is None and outbound.thread_token:
        inquiry = db.scalar(
            select(StatusInquiry).where(StatusInquiry.token == outbound.thread_token.upper())
        )
    if inquiry is not None and outbound.broker_id is not None and inquiry.broker_id != outbound.broker_id:
        return None
    return inquiry


def _insurer_by_domain(db: Session, domain: str) -> int | None:
    if not domain:
        return None
    for profile in db.scalars(
        select(InsurerCollectionProfile).where(InsurerCollectionProfile.sender_domains.is_not(None))
    ):
        for item in profile.sender_domains or []:
            known = str(item or "").strip().lower().lstrip("@")
            if known and (domain == known or domain.endswith("." + known)):
                return profile.insurer_id
    return None


def prefilter(
    db: Session, account: MailAccount, msg: EmailMessage, raw: bytes
) -> Match | None:
    """Decide whether a message may be stored at all. ``None`` = ignore."""
    scope = account.broker_id  # None = the Radal system box (any broker by token)

    if is_bounce(msg):
        outbound = _outbound_by_ids(db, bounced_message_ids(msg, raw), scope)
        if outbound is None:
            return None
        return Match(
            reason="bounce",
            broker_id=outbound.broker_id,
            outbound=outbound,
            inquiry=_inquiry_for_outbound(db, outbound),
            reasons=["bounce_of_outbound"],
        )

    subject = _header(msg, "Subject")
    text = body_text(msg)
    for token in dict.fromkeys(t.upper() for t in TOKEN_RE.findall(f"{subject}\n{text}")):
        inquiry = db.scalar(select(StatusInquiry).where(StatusInquiry.token == token))
        if inquiry is None:
            continue
        if scope is not None and inquiry.broker_id != scope:
            continue
        return Match(
            reason="token",
            broker_id=inquiry.broker_id,
            inquiry=inquiry,
            insurer_id=inquiry.insurer_id,
            reasons=["token_match"],
        )

    outbound = _outbound_by_ids(db, reference_ids(msg), scope)
    if outbound is not None and outbound.broker_id is not None:
        inquiry = _inquiry_for_outbound(db, outbound)
        return Match(
            reason="reply",
            broker_id=outbound.broker_id,
            outbound=outbound,
            inquiry=inquiry,
            insurer_id=inquiry.insurer_id if inquiry is not None else None,
            reasons=["in_reply_to_match"],
        )

    if scope is not None:
        insurer_id = _insurer_by_domain(db, sender_domain(sender_address(msg)))
        if insurer_id is not None:
            return Match(
                reason="insurer_domain",
                broker_id=scope,
                insurer_id=insurer_id,
                reasons=["insurer_sender_domain"],
            )
    return None


# --- keeping a message ------------------------------------------------------------------------

def _find_policy_mentioned(
    db: Session, *, broker_id: int, insurer_id: int | None, text: str, number: str | None = None
) -> Policy | None:
    stmt = select(Policy).where(Policy.broker_id == broker_id)
    if insurer_id is not None:
        stmt = stmt.where(Policy.insurer_id == insurer_id)
    haystack = (text or "").upper()
    wanted = (number or "").strip().upper()
    for policy in db.scalars(stmt.order_by(Policy.id.desc())):
        candidate = (policy.policy_number or "").strip().upper()
        if len(candidate) < 4:
            continue
        if candidate == wanted or re.search(rf"(?<![A-Z0-9]){re.escape(candidate)}(?![A-Z0-9])", haystack):
            return policy
    return None


def _store_eml(
    db: Session,
    *,
    broker_id: int,
    entity_type: EntityType,
    entity_id: int,
    raw: bytes,
    uid: str,
) -> int | None:
    """The ``.eml`` as a document via the documents router's helper (rule 8)."""
    from app.api.routers.documents import store_generated_document  # noqa: PLC0415 - cycle

    try:
        with db.begin_nested():
            doc = store_generated_document(
                db,
                broker_id=broker_id,
                entity_type=entity_type,
                entity_id=entity_id,
                category=DocumentCategory.EMAIL_MESSAGE,
                data=raw,
                original_name=f"correo-{uid}.eml",
                mime_type="message/rfc822",
            )
        return doc.id
    except Exception:  # noqa: BLE001 - a storage hiccup must not lose the evidence
        logger.exception("mail.poll: could not store the .eml (uid %s)", uid)
        return None


def _recipients_for(db: Session, inquiry: StatusInquiry | None, policy: Policy | None, broker_id: int) -> list[int] | None:
    if inquiry is not None and inquiry.created_by_user_id:
        user = db.get(User, inquiry.created_by_user_id)
        if user is not None and user.is_active and user.broker_id == broker_id:
            return [user.id]
    if policy is not None:
        return recipients_for_policy(db, policy)
    return None  # every broker admin


#: Public mailbox providers: their domain proves nothing about the sender, so an
#: address there verifies only when it is the EXACT address Radal wrote to.
FREEMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.es", "outlook.com", "outlook.es",
    "live.com", "live.cl", "msn.com", "yahoo.com", "yahoo.es", "ymail.com", "icloud.com",
    "me.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "zoho.com",
})


def _domain_matches(domain: str, known: str) -> bool:
    known = (known or "").strip().lower().lstrip("@")
    return bool(domain and known) and (domain == known or domain.endswith("." + known))


def sender_verified(db: Session, match: Match, sender: str | None) -> bool:
    """Does the From of a kept mail belong to whom Radal asked / the insurer?

    The inquiry token is NOT a secret (it rides in a subject that gets
    forwarded), so a token match alone must never make a mail look like the
    insurer's answer. Verified when the sender is the inquiry's (or the
    replied outbound's) recipient — same address, or same domain unless it is
    a public mailbox provider — or its domain is one of the insurer's
    ``sender_domains``. A From header can still be forged (no DKIM check
    here): this only stops the score from vouching for a stranger.
    """
    address = (sender or "").strip().lower()
    domain = sender_domain(address)
    if not domain:
        return False
    if match.reason == "insurer_domain":
        return True  # the prefilter matched exactly this
    expected: list[str] = []
    if match.inquiry is not None and match.inquiry.to_address:
        expected.append(match.inquiry.to_address)
    if match.outbound is not None:
        expected.extend(match.outbound.to_addresses or [])
    for item in expected:
        known = str(item or "").strip().lower()
        if not known:
            continue
        if known == address:
            return True
        known_domain = sender_domain(known)
        if known_domain not in FREEMAIL_DOMAINS and _domain_matches(domain, known_domain):
            return True
    insurer_id = match.insurer_id or (match.inquiry.insurer_id if match.inquiry is not None else None)
    profile = db.get(InsurerCollectionProfile, insurer_id) if insurer_id is not None else None
    for known in (profile.sender_domains if profile is not None else None) or []:
        if _domain_matches(domain, str(known or "")):
            return True
    return False


def _score(
    reading: MailReading,
    match: Match,
    inst: CollectionInstallment | None,
    *,
    numbers_matched: bool,
    verified: bool = False,
) -> tuple[Decimal, list[str]]:
    reasons = list(match.reasons)
    reasons.append("sender_matches_insurer" if verified else "sender_unverified")
    score = (reading.confidence or Decimal("0")) * Decimal("0.5")
    if match.reason in ("token", "reply"):
        score += Decimal("30")
    elif match.reason == "insurer_domain":
        score += Decimal("10")
    if reading.used_ai:
        reasons.append("ai_read")
    else:
        reasons.append("heuristic_read")
    if numbers_matched:
        score += Decimal("10")
        reasons.append("installment_number_match")
    if inst is not None and reading.amount is not None:
        cur = reading.currency or _val(inst.currency) or "UF"
        if cur == (_val(inst.currency) or "UF"):
            gross = Decimal(str(inst.gross_amount_uf))
            if gross > 0 and abs(reading.amount - gross) <= max(Decimal("0.01"), gross / 100):
                score += Decimal("10")
                reasons.append("amount_matches_installment")
            else:
                reasons.append("amount_differs")
    if inst is None:
        reasons.append("installment_unknown")
    if reading.reported_status == ReportedStatus.UNKNOWN.value:
        reasons.append("status_unknown")
    score = min(Decimal("100"), score)
    if not verified:
        # A stranger's mail is never a one-click suggestion: needs_review.
        score = min(score, SUGGEST_THRESHOLD - Decimal("0.01"))
    return score.quantize(Decimal("0.01")), reasons


def _evidence_targets(
    db: Session, *, inquiry: StatusInquiry | None, policy: Policy | None, numbers: list[int]
) -> tuple[list[CollectionInstallment | None], bool]:
    """Which cuotas a reading refers to, and whether that came from its numbers."""
    if inquiry is not None:
        asked = inquiry_installments(db, inquiry)
        if numbers:
            chosen = [i for i in asked if i.number in numbers]
            if chosen:
                return list(chosen), True
        return list(asked) or [None], False
    if policy is not None and numbers:
        chosen = [i for i in policy_installments(db, policy) if i.number in numbers]
        if chosen:
            return list(chosen), True
    return [None], False


def _create_evidence(
    db: Session,
    *,
    message: MailMessage,
    match: Match,
    reading: MailReading,
    policy: Policy | None,
    source: EvidenceSource,
) -> list[PaymentEvidence]:
    targets, numbers_matched = _evidence_targets(
        db, inquiry=match.inquiry, policy=policy, numbers=reading.installment_numbers
    )
    status = reading.reported_status
    if reading.classification == InboundClass.CANCELLATION_NOTICE.value:
        status = ReportedStatus.CANCELLED.value
    single = len(targets) == 1
    verified = sender_verified(db, match, message.from_address)
    rows: list[PaymentEvidence] = []
    for inst in targets:
        score, reasons = _score(
            reading, match, inst, numbers_matched=numbers_matched, verified=verified
        )
        suggest = (
            inst is not None
            and status != ReportedStatus.UNKNOWN.value
            and score >= SUGGEST_THRESHOLD
        )
        if inst is not None:
            for older in db.scalars(
                select(PaymentEvidence).where(
                    PaymentEvidence.broker_id == message.broker_id,
                    PaymentEvidence.installment_id == inst.id,
                    PaymentEvidence.status.in_([EvidenceStatus.SUGGESTED, EvidenceStatus.NEEDS_REVIEW]),
                    PaymentEvidence.status_inquiry_id == (match.inquiry.id if match.inquiry else None),
                )
            ):
                if match.inquiry is not None:
                    older.status = EvidenceStatus.SUPERSEDED
        row = PaymentEvidence(
            broker_id=message.broker_id,
            installment_id=inst.id if inst is not None else None,
            source=source,
            mail_message_id=message.id,
            status_inquiry_id=match.inquiry.id if match.inquiry is not None else None,
            document_id=message.document_id,
            extraction_id=reading.extraction.id if reading.extraction is not None else None,
            reported_status=ReportedStatus(status),
            reported_amount=reading.amount if single else None,
            reported_currency=(
                PaymentCurrency(reading.currency) if (single and reading.currency) else None
            ),
            reported_paid_on=reading.paid_on,
            score=score,
            reasons=reasons,
            status=EvidenceStatus.SUGGESTED if suggest else EvidenceStatus.NEEDS_REVIEW,
            note=reading.comment,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def _handle_bounce(db: Session, message: MailMessage, match: Match, *, now: datetime) -> None:
    outbound = match.outbound
    if outbound is None:
        return
    for notice in db.scalars(
        select(CollectionNotice).where(CollectionNotice.mail_message_id == outbound.id)
    ):
        notice.status = NoticeStatus.BOUNCED
        notice.error = "Correo rebotado por el servidor de destino"
    inquiry = match.inquiry
    if inquiry is not None and _val(inquiry.status) in (
        InquiryStatus.SENT.value,
        InquiryStatus.DRAFT.value,
    ):
        inquiry.status = InquiryStatus.FAILED


def process_message(
    db: Session, account: MailAccount, fetched: FetchedMail, *, now: datetime
) -> MailMessage:
    """Prefilter + keep/ignore one fetched message. Flushes only."""
    msg = fetched.message()
    when = occurred_at(msg, now=now)
    key = uid_key(fetched)
    match = prefilter(db, account, msg, fetched.raw)
    message_id = _header(msg, "Message-ID")[:255] or None
    duplicate = (
        match is not None
        and message_id is not None
        and db.scalar(
            select(MailMessage.id).where(
                MailMessage.mail_account_id == account.id,
                MailMessage.direction == MailDirection.INBOUND,
                MailMessage.message_id_header == message_id,
            )
        )
        is not None
    )
    if match is None or duplicate:
        # Ignored (or a re-read of mail already kept — e.g. the 2-day window
        # re-read after a UIDVALIDITY reset): only the key, never the content.
        row = MailMessage(
            broker_id=account.broker_id,
            mail_account_id=account.id,
            direction=MailDirection.INBOUND,
            provider_uid=key,
            occurred_at=when,
            status=MailMessageStatus.IGNORED,
            error="duplicate" if duplicate else None,
        )
        db.add(row)
        db.flush()
        return row

    subject = _header(msg, "Subject")
    text = body_text(msg)
    sender = sender_address(msg)
    message = MailMessage(
        broker_id=match.broker_id,
        mail_account_id=account.id,
        direction=MailDirection.INBOUND,
        message_id_header=message_id,
        in_reply_to=(_header(msg, "In-Reply-To") or None) and _header(msg, "In-Reply-To")[:255],
        thread_token=match.inquiry.token if match.inquiry is not None else None,
        from_address=sender[:255] or None,
        to_addresses=[a for _n, a in getaddresses([_header(msg, "To")]) if a] or None,
        cc_addresses=[a for _n, a in getaddresses([_header(msg, "Cc")]) if a] or None,
        subject=subject[:500],
        body_text=text,
        occurred_at=when,
        provider_uid=key,
        status=MailMessageStatus.RECEIVED,
    )
    db.add(message)
    db.flush()

    if match.reason == "bounce":
        message.classification = InboundClass.BOUNCE
        message.status = MailMessageStatus.PROCESSED
        _handle_bounce(db, message, match, now=now)
        db.flush()
        return message

    broker_id = match.broker_id
    if broker_id is None:  # pragma: no cover - token/reply/domain always carry a broker
        message.status = MailMessageStatus.PROCESSED
        return message

    policy: Policy | None = None
    if match.inquiry is not None:
        policy = db.get(Policy, match.inquiry.policy_id)
        entity_type, entity_id = EntityType.STATUS_INQUIRY, match.inquiry.id
    else:
        policy = _find_policy_mentioned(
            db, broker_id=broker_id, insurer_id=match.insurer_id, text=f"{subject}\n{text}"
        )
        if policy is not None:
            entity_type, entity_id = EntityType.POLICY, policy.id
        else:
            entity_type, entity_id = EntityType.MAIL_ACCOUNT, account.id
    message.document_id = _store_eml(
        db,
        broker_id=broker_id,
        entity_type=entity_type,
        entity_id=entity_id,
        raw=fetched.raw,
        uid=str(fetched.uid),
    )

    context = ""
    if match.inquiry is not None and policy is not None:
        asked = inquiry_installments(db, match.inquiry)
        context = (
            f"Respuesta a la consulta {match.inquiry.token} sobre la póliza {policy.policy_number}; "
            "cuotas consultadas: "
            + ", ".join(f"{i.number} (vence {i.due_date}, {i.gross_amount_uf} {_val(i.currency)})" for i in asked)
        )
    reading: MailReading | None = None
    if message.document_id is not None:
        reading = classify_mail(
            db,
            broker_id=broker_id,
            document_id=message.document_id,
            subject=subject,
            sender=sender,
            body=text,
            context=context,
            has_inquiry=match.inquiry is not None,
        )
    else:
        from app.services.mail.classifier import heuristic_reading  # noqa: PLC0415

        reading = heuristic_reading(subject=subject, body=text, has_inquiry=match.inquiry is not None)
    message.classification = InboundClass(reading.classification)
    message.extraction_id = reading.extraction.id if reading.extraction is not None else None

    if policy is None and reading.policy_number:
        policy = _find_policy_mentioned(
            db,
            broker_id=broker_id,
            insurer_id=match.insurer_id,
            text="",
            number=reading.policy_number,
        )

    evidence: list[PaymentEvidence] = []
    if reading.classification in EVIDENCE_CLASSES:
        source = (
            EvidenceSource.INSURER_REPLY
            if match.inquiry is not None or reading.classification == InboundClass.INSURER_STATUS_REPLY.value
            else EvidenceSource.INSURER_STATEMENT
        )
        evidence = _create_evidence(
            db, message=message, match=match, reading=reading, policy=policy, source=source
        )
    elif reading.classification == InboundClass.UNRELATED.value:
        # Kept by the prefilter, but not about cobranza: drop the body (privacy).
        message.body_text = None

    if match.inquiry is not None and _val(match.inquiry.status) not in (
        InquiryStatus.CANCELLED.value,
        InquiryStatus.RESOLVED.value,
    ):
        match.inquiry.status = InquiryStatus.REPLIED
        match.inquiry.replied_at = now

    params = {
        "classification": reading.classification,
        "reported_status": reading.reported_status,
        "evidence_count": len(evidence),
        "policy_number": policy.policy_number if policy is not None else None,
        "token": match.inquiry.token if match.inquiry is not None else None,
    }
    recipients = _recipients_for(db, match.inquiry, policy, broker_id)
    if match.inquiry is not None:
        notify(
            db,
            broker_id=broker_id,
            user_ids=recipients,
            kind=NotificationKind.INQUIRY_REPLY,
            tray=NotificationTray.TODO,
            origin_type="status_inquiry",
            origin_id=match.inquiry.id,
            params=params,
            link_path=f"/collections/evidence?inquiry_id={match.inquiry.id}",
            now=now,
            renotify=True,
        )
    elif evidence:
        notify(
            db,
            broker_id=broker_id,
            user_ids=recipients,
            kind=NotificationKind.EVIDENCE_PENDING,
            tray=NotificationTray.TODO,
            origin_type="mail_message",
            origin_id=message.id,
            params=params,
            link_path="/collections/evidence",
            now=now,
        )
    message.status = MailMessageStatus.PROCESSED
    db.flush()
    return message


# --- polling ------------------------------------------------------------------------------------

def ensure_system_account(db: Session) -> MailAccount:
    """The NULL-broker Radal system mailbox row (only stores the IMAP cursor)."""
    row = db.scalar(
        select(MailAccount)
        .where(MailAccount.broker_id.is_(None), MailAccount.provider == MailProvider.RADAL_SYSTEM)
        .order_by(MailAccount.id)
    )
    if row is None:
        row = MailAccount(
            broker_id=None,
            provider=MailProvider.RADAL_SYSTEM,
            address=settings.RADAL_MAIL_ADDRESS,
            display_name=settings.RADAL_MAIL_DISPLAY_NAME,
            send_enabled=True,
            receive_enabled=True,
            status=MailAccountStatus.CONNECTED,
        )
        db.add(row)
        db.flush()
    return row


def _account_failed(db: Session, account: MailAccount, error: str, *, now: datetime) -> None:
    was_error = _val(account.status) == MailAccountStatus.ERROR.value
    account.status = MailAccountStatus.ERROR
    account.last_error = error[:2000]
    if was_error:
        return  # alert once, on the transition
    if account.broker_id is None:
        from app.services.mail.ops import notify_ops  # noqa: PLC0415

        notify_ops(db, "No se pudo leer la casilla de Radal", error)
        return
    user_ids: list[int] | None = None
    if account.owner_user_id:
        owner = db.get(User, account.owner_user_id)
        if owner is not None and owner.is_active and owner.broker_id == account.broker_id:
            user_ids = [owner.id]
    notify(
        db,
        broker_id=account.broker_id,
        user_ids=user_ids if user_ids is not None else broker_admin_ids(db, account.broker_id),
        kind=NotificationKind.MAIL_ACCOUNT_ERROR,
        tray=NotificationTray.TODO,
        origin_type="mail_account",
        origin_id=account.id,
        params={"address": account.address},
        link_path="/settings?section=mail",
        now=now,
    )


def poll_account(db: Session, account: MailAccount, *, now: datetime) -> dict[str, int]:
    """Fetch + process new mail of one mailbox. Never raises."""
    stats = {"fetched": 0, "kept": 0, "ignored": 0, "errors": 0}
    try:
        cfg = radal_imap_config() if account.broker_id is None else account_imap_config(account)
        batch, cursor = get_transport().fetch_since(cfg, account.sync_cursor, limit=FETCH_LIMIT)
    except Exception as exc:  # noqa: BLE001
        stats["errors"] += 1
        # MailTransportError text is already a generic Spanish category.
        reason = str(exc) if isinstance(exc, MailTransportError) else "No se pudo leer la casilla"
        _account_failed(db, account, reason, now=now)
        db.flush()
        return stats
    for fetched in batch:
        stats["fetched"] += 1
        uid = uid_key(fetched)
        exists = db.scalar(
            select(MailMessage.id).where(
                MailMessage.mail_account_id == account.id, MailMessage.provider_uid == uid
            )
        )
        if exists is not None:
            continue
        try:
            with db.begin_nested():
                row = process_message(db, account, fetched, now=now)
            if _val(row.status) == MailMessageStatus.IGNORED.value:
                stats["ignored"] += 1
            else:
                stats["kept"] += 1
        except Exception:  # noqa: BLE001 - one bad mail must not block the box
            stats["errors"] += 1
            logger.exception("mail.poll: account %s uid %s failed", account.id, uid)
            with db.begin_nested():
                db.add(
                    MailMessage(
                        broker_id=account.broker_id,
                        mail_account_id=account.id,
                        direction=MailDirection.INBOUND,
                        provider_uid=uid,
                        occurred_at=now,
                        status=MailMessageStatus.IGNORED,
                        error="processing failed",
                    )
                )
    account.sync_cursor = dict(cursor or {})
    account.last_sync_at = now
    if account.broker_id is None or _val(account.status) == MailAccountStatus.ERROR.value:
        account.status = MailAccountStatus.CONNECTED
        account.last_error = None
    db.flush()
    return stats


def run_mail_poll(db: Session, now: datetime) -> dict[str, Any]:
    """The ``mail.poll`` body."""
    if not settings.MAIL_ENABLED:
        return {"skipped": True, "reason": "MAIL_ENABLED is off"}
    totals: dict[str, Any] = {"accounts": 0, "fetched": 0, "kept": 0, "ignored": 0, "errors": 0}
    accounts = [ensure_system_account(db)]
    accounts.extend(
        db.scalars(
            select(MailAccount)
            .where(
                MailAccount.broker_id.is_not(None),
                MailAccount.provider == MailProvider.CUSTOM,
                MailAccount.receive_enabled.is_(True),
                MailAccount.status == MailAccountStatus.CONNECTED,
            )
            .order_by(MailAccount.id)
        )
    )
    for account in accounts:
        totals["accounts"] += 1
        stats = poll_account(db, account, now=now)
        for key, value in stats.items():
            totals[key] += value
        db.commit()  # one mailbox's progress survives the next one's failure
    return totals


__all__ = [
    "Match",
    "body_text",
    "bounced_message_ids",
    "ensure_system_account",
    "is_bounce",
    "poll_account",
    "prefilter",
    "process_message",
    "run_mail_poll",
    "sender_verified",
    "uid_key",
]
