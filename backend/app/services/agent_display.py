"""What the agent's confirm card shows (review r2, B6) — server-built, Spanish.

A proposed write (``agent_action``) carries its raw ``arguments`` (what the
executor will run). The broker must never read those: the review found
"Monto 118.13 · Medio transfer · Fecha 2026-10-19 · Cuota 68" (68 = the internal
id of cuota 1). :func:`action_display` turns the arguments into
``[{label, value}]`` rows:

* Spanish labels; UF / CLP formatted the Chilean way (``UF 118,13``,
  ``$ 45.300``); dates ``dd-mm-aaaa``; booleans as words;
* every id resolved to what the broker knows — the cuota NUMBER and the policy
  number, the insured's name, the account's reference — broker-scoped; an id that
  does not resolve is OMITTED, never printed;
* enums through explicit maps; an unknown key is hidden.

Computed at read time from the action's own session (no column, no migration).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from sqlalchemy.orm import Session, object_session

__all__ = ["action_display", "action_display_for", "payment_result_line"]

Row = dict[str, str]

PAYMENT_MODE_ES = {
    "coupon_book": "Cuponera / aviso de vencimiento",
    "direct_debit": "PAC (cargo en cuenta)",
    "card_debit": "PAT (cargo en tarjeta)",
    "transfer": "Transferencia",
    "single_charge": "Pago contado",
    "other": "Otro medio",
}
EVIDENCE_ES = {
    "declared": "pago declarado",
    "reported": "informado por el pagador",
    "insurer_confirmed": "confirmado por la compañía",
    "provider_confirmed": "confirmado por el medio de pago",
    "settled": "liquidado",
}
STATE_ES = {
    "paid": "cuota pagada",
    "voided": "cuota anulada",
    "pending_review": "cuota por revisar",
    "promised": "cuota en acuerdo de pago",
    "termination_risk": "cuota en riesgo de término",
    "overdue": "cuota vencida",
    "due_today": "cuota vence hoy",
    "upcoming": "cuota por vencer",
}
LEVEL_ES = {"auto": "Automático (el mejor disponible)", "assisted": "Asistida", "record": "Registro"}
NOTICE_KIND_ES = {
    "payer_reminder": "Recordatorio de pago",
    "payer_overdue": "Aviso de cuota vencida",
}
AUDIENCE_ES = {"insured": "El asegurado", "insurer": "La compañía"}
ENTITY_ES = {
    "policy": "Póliza",
    "collection_plan": "Plan de pago",
    "installment": "Cuota",
    "case_file": "Cuenta",
    "client": "Empresa",
    "account_group": "Asegurado",
}


# --- formatting ------------------------------------------------------------------


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def money(value: Any, currency: str = "UF") -> str:
    """``UF 1.181,28`` · ``$ 45.300`` · ``USD 1.234,00`` (Chilean style)."""
    from app.services.notices import format_clp, format_uf  # noqa: PLC0415

    amount = _dec(value)
    if amount is None:
        return "—"
    unit = (currency or "UF").upper()
    if unit == "CLP":
        return f"$ {format_clp(int(amount.to_integral_value()))}"
    return f"{'USD' if unit == 'USD' else 'UF'} {format_uf(amount)}"


def pct(value: Any) -> str:
    amount = _dec(value)
    if amount is None:
        return "—"
    text = f"{amount.normalize():f}".replace(".", ",")
    return f"{text} %"


def day(value: Any) -> str:
    if isinstance(value, date):
        return value.strftime("%d-%m-%Y")
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10]).strftime("%d-%m-%Y")
        except ValueError:
            return value
    return "—"


def _text(value: Any, limit: int = 200) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- resolvers (broker-scoped; None when the id does not resolve) ----------------


def _policy_number(db: Session, broker_id: int, policy_id: Any) -> str | None:
    from app.models.policy import Policy  # noqa: PLC0415
    from app.services import policy_registry as registry  # noqa: PLC0415

    policy = db.get(Policy, policy_id) if policy_id else None
    if policy is None or policy.broker_id != broker_id:
        return None
    if registry.is_placeholder_number(policy.policy_number):
        return "póliza sin n°"
    return f"N° {policy.policy_number}"


def _installment(db: Session, broker_id: int, installment_id: Any):
    from app.models.collection import CollectionInstallment  # noqa: PLC0415

    inst = db.get(CollectionInstallment, installment_id) if installment_id else None
    return inst if inst is not None and inst.broker_id == broker_id else None


def _cuota(db: Session, broker_id: int, installment_id: Any) -> str | None:
    inst = _installment(db, broker_id, installment_id)
    if inst is None:
        return None
    plan = inst.collection_plan
    count = (plan.installment_count or len(plan.installments or [])) if plan else None
    text = f"{inst.number} de {count}" if count else f"{inst.number}"
    number = _policy_number(db, broker_id, plan.policy_id) if plan else None
    return f"{text} · póliza {number}" if number else text


def _cuotas(db: Session, broker_id: int, ids: Any) -> str | None:
    insts = [i for i in (_installment(db, broker_id, x) for x in (ids or [])) if i is not None]
    if not insts:
        return None
    numbers = sorted({int(i.number) for i in insts})
    listed = ", ".join(str(n) for n in numbers[:-1])
    text = f"{listed} y {numbers[-1]}" if listed else str(numbers[-1])
    policy = _policy_number(db, broker_id, insts[0].collection_plan.policy_id)
    return f"{text} · póliza {policy}" if policy else text


def _plan(db: Session, broker_id: int, plan_id: Any) -> str | None:
    from app.models.collection import CollectionPlan  # noqa: PLC0415

    plan = db.get(CollectionPlan, plan_id) if plan_id else None
    if plan is None or plan.broker_id != broker_id:
        return None
    number = _policy_number(db, broker_id, plan.policy_id)
    return f"Póliza {number}" if number else None


def _client(db: Session, broker_id: int, client_id: Any) -> str | None:
    from app.models.client import Client  # noqa: PLC0415
    from app.models.insured import Insured  # noqa: PLC0415
    from app.services.identifiers import display_rut  # noqa: PLC0415

    client = db.get(Client, client_id) if client_id else None
    if client is None or client.broker_id != broker_id:
        return None
    insured = db.get(Insured, client.insured_id)
    if insured is None:
        return None
    rut = display_rut(insured.rut) if insured.rut else None
    return f"{insured.legal_name} ({rut})" if rut else insured.legal_name


def _clients(db: Session, broker_id: int, ids: Any) -> str | None:
    names = [n for n in (_client(db, broker_id, x) for x in (ids or [])) if n]
    return ", ".join(names) if names else None


def _group(db: Session, broker_id: int, group_id: Any) -> str | None:
    from app.models.account_group import AccountGroup  # noqa: PLC0415

    group = db.get(AccountGroup, group_id) if group_id else None
    return group.name if group is not None and group.broker_id == broker_id else None


def _case(db: Session, broker_id: int, case_id: Any) -> str | None:
    from app.models.case_file import CaseFile  # noqa: PLC0415

    case = db.get(CaseFile, case_id) if case_id else None
    if case is None or case.broker_id != broker_id:
        return None
    if case.reference and case.title:
        return f"{case.reference} · {case.title}"
    return case.reference or case.title


def _insurer(db: Session, _broker_id: int, insurer_id: Any) -> str | None:
    from app.models.insurer import Insurer  # noqa: PLC0415

    insurer = db.get(Insurer, insurer_id) if insurer_id else None
    return (insurer.trade_name or insurer.legal_name) if insurer is not None else None


def _subtype(db: Session, _broker_id: int, subtype_id: Any) -> str | None:
    from app.models.catalog import InsuranceSubtype  # noqa: PLC0415

    subtype = db.get(InsuranceSubtype, subtype_id) if subtype_id else None
    return subtype.name if subtype is not None else None


def _document(db: Session, broker_id: int, document_id: Any) -> str | None:
    from app.models.document import Document  # noqa: PLC0415

    doc = db.get(Document, document_id) if document_id else None
    return doc.original_name if doc is not None and doc.broker_id == broker_id else None


def _entity(db: Session, broker_id: int, entity_type: Any, entity_id: Any) -> Row | None:
    kind = str(entity_type or "")
    resolver: dict[str, Callable[..., str | None]] = {
        "policy": lambda: _policy_number(db, broker_id, entity_id),
        "collection_plan": lambda: _plan(db, broker_id, entity_id),
        "installment": lambda: _cuota(db, broker_id, entity_id),
        "case_file": lambda: _case(db, broker_id, entity_id),
        "client": lambda: _client(db, broker_id, entity_id),
        "account_group": lambda: _group(db, broker_id, entity_id),
    }
    if kind not in resolver:
        return None
    value = resolver[kind]()
    return {"label": ENTITY_ES[kind], "value": value} if value else None


# --- the per-key table ------------------------------------------------------------

_ID_KEYS: dict[str, tuple[str, Callable[[Session, int, Any], str | None]]] = {
    "installment_id": ("Cuota", _cuota),
    "installment_ids": ("Cuotas", _cuotas),
    "policy_id": ("Póliza", _policy_number),
    "plan_id": ("Plan de pago", _plan),
    "client_id": ("Empresa", _client),
    "client_ids": ("Empresas", _clients),
    "group_id": ("Asegurado", _group),
    "case_file_id": ("Cuenta", _case),
    "insurer_id": ("Compañía", _insurer),
    "insurance_subtype_id": ("Ramo", _subtype),
    "receipt_document_id": ("Comprobante", _document),
    "document_id": ("Documento", _document),
}
_UF_KEYS = {
    "insured_amount_uf": "Monto asegurado",
    "taxable_premium_uf": "Prima afecta",
    "exempt_premium_uf": "Prima exenta",
    "net_premium_uf": "Prima neta",
    "vat_uf": "IVA",
    "total_premium_uf": "Prima total",
}
_DATE_KEYS = {
    "paid_on": "Fecha de pago",
    "promise_until": "Pagará el",
    "start_date": "Inicio de vigencia",
    "end_date": "Fin de vigencia",
    "period_start": "Vigencia desde",
    "period_end": "Vigencia hasta",
    "issued_at": "Fecha de emisión",
    "follow_up_on": "Seguimiento el",
}
_TEXT_KEYS = {
    "name": "Nombre",
    "title": "Título",
    "policy_number": "N° de póliza",
    "insured_name": "Asegurado",
    "period_label": "Vigencia",
    "to_address": "Para",
    "label": "Etiqueta",
    "note": "Nota",
    "notes": "Notas",
    "body": "Nota",
    "message": "Mensaje",
}
_SKIP = {"kind", "max_hits", "entity_type", "entity_id", "input", "currency"}


def _rows_for(db: Session, broker_id: int, tool: str, args: dict[str, Any]) -> list[Row]:
    from app.services.identifiers import display_rut  # noqa: PLC0415
    from app.services.stage_labels import stage_label  # noqa: PLC0415

    rows: list[Row] = []

    def add(label: str, value: str | None) -> None:
        if value:
            rows.append({"label": label, "value": value})

    if "amount" in args:
        add("Monto", money(args.get("amount"), str(args.get("currency") or "UF")))
    for key, value in args.items():
        if value is None or value == [] or value == "" or key in _SKIP or key == "amount":
            continue
        if key in _ID_KEYS:
            label, resolve = _ID_KEYS[key]
            add(label, resolve(db, broker_id, value))
        elif key in _UF_KEYS:
            unit = "USD" if str(args.get("currency") or "UF").upper() == "USD" else "UF"
            add(_UF_KEYS[key], money(value, unit))
        elif key in _DATE_KEYS:
            add(_DATE_KEYS[key], day(value))
        elif key in _TEXT_KEYS:
            add(_TEXT_KEYS[key], _text(value))
        elif key in ("method", "payment_method"):
            add("Medio de pago", PAYMENT_MODE_ES.get(str(value), "Otro medio"))
        elif key == "commission_pct":
            add("Comisión", pct(value))
        elif key == "confirmed_by_insurer":
            pass  # rendered below for register_payment only
        elif key == "level_preference":
            add("Nivel de cobranza", LEVEL_ES.get(str(value)))
        elif key == "audience":
            add("Destinatario", AUDIENCE_ES.get(str(value)))
        elif key == "expires_in_days":
            add("Vigente por", f"{int(value)} días")
        elif key in ("target_stage", "stage"):
            label = stage_label(value)
            add("Etapa", label if label != "—" else None)
        elif key == "insured_rut":
            add("RUT del asegurado", display_rut(str(value)))
        elif key == "is_provisional":
            add("Certificado provisorio", "Sí" if value else None)
        elif key == "is_internal":
            add("Visibilidad", "Nota interna" if value else "Visible para el equipo y el asegurado")
        elif key in ("policyholder", "payer") and isinstance(value, dict):
            name = _text(value.get("name"))
            rut = display_rut(str(value["rut"])) if value.get("rut") else None
            add("Contratante" if key == "policyholder" else "Pagador", f"{name} ({rut})" if rut else name)
        elif key == "portal":
            add("Portal", str(value).upper())
        # any other key: hidden (never a raw value on the card)

    if tool == "register_payment":
        add(
            "Evidencia",
            "Confirmado por la compañía"
            if args.get("confirmed_by_insurer")
            else "Pago declarado (sin confirmación de la compañía)",
        )
    if tool == "send_payment_reminder":
        add("Tipo de aviso", NOTICE_KIND_ES.get(str(args.get("kind") or "payer_reminder")))
    if tool in ("add_note", "create_share_link"):
        entity = _entity(db, broker_id, args.get("entity_type"), args.get("entity_id"))
        if entity is not None:
            rows.insert(0, entity)
    if tool == "run_portal_search" and isinstance(args.get("input"), dict):
        plate = args["input"].get("plate") or args["input"].get("patente")
        add("Patente", _text(plate))
    return rows


def action_display(
    db: Session, *, broker_id: int, tool: str, arguments: dict[str, Any] | None
) -> list[Row]:
    """The confirm card rows of one proposed write. Never raises."""
    try:
        return _rows_for(db, broker_id, tool, dict(arguments or {}))
    except Exception:  # noqa: BLE001 - a display bug must not hide the action
        return []


def action_display_for(action: Any) -> list[Row]:
    """:func:`action_display` from the row's own session (empty when detached)."""
    db = object_session(action)
    if db is None:
        return []
    return action_display(
        db, broker_id=action.broker_id, tool=action.tool, arguments=action.arguments
    )


def payment_result_line(evidence: str | None, state: str | None) -> str:
    """``"Pago registrado: pago declarado, cuota pagada"`` (the executor's result)."""
    evidence_es = EVIDENCE_ES.get(str(evidence or ""), "pago registrado")
    state_es = STATE_ES.get(str(state or ""), "cuota actualizada")
    return f"Pago registrado: {evidence_es}, {state_es}"
