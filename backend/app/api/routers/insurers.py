"""Insurer catalog API — canonical insurers, native profiles and contacts.

Tenancy on a CANONICAL table
----------------------------
``insurer`` is cross-broker: one identity per company, keyed by **rut + cmf_code**.
It therefore has no ``broker_id`` column, and "scope every query to the caller's
broker" is expressed as a *visibility* + *mutability* rule instead:

* **visible** to a broker = native partners, Radal-seeded companies
  (``created_by_broker_id IS NULL``) and the externals that broker created.
  A company another broker discovered on its own proposal stays invisible.
* **mutable** by a broker = only its own externals
  (``is_native = false AND created_by_broker_id = <caller's broker>``).
  Native partner records and Radal-seeded rows are platform-managed
  (``docs/v2-architecture.md`` §2) — a broker gets a precise 403, never a silent
  no-op, so the UI can render the control disabled instead of dead.

``insurer_contact`` IS tenant data: ``broker_id`` NULL is the global default and
a broker only ever sees / writes ``broker_id IS NULL`` or its own rows.

Commercial confidentiality
--------------------------
``native_insurer_profile`` (agreement, SLA, priority, platform notes) is only
serialized when ``insurer.is_native`` is true — an external company can never
carry commercial fields.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_db, require_permission, resolve_user_type
from app.models.insurance_line import InsuranceLine
from app.models.insurer import (
    Insurer,
    InsurerContact,
    InsurerStatus,
    NativeInsurerProfile,
)
from app.models.user import User
from app.schemas.insurer import (
    InsurerContactCreate,
    InsurerContactRead,
    InsurerContactUpdate,
    InsurerCreate,
    InsurerListResponse,
    InsurerMatchRequest,
    InsurerMatchResult,
    InsurerRead,
    InsurerRecommendation,
    InsurerRecommendationResponse,
    InsurerUpdate,
    ResolvedContact,
)
from app.services.identifiers import (
    InvalidCmf,
    InvalidRut,
    normalize_codigo_cmf,
    normalize_rut,
    validate_codigo_cmf,
    validate_rut,
)

router = APIRouter(prefix="/insurers", tags=["insurers"])

MODULE = "Insurers"


# =============================================================================
# Matching / dedup helper — the entry point the proposal pipeline calls
# =============================================================================

class InsurerIdentityError(ValueError):
    """The insurer could not be identified or created from the given data."""


class InsurerIdentityConflict(ValueError):
    """``rut`` and ``cmf_code`` resolved to two DIFFERENT insurers."""


def _match_rut(value: str | None) -> str | None:
    """Normalize a RUT for LOOKUP (format only, no mod-11)."""
    if not value or not value.strip():
        return None
    try:
        return normalize_rut(value)
    except InvalidRut:
        return None


def _match_cmf(value: str | None) -> str | None:
    """Normalize a CMF code for LOOKUP (trim + uppercase)."""
    if not value or not value.strip():
        return None
    normalized = normalize_codigo_cmf(value)
    return normalized or None


def find_or_create_insurer(
    db: Session,
    *,
    rut: str | None = None,
    cmf_code: str | None = None,
    name: str | None = None,
    created_by_broker_id: int | None = None,
    commit: bool = False,
) -> tuple[Insurer, bool, Literal["rut", "cmf_code"] | None]:
    """Resolve an insurer by identity, creating it as EXTERNAL when unknown.

    Matching is done **only** on the normalized ``rut`` / ``cmf_code``. ``name``
    is never matched on — OCR of a proposal yields "HDI Seguros S.A.",
    "HDI SEGUROS SA" and "H.D.I." for the same company
    (``docs/v2-architecture.md`` §4.3). ``name`` is used solely as the
    ``legal_name`` of a newly created row.

    Returns ``(insurer, created, matched_on)``.

    Raises
    ------
    InsurerIdentityConflict
        ``rut`` and ``cmf_code`` point at two different existing insurers —
        the caller must disambiguate, we must never silently pick one.
    InsurerIdentityError
        No match and the data is insufficient/invalid to create a valid row.
        A proposal-grade insurer needs BOTH a mod-11 valid RUT and a real CMF
        code, plus a name to display.
    """
    lookup_rut = _match_rut(rut)
    lookup_cmf = _match_cmf(cmf_code)

    if not lookup_rut and not lookup_cmf:
        raise InsurerIdentityError(
            "An insurer can only be matched by rut or cmf_code; both were empty "
            "or unparseable. Matching by name is not allowed."
        )

    by_rut = (
        db.execute(select(Insurer).where(Insurer.rut == lookup_rut)).scalar_one_or_none()
        if lookup_rut
        else None
    )
    by_cmf = (
        db.execute(
            select(Insurer).where(Insurer.cmf_code == lookup_cmf)
        ).scalar_one_or_none()
        if lookup_cmf
        else None
    )

    if by_rut is not None and by_cmf is not None and by_rut.id != by_cmf.id:
        raise InsurerIdentityConflict(
            f"rut {lookup_rut} belongs to insurer {by_rut.id} but cmf_code "
            f"{lookup_cmf} belongs to insurer {by_cmf.id}"
        )

    if by_rut is not None:
        return by_rut, False, "rut"
    if by_cmf is not None:
        return by_cmf, False, "cmf_code"

    # --- Unknown company: create it as EXTERNAL (is_native = False) ----------
    display_name = (name or "").strip()
    if not display_name:
        raise InsurerIdentityError(
            "Cannot create an unknown insurer without a name to display"
        )
    try:
        canonical_rut = validate_rut(rut or "")
    except InvalidRut as exc:
        raise InsurerIdentityError(
            f"A new insurer needs a mod-11 valid RUT: {exc}"
        ) from exc
    try:
        canonical_cmf = validate_codigo_cmf(cmf_code or "", allow_tbd=False)
    except InvalidCmf as exc:
        raise InsurerIdentityError(
            f"A new insurer needs a valid CMF code: {exc}"
        ) from exc

    insurer = Insurer(
        rut=canonical_rut,
        cmf_code=canonical_cmf,
        legal_name=display_name[:255],
        is_native=False,
        status=InsurerStatus.ACTIVE,
        created_by_broker_id=created_by_broker_id,
    )
    db.add(insurer)
    try:
        db.flush()
    except IntegrityError:
        # Concurrent create of the same identity — adopt the winner.
        db.rollback()
        existing = db.execute(
            select(Insurer).where(
                or_(Insurer.rut == canonical_rut, Insurer.cmf_code == canonical_cmf)
            )
        ).scalars().first()
        if existing is None:
            raise
        return existing, False, "rut" if existing.rut == canonical_rut else "cmf_code"

    if commit:
        db.commit()
        db.refresh(insurer)
    return insurer, True, None


# =============================================================================
# Access helpers
# =============================================================================

def _is_platform(user: User) -> bool:
    return resolve_user_type(user) == "platform"


def _broker_context(user: User, broker_id: int | None = None) -> int:
    """The broker whose workspace this request acts in.

    Broker/insurer/insured users are pinned to their own ``broker_id`` and may
    never name another one. A platform user has no broker, so it must pass
    ``broker_id`` explicitly for broker-scoped operations.
    """
    own = getattr(user, "broker_id", None)
    if own is not None:
        if broker_id is not None and broker_id != own:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot act on another broker's workspace",
            )
        return own
    if broker_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="broker_id is required for platform users on broker-scoped calls",
        )
    return broker_id


def _optional_broker_context(user: User, broker_id: int | None = None) -> int | None:
    """Like :func:`_broker_context` but lets a platform user span every broker."""
    own = getattr(user, "broker_id", None)
    if own is None and broker_id is None:
        return None
    return _broker_context(user, broker_id)


def _visible_insurers(user: User):
    """Base SELECT restricted to the insurers the caller may see."""
    stmt = select(Insurer)
    if _is_platform(user):
        return stmt
    broker_id = getattr(user, "broker_id", None)
    return stmt.where(
        or_(
            Insurer.is_native.is_(True),
            Insurer.created_by_broker_id.is_(None),
            Insurer.created_by_broker_id == broker_id,
        )
    )


def _can_edit_insurer(user: User, insurer: Insurer) -> bool:
    """Platform edits anything; a broker edits only its own external companies."""
    if _is_platform(user):
        return True
    if insurer.is_native:
        return False
    return (
        insurer.created_by_broker_id is not None
        and insurer.created_by_broker_id == getattr(user, "broker_id", None)
    )


def _get_visible_insurer(db: Session, user: User, insurer_id: int) -> Insurer:
    insurer = db.execute(
        _visible_insurers(user)
        .where(Insurer.id == insurer_id)
        .options(selectinload(Insurer.native_profile))
    ).scalar_one_or_none()
    if insurer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Insurer not found"
        )
    return insurer


def _require_editable(user: User, insurer: Insurer) -> None:
    if _can_edit_insurer(user, insurer):
        return
    detail = (
        "Native partner insurers are managed by the Radal platform"
        if insurer.is_native
        else "This insurer record is not owned by your broker"
    )
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _require_platform(user: User, what: str) -> None:
    if not _is_platform(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{what} is managed by the Radal platform",
        )


def _check_insurance_line(db: Session, broker_id: int, line_id: int | None) -> InsuranceLine | None:
    """A line is usable when it is global (broker_id NULL) or the caller's own."""
    if line_id is None:
        return None
    line = db.get(InsuranceLine, line_id)
    if line is None or (line.broker_id is not None and line.broker_id != broker_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Insurance line not found"
        )
    return line


