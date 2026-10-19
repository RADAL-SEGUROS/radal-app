"""Payer notices: templates, rendering, preview/send and the daily job (v12, 03 §7, 04 §5).

* **Templates** — resolved by B2's
  :func:`app.services.collection.templates.effective_template` (one source of
  truth): a broker's own ``notice_template`` rows win, a slot switched off
  (``is_active = False``) sends NOTHING, otherwise the Spanish defaults in
  ``app/db/seed_assets/notice_templates_es.json`` (data).
  Rendering is a tiny ``{{var}}`` replacer (no Jinja): unknown variables render
  as an empty string; in the HTML part every literal AND every value is
  HTML-escaped (a template or an insured name can never inject markup) and
  ``http(s)`` values become links.
* **Context** — amounts in UF with the CLP equivalent from ``currency_rate``
  (:mod:`app.services.fx`; no rate within the look-back => ``"—"``, never a
  guess), the insurer's payment instructions (Radal profile ⊕ broker
  override, field by field) and a FRESH insured share link: with ``can_pay``
  when the cuota resolves to ``native``, without it when ``assisted``.
* **Sending** — :func:`send_notice` records one ``collection_notice`` and one
  ``mail_message`` (through :func:`app.services.mail.sender.send_mail`).
  WhatsApp is an open door only -> :class:`NoticeError` 409 "pronto".
* **Dedupe** — the job always sets ``cadence_level``, so the unique
  ``uq_collection_notice_once`` (installment, kind, level, channel) holds; a
  FAILED row is retried in place. One-off sends (``cadence_level`` NULL, where
  NULLs never collide in the index) are deduplicated in code: one per
  (installment, kind, channel) per business day.
* **The job** (``collection.notices``, :func:`run_collection_notices`) sends
  only when, all at once: the broker's master switch
  ``collection_settings.notices_enabled`` is on AND the template of that exact
  slot is approved (approval is per template, review v12 #14), the plan has ``notices_enabled``, it is not paused
  (``notices_paused_until``), the cuota is neither paid, voided nor promised,
  its level is not ``record``, there is a payer contact, and a template exists
  for that exact cadence step.

Preview/send flush; the CALLER commits. The job commits per cuota (see
:func:`run_collection_notices`).
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.base_class import utcnow
from app.models.broker import Broker
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.collection_settings import CollectionNotice, CollectionSettings, NoticeTemplate
from app.models.enums import (
    CollectionLevelPreference,
    MailMessageStatus,
    NoticeChannel,
    NoticeKind,
    NoticeStatus,
    PartyRole,
    ShareAudience,
    ShareEntity,
)
from app.models.insurer import Insurer
from app.services.payments.insurers import INSTRUCTION_KEYS
from app.models.policy import Policy, PolicyStatus
from app.services import fx
from app.services.collection import templates as notice_templates
from app.services.collection.resolver import (
    CollectionMode,
    lead_insurer_id,
    payer_contact_email,
    resolve_collection_mode,
)
from app.services.collection.status import (
    broker_collection_params,
    compute_installment_state,
)
from app.services.identifiers import display_rut
from app.services.mail.sender import send_mail
from app.services.share_links import create_share_link, share_url

logger = logging.getLogger(__name__)

SEED_PATH = Path(__file__).resolve().parent.parent / "db" / "seed_assets" / "notice_templates_es.json"
BUSINESS_TZ = "America/Santiago"
SHARE_LINK_DAYS = 30

PRONTO_WHATSAPP = "Pronto: el envío por WhatsApp está pendiente de aprobación de Meta"
REASON_NO_PAYER = "La póliza no tiene un correo de pagador o contratante para enviar avisos"
REASON_SETTLED = "La cuota ya está pagada o anulada; no corresponde enviar avisos"
REASON_DUPLICATE = "Ya se envió este aviso para la cuota hoy"
REASON_NO_TEMPLATE = "No hay una plantilla de aviso para este tipo"
REASON_INQUIRY_KIND = "La consulta a la compañía se envía desde Consultas de estado, no como aviso al pagador"

PAYER_KINDS = (NoticeKind.PAYER_REMINDER, NoticeKind.PAYER_OVERDUE, NoticeKind.PAYMENT_LINK)

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_URL_RE = re.compile(r"^https?://\S+$")


class NoticeError(Exception):
    """A refused preview/send. ``status_code`` maps to the HTTP answer."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _val(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


