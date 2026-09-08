"""The agent's typed tool registry (v4 agent spec §4).

One dataclass, one dict — mirroring the extraction registry's shape so the
pattern is already familiar to the codebase. READ tools execute inline inside
the model loop under the CALLER's own grants; WRITE tools never execute there:
the loop persists an ``agent_action`` row and only the confirm endpoint runs
``execute`` — under the confirming user's real RBAC gate (CLAUDE.md rule 6).

Every ``execute`` takes ``(db, *, broker_id, user, args)`` — the caller's
identity, always. There is no service account and no ambient privilege: a tool
can never read or write anything the human in the chair could not. Executors
REUSE the exact transaction the normal router performs (they call the router's
own functions and helpers), so a confirmed action is indistinguishable in the
database from the same operation performed through the UI.

Tool descriptions and card summaries are Spanish — they are LLM prompt /
user-facing copy, the two places Spanish belongs (CLAUDE.md rule 1).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.permissions import user_has_permission
from app.core.roles_config import ACTIONS, MODULES
from app.models.enums import EntityType
from app.models.user import User

__all__ = [
    "ToolSpec",
    "TOOL_REGISTRY",
    "READ_TOOLS",
    "WRITE_TOOLS",
    "openai_tools",
    "gates_for",
    "action_anchor",
    "action_allowed",
]

# Fold-back truncation budget for a tool result (spec §5.3 / §4).
DEFAULT_MAX_RESULT_CHARS = 6_000

#: Spanish labels for permission-denied folds — the model answers gracefully
#: instead of the turn 403ing.
_MODULE_ES = {
    "Dashboard": "el panel",
    "Groups": "los grupos",
    "CaseFiles": "los expedientes",
    "Proposals": "las propuestas",
    "Quotes": "las cotizaciones",
    "Documents": "los documentos",
    "Clients": "los clientes",
    "Policies": "las pólizas",
    "Leads": "los leads",
}


# =============================================================================
# Args schemas — validate the model's arguments BEFORE anything runs/persists.
# =============================================================================


class _Args(BaseModel):
    """Base for tool arguments: tolerate hallucinated extra keys, strip noise."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class SearchEntitiesArgs(_Args):
    query: str = Field(..., min_length=2, max_length=120)
    kinds: list[Literal["groups", "clients", "assets", "quotes", "proposals"]] | None = None


class GetGroupTreeArgs(_Args):
    group_id: int = Field(..., gt=0)


class GetCaseFileArgs(_Args):
    case_file_id: int = Field(..., gt=0)


class GetCaseHistoryArgs(_Args):
    case_file_id: int = Field(..., gt=0)


class GetQuoteComparisonArgs(_Args):
    quote_request_id: int = Field(..., gt=0)
    include_rejected: bool = False


class ListDocumentsArgs(_Args):
    case_file_id: int | None = Field(default=None, gt=0)
    entity_type: str | None = None
    entity_id: int | None = Field(default=None, gt=0)
    category: str | None = None
    section: str | None = None

    @model_validator(mode="after")
    def _at_least_one_anchor(self) -> "ListDocumentsArgs":
        if self.case_file_id is None and not (self.entity_type and self.entity_id):
            raise ValueError(
                "list_documents needs an anchor: case_file_id, or entity_type + entity_id"
            )
        return self


class CreateGroupArgs(_Args):
    name: str = Field(..., min_length=1, max_length=255)
    notes: str | None = None
    client_ids: list[int] | None = None


class AttachClientToGroupArgs(_Args):
    group_id: int = Field(..., gt=0)
    client_id: int = Field(..., gt=0)


class CreateCaseFileArgs(_Args):
    client_id: int = Field(..., gt=0)
    # Post-sale kinds are NOT proposable by the agent this pass: they hang off
    # policies with guards the chat cannot review.
    kind: Literal["account"] = "account"
    title: str = Field(..., min_length=1, max_length=255)
    insurance_line_id: int | None = Field(default=None, gt=0)
    group_id: int | None = Field(default=None, gt=0)
    period_start: date | None = None
    period_end: date | None = None