# =============================================================================
# Serialization
# =============================================================================

def _insurer_read(insurer: Insurer, user: User) -> InsurerRead:
    payload = InsurerRead.model_validate(insurer)
    # Commercial terms exist only for native partners — never leak them on an
    # external (broker-supplied) company.
    if not insurer.is_native:
        payload.native_profile = None
    payload.can_edit = _can_edit_insurer(user, insurer)
    return payload


def _contact_read(contact: InsurerContact, user: User) -> InsurerContactRead:
    payload = InsurerContactRead.model_validate(contact)
    payload.scope = "global" if contact.broker_id is None else "broker"
    payload.can_edit = _is_platform(user) or (
        contact.broker_id is not None
        and contact.broker_id == getattr(user, "broker_id", None)
    )
    return payload


# =============================================================================
# Insurers
# =============================================================================

@router.get("", response_model=InsurerListResponse)
def list_insurers(
    is_native: bool | None = Query(default=None, description="Filter native / external"),
    status_filter: InsurerStatus | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, max_length=120, description="Name / rut / cmf_code"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(MODULE, "View")),
) -> InsurerListResponse:
    """List the insurers visible to the caller's broker."""
    stmt = _visible_insurers(current_user)
    if is_native is not None:
        stmt = stmt.where(Insurer.is_native.is_(is_native))
    if status_filter is not None:
        stmt = stmt.where(Insurer.status == status_filter)
    if q:
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Insurer.legal_name.ilike(needle),
                Insurer.trade_name.ilike(needle),
                Insurer.rut.ilike(needle),
                Insurer.cmf_code.ilike(needle),
            )
        )

    total = db.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()

    rows = (
        db.execute(
            stmt.options(selectinload(Insurer.native_profile))
            .order_by(Insurer.is_native.desc(), Insurer.legal_name.asc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return InsurerListResponse(
        items=[_insurer_read(row, current_user) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/recommendations", response_model=InsurerRecommendationResponse)
def recommend_insurers(
    insurance_line_id: int | None = Query(default=None),
    broker_id: int | None = Query(default=None, description="Platform users only"),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(MODULE, "View")),
) -> InsurerRecommendationResponse:
    """Native partners suggested for an insurance line.

    Ranking: a partner that has a contact specific to this line first, then by
    ``native_insurer_profile.priority`` (lower = better), then by name.
    ``reason`` is a stable English key the UI translates via i18n.
    """
    broker = _broker_context(current_user, broker_id)
    line = _check_insurance_line(db, broker, insurance_line_id)

    rows = (
        db.execute(
            select(Insurer, NativeInsurerProfile)
            .outerjoin(
                NativeInsurerProfile, NativeInsurerProfile.insurer_id == Insurer.id
            )
            .where(
                Insurer.is_native.is_(True),
                Insurer.status == InsurerStatus.ACTIVE,
            )
            .options(selectinload(Insurer.native_profile))
        )
        .all()
    )

    items: list[InsurerRecommendation] = []
    for insurer, profile in rows:
        resolved = _resolve_contact(db, insurer.id, broker, insurance_line_id)
        has_line_contact = resolved is not None and resolved.match_level in (
            "broker_line",
            "line",
        )
        if has_line_contact:
            reason = "native_partner_with_line_contact"
        elif resolved is not None:
            reason = "native_partner_with_contact"
        else:
            reason = "native_partner"
        items.append(
            InsurerRecommendation(
                insurer=_insurer_read(insurer, current_user),
                priority=profile.priority if profile is not None else 100,
                sla_hours=profile.sla_hours if profile is not None else None,
                onboarded_at=profile.onboarded_at if profile is not None else None,
                has_line_contact=has_line_contact,
                contact=resolved,
                reason=reason,
            )
        )

    items.sort(
        key=lambda i: (
            0 if i.has_line_contact else 1,
            i.priority,
            i.insurer.legal_name.lower(),
        )
    )
    return InsurerRecommendationResponse(
        insurance_line_id=insurance_line_id,
        insurance_line_name=line.name if line is not None else None,
        items=items[:limit],
    )


@router.post("/match", response_model=InsurerMatchResult)
def match_insurer(
    payload: InsurerMatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(MODULE, "Create")),
) -> InsurerMatchResult:
    """Resolve an insurer by rut/cmf_code, creating it as external if unknown.

    HTTP face of :func:`find_or_create_insurer` — this is what the proposal
    pipeline uses to attach an uploaded proposal to a company.
    """
    broker_id = getattr(current_user, "broker_id", None)
    try:
        insurer, created, matched_on = find_or_create_insurer(
            db,
            rut=payload.rut,
            cmf_code=payload.cmf_code,
            name=payload.legal_name,
            created_by_broker_id=broker_id,
            commit=False,
        )
    except InsurerIdentityConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except InsurerIdentityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    db.commit()
    db.refresh(insurer)
    return InsurerMatchResult(
        insurer=_insurer_read(insurer, current_user),
        created=created,
        matched_on=matched_on,
    )


@router.post("", response_model=InsurerRead, status_code=status.HTTP_201_CREATED)
def create_insurer(
    payload: InsurerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(MODULE, "Create")),
) -> InsurerRead:
    """Register a company. Brokers may only add EXTERNAL insurers."""
    if payload.is_native or payload.native_profile is not None:
        _require_platform(current_user, "Native partner insurers")

    existing = db.execute(
        select(Insurer).where(
            or_(Insurer.rut == payload.rut, Insurer.cmf_code == payload.cmf_code)
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"An insurer with this rut/cmf_code already exists (id={existing.id})"
            ),
        )

    insurer = Insurer(
        rut=payload.rut,
        cmf_code=payload.cmf_code,
        legal_name=payload.legal_name,
        trade_name=payload.trade_name,
        is_native=payload.is_native,
        status=payload.status,
        cmf_status=payload.cmf_status,
        payment_url=payload.payment_url,
        # A broker-created company is owned by that broker; a platform-seeded
        # one stays global (NULL) and is visible to every tenant.
        created_by_broker_id=getattr(current_user, "broker_id", None),
    )
    db.add(insurer)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An insurer with this rut/cmf_code already exists",
        ) from exc

    if payload.is_native:
        profile_data = (
            payload.native_profile.model_dump()
            if payload.native_profile is not None
            else {}
        )
        db.add(NativeInsurerProfile(insurer_id=insurer.id, **profile_data))

    db.commit()
    db.refresh(insurer)
    return _insurer_read(insurer, current_user)


@router.get("/{insurer_id}", response_model=InsurerRead)
def get_insurer(
    insurer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(MODULE, "View")),
) -> InsurerRead:
    """One insurer. Commercial profile included only when native."""
    return _insurer_read(_get_visible_insurer(db, current_user, insurer_id), current_user)