# --- time ----------------------------------------------------------------------

def business_today(now: datetime | None = None) -> date:
    """``now`` as a Santiago calendar date (UTC fallback without tzdata)."""
    moment = now or utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        return moment.astimezone(ZoneInfo(BUSINESS_TZ)).date()
    except Exception:  # noqa: BLE001
        return moment.astimezone(timezone.utc).date()


# --- formatting ------------------------------------------------------------------

def _group_thousands(digits: str) -> str:
    out = []
    while len(digits) > 3:
        out.insert(0, digits[-3:])
        digits = digits[:-3]
    out.insert(0, digits)
    return ".".join(out)


def format_uf(value: Decimal | int | str | None) -> str:
    """Chilean format: ``1.234,5678`` (2-4 decimals, trailing zeros trimmed to 2)."""
    if value is None:
        return "—"
    amount = Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    sign = "-" if amount < 0 else ""
    integer, _, frac = f"{abs(amount):.4f}".partition(".")
    frac = frac.rstrip("0")
    if len(frac) < 2:
        frac = frac.ljust(2, "0")
    return f"{sign}{_group_thousands(integer)},{frac}"


def format_clp(value: int | None) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    return sign + _group_thousands(str(abs(int(value))))


def format_date(value: date | None) -> str:
    return value.strftime("%d-%m-%Y") if value else "—"


# --- templates ---------------------------------------------------------------------

@dataclass(frozen=True)
class TemplateView:
    kind: str
    cadence_level: int | None
    channel: str
    subject: str
    body: str
    source: str  # "broker" | "default"
    id: int | None = None
    #: Per-template approval (review v12 #14); the job sends only approved slots.
    approved: bool = False


@lru_cache(maxsize=1)
def _seed() -> tuple[TemplateView, ...]:
    try:
        data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing seed file degrades to "no template"
        logger.exception("notices: could not read %s", SEED_PATH)
        return ()
    rows = []
    for item in data.get("templates") or []:
        rows.append(
            TemplateView(
                kind=str(item.get("kind")),
                cadence_level=item.get("cadence_level"),
                channel=str(item.get("channel") or "email"),
                subject=str(item.get("subject") or ""),
                body=str(item.get("body") or ""),
                source="default",
            )
        )
    return tuple(rows)


def default_templates() -> list[TemplateView]:
    return list(_seed())


def _view_of(item: Any) -> TemplateView:
    return TemplateView(
        kind=str(item.kind),
        cadence_level=item.cadence_level,
        channel=str(item.channel or "email"),
        subject=item.subject,
        body=item.body,
        source="default" if item.is_default else "broker",
        id=item.id,
        approved=notice_templates.is_approved(item),
    )


def effective_template(
    db: Session,
    *,
    broker_id: int,
    kind: NoticeKind | str,
    cadence_level: int | None = None,
    channel: NoticeChannel | str = NoticeChannel.EMAIL,
    strict: bool = False,
) -> TemplateView | None:
    """The template to use — ONE source of truth: B2's
    :func:`app.services.collection.templates.effective_template` (the broker's
    row for the exact slot, else the kind's ``cadence_level = NULL`` row, each
    falling back to the seeded default; a slot the broker switched OFF —
    ``is_active = False`` — is ``None``, never the seed).

    ``strict=True`` (the job) stops there. One-off sends (``strict=False``)
    may also borrow the nearest ACTIVE template of the same kind, but never
    when the broker switched the requested slot (or the kind's NULL-level
    fallback) off.
    """
    kind_v = _val(kind)
    channel_v = _val(channel) or NoticeChannel.EMAIL.value
    found = notice_templates.effective_template(db, broker_id, kind_v, cadence_level, channel_v)
    if found is not None:
        return _view_of(found)
    if strict:
        return None
    level = int(cadence_level) if cadence_level is not None else None
    same_kind = [
        t
        for t in notice_templates.effective_templates(db, broker_id)
        if t.kind == kind_v and t.channel == channel_v
    ]
    if any(t.cadence_level in (level, None) and not t.is_active for t in same_kind):
        return None  # the broker turned this slot off: respect it
    active = [t for t in same_kind if t.is_active]
    if not active:
        return None
    if level is not None:
        return _view_of(min(active, key=lambda t: abs((t.cadence_level or 0) - level)))
    return _view_of(active[0])