class RenewCaseArgs(_Args):
    case_file_id: int = Field(..., gt=0)
    period_start: date
    period_end: date
    period_label: str | None = Field(default=None, min_length=1, max_length=32)
    note: str | None = Field(default=None, max_length=4000)


class AddNoteArgs(_Args):
    entity_type: str = Field(..., min_length=1, max_length=30)
    entity_id: int = Field(..., gt=0)
    body: str = Field(..., min_length=1, max_length=4000)
    is_internal: bool = True
    follow_up_on: date | None = None


# =============================================================================
# The spec
# =============================================================================


@dataclass(frozen=True)
class ToolSpec:
    """One tool: registry key + OpenAI function name, gate, schema, executor."""

    name: str
    kind: Literal["read", "write"]
    module: str  # RBAC module gate (registry-sanity checked against MODULES)
    action: str  # RBAC action gate (checked against ACTIONS)
    description: str  # Spanish, shown to the model
    args_schema: type[BaseModel]
    # read: runs inline in the loop; write: runs ONLY at confirm time.
    execute: Callable[..., dict]
    # write only: the Spanish card sentence, built server-side, never by the model.
    summarize: Callable[..., str] | None = None
    # write only: broker-scoped existence checks run at PROPOSAL time so a
    # doomed proposal is refused immediately. Raises HTTPException.
    validate_proposal: Callable[..., None] | None = None
    # Dynamic gate resolution (add_note). Returns [(module, action), ...];
    # None means [(self.module, self.action)]. May raise ValueError for
    # unresolvable arguments.
    resolve_gates: Callable[[Any], list[tuple[str, str]]] | None = None
    # Which entity family the action is ABOUT — anchors the activity trail even
    # for a discard, where nothing was created. Returns (EntityType, id|None).
    anchor: Callable[[dict], tuple[EntityType, int | None]] | None = None
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS


def gates_for(spec: ToolSpec, args: Any) -> list[tuple[str, str]]:
    """The (module, action) pairs a caller must hold for this call."""
    if spec.resolve_gates is not None:
        return spec.resolve_gates(args)
    return [(spec.module, spec.action)]


def permission_denied_payload(module: str, action: str) -> dict:
    label = _MODULE_ES.get(module, module)
    return {
        "error": "permission_denied",
        "module": module,
        "action": action,
        "detail": f"El usuario no tiene permiso de {action} sobre {label}",
    }


# =============================================================================
# Shared result helpers
# =============================================================================

_STRIP_KEYS = {"s3_key", "bucket", "hashed_password"}


def _strip_keys(node: Any) -> Any:
    """Remove storage keys and secrets from a folded tool result, recursively.

    The model reasons over metadata only — never raw storage locations
    (CLAUDE.md rule 8: files are served through the broker-scoped document
    routes, not by key).
    """
    if isinstance(node, dict):
        return {k: _strip_keys(v) for k, v in node.items() if k not in _STRIP_KEYS}
    if isinstance(node, list):
        return [_strip_keys(item) for item in node]
    return node


def _dump(model: BaseModel) -> dict:
    return _strip_keys(json.loads(model.model_dump_json()))


# =============================================================================
# READ executors — all reuse the routers' own broker-scoped queries/helpers.
# Router imports are lazy: this module is imported by services and routers and
# must not create an import cycle.
# =============================================================================


def _exec_search_entities(db: Session, *, broker_id: int, user: User, args: SearchEntitiesArgs) -> dict:
    from app.api.routers.search import global_search

    results = global_search(q=args.query, db=db, broker_id=broker_id, current_user=user)
    payload = _dump(results)
    if args.kinds:
        wanted = set(args.kinds)
        payload = {kind: hits for kind, hits in payload.items() if kind in wanted}
    return payload


def _exec_get_group_tree(db: Session, *, broker_id: int, user: User, args: GetGroupTreeArgs) -> dict:
    from app.services.navigator import GroupNotFound, build_group_tree

    try:
        tree = build_group_tree(db, user, args.group_id, broker_id=broker_id)
    except GroupNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc) or "Group not found"
        ) from exc
    return _dump(tree)