@router.patch("/{insurer_id}", response_model=InsurerRead)
def update_insurer(
    insurer_id: int,
    payload: InsurerUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(MODULE, "Edit")),
) -> InsurerRead:
    """Update an insurer.

    Identity (``rut`` / ``cmf_code``), the ``is_native`` flag and the commercial
    profile are platform-only. A broker may edit the descriptive fields of the
    external companies it created.
    """
    insurer = _get_visible_insurer(db, current_user, insurer_id)
    _require_editable(current_user, insurer)

    data = payload.model_dump(exclude_unset=True)
    profile_patch = data.pop("native_profile", None)

    if any(key in data for key in ("rut", "cmf_code")):
        _require_platform(current_user, "Insurer identity (rut / cmf_code)")
    if "is_native" in data:
        _require_platform(current_user, "The native partner flag")
    if profile_patch is not None:
        _require_platform(current_user, "The commercial profile")

    for field, value in data.items():
        setattr(insurer, field, value)

    if profile_patch is not None:
        target_native = data.get("is_native", insurer.is_native)
        if not target_native:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only a native insurer can carry a commercial profile",
            )
        profile = insurer.native_profile
        if profile is None:
            profile = NativeInsurerProfile(insurer_id=insurer.id)
            db.add(profile)
        # exclude_unset propagates into the nested model, so every key present
        # here was explicitly sent (an explicit null clears the field).
        for field, value in profile_patch.items():
            setattr(profile, field, value)

    # Turning a partner into an external company drops its commercial terms.
    if data.get("is_native") is False and insurer.native_profile is not None:
        db.delete(insurer.native_profile)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another insurer already uses this rut/cmf_code",
        ) from exc
    db.refresh(insurer)
    return _insurer_read(insurer, current_user)


