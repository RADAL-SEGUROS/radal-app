"""``/leads`` — the pipeline of data-point-only prospects.

A lead exists BEFORE there is an insured RUT, so it is deliberately not a
``client``: no files, no placement, just a tracked data point with notes and one
follow-up date. ``POST /leads/{id}/convert`` is the single door out of the
pipeline: it creates (or attaches) the canonical insured by RUT, the broker's
client row, an asset, a placement and the account ``case_file(stage=intake)`` —
all in ONE transaction.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.api.routers.clients import record_activity
from app.models.account_client import AccountClient, AccountClientRole
from app.models.activity import Note
from app.models.asset import Asset
from app.models.case_file import CaseFile
from app.models.client import Client
from app.models.enums import CaseFileKind, CaseFileStatus, CaseStage, EntityType, LeadStatus
from app.models.insurance_line import InsuranceLine
from app.models.insured import Insured
from app.models.placement import Placement, PlacementStatus
from app.models.sales_lead import SalesLead
from app.models.user import User
from app.schemas.lead import (
    LeadConversionResult,
    LeadConvert,
    LeadCreate,
    LeadListResponse,
    LeadRead,
    LeadSummary,
    LeadUpdate,
)
from app.services import case_files as machine

router = APIRouter(prefix="/leads", tags=["leads"])


# --- Helpers -------------------------------------------------------------------

def _get_lead(db: Session, lead_id: int, broker_id: int) -> SalesLead:
    lead = db.scalars(
        select(SalesLead).where(
            SalesLead.id == lead_id, SalesLead.broker_id == broker_id
        )
    ).first()
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    return lead


def _resolve_line(db: Session, line_id: int | None, broker_id: int) -> InsuranceLine | None:
    """A broker may use its own lines or the global (``broker_id`` NULL) catalogue."""
    if line_id is None:
        return None
    line = db.scalars(
        select(InsuranceLine).where(
            InsuranceLine.id == line_id,
            or_(InsuranceLine.broker_id == broker_id, InsuranceLine.broker_id.is_(None)),
        )
    ).first()
    if line is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Insurance line not found"
        )
    return line


def _read(db: Session, lead: SalesLead) -> LeadRead:
    line = (
        db.get(InsuranceLine, lead.insurance_line_id)
        if lead.insurance_line_id is not None
        else None
    )
    notes = db.scalar(
        select(func.count(Note.id)).where(
            Note.broker_id == lead.broker_id,
            Note.entity_type == EntityType.SALES_LEAD,
            Note.entity_id == lead.id,
        )
    )
    return LeadRead.model_validate(lead).model_copy(
        update={
            "insurance_line_name": line.name if line else None,
            "notes_count": int(notes or 0),
        }
    )


# --- Endpoints -----------------------------------------------------------------

@router.get("/summary", response_model=LeadSummary)
def get_leads_summary(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Leads", "View")),
) -> LeadSummary:
    """Totals, the status split and the two follow-up counters the board shows."""
    rows = db.execute(
        select(SalesLead.status, func.count(SalesLead.id))
        .where(SalesLead.broker_id == broker_id)
        .group_by(SalesLead.status)
    ).all()
    by_status = {str(key): int(value) for key, value in rows}

    today = date.today()
    week = today + timedelta(days=7)
    live = (LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.QUALIFIED)
    due_this_week = db.scalar(
        select(func.count(SalesLead.id)).where(
            SalesLead.broker_id == broker_id,
            SalesLead.follow_up_on.is_not(None),
            SalesLead.follow_up_on >= today,
            SalesLead.follow_up_on <= week,
            SalesLead.status.in_(live),
        )
    )
    overdue = db.scalar(
        select(func.count(SalesLead.id)).where(
            SalesLead.broker_id == broker_id,
            SalesLead.follow_up_on.is_not(None),
            SalesLead.follow_up_on < today,
            SalesLead.status.in_(live),
        )
    )
    return LeadSummary(
        total=sum(by_status.values()),
        by_status={s.value: by_status.get(s.value, 0) for s in LeadStatus},
        due_this_week=int(due_this_week or 0),
        overdue=int(overdue or 0),
    )


@router.get("", response_model=LeadListResponse)
def list_leads(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Leads", "View")),
    status_filter: list[LeadStatus] | None = Query(default=None, alias="status"),
    owner_id: int | None = Query(default=None, gt=0),
    insurance_line_id: int | None = Query(default=None, gt=0),
    follow_up_before: date | None = Query(default=None),
    q: str | None = Query(default=None, description="name, RUT or contact"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> LeadListResponse:
    stmt = select(SalesLead).where(SalesLead.broker_id == broker_id)
    if status_filter:
        stmt = stmt.where(SalesLead.status.in_(status_filter))
    if owner_id is not None:
        stmt = stmt.where(SalesLead.owner_user_id == owner_id)
    if insurance_line_id is not None:
        stmt = stmt.where(SalesLead.insurance_line_id == insurance_line_id)
    if follow_up_before is not None:
        stmt = stmt.where(
            SalesLead.follow_up_on.is_not(None), SalesLead.follow_up_on <= follow_up_before
        )
    if q:
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                SalesLead.name.ilike(needle),
                SalesLead.rut.ilike(needle),
                SalesLead.contact_name.ilike(needle),
                SalesLead.contact_email.ilike(needle),
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(SalesLead.id.desc()).offset(offset).limit(limit)
    ).all()
    return LeadListResponse(
        items=[_read(db, row) for row in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=LeadRead, status_code=status.HTTP_201_CREATED)
def create_lead(
    payload: LeadCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Leads", "Create")),
) -> LeadRead:
    """Track a prospect. The RUT is optional and validated only when supplied."""
    _resolve_line(db, payload.insurance_line_id, broker_id)

    lead = SalesLead(
        broker_id=broker_id,
        **payload.model_dump(exclude_unset=False),
    )
    if lead.owner_user_id is None:
        lead.owner_user_id = current_user.id
    db.add(lead)
    db.flush()

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="lead.created",
        entity_type=EntityType.SALES_LEAD,
        entity_id=lead.id,
        description=f"Lead {lead.name}",
        meta={"status": lead.status.value, "source": lead.source},
    )
    db.commit()
    db.refresh(lead)
    return _read(db, lead)


@router.get("/{lead_id}", response_model=LeadRead)
def get_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Leads", "View")),
) -> LeadRead:
    return _read(db, _get_lead(db, lead_id, broker_id))


@router.patch("/{lead_id}", response_model=LeadRead)
def update_lead(
    lead_id: int,
    payload: LeadUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Leads", "Edit")),
) -> LeadRead:
    lead = _get_lead(db, lead_id, broker_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return _read(db, lead)
    if "insurance_line_id" in changes:
        _resolve_line(db, changes["insurance_line_id"], broker_id)
    if changes.get("status") == LeadStatus.CONVERTED and lead.converted_case_file_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A lead becomes 'converted' through POST /leads/{id}/convert",
        )
    for field, value in changes.items():
        setattr(lead, field, value)

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="lead.updated",
        entity_type=EntityType.SALES_LEAD,
        entity_id=lead.id,
        description=f"Lead actualizado ({', '.join(sorted(changes))})",
        meta={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(lead)
    return _read(db, lead)


@router.delete("/{lead_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Leads", "Delete")),
) -> Response:
    lead = _get_lead(db, lead_id, broker_id)
    if lead.converted_case_file_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A converted lead is history; it cannot be deleted",
        )
    db.delete(lead)
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="lead.deleted",
        entity_type=EntityType.SALES_LEAD,
        entity_id=lead_id,
        description="Lead eliminado",
        meta={},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{lead_id}/convert", response_model=LeadConversionResult)
def convert_lead(
    lead_id: int,
    payload: LeadConvert,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Leads", "Edit")),
    _case_gate: User = Depends(require_permission("CaseFiles", "Create")),
) -> LeadConversionResult:
    """Advance the lead: insured + client + asset + placement + account case file.

    One transaction. The insured is CANONICAL and matched by RUT — a RUT another
    broker already knows is reused, never duplicated; confidentiality comes from
    tenant scoping on ``client``, not from refusing to share the canonical row.
    """
    lead = _get_lead(db, lead_id, broker_id)
    if lead.converted_case_file_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Lead {lead.id} was already converted "
            f"(case file {lead.converted_case_file_id})",
        )

    line = _resolve_line(db, payload.insurance_line_id or lead.insurance_line_id, broker_id)
    if line is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="insurance_line_id is required to open the placement",
        )

    insured = db.scalars(select(Insured).where(Insured.rut == payload.insured_rut)).first()
    if insured is None:
        insured = Insured(rut=payload.insured_rut, legal_name=payload.insured_legal_name)
        db.add(insured)
        db.flush()

    client = db.scalars(
        select(Client).where(
            Client.broker_id == broker_id, Client.insured_id == insured.id
        )
    ).first()
    if client is None:
        client = Client(broker_id=broker_id, insured_id=insured.id)
        db.add(client)
        db.flush()

    asset_payload = payload.asset
    asset = Asset(
        broker_id=broker_id,
        client_id=client.id,
        asset_type=asset_payload.asset_type if asset_payload else "property",
        name=asset_payload.name if asset_payload else payload.insured_legal_name,
    )
    if asset_payload is not None:
        for field in ("address", "commune", "region"):
            value = getattr(asset_payload, field, None)
            if value is not None and hasattr(asset, field):
                setattr(asset, field, value)
    db.add(asset)
    db.flush()

    # The group and the vigencia travel with the lead: "crear grupo → crear
    # cuenta" starts at the lead (spec v3 §2.3), and the folder's period is
    # authoritative from the moment it opens.
    period_start = payload.period_start or lead.period_start
    period_end = payload.period_end or lead.period_end
    account_group_id = lead.account_group_id or client.account_group_id
    if account_group_id is not None and client.account_group_id is None:
        client.account_group_id = account_group_id
    if period_start is None or period_end is None:
        # Rule 1 — the vigencia IS the folder. Conversion is the second door
        # into ``case_file(kind=account)`` and must refuse exactly what
        # ``POST /case-files`` refuses, or the lead opens a period-less folder
        # that no group tree can place and no reperiod can move.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "period_start and period_end are required to convert a lead: "
                "the vigencia IS the folder"
            ),
        )

    period_label = machine.period_label_for(period_start, period_end)

    # Rule 5 — no hybrid states: one OPEN folder per (group, line, vigencia).
    # The same guard the create / renew / reperiod doors run.
    clash = machine.find_open_folder(
        db,
        broker_id=broker_id,
        account_group_id=account_group_id,
        insurance_line_id=line.id,
        period_start=period_start,
        period_end=period_end,
    )
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "folder_exists",
                "case_file_id": clash.id,
                "reference": clash.reference,
                "detail": (
                    "An open folder for this group, line and vigencia already "
                    f"exists (case file {clash.id})"
                ),
            },
        )

    placement = Placement(
        broker_id=broker_id,
        client_id=client.id,
        asset_id=asset.id,
        insurance_line_id=line.id,
        status=PlacementStatus.DRAFT,
        period=payload.period or period_label,
        period_start=period_start,
        period_end=period_end,
    )
    db.add(placement)
    db.flush()

    case = CaseFile(
        broker_id=broker_id,
        client_id=client.id,
        placement_id=placement.id,
        insurance_line_id=line.id,
        kind=CaseFileKind.ACCOUNT,
        stage=CaseStage.INTAKE,
        status=CaseFileStatus.OPEN,
        reference=machine.build_reference(
            db,
            broker_id=broker_id,
            year=period_start.year if period_start is not None else None,
        ),
        title=payload.title or f"{insured.legal_name} · {line.name}",
        sequence_no=1,
        version=1,
        owner_user_id=lead.owner_user_id or current_user.id,
        summary=lead.summary,
        meta={"converted_from_lead_id": lead.id},
        account_group_id=account_group_id,
        period_start=period_start,
        period_end=period_end,
        period_label=period_label,
    )
    db.add(case)
    db.flush()
    placement.case_file_id = case.id

    # The contratante is the account's primary member (rule 6).
    db.add(
        AccountClient(
            broker_id=broker_id,
            case_file_id=case.id,
            client_id=client.id,
            role=AccountClientRole.POLICYHOLDER,
            is_primary=True,
        )
    )

    machine.record_stage_event(
        db, case=case, from_stage=CaseStage.LEAD, to_stage=CaseStage.INTAKE,
        user=current_user, note=f"Convertido desde el lead {lead.name}",
        meta={"sales_lead_id": lead.id},
    )

    lead.status = LeadStatus.CONVERTED
    lead.converted_client_id = client.id
    lead.converted_case_file_id = case.id
    if lead.rut is None:
        lead.rut = insured.rut

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="lead.converted",
        entity_type=EntityType.SALES_LEAD,
        entity_id=lead.id,
        description=f"Lead convertido en expediente {case.reference}",
        meta={
            "client_id": client.id,
            "insured_id": insured.id,
            "placement_id": placement.id,
            "case_file_id": case.id,
        },
    )
    db.commit()
    db.refresh(lead)
    db.refresh(case)
    return LeadConversionResult(
        lead=_read(db, lead),
        client_id=client.id,
        insured_id=insured.id,
        asset_id=asset.id,
        placement_id=placement.id,
        case_file_id=case.id,
        case_file_reference=case.reference,
    )


__all__ = ["router"]