def render_text(template: str, context: dict[str, Any]) -> str:
    """Replace ``{{var}}`` with ``context[var]``; unknown -> empty string."""

    def _sub(match: re.Match) -> str:
        value = context.get(match.group(1))
        return "" if value is None else str(value)

    return _VAR_RE.sub(_sub, template or "")


def _html_value(value: Any) -> str:
    text = "" if value is None else str(value)
    escaped = html_lib.escape(text)
    if _URL_RE.match(text):
        return f'<a href="{escaped}">{escaped}</a>'
    return escaped


def render_html(template: str, context: dict[str, Any]) -> str:
    """The HTML part: literals and values escaped, newlines -> ``<br>``/``<p>``."""
    out: list[str] = []
    pos = 0
    source = template or ""
    for match in _VAR_RE.finditer(source):
        out.append(html_lib.escape(source[pos : match.start()]))
        out.append(_html_value(context.get(match.group(1))))
        pos = match.end()
    out.append(html_lib.escape(source[pos:]))
    rendered = "".join(out)
    paragraphs = [p for p in rendered.split("\n\n") if p.strip()]
    body = "".join("<p>" + p.replace("\n", "<br>") + "</p>" for p in paragraphs)
    return (
        '<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;'
        'font-size:14px;line-height:1.5;color:#1f2937">' + body + "</body></html>"
    )


# --- context ------------------------------------------------------------------------

_INSTRUCTION_LABELS = {
    "portal_url": "Pago en línea",
    "bank_name": "Banco",
    "account_type": "Tipo de cuenta",
    "account_number": "N° de cuenta",
    "account_holder": "Titular",
    "account_rut": "RUT titular",
    "transfer_email": "Enviar comprobante de transferencia a",
    "receipt_email": "Correo para comprobantes",
    "notes": "Notas",
}


def merged_instructions(db: Session, *, broker_id: int, insurer_id: int | None) -> dict[str, Any]:
    """Radal's profile instructions overlaid field-by-field by the broker override.

    Delegates to lane B3's :func:`app.services.payments.insurers.merged_instructions`
    so the reminder, the share page and ``GET /payments/insurers`` never differ.
    """
    if insurer_id is None:
        return {}
    from app.services.payments.insurers import merged_instructions as _merged  # noqa: PLC0415

    return _merged(db, broker_id=broker_id, insurer_id=insurer_id)


def instructions_text(instructions: dict[str, Any]) -> str:
    lines = [
        f"{_INSTRUCTION_LABELS[key]}: "
        f"{display_rut(instructions[key]) if key == 'account_rut' else instructions[key]}"
        for key in INSTRUCTION_KEYS
        if instructions.get(key) not in (None, "")
    ]
    if not lines:
        return "Paga directamente a la compañía por sus canales habituales o consulta a tu corredora."
    return "\n".join(lines)


def _insurer_name(db: Session, insurer_id: int | None) -> str:
    insurer = db.get(Insurer, insurer_id) if insurer_id is not None else None
    if insurer is None:
        return "la compañía"
    return insurer.trade_name or insurer.legal_name