# =============================================================================
# Contacts
# =============================================================================

_MATCH_ORDER: tuple[str, ...] = ("broker_line", "broker", "line", "global")


def _visible_contacts(insurer_id: int, broker_id: int | None):
    """Contacts a broker may see: its own overrides + the global defaults.

    ``broker_id`` None = a platform user spanning every broker.
    """
    stmt = select(InsurerContact).where(InsurerContact.insurer_id == insurer_id)
    if broker_id is None:
        return stmt
    return stmt.where(
        or_(InsurerContact.broker_id.is_(None), InsurerContact.broker_id == broker_id)
    )


def _resolve_contact(
    db: Session, insurer_id: int, broker_id: int, insurance_line_id: int | None
) -> ResolvedContact | None:
    """Effective contact for (insurer, broker, line).

    Fallback order (``docs/v2-architecture.md`` §3.1):
    (broker+line) -> (broker) -> (line) -> global. Within a tier, ``is_primary``
    wins, then the oldest row, so the answer is deterministic.
    """
    candidates = (
        db.execute(
            select(InsurerContact)
            .where(
                InsurerContact.insurer_id == insurer_id,
                or_(
                    InsurerContact.broker_id.is_(None),
                    InsurerContact.broker_id == broker_id,
                ),
                or_(
                    InsurerContact.insurance_line_id.is_(None),
                    InsurerContact.insurance_line_id == insurance_line_id,
                )
                if insurance_line_id is not None
                else InsurerContact.insurance_line_id.is_(None),
            )
            .order_by(InsurerContact.is_primary.desc(), InsurerContact.id.asc())
        )
        .scalars()
        .all()
    )
    if not candidates:
        return None

    def level(contact: InsurerContact) -> str:
        broker_match = contact.broker_id is not None
        line_match = contact.insurance_line_id is not None
        if broker_match and line_match:
            return "broker_line"
        if broker_match:
            return "broker"
        if line_match:
            return "line"
        return "global"

    for tier in _MATCH_ORDER:
        for contact in candidates:
            if level(contact) == tier:
                payload = InsurerContactRead.model_validate(contact)
                payload.scope = "global" if contact.broker_id is None else "broker"
                return ResolvedContact(
                    insurer_id=insurer_id,
                    broker_id=broker_id,
                    insurance_line_id=insurance_line_id,
                    match_level=tier,  # type: ignore[arg-type]
                    contact=payload,
                )
    return None


