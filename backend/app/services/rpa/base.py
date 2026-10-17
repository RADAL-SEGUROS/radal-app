"""The connector contract of the portal-search RPA (06-rpa §1).

A **connector** knows one insurer portal: its key, its version, which catalogue
subtypes it quotes, how to validate the search form, and how to drive the
portal once a Playwright ``page`` exists. The **engine** owns everything around
that: claiming the run, the browser, timeouts, the step log, the failure
screenshot, the taxonomy mapping, the quotes, the notifications.

Everything a connector writes to the step log goes through
:meth:`RunContext.log_step`, which sanitises it: no credential value, no HTML,
one short line. That log is shown to the broker.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Protocol, runtime_checkable

#: Max length of one step ``detail`` (it is UI copy, not a log dump).
STEP_DETAIL_MAX = 240
#: Max length of the internal ``error_detail``.
ERROR_DETAIL_MAX = 2000

_TAG_RE = re.compile(r"<[^>]{0,2000}>")
_WS_RE = re.compile(r"\s+")


def scrub(text: Any, secrets: "list[str] | tuple[str, ...]" = (), *, limit: int = STEP_DETAIL_MAX) -> str:
    """One safe line: tags stripped, secrets masked, whitespace folded, truncated.

    Playwright messages carry multi-line call logs (and, for a failed ``fill``,
    potentially the value); only the first line survives, and every secret is
    replaced by ``***`` wherever it appears.
    """
    if text is None:
        return ""
    value = str(text)
    value = value.split("\n", 1)[0] if limit <= STEP_DETAIL_MAX else value
    value = _TAG_RE.sub(" ", value)
    for secret in secrets:
        if secret and len(secret) >= 2:
            value = value.replace(secret, "***")
    value = _WS_RE.sub(" ", value).strip()
    if len(value) > limit:
        value = value[: limit - 1] + "…"
    return value


@dataclass
class QuoteOut:
    """One plan the portal returned (becomes a ``portal_quote`` row)."""

    plan_name: str | None
    premium_uf: Decimal | None = None
    premium_clp: int | None = None
    deductible: str | None = None
    coverages: list[str] | None = None
    raw: dict[str, Any] | None = None


@dataclass
class PortalResult:
    quotes: list[QuoteOut] = field(default_factory=list)


@dataclass
class RunContext:
    """What a connector receives. ``page`` is a Playwright sync ``Page``."""

    page: Any
    input: dict[str, Any]
    credentials: dict[str, str]
    base_url: str = ""
    #: per-step timeout overrides by step name (ms) — tests and ops tuning.
    step_timeouts: dict[str, int] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    screenshots: list[tuple[str, bytes]] = field(default_factory=list)
    _t0: float = field(default_factory=time.monotonic)

    @property
    def secrets(self) -> list[str]:
        return [v for v in (self.credentials or {}).values() if isinstance(v, str) and v]

    def log_step(self, name: str, ok: bool, detail: str | None = None, *, op: str | None = None) -> dict[str, Any]:
        """Append one sanitised entry to the step log and return it."""
        now = time.monotonic()
        entry = {
            "name": scrub(name, self.secrets, limit=64) or "step",
            "op": scrub(op, self.secrets, limit=32) if op else None,
            "ok": bool(ok),
            "detail": scrub(detail, self.secrets) if detail else None,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "elapsed_ms": int((now - self._t0) * 1000),
        }
        self.steps.append(entry)
        return entry

    def screenshot(self, name: str) -> bytes | None:
        """Capture a PNG of the current page (kept in memory; never raises)."""
        try:
            data = self.page.screenshot(full_page=True, timeout=5000)
        except Exception:  # noqa: BLE001 - evidence is best-effort
            return None
        self.screenshots.append((name, data))
        return data


@runtime_checkable
class PortalConnector(Protocol):
    """One insurer portal. Implementations are stateless singletons."""

    key: str
    name: str
    version: str
    supported_subtypes: tuple[str, ...]
    requires_credentials: bool

    @property
    def base_url(self) -> str: ...

    def validate_input(self, data: dict[str, Any]) -> dict[str, Any]:
        """Return the normalised input or raise ``InputInvalid``."""
        ...

    def input_fields(self) -> list[dict[str, Any]]:
        """The search form the frontend renders (names, types, options)."""
        ...

    def insurer_identity(self) -> dict[str, str]:
        """``{"rut": ..., "cmf_code": ...}`` used to link quotes to an insurer."""
        ...

    def run(self, ctx: RunContext) -> PortalResult: ...


class InputInvalid(ValueError):
    """The search form failed validation. ``errors`` = ``[{field, message}]`` (Spanish)."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__("; ".join(f"{e['field']}: {e['message']}" for e in errors))
        self.errors = errors


#: ``() -> ContextManager[Page]`` — how the engine obtains a browser page.
BrowserFactory = Callable[[], Any]


__all__ = [
    "BrowserFactory",
    "ERROR_DETAIL_MAX",
    "InputInvalid",
    "PortalConnector",
    "PortalResult",
    "QuoteOut",
    "RunContext",
    "STEP_DETAIL_MAX",
    "scrub",
]