def insured_name(policy: Policy) -> str:
    for party in getattr(policy, "parties", None) or []:
        if _val(party.role) == PartyRole.INSURED.value and party.name:
            return party.name
    client = getattr(policy, "client", None)
    insured = getattr(client, "insured", None) if client is not None else None
    if insured is not None:
        return insured.trade_name or insured.legal_name
    return "cliente"


def recipient_name(policy: Policy) -> str:
    """Who the payer reminder greets: the party that RECEIVES it (review v12 #2).

    The mail goes to :func:`payer_contact_email` (payer, else policyholder, else
    the insured's contact) — so the greeting is that party's name: the payer
    party whose e-mail it is, else the first named payer, else the
    policyholder, else the insured (:func:`insured_name`).
    """
    parties = list(getattr(policy, "parties", None) or [])
    email = payer_contact_email(policy)
    if email:
        for role in (PartyRole.PAYER.value, PartyRole.POLICYHOLDER.value):
            for party in parties:
                if _val(party.role) == role and party.email == email and party.name:
                    return party.name
    for role in (PartyRole.PAYER.value, PartyRole.POLICYHOLDER.value):
        for party in parties:
            if _val(party.role) == role and party.name:
                return party.name
    return insured_name(policy)


def installments_table(
    installments: list[CollectionInstallment], *, uf_rate: Decimal | None
) -> str:
    """A plain-text table of cuotas (number, due date, UF, CLP)."""
    lines = []
    for inst in installments:
        clp = fx.uf_to_clp(inst.gross_amount_uf, uf_rate) if uf_rate else None
        clp_part = f" (aprox. ${format_clp(clp)})" if clp is not None else ""
        lines.append(
            f"- Cuota {inst.number}: vence el {format_date(inst.due_date)} · "
            f"UF {format_uf(inst.gross_amount_uf)}{clp_part}"
        )
    return "\n".join(lines)


def build_context(
    db: Session,
    *,
    broker_id: int,
    installment: CollectionInstallment,
    today: date,
    share_link_url: str | None = None,
    inquiry_token: str | None = None,
    installments: list[CollectionInstallment] | None = None,
) -> dict[str, Any]:
    """Every template variable of 04-mail §5 for one cuota."""
    plan = installment.collection_plan
    policy = plan.policy
    insurer_id = lead_insurer_id(policy)
    broker = db.get(Broker, broker_id)
    rate = fx.get_uf_rate(db, today)
    rows = installments if installments is not None else [installment]
    # The payer is asked for what is still OWED, not the cuota's gross (review
    # v12 #2: "Monto: UF 28,80" on a cuota with UF 3,80 already paid).
    state = installment_state(db, installment, today=today)
    gross = Decimal(str(installment.gross_amount_uf))
    balance = state.balance_uf if state.state not in ("paid", "voided") else Decimal("0")
    paid = state.paid_uf
    balance_clp = fx.uf_to_clp(balance, rate) if rate else None
    paid_note = (
        f" Ya registramos un pago de UF {format_uf(paid)} de UF {format_uf(gross)}."
        if paid > 0 and balance > 0
        else ""
    )
    return {
        "broker_name": (broker.trade_name or broker.legal_name) if broker else "",
        "insured_name": insured_name(policy),
        "recipient_name": recipient_name(policy),
        "policy_number": policy.policy_number,
        "insurer_name": _insurer_name(db, insurer_id),
        "installment_number": installment.number,
        "installment_count": plan.installment_count or len(plan.installments),
        "due_date": format_date(installment.due_date),
        # ``amount_*`` (pre-review templates) now read the open balance too.
        "amount_uf": format_uf(balance),
        "amount_clp": format_clp(balance_clp),
        "balance_uf": format_uf(balance),
        "balance_clp": format_clp(balance_clp),
        "paid_uf": format_uf(paid),
        "gross_uf": format_uf(gross),
        "paid_note": paid_note,
        "share_url": share_link_url or "",
        "insurer_instructions": instructions_text(
            merged_instructions(db, broker_id=broker_id, insurer_id=insurer_id)
        ),
        "inquiry_token": inquiry_token or "",
        "installments_table": installments_table(rows, uf_rate=rate),
    }