@router.get("/{insurer_id}/contacts", response_model=list[InsurerContactRead])
def list_contacts(
    insurer_id: int,
    insurance_line_id: int | None = Query(default=None),
    broker_id: int | None = Query(default=None, description="Platform users only"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(MODULE, "View")),
) -> list[InsurerContactRead]:
    """All contacts of an insurer visible to the caller's broker."""
    _get_visible_insurer(db, current_user, insurer_id)
    broker = _optional_broker_context(current_user, broker_id)
    stmt = _visible_contacts(insurer_id, broker)
    if insurance_line_id is not None:
        stmt = stmt.where(
            or_(
                InsurerContact.insurance_line_id.is_(None),
                InsurerContact.insurance_line_id == insurance_line_id,
            )
        )
    rows = (
        db.execute(
            stmt.order_by(
                InsurerContact.broker_id.is_(None),
                InsurerContact.is_primary.desc(),
                InsurerContact.id.asc(),
            )
        )
        .scalars()
        .all()
    )
    return [_contact_read(row, current_user) for row in rows]


@router.get("/{insurer_id}/contacts/resolve", response_model=ResolvedContact)
def resolve_contact(
    insurer_id: int,
    insurance_line_id: int | None = Query(default=None),
    broker_id: int | None = Query(default=None, description="Platform users only"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(MODULE, "View")),
) -> ResolvedContact:
    """The single effective contact for this broker + line.

    Applies (broker+line) -> (broker) -> (line) -> global and reports which tier
    answered, so the UI can show "using the global default".
    """
    _get_visible_insurer(db, current_user, insurer_id)
    broker = _broker_context(current_user, broker_id)
    _check_insurance_line(db, broker, insurance_line_id)
    resolved = _resolve_contact(db, insurer_id, broker, insurance_line_id)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No contact resolves for this insurer / broker / line",
        )
    return resolved


