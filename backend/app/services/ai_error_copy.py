"""User-facing codes + Spanish copy for a typed AI failure (docs/v12/11-fast-reading.md §7).

Errors are honest: a provider TIMEOUT is not "El proveedor de IA no está disponible". The
stable machine vocabulary here is the one the fast-reading contract publishes
(``ai_timeout`` · ``ai_unavailable`` · ``ai_parse`` · ``document_unavailable``); the finer
``X-Radal-AI-Error`` header code (``ai_provider`` / ``ai_not_configured`` / …) is unchanged.
"""
from __future__ import annotations

from typing import Any

AI_TIMEOUT_ES = "La lectura tardó más de lo esperado. Reintenta; si persiste, avísanos."
AI_PROVIDER_DOWN_ES = (
    "El proveedor de IA no está disponible en este momento. Reintenta en unos momentos."
)
AI_PARSE_ES = "La IA respondió algo que no se pudo leer. Reintenta; si persiste, avísanos."
DOCUMENT_UNAVAILABLE_ES = (
    "No se pudo leer el texto del archivo. Sube un PDF o Word con texto (no escaneado)."
)


def ai_user_error(exc: BaseException) -> dict[str, Any]:
    """``{"code", "message"}`` for the response ``detail`` of an AI-backed endpoint."""
    from app.services import ai

    if isinstance(exc, ai.AITimeout):
        return {"code": "ai_timeout", "message": AI_TIMEOUT_ES}
    if isinstance(exc, ai.AINotConfigured):
        return {"code": "ai_unavailable", "message": ai.AI_UNAVAILABLE_ES}
    if isinstance(exc, ai.AIParseError):
        return {"code": "ai_parse", "message": AI_PARSE_ES}
    if isinstance(exc, ai.DocumentUnavailable):
        return {"code": "document_unavailable", "message": DOCUMENT_UNAVAILABLE_ES}
    if isinstance(exc, ai.AIProviderError):
        return {"code": "ai_unavailable", "message": AI_PROVIDER_DOWN_ES}
    return {"code": ai.ai_error_code(exc), "message": AI_PROVIDER_DOWN_ES}


__all__ = [
    "AI_PARSE_ES",
    "AI_PROVIDER_DOWN_ES",
    "AI_TIMEOUT_ES",
    "DOCUMENT_UNAVAILABLE_ES",
    "ai_user_error",
]