# --- preview / send ------------------------------------------------------------------

@dataclass
class NoticeDraft:
    kind: str
    cadence_level: int | None
    channel: str
    to_address: str | None
    recipient_party_id: int | None
    subject: str
    text: str
    html: str
    mode: CollectionMode
    template_source: str
    warnings: list[str] = field(default_factory=list)
    #: Who the notice greets (``{{recipient_name}}``) and what it asks for.
    recipient_name: str | None = None
    amount_uf: Decimal | None = None
    paid_uf: Decimal | None = None
    gross_uf: Decimal | None = None


def _payer_party_id(policy: Policy, email: str | None) -> int | None:
    if not email:
        return None
    for role in (PartyRole.PAYER.value, PartyRole.POLICYHOLDER.value):
        for party in getattr(policy, "parties", None) or []:
            if _val(party.role) == role and party.email == email:
                return party.id
    return None


def _check_channel(channel: NoticeChannel | str) -> str:
    value = _val(channel) or NoticeChannel.EMAIL.value
    if value == NoticeChannel.WHATSAPP.value:
        raise NoticeError(409, PRONTO_WHATSAPP)
    if value != NoticeChannel.EMAIL.value:
        raise NoticeError(422, "Canal de aviso desconocido")
    return value


def _check_kind(kind: NoticeKind | str) -> str:
    value = _val(kind)
    if value == NoticeKind.INSURER_STATUS_INQUIRY.value:
        raise NoticeError(422, REASON_INQUIRY_KIND)
    if value not in {k.value for k in PAYER_KINDS}:
        raise NoticeError(422, "Tipo de aviso desconocido")
    return value


def installment_state(db: Session, installment: CollectionInstallment, *, today: date):
    from app.services.collection.status import installment_review_cutoff  # noqa: PLC0415

    cadence, tolerance = broker_collection_params(db, installment.broker_id)
    return compute_installment_state(
        installment,
        list(installment.payments),
        today=today,
        cadence_days=cadence,
        tolerance_uf=tolerance,
        # Review r2 (D1): a past cuota awaiting the broker's decision is never
        # chased with an overdue notice.
        review_cutoff=installment_review_cutoff(db, installment),
    )


def draft_notice(
    db: Session,
    *,
    broker_id: int,
    installment: CollectionInstallment,
    kind: NoticeKind | str,
    cadence_level: int | None = None,
    channel: NoticeChannel | str = NoticeChannel.EMAIL,
    today: date,
    share_link_url: str | None = None,
    strict: bool = False,
) -> NoticeDraft:
    """Render a notice for one cuota (no writes). Raises :class:`NoticeError`."""
    channel_v = _check_channel(channel)
    kind_v = _check_kind(kind)
    mode = resolve_collection_mode(db, installment, broker_id=broker_id)
    template = effective_template(
        db,
        broker_id=broker_id,
        kind=kind_v,
        cadence_level=cadence_level,
        channel=channel_v,
        strict=strict,
    )
    if template is None:
        raise NoticeError(422, REASON_NO_TEMPLATE)
    policy = installment.collection_plan.policy
    to = payer_contact_email(policy)
    placeholder = share_link_url or "(el enlace a las cuotas se genera al enviar)"
    context = build_context(
        db,
        broker_id=broker_id,
        installment=installment,
        today=today,
        share_link_url=placeholder,
    )
    warnings: list[str] = []
    if context["balance_clp"] == "—":
        warnings.append("uf_rate_missing")
    if not to:
        warnings.append("payer_contact")
    state = installment_state(db, installment, today=today)
    return NoticeDraft(
        kind=kind_v,
        cadence_level=cadence_level,
        channel=channel_v,
        to_address=to,
        recipient_party_id=_payer_party_id(policy, to),
        subject=render_text(template.subject, context)[:255],
        text=render_text(template.body, context),
        html=render_html(template.body, context),
        mode=mode,
        template_source=template.source,
        warnings=warnings,
        recipient_name=context["recipient_name"],
        amount_uf=state.balance_uf,
        paid_uf=state.paid_uf,
        gross_uf=Decimal(str(installment.gross_amount_uf)),
    )