@router.post(
    "/{insurer_id}/contacts",
    response_model=InsurerContactRead,
    status_code=status.HTTP_201_CREATED,
)
def create_contact(
    insurer_id: int,
    payload: InsurerContactCreate,
    broker_id: int | None = Query(default=None, description="Platform users only"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(MODULE, "Create")),
) -> InsurerContactRead:
    """Add a contact. ``scope='global'`` (broker_id NULL) is platform-only."""
    _get_visible_insurer(db, current_user, insurer_id)

    if payload.scope == "global":
        _require_platform(current_user, "Global insurer contacts")
        owner_broker_id: int | None = None
    else:
        owner_broker_id = _broker_context(current_user, broker_id)
        _check_insurance_line(db, owner_broker_id, payload.insurance_line_id)

    if payload.scope == "global" and payload.insurance_line_id is not None:
        # A global contact may still be line-scoped, but only to a GLOBAL line.
        line = db.get(InsuranceLine, payload.insurance_line_id)
        if line is None or line.broker_id is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A global contact can only be scoped to a global insurance line",
            )

    contact = InsurerContact(
        insurer_id=insurer_id,
        broker_id=owner_broker_id,
        insurance_line_id=payload.insurance_line_id,
        name=payload.name,
        email=str(payload.email) if payload.email else None,
        phone=payload.phone,
        role=payload.role,
        is_primary=payload.is_primary,
    )
    db.add(contact)
    db.flush()
    if payload.is_primary:
        _demote_siblings(db, contact)
    db.commit()
    db.refresh(contact)
    return _contact_read(contact, current_user)


@router.patch(
    "/{insurer_id}/contacts/{contact_id}", response_model=InsurerContactRead
)
def update_contact(
    insurer_id: int,
    contact_id: int,
    payload: InsurerContactUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(MODULE, "Edit")),
) -> InsurerContactRead:
    """Update a contact owned by the caller's broker (global ones: platform-only)."""
    contact = _get_writable_contact(db, current_user, insurer_id, contact_id)
    data = payload.model_dump(exclude_unset=True)

    if "insurance_line_id" in data and data["insurance_line_id"] is not None:
        scope_broker = contact.broker_id
        if scope_broker is None:
            line = db.get(InsuranceLine, data["insurance_line_id"])
            if line is None or line.broker_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="A global contact can only be scoped to a global insurance line",
                )
        else:
            _check_insurance_line(db, scope_broker, data["insurance_line_id"])

    for field, value in data.items():
        setattr(contact, field, str(value) if field == "email" and value else value)

    if data.get("is_primary"):
        _demote_siblings(db, contact)
    db.commit()
    db.refresh(contact)
    return _contact_read(contact, current_user)


@router.delete(
    "/{insurer_id}/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_contact(
    insurer_id: int,
    contact_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(MODULE, "Delete")),
) -> Response:
    """Delete a contact owned by the caller's broker (global ones: platform-only)."""
    contact = _get_writable_contact(db, current_user, insurer_id, contact_id)
    db.delete(contact)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _get_writable_contact(
    db: Session, user: User, insurer_id: int, contact_id: int
) -> InsurerContact:
    _get_visible_insurer(db, user, insurer_id)
    contact = db.get(InsurerContact, contact_id)
    if contact is None or contact.insurer_id != insurer_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found"
        )
    if _is_platform(user):
        return contact
    broker_id = getattr(user, "broker_id", None)
    if contact.broker_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Global insurer contacts are managed by the Radal platform",
        )
    if contact.broker_id != broker_id:
        # Another tenant's row: report it as absent, never as forbidden.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found"
        )
    return contact


def _demote_siblings(db: Session, contact: InsurerContact) -> None:
    """Keep at most one primary contact per (insurer, broker, line) bucket."""
    siblings = (
        db.execute(
            select(InsurerContact).where(
                InsurerContact.insurer_id == contact.insurer_id,
                InsurerContact.id != contact.id,
                InsurerContact.is_primary.is_(True),
                InsurerContact.broker_id.is_(None)
                if contact.broker_id is None
                else InsurerContact.broker_id == contact.broker_id,
                InsurerContact.insurance_line_id.is_(None)
                if contact.insurance_line_id is None
                else InsurerContact.insurance_line_id == contact.insurance_line_id,
            )
        )
        .scalars()
        .all()
    )
    for sibling in siblings:
        sibling.is_primary = False


__all__ = [
    "router",
    "find_or_create_insurer",
    "InsurerIdentityError",
    "InsurerIdentityConflict",
]
