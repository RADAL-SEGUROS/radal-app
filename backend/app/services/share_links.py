"""Public share links — the access of people who do not log in (v12, 02 §7).

Phase-1 skeleton: mint, resolve, revoke, count a view, build the URLs. Lane B5
completes the audience projections in ``routers/public_share.py`` (and may
extend this module, not change these signatures).

Security model (product decision §1.7, ``offerings.public_router`` pattern):

* the token IS the credential — ``secrets.token_urlsafe(24)`` (32 chars, 192
  bits), unique, looked up by exact match;
* a link always points at ONE row of ONE broker: :func:`create_share_link`
  resolves the target with the caller's ``broker_id`` and refuses anything else
  (:class:`ShareTargetNotFound` -> the router's 404), and the public side must
  resolve ``entity_id`` again with ``link.broker_id``;
* unknown, revoked and expired tokens are indistinguishable: all ``None`` from
  :func:`resolve_share_token` (-> 404);
* every link expires: ``expires_in_days`` defaults to
  :data:`DEFAULT_EXPIRES_IN_DAYS` and is capped at :data:`MAX_EXPIRES_IN_DAYS`;
* ``permissions`` is normalised to the four known booleans, with defaults by
  audience (insured: pay + upload receipt; insurer: report status).
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from sqlalchemy import select

from app.core.config import settings
from app.models.base_class import utcnow
from app.models.case_file import CaseFile
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.broker import Broker
from app.models.enums import (
    EvidenceSource,
    EvidenceStatus,
    PartyRole,
    ReportedStatus,
    ShareAudience,
    ShareEntity,
)
from app.models.insurer import Insurer
from app.services.payments.insurers import INSTRUCTION_KEYS
from app.models.mail import PaymentEvidence, StatusInquiry
from app.models.policy import Policy
from app.models.share_link import ShareLink
from app.services.collection.resolver import lead_insurer_id, resolve_collection_mode
from app.services.collection.status import broker_collection_params, compute_installment_state
from app.services.fx import get_uf_rate, get_usd_rate, uf_to_clp

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.user import User

DEFAULT_EXPIRES_IN_DAYS = 60
MAX_EXPIRES_IN_DAYS = 365

PERMISSION_KEYS: tuple[str, ...] = (
    "can_pay",
    "can_upload_receipt",
    "can_report_status",
    "show_documents",
)
DEFAULT_PERMISSIONS: dict[str, dict[str, bool]] = {
    ShareAudience.INSURED.value: {
        "can_pay": True,
        "can_upload_receipt": True,
        "can_report_status": False,
        "show_documents": False,
    },
    ShareAudience.INSURER.value: {
        "can_pay": False,
        "can_upload_receipt": False,
        "can_report_status": True,
        "show_documents": False,
    },
}

#: The model each ``ShareEntity`` points at (every one carries ``broker_id``).
ENTITY_MODELS: dict[str, type] = {
    ShareEntity.POLICY.value: Policy,
    ShareEntity.COLLECTION_PLAN.value: CollectionPlan,
    ShareEntity.INSTALLMENT.value: CollectionInstallment,
    ShareEntity.CASE_FILE.value: CaseFile,
    ShareEntity.STATUS_INQUIRY.value: StatusInquiry,
}


class ShareTargetNotFound(LookupError):
    """The entity does not exist inside the caller's broker (router -> 404)."""


class ShareAudienceNotAllowed(ValueError):
    """The entity cannot be shared with that audience (router -> 422).

    ``str(exc)`` is the Spanish, user-facing reason.
    """


#: Spanish reasons for the entity × audience pairs a link may never take.
REASON_CASE_FILE_INSURER = (
    "Un expediente solo se comparte con el asegurado; para la compañía comparte la póliza."
)
REASON_INQUIRY_INSURED = "Una consulta de estado solo se comparte con la compañía."