def _notice_status(mail_status: Any) -> NoticeStatus:
    value = _val(mail_status)
    if value == MailMessageStatus.SENT.value:
        return NoticeStatus.SENT
    if value == MailMessageStatus.SKIPPED.value:
        return NoticeStatus.SKIPPED
    if value == MailMessageStatus.QUEUED.value:
        return NoticeStatus.QUEUED
    return NoticeStatus.FAILED


def _day_bounds(today: date) -> tuple[datetime, datetime]:
    """UTC bounds of a Santiago business day (approximate with tz when present)."""
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(BUSINESS_TZ)
    except Exception:  # noqa: BLE001
        tz = timezone.utc
    start = datetime(today.year, today.month, today.day, tzinfo=tz).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def find_existing_notice(
    db: Session,
    *,
    installment_id: int,
    kind: str,
    cadence_level: int | None,
    channel: str,
    today: date,
) -> CollectionNotice | None:
    stmt = select(CollectionNotice).where(
        CollectionNotice.installment_id == installment_id,
        CollectionNotice.kind == kind,
        CollectionNotice.channel == channel,
    )
    if cadence_level is not None:
        return db.scalar(stmt.where(CollectionNotice.cadence_level == cadence_level))
    start, end = _day_bounds(today)
    rows = db.scalars(
        stmt.where(
            CollectionNotice.cadence_level.is_(None),
            CollectionNotice.status.in_([NoticeStatus.SENT, NoticeStatus.QUEUED]),
        ).order_by(CollectionNotice.id.desc())
    ).all()
    for row in rows:
        created = row.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created is not None and start <= created < end:
            return row
    return None


def send_notice(
    db: Session,
    *,
    broker_id: int,
    installment: CollectionInstallment,
    kind: NoticeKind | str,
    cadence_level: int | None = None,
    channel: NoticeChannel | str = NoticeChannel.EMAIL,
    user: Any = None,
    now: datetime | None = None,
    strict: bool = False,
    account: Any = "auto",
) -> CollectionNotice:
    """Render, mint the share link, send and record one notice. Flushes only.

    Raises :class:`NoticeError` (409 whatsapp / duplicate / settled, 422 no
    payer contact / no template). A send failure is NOT an error: it is a
    ``failed`` notice (and mail) row.
    """
    moment = now or utcnow()
    today = business_today(moment)
    channel_v = _check_channel(channel)
    kind_v = _check_kind(kind)
    if installment.broker_id != broker_id:
        raise NoticeError(404, "Cuota no encontrada")
    state = installment_state(db, installment, today=today)
    if state.state in ("paid", "voided"):
        raise NoticeError(409, REASON_SETTLED)

    existing = find_existing_notice(
        db,
        installment_id=installment.id,
        kind=kind_v,
        cadence_level=cadence_level,
        channel=channel_v,
        today=today,
    )
    retry_row: CollectionNotice | None = None
    if existing is not None:
        if cadence_level is not None and _val(existing.status) == NoticeStatus.FAILED.value:
            retry_row = existing
        else:
            raise NoticeError(409, REASON_DUPLICATE)

    policy = installment.collection_plan.policy
    to = payer_contact_email(policy)
    if not to:
        raise NoticeError(422, REASON_NO_PAYER)
    if effective_template(
        db, broker_id=broker_id, kind=kind_v, cadence_level=cadence_level, channel=channel_v,
        strict=strict,
    ) is None:
        raise NoticeError(422, REASON_NO_TEMPLATE)
    mode = resolve_collection_mode(db, installment, broker_id=broker_id)
    link = create_share_link(
        db,
        broker_id=broker_id,
        user=user,
        entity_type=ShareEntity.POLICY,
        entity_id=policy.id,
        audience=ShareAudience.INSURED,
        permissions={"can_pay": mode.level == "native", "can_upload_receipt": True},
        expires_in_days=SHARE_LINK_DAYS,
        label="Aviso de cobranza",
        now=moment,
    )
    draft = draft_notice(
        db,
        broker_id=broker_id,
        installment=installment,
        kind=kind_v,
        cadence_level=cadence_level,
        channel=channel_v,
        today=today,
        share_link_url=share_url(link.token),
        strict=strict,
    )
    mail = send_mail(
        db,
        broker_id=broker_id,
        kind=kind_v,
        to=[to],
        subject=draft.subject,
        text=draft.text,
        html=draft.html,
        account=account,
        now=moment,
    )
    status_v = _notice_status(mail.status)
    row = retry_row or CollectionNotice(
        broker_id=broker_id,
        installment_id=installment.id,
        kind=kind_v,
        cadence_level=cadence_level,
        channel=channel_v,
    )
    row.recipient_party_id = draft.recipient_party_id
    row.to_address = to[:255]
    row.subject = draft.subject[:255]
    row.mail_message_id = mail.id
    row.share_link_id = link.id
    row.status = status_v
    row.sent_at = moment if status_v == NoticeStatus.SENT else None
    row.error = mail.error
    if retry_row is None:
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            raise NoticeError(409, REASON_DUPLICATE) from None
    else:
        db.flush()
    return row


