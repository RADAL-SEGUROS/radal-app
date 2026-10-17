"""Classify + read one KEPT inbound mail (v12, 04-mail §4.3).

Only mail that passed the privacy prefilter reaches this module. One forced
tool call (``classify_inbound_mail``, task ``CLASSIFY_MAIL`` -> small tier via
:func:`app.services.ai_models.model_for`) both classifies the mail and reads
``{reported_status, installment_numbers[], amount, currency, paid_on,
policy_number, comment}``.

Rule 6: this only SUGGESTS. Every attempt persists an ``extraction`` row
(kind ``other``, category ``email_message``) against the stored ``.eml``
document — succeeded with the model's read, or failed with the error. When the
provider is unavailable (unconfigured, down, unparseable) a conservative
keyword heuristic stands in so the mail is still routed to a human; its
evidence lands as ``needs_review`` because the score stays low.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ai import Extraction, ExtractionKind, ExtractionStatus
from app.models.document import DocumentCategory
from app.models.enums import InboundClass, ReportedStatus

PROMPT_VERSION = "inbound-mail-v1"
TOOL_NAME = "classify_inbound_mail"
MAX_BODY_CHARS = 8_000

_CLASSES = (
    InboundClass.INSURER_STATUS_REPLY.value,
    InboundClass.INSURER_STATEMENT.value,
    InboundClass.CANCELLATION_NOTICE.value,
    InboundClass.PAYER_REPLY.value,
    InboundClass.UNRELATED.value,
)
_STATUSES = tuple(s.value for s in ReportedStatus)
_CURRENCIES = ("UF", "CLP", "USD")

TOOL_SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Clasifica un correo recibido por una corredora de seguros chilena y extrae el "
        "estado de pago de las cuotas que menciona."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "classification": {"type": "string", "enum": list(_CLASSES)},
            "confidence": {"type": "number", "description": "0 a 100"},
            "reported_status": {"type": "string", "enum": list(_STATUSES)},
            "installment_numbers": {"type": "array", "items": {"type": "integer"}},
            "amount": {"type": ["number", "null"]},
            "currency": {"type": ["string", "null"], "enum": [*_CURRENCIES, None]},
            "paid_on": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
            "policy_number": {"type": ["string", "null"]},
            "comment": {"type": ["string", "null"]},
        },
        "required": ["classification", "reported_status"],
    },
}

_SYSTEM = """Eres el asistente de cobranza de una corredora de seguros en Chile.
Recibes UN correo que llegó a la casilla de la corredora o de Radal. Clasifícalo:
- insurer_status_reply: la compañía responde una consulta de estado de pago (suele traer [RADAL-INQ-…]).
- insurer_statement: la compañía informa pagos, cuotas pendientes o un estado de cuenta sin que se le haya preguntado.
- cancellation_notice: la compañía avisa término, anulación o resciliación de la póliza por no pago.
- payer_reply: responde el asegurado o pagador.
- unrelated: cualquier otra cosa.
Luego indica el estado de pago informado (paid, unpaid, partial, cancelled o unknown), los números de cuota
mencionados, el monto, la moneda (UF, CLP o USD), la fecha de pago (YYYY-MM-DD) y el número de póliza si aparece.
No inventes datos: si algo no está en el correo, déjalo en null. Responde SOLO llamando a la herramienta."""


@dataclass
class MailReading:
    classification: str
    reported_status: str
    installment_numbers: list[int] = field(default_factory=list)
    amount: Decimal | None = None
    currency: str | None = None
    paid_on: date | None = None
    policy_number: str | None = None
    comment: str | None = None
    confidence: Decimal = Decimal("0")
    used_ai: bool = False
    extraction: Extraction | None = None


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in folded if not unicodedata.combining(ch)).lower()


_CUOTA_RE = re.compile(r"cuotas?\s*(?:n(?:ro|um|°|º|o)?\.?\s*)?((?:\d{1,3}\s*(?:,|y|-|/)?\s*)+)")


def _numbers(text: str) -> list[int]:
    found: list[int] = []
    for match in _CUOTA_RE.finditer(_plain(text)):
        for part in re.findall(r"\d{1,3}", match.group(1)):
            number = int(part)
            if 0 < number < 400 and number not in found:
                found.append(number)
    return found


def heuristic_reading(*, subject: str, body: str, has_inquiry: bool) -> MailReading:
    """Keyword fallback when the AI is unavailable (conservative by design)."""
    text = _plain(f"{subject}\n{body}")
    status = ReportedStatus.UNKNOWN.value
    if re.search(r"termino de (la )?poliza|anulacion de (la )?poliza|resciliaci|cancelacion de (la )?poliza", text):
        status = ReportedStatus.CANCELLED.value
    elif re.search(r"no (registra|presenta|figura)\w* pago|impag|no (esta|se encuentra) pagad|pendiente de pago|no pagad", text):
        status = ReportedStatus.UNPAID.value
    elif re.search(r"pago parcial|parcialmente", text):
        status = ReportedStatus.PARTIAL.value
    elif re.search(r"pagad|registra(n)? (el )?pago|recaudad|abonad|cancelada en su totalidad", text):
        status = ReportedStatus.PAID.value
    if status == ReportedStatus.CANCELLED.value:
        classification = InboundClass.CANCELLATION_NOTICE.value
    elif has_inquiry:
        classification = InboundClass.INSURER_STATUS_REPLY.value
    elif status != ReportedStatus.UNKNOWN.value:
        classification = InboundClass.INSURER_STATEMENT.value
    else:
        classification = InboundClass.UNRELATED.value
    return MailReading(
        classification=classification,
        reported_status=status,
        installment_numbers=_numbers(f"{subject}\n{body}"),
        confidence=Decimal("30") if status != ReportedStatus.UNKNOWN.value else Decimal("10"),
    )


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() and result > 0 else None


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _normalise(parsed: dict[str, Any]) -> MailReading:
    classification = str(parsed.get("classification") or "").strip().lower()
    if classification not in _CLASSES:
        classification = InboundClass.UNRELATED.value
    status = str(parsed.get("reported_status") or "").strip().lower()
    if status not in _STATUSES:
        status = ReportedStatus.UNKNOWN.value
    numbers: list[int] = []
    for item in parsed.get("installment_numbers") or []:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if 0 < number < 400 and number not in numbers:
            numbers.append(number)
    currency = str(parsed.get("currency") or "").strip().upper() or None
    if currency not in _CURRENCIES:
        currency = None
    confidence = _to_decimal(parsed.get("confidence")) or Decimal("50")
    confidence = min(Decimal("100"), confidence)
    return MailReading(
        classification=classification,
        reported_status=status,
        installment_numbers=numbers,
        amount=_to_decimal(parsed.get("amount")),
        currency=currency,
        paid_on=_to_date(parsed.get("paid_on")),
        policy_number=(str(parsed.get("policy_number")).strip()[:64] or None)
        if parsed.get("policy_number")
        else None,
        comment=(str(parsed.get("comment"))[:1000] if parsed.get("comment") else None),
        confidence=confidence,
        used_ai=True,
    )


def _messages(*, subject: str, sender: str, body: str, context: str) -> list[dict[str, str]]:
    user = (
        f"Contexto: {context or 'sin consulta asociada'}\n"
        f"De: {sender}\nAsunto: {subject}\n\n"
        f"Cuerpo:\n{(body or '')[:MAX_BODY_CHARS]}"
    )
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def classify_mail(
    db: Session,
    *,
    broker_id: int,
    document_id: int,
    subject: str,
    sender: str,
    body: str,
    context: str = "",
    has_inquiry: bool = False,
) -> MailReading:
    """Classify + read; ALWAYS persists an ``extraction`` row. Never raises."""
    from app.services import ai as ai_service  # noqa: PLC0415 - heavy module, lazy
    from app.services.ai_models import AITask, model_for  # noqa: PLC0415

    model = model_for(AITask.CLASSIFY_MAIL)
    extraction = Extraction(
        broker_id=broker_id,
        document_id=document_id,
        kind=ExtractionKind.OTHER,
        category=DocumentCategory.EMAIL_MESSAGE,
        model=model,
        prompt_version=PROMPT_VERSION,
        status=ExtractionStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(extraction)
    db.flush()
    try:
        result = ai_service.tool_call(
            messages=_messages(subject=subject, sender=sender, body=body, context=context),
            tool_schema=TOOL_SCHEMA,
            model=model,
            timeout=min(int(settings.AI_TIMEOUT_SECONDS), 60),
            max_retries=1,
        )
        if not isinstance(result.parsed, dict):
            raise ai_service.AIParseError("The tool call returned no object")
        reading = _normalise(result.parsed)
        extraction.status = ExtractionStatus.SUCCEEDED
        extraction.model = result.model or model
        extraction.raw_output = result.raw
        extraction.parsed = _jsonable(reading)
        extraction.confidence = reading.confidence
        usage = result.usage or {}
        extraction.prompt_tokens = usage.get("prompt_tokens")
        extraction.completion_tokens = usage.get("completion_tokens")
    except Exception as exc:  # noqa: BLE001 - an AI failure never stops the poll
        reading = heuristic_reading(subject=subject, body=body, has_inquiry=has_inquiry)
        extraction.status = ExtractionStatus.FAILED
        extraction.error = f"{type(exc).__name__}: {exc}"[:2000]
        extraction.parsed = {**_jsonable(reading), "heuristic": True}
        extraction.confidence = reading.confidence
    extraction.finished_at = datetime.now(timezone.utc)
    db.flush()
    reading.extraction = extraction
    return reading


def _jsonable(reading: MailReading) -> dict[str, Any]:
    return {
        "classification": reading.classification,
        "reported_status": reading.reported_status,
        "installment_numbers": list(reading.installment_numbers),
        "amount": str(reading.amount) if reading.amount is not None else None,
        "currency": reading.currency,
        "paid_on": reading.paid_on.isoformat() if reading.paid_on else None,
        "policy_number": reading.policy_number,
        "comment": reading.comment,
        "confidence": str(reading.confidence),
    }


__all__ = [
    "MailReading",
    "PROMPT_VERSION",
    "TOOL_NAME",
    "TOOL_SCHEMA",
    "classify_mail",
    "heuristic_reading",
]