def _exec_get_case_file(db: Session, *, broker_id: int, user: User, args: GetCaseFileArgs) -> dict:
    from app.api.routers.case_files import _get_case
    from app.models.case_file import CaseFile
    from app.models.client import Client
    from app.models.document import Document
    from app.models.insured import Insured
    from app.services import case_files as machine

    case = _get_case(db, args.case_file_id, broker_id, user)

    client_name = None
    client_rut = None
    client = db.get(Client, case.client_id) if case.client_id else None
    if client is not None:
        insured = db.get(Insured, client.insured_id)
        if insured is not None:
            client_name, client_rut = insured.legal_name, insured.rut

    doc_counts = {
        (section.value if section is not None else "unfiled"): int(count)
        for section, count in db.execute(
            select(Document.section, func.count(Document.id))
            .where(Document.broker_id == broker_id, Document.case_file_id == case.id)
            .group_by(Document.section)
        ).all()
    }
    children = db.scalars(
        select(CaseFile)
        .where(CaseFile.broker_id == broker_id, CaseFile.parent_case_file_id == case.id)
        .order_by(CaseFile.id)
    ).all()
    options = machine.transition_options(db, case)

    return {
        "id": case.id,
        "reference": case.reference,
        "title": case.title,
        "kind": case.kind.value,
        "stage": case.stage.value,
        "status": case.status.value,
        "period_label": case.period_label,
        "period_start": case.period_start.isoformat() if case.period_start else None,
        "period_end": case.period_end.isoformat() if case.period_end else None,
        "client": {"id": case.client_id, "legal_name": client_name, "rut": client_rut},
        "account_group_id": case.account_group_id,
        "placement_id": case.placement_id,
        "policy_id": case.policy_id,
        "documents_by_section": doc_counts,
        "children": [
            {"id": child.id, "reference": child.reference, "kind": child.kind.value,
             "stage": child.stage.value}
            for child in children
        ],
        "transitions": [
            {"to_stage": option.to_stage.value, "allowed": option.allowed,
             "reason": option.reason}
            for option in options
        ],
        "summary": case.summary,
    }


def _exec_get_case_history(db: Session, *, broker_id: int, user: User, args: GetCaseHistoryArgs) -> dict:
    from app.api.routers.case_files import get_case_file_history

    payload = get_case_file_history(
        args.case_file_id, db=db, broker_id=broker_id, current_user=user
    )
    return _dump(payload)


def _exec_get_quote_comparison(
    db: Session, *, broker_id: int, user: User, args: GetQuoteComparisonArgs
) -> dict:
    from app.api.routers.quotes import compare_proposals

    payload = compare_proposals(
        quote_id=args.quote_request_id,
        db=db,
        broker_id=broker_id,
        _user=user,
        include_rejected=args.include_rejected,
    )
    return _dump(payload)