# --- the job ----------------------------------------------------------------------

def cadence_step(days_relative: int, cadence: list[int]) -> int | None:
    """The latest cadence step reached ``days_relative`` days from the due date."""
    reached = [int(level) for level in cadence if int(level) <= days_relative]
    return max(reached) if reached else None


def kind_for_level(level: int) -> NoticeKind:
    return NoticeKind.PAYER_REMINDER if level <= 0 else NoticeKind.PAYER_OVERDUE


def _notice_step(
    db: Session,
    *,
    broker_id: int,
    plan: CollectionPlan,
    inst: CollectionInstallment,
    cadence: list[int],
    today: date,
    now: datetime,
) -> str:
    """Decide and (maybe) send the notice of ONE cuota; returns the stats key."""
    if inst.due_date is None:
        return "not_due"
    state = installment_state(db, inst, today=today)
    if state.state in ("paid", "voided", "promised"):
        return "settled"
    if state.state == "pending_review":
        return "pending_review"
    level = cadence_step((today - inst.due_date).days, cadence)
    if level is None:
        return "not_due"
    kind = kind_for_level(level)
    existing = find_existing_notice(
        db,
        installment_id=inst.id,
        kind=kind.value,
        cadence_level=level,
        channel=NoticeChannel.EMAIL.value,
        today=today,
    )
    if existing is not None and _val(existing.status) != NoticeStatus.FAILED.value:
        return "already_sent"
    template = effective_template(
        db, broker_id=broker_id, kind=kind, cadence_level=level, strict=True
    )
    if template is None:
        return "no_template"
    if not template.approved:
        # Approval is per template: an edited (or never approved) slot sends
        # nothing until the broker approves it; the others keep sending.
        return "unapproved_template"
    mode = resolve_collection_mode(db, inst, broker_id=broker_id)
    if mode.level == "record":
        return "record_level"
    if not payer_contact_email(plan.policy):
        return "no_payer_contact"
    try:
        notice = send_notice(
            db,
            broker_id=broker_id,
            installment=inst,
            kind=kind,
            cadence_level=level,
            now=now,
            strict=True,
        )
    except NoticeError as exc:
        logger.info("notices: inst %s skipped: %s", inst.id, exc.detail)
        return "already_sent" if exc.status_code == 409 else "failed"
    status_v = _val(notice.status)
    if status_v == NoticeStatus.SENT.value:
        return "sent"
    if status_v == NoticeStatus.SKIPPED.value:
        return "skipped_mail_off"
    return "failed"