def audience_refusal(entity_type: "ShareEntity | str", audience: "ShareAudience | str") -> str | None:
    """Why ``entity_type`` may not be shared with ``audience`` (``None`` = allowed).

    * a ``case_file`` holds policies of SEVERAL insurers (ramo × vigencia × N
      RUTs), so an insurer link to it would show insurer X the policies,
      cuotas and balances placed with insurer Y — insurer links target the
      policy / plan / cuota / inquiry instead;
    * a ``status_inquiry`` is the broker's question TO the insurer.

    Enforced at three points so no door bypasses it: the router (422),
    :func:`create_share_link` (:class:`ShareAudienceNotAllowed`) and
    :func:`resolve_share_scope` (``None`` -> 404 for any legacy row).
    """
    entity = _val(entity_type)
    aud = _val(audience)
    if entity == ShareEntity.CASE_FILE.value and aud == ShareAudience.INSURER.value:
        return REASON_CASE_FILE_INSURER
    if entity == ShareEntity.STATUS_INQUIRY.value and aud != ShareAudience.INSURER.value:
        return REASON_INQUIRY_INSURED
    return None


def _val(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; every stored datetime is UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def new_token() -> str:
    return secrets.token_urlsafe(24)


def normalize_permissions(
    audience: ShareAudience | str, permissions: dict[str, Any] | None
) -> dict[str, bool]:
    """Audience defaults overlaid with the known keys of ``permissions``.

    Unknown keys are dropped. Capabilities that do not fit the audience are
    forced off (an insurer link never pays; an insured link never reports the
    insurer's status) so a crafted request cannot widen a link.
    """
    aud = _val(audience)
    if aud not in DEFAULT_PERMISSIONS:
        raise ValueError(f"unknown share audience: {aud!r}")
    result = dict(DEFAULT_PERMISSIONS[aud])
    for key in PERMISSION_KEYS:
        if permissions and key in permissions and permissions[key] is not None:
            result[key] = bool(permissions[key])
    if aud == ShareAudience.INSURER.value:
        result["can_pay"] = False
        result["can_upload_receipt"] = False
    else:
        result["can_report_status"] = False
    return result


def resolve_share_entity(
    db: "Session", *, broker_id: int, entity_type: ShareEntity | str, entity_id: int
):
    """The target row inside ``broker_id``, or ``None`` (unknown type included)."""
    model = ENTITY_MODELS.get(_val(entity_type))
    if model is None:
        return None
    row = db.get(model, entity_id)
    if row is None or getattr(row, "broker_id", None) != broker_id:
        return None
    return row


def create_share_link(
    db: "Session",
    *,
    broker_id: int,
    user: "User | None",
    entity_type: ShareEntity | str,
    entity_id: int,
    audience: ShareAudience | str,
    permissions: dict[str, Any] | None = None,
    expires_in_days: int | None = None,
    label: str | None = None,
    now: datetime | None = None,
) -> ShareLink:
    """Mint a link to one of the broker's rows. Added + flushed; caller commits.

    Raises :class:`ShareTargetNotFound` when the entity is not the broker's,
    :class:`ShareAudienceNotAllowed` for a forbidden entity × audience pair
    (:func:`audience_refusal`) and ``ValueError`` for an unknown audience /
    out-of-range expiry.
    """
    entity = ShareEntity(_val(entity_type))
    aud = ShareAudience(_val(audience))
    refusal = audience_refusal(entity, aud)
    if refusal is not None:
        raise ShareAudienceNotAllowed(refusal)
    if resolve_share_entity(db, broker_id=broker_id, entity_type=entity, entity_id=entity_id) is None:
        raise ShareTargetNotFound(f"{entity.value} {entity_id} not found")
    days = DEFAULT_EXPIRES_IN_DAYS if expires_in_days is None else int(expires_in_days)
    if days < 1 or days > MAX_EXPIRES_IN_DAYS:
        raise ValueError(f"expires_in_days must be between 1 and {MAX_EXPIRES_IN_DAYS}")
    moment = _aware(now) or utcnow()
    link = ShareLink(
        broker_id=broker_id,
        token=new_token(),
        entity_type=entity,
        entity_id=entity_id,
        audience=aud,
        permissions=normalize_permissions(aud, permissions),
        label=label[:160] if label else None,
        expires_at=moment + timedelta(days=days),
        created_by_user_id=getattr(user, "id", None),
        view_count=0,
    )
    db.add(link)
    db.flush()
    return link


def is_link_live(link: ShareLink, *, now: datetime) -> bool:
    if link.revoked_at is not None:
        return False
    expires = _aware(link.expires_at)
    return expires is None or expires > (_aware(now) or utcnow())


def resolve_share_token(db: "Session", token: str, *, now: datetime) -> ShareLink | None:
    """The live link for ``token``; ``None`` if unknown, revoked or expired."""
    if not token or not isinstance(token, str) or len(token) > 64:
        return None
    link = db.scalar(select(ShareLink).where(ShareLink.token == token))
    if link is None or not is_link_live(link, now=now):
        return None
    return link


def record_share_view(link: ShareLink, *, now: datetime) -> None:
    """Count one public view (the caller commits)."""
    link.view_count = (link.view_count or 0) + 1
    link.last_viewed_at = _aware(now) or utcnow()


def revoke_share_link(link: ShareLink, *, now: datetime | None = None) -> None:
    """Revoke (idempotent: the first revocation time is kept)."""
    if link.revoked_at is None:
        link.revoked_at = _aware(now) or utcnow()


def link_permissions(link: ShareLink) -> dict[str, bool]:
    """The link's effective permissions (normalised, defaults filled)."""
    return normalize_permissions(link.audience, link.permissions)


def share_url(token: str) -> str:
    """The public SPA URL of a link: ``{PUBLIC_APP_URL}/s/{token}``."""
    return f"{settings.public_app_url}/s/{token}"


def whatsapp_url(text: str) -> str:
    """A WhatsApp click-to-chat URL that pre-fills ``text`` (no recipient)."""
    return "https://wa.me/?text=" + quote(text or "", safe="")


# =============================================================================
# Lane B5 — audience projections (what the public page may see)
# =============================================================================
#
# The public side NEVER trusts the polymorphic pointer alone: every row below is
# re-resolved with ``link.broker_id`` and the scope is narrowed to the ONE
# policy / plan / cuota / inquiry / case the link names. What is projected is a
# whitelist: no commission, no net premium, no notes, no document keys, no
# payer contacts, no other policy of the client.

#: The RBAC module whose ``View`` gates minting a link to each entity type.
ENTITY_MODULES: dict[str, str] = {
    ShareEntity.POLICY.value: "Policies",
    ShareEntity.COLLECTION_PLAN.value: "Collections",
    ShareEntity.INSTALLMENT.value: "Collections",
    ShareEntity.CASE_FILE.value: "CaseFiles",
    ShareEntity.STATUS_INQUIRY.value: "Collections",
}


#: Cuota states with nothing left to pay.
_CLOSED_STATES = frozenset({"paid", "voided"})

BUSINESS_TZ_NAME = "America/Santiago"


def module_for_entity(entity_type: ShareEntity | str) -> str:
    """The module whose ``View`` grant gates links to ``entity_type``."""
    return ENTITY_MODULES[_val(entity_type)]


def business_today(now: datetime | None = None) -> date:
    """Today's business date (America/Santiago; UTC if tzdata is missing)."""
    moment = _aware(now) or utcnow()
    try:
        from zoneinfo import ZoneInfo

        return moment.astimezone(ZoneInfo(BUSINESS_TZ_NAME)).date()
    except Exception:  # noqa: BLE001 - no tzdata: UTC is still deterministic
        return moment.astimezone(timezone.utc).date()


@dataclass
class ShareScope:
    """Everything one link may show, already tenant-checked."""

    policies: list[Policy] = field(default_factory=list)
    installments: list[CollectionInstallment] = field(default_factory=list)
    inquiry: StatusInquiry | None = None
    case_file: CaseFile | None = None

    def installment(self, installment_id: int) -> CollectionInstallment | None:
        for inst in self.installments:
            if inst.id == installment_id:
                return inst
        return None

    def policy_of(self, inst: CollectionInstallment) -> Policy | None:
        plan = inst.collection_plan
        for policy in self.policies:
            if plan is not None and policy.id == plan.policy_id:
                return policy
        return None


def _broker_policy(db: "Session", broker_id: int, policy_id: int | None) -> Policy | None:
    if policy_id is None:
        return None
    policy = db.get(Policy, policy_id)
    if policy is None or policy.broker_id != broker_id:
        return None
    return policy


def _installments_of_plans(
    db: "Session", broker_id: int, plan_ids: list[int]
) -> list[CollectionInstallment]:
    if not plan_ids:
        return []
    return list(
        db.scalars(
            select(CollectionInstallment)
            .where(
                CollectionInstallment.broker_id == broker_id,
                CollectionInstallment.collection_plan_id.in_(plan_ids),
            )
            .order_by(
                CollectionInstallment.collection_plan_id,
                CollectionInstallment.number,
                CollectionInstallment.id,
            )
        )
    )


def _plan_ids_of_policies(db: "Session", broker_id: int, policy_ids: list[int]) -> list[int]:
    if not policy_ids:
        return []
    return list(
        db.scalars(
            select(CollectionPlan.id)
            .where(
                CollectionPlan.broker_id == broker_id,
                CollectionPlan.policy_id.in_(policy_ids),
            )
            .order_by(CollectionPlan.id)
        )
    )


def _int_ids(values: Any) -> set[int]:
    ids: set[int] = set()
    for value in values or []:
        try:
            ids.add(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def resolve_share_scope(db: "Session", link: ShareLink) -> ShareScope | None:
    """The policies + cuotas ``link`` reaches, or ``None`` (-> 404).

    * ``policy``          -> that policy and the cuotas of all its plans;
    * ``collection_plan`` -> the plan's policy, that plan's cuotas only;
    * ``installment``     -> the cuota's policy, that cuota only;
    * ``status_inquiry``  -> the inquiry's policy, ONLY its ``installment_ids``;
    * ``case_file``       -> the case's policies (``policy_id`` + policies filed
      in it); a ``collection`` case narrows to the plans filed in it. INSURED
      audience only: a case spans several insurers (:func:`audience_refusal`).

    A link whose entity × audience pair is refused resolves to ``None`` too.
    """
    if audience_refusal(link.entity_type, link.audience) is not None:
        return None
    broker_id = link.broker_id
    entity = resolve_share_entity(
        db, broker_id=broker_id, entity_type=link.entity_type, entity_id=link.entity_id
    )
    if entity is None:
        return None
    kind = _val(link.entity_type)

    if kind == ShareEntity.POLICY.value:
        policy = entity
        plan_ids = _plan_ids_of_policies(db, broker_id, [policy.id])
        return ShareScope(policies=[policy], installments=_installments_of_plans(db, broker_id, plan_ids))

    if kind == ShareEntity.COLLECTION_PLAN.value:
        policy = _broker_policy(db, broker_id, entity.policy_id)
        if policy is None:
            return None
        return ShareScope(
            policies=[policy], installments=_installments_of_plans(db, broker_id, [entity.id])
        )

    if kind == ShareEntity.INSTALLMENT.value:
        plan = entity.collection_plan
        policy = _broker_policy(db, broker_id, plan.policy_id if plan else None)
        if plan is None or plan.broker_id != broker_id or policy is None:
            return None
        return ShareScope(policies=[policy], installments=[entity])

    if kind == ShareEntity.STATUS_INQUIRY.value:
        policy = _broker_policy(db, broker_id, entity.policy_id)
        if policy is None:
            return None
        wanted = _int_ids(entity.installment_ids)
        plan_ids = _plan_ids_of_policies(db, broker_id, [policy.id])
        installments = [
            inst for inst in _installments_of_plans(db, broker_id, plan_ids) if inst.id in wanted
        ]
        return ShareScope(policies=[policy], installments=installments, inquiry=entity)

    if kind == ShareEntity.CASE_FILE.value:
        case = entity
        policy_ids: list[int] = []
        if case.policy_id is not None:
            policy_ids.append(case.policy_id)
        policy_ids.extend(
            pid
            for pid in db.scalars(
                select(Policy.id)
                .where(Policy.broker_id == broker_id, Policy.case_file_id == case.id)
                .order_by(Policy.id)
            )
            if pid not in policy_ids
        )
        policies = [p for p in (_broker_policy(db, broker_id, pid) for pid in policy_ids) if p]
        plan_ids: list[int] = []
        if _val(case.kind) == "collection":
            plan_ids = list(
                db.scalars(
                    select(CollectionPlan.id)
                    .where(
                        CollectionPlan.broker_id == broker_id,
                        CollectionPlan.case_file_id == case.id,
                    )
                    .order_by(CollectionPlan.id)
                )
            )
            # A plan filed in the case may belong to a policy not listed yet.
            for plan_id in plan_ids:
                plan = db.get(CollectionPlan, plan_id)
                if plan is not None and all(p.id != plan.policy_id for p in policies):
                    extra = _broker_policy(db, broker_id, plan.policy_id)
                    if extra is not None:
                        policies.append(extra)
        if not plan_ids:
            plan_ids = _plan_ids_of_policies(db, broker_id, [p.id for p in policies])
        return ShareScope(
            policies=policies,
            installments=_installments_of_plans(db, broker_id, plan_ids),
            case_file=case,
        )

    return None  # pragma: no cover - ShareEntity is exhaustively mapped


def find_link_inquiry(db: "Session", link: ShareLink) -> StatusInquiry | None:
    """The status inquiry this link answers (its entity, or the one carrying it)."""
    if _val(link.entity_type) == ShareEntity.STATUS_INQUIRY.value:
        row = db.get(StatusInquiry, link.entity_id)
        return row if row is not None and row.broker_id == link.broker_id else None
    return db.scalar(
        select(StatusInquiry)
        .where(StatusInquiry.broker_id == link.broker_id, StatusInquiry.share_link_id == link.id)
        .order_by(StatusInquiry.id.desc())
    )


def _org_name(row: Any) -> str | None:
    if row is None:
        return None
    return getattr(row, "trade_name", None) or getattr(row, "legal_name", None)


def merged_payment_instructions(
    db: "Session", *, broker_id: int, insurer_id: int | None
) -> dict[str, str]:
    """Radal's insurer profile ⊕ the broker's override, field by field.

    ONE implementation for the share page, the payer notices and
    ``GET /payments/insurers``: lane B3's
    :func:`app.services.payments.insurers.merged_instructions` (only
    :data:`INSTRUCTION_KEYS`; a blank override never erases the profile's;
    legacy ``insurer.payment_url`` fills ``portal_url`` last).
    """
    if insurer_id is None:
        return {}
    from app.services.payments.insurers import merged_instructions  # noqa: PLC0415

    return merged_instructions(db, broker_id=broker_id, insurer_id=insurer_id)


def policy_insured_name(policy: Policy) -> str | None:
    """The insured's display name: primary insured party, else the client's insured."""
    parties = [
        p for p in (getattr(policy, "parties", None) or []) if _val(p.role) == PartyRole.INSURED.value
    ]
    parties.sort(key=lambda p: (not p.is_primary, p.id or 0))
    if parties:
        return parties[0].name
    client = getattr(policy, "client", None)
    return _org_name(getattr(client, "insured", None))


def _line_names(policy: Policy) -> tuple[str | None, str | None, str | None]:
    subtype = getattr(policy, "insurance_subtype", None)
    line = getattr(subtype, "insurance_line", None) if subtype is not None else None
    line = line or getattr(policy, "insurance_line", None)
    return (
        getattr(line, "name", None),
        getattr(subtype, "code", None),
        getattr(subtype, "name", None),
    )


def share_message(
    link: ShareLink,
    *,
    url: str,
    broker_name: str | None,
    policy_number: str | None,
    insurer_name: str | None,
) -> str:
    """The Spanish text pre-filled in WhatsApp (and handy for e-mail)."""
    broker = broker_name or "tu corredora"
    number = f" N° {policy_number}" if policy_number else ""
    if _val(link.audience) == ShareAudience.INSURER.value:
        return (
            f"Hola, les escribe {broker}. ¿Nos pueden confirmar el estado de pago de la "
            f"póliza{number}? Pueden informarlo directamente en este enlace: {url}"
        )
    company = f" con {insurer_name}" if insurer_name else ""
    sentence = f"Hola, {broker} te comparte el detalle de las cuotas de tu póliza{number}{company}"
    # "HDI Seguros S.A." already ends the sentence: never print "S.A..".
    return f"{sentence.rstrip('.')}. Revísalo aquí: {url}"


def payable_through_link(link: ShareLink, policy: Policy) -> bool:
    """Whether lane B3's checkout accepts this policy's cuotas through ``link``.

    Always ``True`` now: B3's checkout scopes a link with
    :func:`resolve_share_scope` too, so every cuota this projection shows is a
    cuota the checkout takes. Kept as a seam (the projection still calls it).
    """
    return True


def _to_clp(amount: Decimal, rate: Decimal | None) -> int | None:
    if rate is None:
        return None
    try:
        return uf_to_clp(amount, rate)
    except ValueError:
        return None


def build_public_view(
    db: "Session", link: ShareLink, *, now: datetime, logo_url: str | None = None
) -> dict[str, Any] | None:
    """The audience projection of a live link (``None`` if its target is gone).

    Read-only: counting the view is the router's job (it commits).
    """
    scope = resolve_share_scope(db, link)
    if scope is None:
        return None
    perms = link_permissions(link)
    audience = _val(link.audience)
    is_insured = audience == ShareAudience.INSURED.value
    today = business_today(now)
    cadence, tolerance = broker_collection_params(db, link.broker_id)
    uf_rate = get_uf_rate(db, today)
    usd_rate = get_usd_rate(db, today)
    broker = db.get(Broker, link.broker_id)

    # --- policies ----------------------------------------------------------
    policies: list[dict[str, Any]] = []
    needs_instructions: set[int] = set()
    by_policy: dict[int, dict[str, Any]] = {}
    for policy in scope.policies:
        line_name, subtype_code, subtype_name = _line_names(policy)
        row = {
            "id": policy.id,
            "policy_number": policy.policy_number,
            "insurer_name": _org_name(getattr(policy, "insurer", None)),
            "line_name": line_name,
            "subtype_code": subtype_code,
            "subtype_name": subtype_name,
            "start_date": policy.start_date,
            "end_date": policy.end_date,
            "insured_name": policy_insured_name(policy),
            "currency": _val(policy.currency) or "UF",
            "payment_instructions": None,
        }
        policies.append(row)
        by_policy[policy.id] = row

    # --- what this link already reported (insurer side) ---------------------
    reported: dict[int, dict[str, Any]] = {}
    inst_ids = [inst.id for inst in scope.installments]
    if not is_insured and inst_ids:
        rows = db.scalars(
            select(PaymentEvidence)
            .where(
                PaymentEvidence.broker_id == link.broker_id,
                PaymentEvidence.source == EvidenceSource.INSURER_SHARE_LINK,
                PaymentEvidence.installment_id.in_(inst_ids),
            )
            .order_by(PaymentEvidence.id)
        )
        for ev in rows:
            if evidence_link_id(ev) != link.id:
                continue
            reported[ev.installment_id] = {
                "status": _val(ev.reported_status),
                "reported_at": _aware(ev.created_at),
            }

    # --- receipts the payer already sent, still awaiting the broker (insured) --
    # Without this the page offered the upload again on reload and the insured
    # sent the same receipt many times (QA v12).
    receipt_pending: dict[int, dict[str, Any]] = {}
    if is_insured and inst_ids:
        rows = db.scalars(
            select(PaymentEvidence)
            .where(
                PaymentEvidence.broker_id == link.broker_id,
                PaymentEvidence.source == EvidenceSource.PAYER_UPLOAD,
                PaymentEvidence.status.in_(
                    (EvidenceStatus.SUGGESTED, EvidenceStatus.NEEDS_REVIEW)
                ),
                PaymentEvidence.installment_id.in_(inst_ids),
            )
            .order_by(PaymentEvidence.id)
        )
        for ev in rows:
            receipt_pending[ev.installment_id] = {
                "status": _val(ev.status),
                "reported_at": _aware(ev.created_at),
            }

    # --- cuotas --------------------------------------------------------------
    installments: list[dict[str, Any]] = []
    for inst in scope.installments:
        policy = scope.policy_of(inst)
        if policy is None:
            continue
        state = compute_installment_state(
            inst, list(inst.payments), today=today, cadence_days=cadence, tolerance_uf=tolerance
        )
        currency = _val(inst.currency) or "UF"
        rate = usd_rate if currency == "USD" else uf_rate
        amount = Decimal(str(inst.gross_amount_uf or 0))
        open_cuota = state.state not in _CLOSED_STATES
        item: dict[str, Any] = {
            "id": inst.id,
            "policy_id": policy.id,
            "number": inst.number,
            "coupon_number": inst.coupon_number,
            "due_date": inst.due_date,
            "currency": currency,
            "amount_uf": amount,
            "amount_clp": _to_clp(amount, rate),
            "paid_uf": state.paid_uf,
            "balance_uf": state.balance_uf,
            "balance_clp": _to_clp(state.balance_uf, rate) if open_cuota else 0,
            "state": state.state,
            "partial": "partial" in state.flags,
            "days_late": state.days_late,
            "level": None,
            "action": "none",
            "can_upload_receipt": False,
            "can_report": False,
            "reported": reported.get(inst.id),
            "receipt_pending": receipt_pending.get(inst.id),
        }
        if is_insured:
            try:
                mode = resolve_collection_mode(db, inst, broker_id=link.broker_id)
                level = mode.level
            except LookupError:  # pragma: no cover - scope is broker-checked
                level = "record"
            item["level"] = level
            if open_cuota:
                if level == "native" and perms["can_pay"] and payable_through_link(link, policy):
                    item["action"] = "pay"
                else:
                    item["action"] = "instructions"
                    needs_instructions.add(policy.id)
            item["can_upload_receipt"] = bool(perms["can_upload_receipt"]) and state.state != "voided"
        else:
            item["can_report"] = bool(perms["can_report_status"])
            item["action"] = "report" if item["can_report"] else "none"
        installments.append(item)

    if is_insured:
        for policy in scope.policies:
            if policy.id not in needs_instructions:
                continue
            insurer_id = lead_insurer_id(policy)
            merged = merged_payment_instructions(db, broker_id=link.broker_id, insurer_id=insurer_id)
            insurer = db.get(Insurer, insurer_id) if insurer_id is not None else None
            by_policy[policy.id]["payment_instructions"] = {
                "insurer_name": _org_name(insurer),
                **{key: merged.get(key) for key in INSTRUCTION_KEYS},
            }

    inquiry = scope.inquiry if scope.inquiry is not None else (
        None if is_insured else find_link_inquiry(db, link)
    )
    case = scope.case_file
    return {
        "audience": audience,
        "entity_type": _val(link.entity_type),
        "label": link.label,
        "expires_at": _aware(link.expires_at),
        "permissions": perms,
        "today": today,
        "broker": {"name": _org_name(broker), "logo_url": logo_url},
        "policy": policies[0] if policies else None,
        "policies": policies,
        "installments": installments,
        "uf_rate": uf_rate,
        "case_file": (
            {"reference": case.reference, "title": case.title, "period_label": case.period_label}
            if case is not None
            else None
        ),
        "inquiry": (
            {"reference": inquiry.token, "status": _val(inquiry.status)}
            if inquiry is not None and not is_insured
            else None
        ),
        "report_statuses": (
            [s.value for s in REPORTABLE_STATUSES]
            if not is_insured and perms["can_report_status"]
            else []
        ),
    }


#: What an insurer may report through a link (``unknown`` is internal).
REPORTABLE_STATUSES: tuple[ReportedStatus, ...] = (
    ReportedStatus.PAID,
    ReportedStatus.UNPAID,
    ReportedStatus.PARTIAL,
    ReportedStatus.CANCELLED,
)

#: ``payment_evidence.reasons`` entry code recording which link produced it.
LINK_REASON_CODE = "share_link"


def link_reason(link: ShareLink, detail: str) -> dict[str, Any]:
    """The ``reasons`` entry tying an evidence row to the link that produced it."""
    return {"code": LINK_REASON_CODE, "share_link_id": link.id, "detail": detail}


def evidence_link_id(evidence: PaymentEvidence) -> int | None:
    """The share link recorded in an evidence's ``reasons`` (or ``None``)."""
    reasons = evidence.reasons
    if isinstance(reasons, dict):
        reasons = [reasons]
    for entry in reasons or []:
        if isinstance(entry, dict) and entry.get("code") == LINK_REASON_CODE:
            try:
                return int(entry.get("share_link_id"))
            except (TypeError, ValueError):
                return None
    return None


__all__ = [
    "REASON_CASE_FILE_INSURER",
    "REASON_INQUIRY_INSURED",
    "ShareAudienceNotAllowed",
    "audience_refusal",
    "payable_through_link",
    "ENTITY_MODULES",
    "INSTRUCTION_KEYS",
    "LINK_REASON_CODE",
    "REPORTABLE_STATUSES",
    "evidence_link_id",
    "link_reason",
    "policy_insured_name",
    "ShareScope",
    "build_public_view",
    "business_today",
    "find_link_inquiry",
    "merged_payment_instructions",
    "module_for_entity",
    "resolve_share_scope",
    "share_message",
    "DEFAULT_EXPIRES_IN_DAYS",
    "DEFAULT_PERMISSIONS",
    "ENTITY_MODELS",
    "MAX_EXPIRES_IN_DAYS",
    "PERMISSION_KEYS",
    "ShareTargetNotFound",
    "create_share_link",
    "is_link_live",
    "link_permissions",
    "new_token",
    "normalize_permissions",
    "record_share_view",
    "resolve_share_entity",
    "resolve_share_token",
    "revoke_share_link",
    "share_url",
    "whatsapp_url",
]