def _exec_list_documents(db: Session, *, broker_id: int, user: User, args: ListDocumentsArgs) -> dict:
    from app.models.document import Document, DocumentCategory
    from app.models.enums import CaseSection

    def _member(enum_cls, raw: str, label: str):
        try:
            return enum_cls(raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown {label} '{raw}'",
            ) from exc

    stmt = select(Document).where(Document.broker_id == broker_id)
    if args.case_file_id is not None:
        stmt = stmt.where(Document.case_file_id == args.case_file_id)
    if args.entity_type and args.entity_id:
        stmt = stmt.where(
            Document.entity_type == _member(EntityType, args.entity_type, "entity_type"),
            Document.entity_id == args.entity_id,
        )
    if args.category:
        stmt = stmt.where(
            Document.category == _member(DocumentCategory, args.category, "category")
        )
    if args.section:
        stmt = stmt.where(
            Document.section == _member(CaseSection, args.section, "section")
        )
    rows = db.scalars(stmt.order_by(Document.id).limit(50)).all()
    return {
        "documents": [
            {
                "id": row.id,
                "category": row.category.value if row.category else None,
                "section": row.section.value if row.section else None,
                "document_code": row.document_code,
                "original_name": row.original_name,
                "mime_type": row.mime_type,
                "case_file_id": row.case_file_id,
                "uploaded_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "total": len(rows),
    }


# =============================================================================
# WRITE validators / executors / summaries
# =============================================================================


def _validate_create_group(db: Session, *, broker_id: int, user: User, args: CreateGroupArgs) -> None:
    from app.api.routers.account_groups import _resolve_client

    for client_id in args.client_ids or []:
        _resolve_client(db, client_id, broker_id)  # 404 outside the tenant


def _exec_create_group(db: Session, *, broker_id: int, user: User, args: CreateGroupArgs) -> dict:
    from app.api.routers.account_groups import create_account_group
    from app.schemas.account_group import AccountGroupCreate

    detail = create_account_group(
        payload=AccountGroupCreate(
            name=args.name, notes=args.notes, client_ids=args.client_ids
        ),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return {
        "entity_type": EntityType.ACCOUNT_GROUP.value,
        "entity_id": detail.id,
        "url": f"/groups/{detail.id}",
        "detail": f"Grupo «{detail.name}» creado",
    }


def _summarize_create_group(db: Session, *, broker_id: int, args: CreateGroupArgs) -> str:
    extra = ""
    if args.client_ids:
        count = len(args.client_ids)
        extra = f" y adjuntar {count} cliente{'s' if count != 1 else ''}"
    return f"Crear el grupo «{args.name}»{extra}"


def _gates_create_group(args: CreateGroupArgs) -> list[tuple[str, str]]:
    gates = [("Groups", "Create")]
    if args.client_ids:
        gates.append(("Groups", "Edit"))  # attaching is an Edit, matching the route
    return gates


def _validate_attach_client(
    db: Session, *, broker_id: int, user: User, args: AttachClientToGroupArgs
) -> None:
    from app.api.routers.account_groups import _get_group, _resolve_client

    _get_group(db, args.group_id, broker_id)
    _resolve_client(db, args.client_id, broker_id)


def _exec_attach_client(
    db: Session, *, broker_id: int, user: User, args: AttachClientToGroupArgs
) -> dict:
    from app.api.routers.account_groups import attach_client
    from app.schemas.account_group import AccountGroupAttachClient

    detail = attach_client(
        group_id=args.group_id,
        payload=AccountGroupAttachClient(client_id=args.client_id),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return {
        "entity_type": EntityType.ACCOUNT_GROUP.value,
        "entity_id": detail.id,
        "url": f"/groups/{detail.id}",
        "detail": f"Cliente adjuntado al grupo «{detail.name}»",
    }


def _summarize_attach_client(
    db: Session, *, broker_id: int, args: AttachClientToGroupArgs
) -> str:
    from app.models.account_group import AccountGroup
    from app.models.client import Client
    from app.models.insured import Insured

    group = db.get(AccountGroup, args.group_id)
    group_name = group.name if group is not None and group.broker_id == broker_id else f"#{args.group_id}"
    client_name = f"#{args.client_id}"
    client = db.get(Client, args.client_id)
    if client is not None and client.broker_id == broker_id:
        insured = db.get(Insured, client.insured_id)
        if insured is not None:
            client_name = insured.legal_name
    return f"Adjuntar el cliente {client_name} al grupo «{group_name}»"


def _validate_create_case_file(
    db: Session, *, broker_id: int, user: User, args: CreateCaseFileArgs
) -> None:
    from app.api.routers.account_groups import _get_group
    from app.api.routers.case_files import _resolve_client

    _resolve_client(db, args.client_id, broker_id)
    if args.group_id is not None:
        _get_group(db, args.group_id, broker_id)
    if args.period_start is None or args.period_end is None:
        # The route would refuse this at confirm; refuse the doomed proposal now.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "period_start and period_end are required for an account case "
                "file: the vigencia IS the folder"
            ),
        )


def _exec_create_case_file(
    db: Session, *, broker_id: int, user: User, args: CreateCaseFileArgs
) -> dict:
    from app.api.routers.case_files import create_case_file
    from app.models.case_file import CaseFileKind
    from app.schemas.case_file import CaseFileCreate

    detail = create_case_file(
        payload=CaseFileCreate(
            kind=CaseFileKind.ACCOUNT,
            client_id=args.client_id,
            title=args.title,
            insurance_line_id=args.insurance_line_id,
            account_group_id=args.group_id,
            period_start=args.period_start,
            period_end=args.period_end,
        ),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return {
        "entity_type": EntityType.CASE_FILE.value,
        "entity_id": detail.id,
        "url": f"/cases/{detail.id}",
        "detail": f"Expediente {detail.reference or detail.id} creado",
    }


def _summarize_create_case_file(
    db: Session, *, broker_id: int, args: CreateCaseFileArgs
) -> str:
    return f"Abrir el expediente «{args.title}» para el cliente #{args.client_id}"


def _validate_renew_case(db: Session, *, broker_id: int, user: User, args: RenewCaseArgs) -> None:
    from app.api.routers.case_files import _get_case

    _get_case(db, args.case_file_id, broker_id, user)


def _exec_renew_case(db: Session, *, broker_id: int, user: User, args: RenewCaseArgs) -> dict:
    from app.api.routers.case_files import renew_case_file
    from app.schemas.case_file import CaseRenewBody

    detail = renew_case_file(
        case_id=args.case_file_id,
        payload=CaseRenewBody(
            period_start=args.period_start,
            period_end=args.period_end,
            period_label=args.period_label,
        ),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    if args.note:
        from app.api.routers.notes import create_note
        from app.schemas.note import NoteCreate

        create_note(
            payload=NoteCreate(
                entity_type=EntityType.CASE_FILE, entity_id=detail.id, body=args.note
            ),
            db=db,
            broker_id=broker_id,
            current_user=user,
        )
    return {
        "entity_type": EntityType.CASE_FILE.value,
        "entity_id": detail.id,
        "url": f"/cases/{detail.id}",
        "detail": f"Renovación abierta como {detail.reference or detail.id}",
    }


def _summarize_renew_case(db: Session, *, broker_id: int, args: RenewCaseArgs) -> str:
    from app.models.case_file import CaseFile

    case = db.get(CaseFile, args.case_file_id)
    label = (
        case.reference or f"#{args.case_file_id}"
        if case is not None and case.broker_id == broker_id
        else f"#{args.case_file_id}"
    )
    return (
        f"Abrir la renovación de {label} para la vigencia "
        f"{args.period_start.isoformat()} → {args.period_end.isoformat()}"
    )


def _note_entity_type(raw: str) -> EntityType:
    try:
        return EntityType(raw)
    except ValueError as exc:
        raise ValueError(f"Notes cannot be attached to '{raw}'") from exc


def _gates_add_note(args: AddNoteArgs) -> list[tuple[str, str]]:
    from app.api.routers.notes import MODULE_BY_ENTITY

    entity_type = _note_entity_type(args.entity_type)
    module = MODULE_BY_ENTITY.get(entity_type)
    if module is None:
        raise ValueError(f"Notes cannot be attached to '{args.entity_type}'")
    return [(module, "Comment")]


def _validate_add_note(db: Session, *, broker_id: int, user: User, args: AddNoteArgs) -> None:
    from app.api.routers.notes import _resolve_target

    _resolve_target(db, _note_entity_type(args.entity_type), args.entity_id, broker_id)


def _exec_add_note(db: Session, *, broker_id: int, user: User, args: AddNoteArgs) -> dict:
    from app.api.routers.notes import create_note
    from app.schemas.note import NoteCreate

    note = create_note(
        payload=NoteCreate(
            entity_type=_note_entity_type(args.entity_type),
            entity_id=args.entity_id,
            body=args.body,
            is_internal=args.is_internal,
            follow_up_on=args.follow_up_on,
        ),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return {
        "entity_type": args.entity_type,
        "entity_id": args.entity_id,
        "url": (f"/cases/{args.entity_id}" if args.entity_type == "case_file" else None),
        "detail": f"Nota agregada (nota #{note.id})",
        "note_id": note.id,
    }


def _summarize_add_note(db: Session, *, broker_id: int, args: AddNoteArgs) -> str:
    body = args.body if len(args.body) <= 80 else args.body[:77] + "…"
    return f"Agregar una nota a {args.entity_type} #{args.entity_id}: «{body}»"


# =============================================================================
# The registry
# =============================================================================

TOOL_REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        # --- READ ------------------------------------------------------------
        ToolSpec(
            name="search_entities",
            kind="read",
            module="Dashboard",
            action="View",
            description=(
                "Busca en el espacio de trabajo de la corredora: grupos, clientes, "
                "bienes, cotizaciones y propuestas. Entrega hasta 5 resultados por "
                "tipo con su id y etiqueta."
            ),
            args_schema=SearchEntitiesArgs,
            execute=_exec_search_entities,
        ),
        ToolSpec(
            name="get_group_tree",
            kind="read",
            module="Groups",
            action="View",
            description=(
                "El árbol completo de un grupo: clientes con sus RUT, vigencias, "
                "ramos y los expedientes de cada uno con su etapa."
            ),
            args_schema=GetGroupTreeArgs,
            execute=_exec_get_group_tree,
            resolve_gates=lambda args: [("Groups", "View"), ("CaseFiles", "View")],
        ),
        ToolSpec(
            name="get_case_file",
            kind="read",
            module="CaseFiles",
            action="View",
            description=(
                "El detalle de un expediente: referencia, etapa, vigencia, cliente, "
                "documentos por sección, hijos de post-venta y las transiciones "
                "permitidas con el motivo cuando una no lo está."
            ),
            args_schema=GetCaseFileArgs,
            execute=_exec_get_case_file,
        ),
        ToolSpec(
            name="get_case_history",
            kind="read",
            module="CaseFiles",
            action="View",
            description=(
                "La cadena de origen de un expediente y el contexto de la vigencia "
                "anterior: qué se aseguró, qué pólizas salieron y qué costó en "
                "siniestros. Úsalo para comparar contra la vigencia anterior."
            ),
            args_schema=GetCaseHistoryArgs,
            execute=_exec_get_case_history,
        ),
        ToolSpec(
            name="get_quote_comparison",
            kind="read",
            module="Proposals",
            action="View",
            description=(
                "El comparador de una solicitud de cotización: prima por propuesta "
                "(afecta, exenta, neta, IVA, total en UF), tasas, deducibles "
                "alineados por peligro y coberturas/exclusiones alineadas."
            ),
            args_schema=GetQuoteComparisonArgs,
            execute=_exec_get_quote_comparison,
        ),
        ToolSpec(
            name="list_documents",
            kind="read",
            module="Documents",
            action="View",
            description=(
                "Lista los documentos de un expediente u otra entidad: categoría, "
                "sección, código y nombre. Solo metadatos; nunca el archivo."
            ),
            args_schema=ListDocumentsArgs,
            execute=_exec_list_documents,
        ),
        # --- WRITE (propose-only in the loop; execute at confirm) -------------
        ToolSpec(
            name="create_group",
            kind="write",
            module="Groups",
            action="Create",
            description=(
                "PROPONE crear un grupo de cuentas (y opcionalmente adjuntar "
                "clientes existentes por id). No escribe nada: la persona debe "
                "confirmar la acción en el chat."
            ),
            args_schema=CreateGroupArgs,
            execute=_exec_create_group,
            summarize=_summarize_create_group,
            validate_proposal=_validate_create_group,
            resolve_gates=_gates_create_group,
            anchor=lambda arguments: (EntityType.ACCOUNT_GROUP, None),
        ),
        ToolSpec(
            name="attach_client_to_group",
            kind="write",
            module="Groups",
            action="Edit",
            description=(
                "PROPONE adjuntar un cliente existente a un grupo. No escribe "
                "nada: la persona debe confirmar la acción en el chat."
            ),
            args_schema=AttachClientToGroupArgs,
            execute=_exec_attach_client,
            summarize=_summarize_attach_client,
            validate_proposal=_validate_attach_client,
            anchor=lambda arguments: (
                EntityType.ACCOUNT_GROUP, arguments.get("group_id")
            ),
        ),
        ToolSpec(
            name="create_case_file",
            kind="write",
            module="CaseFiles",
            action="Create",
            description=(
                "PROPONE abrir un expediente de cuenta (kind=account) para un "
                "cliente, con su vigencia (period_start y period_end son "
                "obligatorios). No escribe nada: la persona debe confirmar."
            ),
            args_schema=CreateCaseFileArgs,
            execute=_exec_create_case_file,
            summarize=_summarize_create_case_file,
            validate_proposal=_validate_create_case_file,
            anchor=lambda arguments: (EntityType.CASE_FILE, None),
        ),
        ToolSpec(
            name="renew_case",
            kind="write",
            module="CaseFiles",
            action="Create",
            description=(
                "PROPONE abrir la renovación de un expediente para la próxima "
                "vigencia (period_start y period_end). No escribe nada: la "
                "persona debe confirmar."
            ),
            args_schema=RenewCaseArgs,
            execute=_exec_renew_case,
            summarize=_summarize_renew_case,
            validate_proposal=_validate_renew_case,
            anchor=lambda arguments: (
                EntityType.CASE_FILE, arguments.get("case_file_id")
            ),
        ),
        ToolSpec(
            name="add_note",
            kind="write",
            module="CaseFiles",  # display default; the gate resolves per entity_type
            action="Comment",
            description=(
                "PROPONE agregar una nota a una entidad (case_file, client, "
                "policy, proposal, sales_lead…), opcionalmente con fecha de "
                "seguimiento. No escribe nada: la persona debe confirmar."
            ),
            args_schema=AddNoteArgs,
            execute=_exec_add_note,
            summarize=_summarize_add_note,
            validate_proposal=_validate_add_note,
            resolve_gates=_gates_add_note,
            anchor=lambda arguments: (
                _note_entity_type(str(arguments.get("entity_type"))),
                arguments.get("entity_id"),
            ),
        ),
    )
}

READ_TOOLS = tuple(s.name for s in TOOL_REGISTRY.values() if s.kind == "read")
WRITE_TOOLS = tuple(s.name for s in TOOL_REGISTRY.values() if s.kind == "write")


def _validate_registry() -> None:
    """Import-time typo guard, like ``require_permission``'s ValueError."""
    for spec in TOOL_REGISTRY.values():
        if spec.module not in MODULES:
            raise ValueError(f"Tool {spec.name!r} names unknown module {spec.module!r}")
        if spec.action not in ACTIONS:
            raise ValueError(f"Tool {spec.name!r} names unknown action {spec.action!r}")
        if spec.kind == "write" and spec.summarize is None:
            raise ValueError(f"Write tool {spec.name!r} must define summarize()")


_validate_registry()


def openai_tools() -> list[dict]:
    """The registry as OpenAI-compatible ``tools`` dicts.

    Parameters are the args schema's JSON Schema run through the existing
    ``_compact_schema`` pruning in :mod:`app.services.ai` (title/default noise
    stripped), so the payload stays a model-friendly size.
    """
    from app.services.ai import _compact_schema

    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": _compact_schema(spec.args_schema),
            },
        }
        for spec in TOOL_REGISTRY.values()
    ]


def action_anchor(tool: str, arguments: dict) -> tuple[EntityType, int | None]:
    """Which entity the action is about — anchors activity rows (discard incl.)."""
    spec = TOOL_REGISTRY.get(tool)
    if spec is not None and spec.anchor is not None:
        try:
            return spec.anchor(dict(arguments or {}))
        except Exception:  # noqa: BLE001 - a broken anchor must not block resolution
            pass
    return (EntityType.CASE_FILE, None)


def action_allowed(action: Any, user: User) -> bool:
    """Whether ``user`` currently holds every gate this action needs.

    Computed per response for the caller so the card can render Confirmar
    disabled-with-reason; the confirm endpoint re-checks regardless.
    """
    spec = TOOL_REGISTRY.get(action.tool)
    if spec is None:
        return False
    try:
        args = spec.args_schema.model_validate(action.arguments or {})
        gates = gates_for(spec, args)
    except Exception:  # noqa: BLE001 - unresolvable arguments -> not confirmable
        return False
    return all(user_has_permission(user, module, act) for module, act in gates)