def run_collection_notices(db: Session, now: datetime) -> dict[str, Any]:
    """The ``collection.notices`` body. Never sends a backlog: one step per cuota.

    Every cuota is its own unit of work: it runs in a SAVEPOINT and is
    COMMITTED as soon as it is decided, so a mail that went out keeps its
    ``collection_notice`` + ``mail_message`` rows even if a later cuota blows
    up (otherwise the runner's rollback would forget it and a re-trigger
    would mail the same payer twice). A cuota that raises is logged, counted
    in ``errors`` and retried by the next daily run.
    """
    today = business_today(now)
    stats: dict[str, int] = {
        "brokers": 0,
        "plans": 0,
        "sent": 0,
        "skipped_mail_off": 0,
        "failed": 0,
        "already_sent": 0,
        "no_payer_contact": 0,
        "no_template": 0,
        "unapproved_template": 0,
        "record_level": 0,
        "not_due": 0,
        "settled": 0,
        "pending_review": 0,
        "paused": 0,
        "errors": 0,
    }
    broker_ids = list(
        db.scalars(
            select(CollectionSettings.broker_id)
            .where(
                CollectionSettings.notices_enabled.is_(True),
                # "Some template is approved" (cleared only when none is left);
                # each slot is then checked on its own template's approval.
                CollectionSettings.notices_approved_at.is_not(None),
            )
            .order_by(CollectionSettings.broker_id)
        )
    )
    for broker_id in broker_ids:
        stats["brokers"] += 1
        try:
            cadence, _tolerance = broker_collection_params(db, broker_id)
            plan_ids = list(
                db.scalars(
                    select(CollectionPlan.id)
                    .join(Policy, Policy.id == CollectionPlan.policy_id)
                    .where(
                        CollectionPlan.broker_id == broker_id,
                        CollectionPlan.notices_enabled.is_(True),
                        Policy.broker_id == broker_id,
                        Policy.status.not_in([PolicyStatus.CANCELLED, PolicyStatus.DRAFT]),
                        Policy.terminated_on.is_(None),
                    )
                    .order_by(CollectionPlan.id)
                )
            )
        except Exception:  # noqa: BLE001 - one broker must not stop the others
            db.rollback()
            logger.exception("notices: broker %s could not be planned", broker_id)
            stats["errors"] += 1
            continue
        for plan_id in plan_ids:
            plan = db.get(CollectionPlan, plan_id)
            if plan is None or plan.broker_id != broker_id:
                continue
            stats["plans"] += 1
            if plan.notices_paused_until is not None and plan.notices_paused_until >= today:
                stats["paused"] += 1
                continue
            if _val(plan.level_preference) == CollectionLevelPreference.RECORD.value:
                stats["record_level"] += 1
                continue
            inst_ids = [i.id for i in sorted(plan.installments, key=lambda i: i.number)]
            for inst_id in inst_ids:
                inst = db.get(CollectionInstallment, inst_id)
                if inst is None:
                    continue
                try:
                    with db.begin_nested():
                        key = _notice_step(
                            db,
                            broker_id=broker_id,
                            plan=plan,
                            inst=inst,
                            cadence=cadence,
                            today=today,
                            now=now,
                        )
                    db.commit()  # this cuota's mail + rows survive whatever comes next
                except Exception:  # noqa: BLE001 - one cuota must not lose the others' sends
                    db.rollback()
                    logger.exception("notices: inst %s failed", inst_id)
                    stats["errors"] += 1
                    plan = db.get(CollectionPlan, plan_id) or plan
                    continue
                stats[key] += 1
    return stats


__all__ = [
    "NoticeDraft",
    "NoticeError",
    "TemplateView",
    "build_context",
    "business_today",
    "cadence_step",
    "default_templates",
    "draft_notice",
    "effective_template",
    "find_existing_notice",
    "format_clp",
    "format_date",
    "format_uf",
    "instructions_text",
    "installments_table",
    "merged_instructions",
    "render_html",
    "recipient_name",
    "render_text",
    "run_collection_notices",
    "send_notice",
]
