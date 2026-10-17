"""The step interpreter: runs a portal's JSON step map on a Playwright page.

A portal change should usually be a JSON edit (``selectors/<portal>.json``),
not code. The map is a list of steps; each step has a ``name`` (shown in the
step log), an ``op`` and op-specific fields:

``goto``          ``path`` (joined to the connector's base URL). Connection
                  refused / 5xx -> ``portal_unavailable``; navigation timeout ->
                  ``timeout``; 404 -> ``layout_changed`` (the page moved).
``fill``          ``selector``, ``value`` (a template, see below).
``select``        ``selector``, ``value`` — ``<select>`` by option value.
``click``         ``selector``.
``wait_for``      ``selector`` the page must show; optional ``failures``
                  ``{selector: error_kind}`` that end the run with that kind if
                  they appear first (``#login-error`` -> ``login_failed``,
                  ``#no-results`` -> ``no_results``); optional ``on_timeout``
                  (``layout_changed`` default, or ``timeout`` for steps that wait
                  on the portal's own computation).
``extract_table`` ``rows`` (selector of the row elements) and ``columns``
                  ``{field: {selector, type}}`` with type ``text`` | ``uf`` |
                  ``clp`` | ``list``. Zero rows -> ``no_results``.

Every step may carry ``timeout_ms``; the map's ``timeouts.default_ms`` /
``timeouts.navigation_ms`` apply otherwise, and ``RunContext.step_timeouts``
overrides by step name.

Templates: ``{input.<field>}`` and ``{credentials.<field>}`` are substituted;
``value_map`` (``{"use": {"particular": "PARTICULAR"}}``) translates an input
value to the portal's own option value first. The log NEVER contains a value —
only the step name, op and (on failure) the selector.

The map's ``captcha_selectors`` are checked after every navigation and folded
into every ``wait_for`` as ``captcha`` failures.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.enums import PortalErrorKind
from app.services.rpa.base import QuoteOut, RunContext
from app.services.rpa.errors import (
    CaptchaDetected,
    LayoutChanged,
    LoginFailed,
    NoResults,
    PortalError,
    PortalTimeout,
    PortalUnavailable,
)

_TEMPLATE_RE = re.compile(r"\{(input|credentials)\.([a-zA-Z0-9_]+)\}")

_KIND_TO_EXC: dict[str, type[PortalError]] = {
    PortalErrorKind.LOGIN_FAILED.value: LoginFailed,
    PortalErrorKind.LAYOUT_CHANGED.value: LayoutChanged,
    PortalErrorKind.TIMEOUT.value: PortalTimeout,
    PortalErrorKind.CAPTCHA.value: CaptchaDetected,
    PortalErrorKind.NO_RESULTS.value: NoResults,
    PortalErrorKind.PORTAL_UNAVAILABLE.value: PortalUnavailable,
}

#: Spanish, short: these become the step log the broker reads.
_MSG = {
    "not_found": "No se encontró el elemento esperado ({selector}).",
    "timeout": "El portal no respondió a tiempo.",
    "unavailable": "No fue posible conectarse al portal.",
    "http": "El portal respondió con un error (HTTP {status}).",
    "captcha": "El portal pidió verificación humana.",
    "login_failed": "El portal rechazó el usuario o la clave.",
    "failure": "El portal mostró un error inesperado.",
    "rows": "{n} planes leídos.",
    "no_rows": "El portal no entregó planes.",
}


def _playwright_errors() -> tuple[type[BaseException], type[BaseException]]:
    from playwright.sync_api import Error as PwError  # noqa: PLC0415
    from playwright.sync_api import TimeoutError as PwTimeout  # noqa: PLC0415

    return PwTimeout, PwError


def render_template(template: Any, ctx: RunContext, value_map: dict[str, dict[str, str]] | None = None) -> str:
    """Substitute ``{input.x}`` / ``{credentials.x}``. Missing values render empty."""
    text = "" if template is None else str(template)
    value_map = value_map or {}

    def _sub(match: re.Match[str]) -> str:
        source, name = match.group(1), match.group(2)
        bag = ctx.input if source == "input" else ctx.credentials
        raw = (bag or {}).get(name)
        value = "" if raw is None else str(raw)
        if source == "input" and name in value_map:
            value = value_map[name].get(value, value)
        return value

    return _TEMPLATE_RE.sub(_sub, text)


def parse_uf(text: str | None) -> Decimal | None:
    """``"UF 24,50"`` / ``"24.5"`` / ``"1.234,5678"`` -> Decimal (4 dp)."""
    if not text:
        return None
    cleaned = re.sub(r"[^0-9,.\-]", "", text)
    if not cleaned:
        return None
    if "," in cleaned:
        # Chilean format: dots group thousands, the comma is the decimal mark.
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return Decimal(cleaned).quantize(Decimal("0.0001"))
    except InvalidOperation:
        return None


def parse_clp(text: str | None) -> int | None:
    """``"$ 950.000"`` -> 950000. CLP has no decimals."""
    if not text:
        return None
    head = text.split(",", 1)[0]
    digits = re.sub(r"[^0-9]", "", head)
    return int(digits) if digits else None


def parse_list(text: str | None) -> list[str]:
    if not text:
        return []
    return [part.strip() for part in re.split(r"[;\n•]", text) if part.strip()]


class StepRunner:
    """Executes one portal map against ``ctx.page``."""

    def __init__(self, spec: dict[str, Any], ctx: RunContext) -> None:
        self.spec = spec
        self.ctx = ctx
        timeouts = spec.get("timeouts") or {}
        self.default_ms = int(timeouts.get("default_ms", 15000))
        self.navigation_ms = int(timeouts.get("navigation_ms", 30000))
        self.captcha = list(spec.get("captcha_selectors") or [])
        self.value_map = spec.get("value_map") or {}
        self.rows: list[dict[str, Any]] = []
        self.PwTimeout, self.PwError = _playwright_errors()

    # --- helpers --------------------------------------------------------------

    def _timeout(self, step: dict[str, Any], default: int) -> int:
        name = step.get("name") or ""
        if name in self.ctx.step_timeouts:
            return int(self.ctx.step_timeouts[name])
        return int(step.get("timeout_ms", default))

    def _url(self, path: str) -> str:
        if re.match(r"^https?://", path or ""):
            return path
        return self.ctx.base_url.rstrip("/") + "/" + (path or "").lstrip("/")

    def _visible(self, selector: str) -> bool:
        try:
            loc = self.ctx.page.locator(selector)
            return loc.count() > 0 and loc.first.is_visible()
        except Exception:  # noqa: BLE001 - a bad selector is "not there"
            return False

    def _check_captcha(self, name: str) -> None:
        for selector in self.captcha:
            if self._visible(selector):
                raise CaptchaDetected(f"captcha selector visible: {selector}", step=name)

    def _not_found(self, name: str, selector: str, exc: BaseException) -> LayoutChanged:
        self._check_captcha(name)
        return LayoutChanged(f"selector not found: {selector} ({type(exc).__name__})", step=name)

    # --- ops ------------------------------------------------------------------

    def op_goto(self, step: dict[str, Any]) -> str | None:
        name = step["name"]
        url = self._url(render_template(step.get("path", ""), self.ctx))
        try:
            response = self.ctx.page.goto(url, wait_until="domcontentloaded", timeout=self._timeout(step, self.navigation_ms))
        except self.PwTimeout as exc:
            raise PortalTimeout(f"navigation timeout: {step.get('path')}", step=name) from exc
        except self.PwError as exc:
            raise PortalUnavailable(f"navigation error: {str(exc).splitlines()[0][:200]}", step=name) from exc
        status = response.status if response is not None else None
        if status is not None and status >= 500:
            raise PortalUnavailable(f"HTTP {status} on {step.get('path')}", step=name)
        if status is not None and status in (404, 410):
            raise LayoutChanged(f"HTTP {status} on {step.get('path')}", step=name)
        self._check_captcha(name)
        return None

    def op_fill(self, step: dict[str, Any]) -> str | None:
        name, selector = step["name"], step["selector"]
        value = render_template(step.get("value", ""), self.ctx, self.value_map)
        try:
            self.ctx.page.fill(selector, value, timeout=self._timeout(step, self.default_ms))
        except self.PwTimeout as exc:
            raise self._not_found(name, selector, exc) from exc
        return None

    def op_select(self, step: dict[str, Any]) -> str | None:
        name, selector = step["name"], step["selector"]
        value = render_template(step.get("value", ""), self.ctx, self.value_map)
        try:
            self.ctx.page.select_option(selector, value=value, timeout=self._timeout(step, self.default_ms))
        except self.PwTimeout as exc:
            raise self._not_found(name, selector, exc) from exc
        except self.PwError as exc:  # option missing: the portal changed its list
            raise LayoutChanged(f"option not selectable in {selector}", step=name) from exc
        return None

    def op_click(self, step: dict[str, Any]) -> str | None:
        name, selector = step["name"], step["selector"]
        try:
            self.ctx.page.click(selector, timeout=self._timeout(step, self.default_ms))
        except self.PwTimeout as exc:
            raise self._not_found(name, selector, exc) from exc
        return None

    def op_wait_for(self, step: dict[str, Any]) -> str | None:
        name, selector = step["name"], step["selector"]
        failures: dict[str, str] = dict(step.get("failures") or {})
        for captcha in self.captcha:
            failures.setdefault(captcha, PortalErrorKind.CAPTCHA.value)
        union = ", ".join([selector, *failures.keys()])
        try:
            self.ctx.page.wait_for_selector(union, state="visible", timeout=self._timeout(step, self.default_ms))
        except self.PwTimeout as exc:
            self._check_captcha(name)
            if step.get("on_timeout") == PortalErrorKind.TIMEOUT.value:
                raise PortalTimeout(f"waited too long for {selector}", step=name) from exc
            raise LayoutChanged(f"selector not found: {selector}", step=name) from exc
        for fail_selector, kind in failures.items():
            if self._visible(fail_selector):
                exc_cls = _KIND_TO_EXC.get(kind, LayoutChanged)
                raise exc_cls(f"failure selector visible: {fail_selector}", step=name)
        return None

    def op_extract_table(self, step: dict[str, Any]) -> str | None:
        name = step["name"]
        columns: dict[str, dict[str, str]] = step.get("columns") or {}
        rows = self.ctx.page.locator(step["rows"])
        count = rows.count()
        if count == 0:
            raise NoResults("the results table has no rows", step=name)
        out: list[dict[str, Any]] = []
        for i in range(count):
            row = rows.nth(i)
            record: dict[str, Any] = {}
            for field_name, col in columns.items():
                cell = row.locator(col["selector"])
                if cell.count() == 0:
                    if col.get("required"):
                        raise LayoutChanged(f"column missing: {field_name} ({col['selector']})", step=name)
                    record[field_name] = None
                    continue
                record[field_name] = (cell.first.inner_text(timeout=self._timeout(step, self.default_ms)) or "").strip()
            out.append(record)
        self.rows = out
        return _MSG["rows"].format(n=len(out))

    # --- driver ---------------------------------------------------------------

    def run(self) -> list[dict[str, Any]]:
        for step in self.spec.get("steps") or []:
            name = step.get("name") or step.get("op") or "step"
            step = {**step, "name": name}
            op = step.get("op")
            handler = getattr(self, f"op_{op}", None)
            if handler is None:
                raise LayoutChanged(f"unknown op in the step map: {op}", step=name)
            try:
                note = handler(step)
            except PortalError as exc:
                exc.step = exc.step or name
                self.ctx.log_step(name, False, _failure_message(exc, step), op=op)
                raise
            self.ctx.log_step(name, True, note, op=op)
        return self.rows


def _failure_message(exc: PortalError, step: dict[str, Any]) -> str:
    kind = exc.kind
    if kind == PortalErrorKind.LAYOUT_CHANGED:
        return _MSG["not_found"].format(selector=step.get("selector") or step.get("rows") or step.get("path") or "?")
    if kind == PortalErrorKind.TIMEOUT:
        return _MSG["timeout"]
    if kind == PortalErrorKind.PORTAL_UNAVAILABLE:
        match = re.search(r"HTTP (\d+)", exc.detail or "")
        return _MSG["http"].format(status=match.group(1)) if match else _MSG["unavailable"]
    if kind == PortalErrorKind.CAPTCHA:
        return _MSG["captcha"]
    if kind == PortalErrorKind.NO_RESULTS:
        return _MSG["no_rows"]
    if kind == PortalErrorKind.LOGIN_FAILED:
        return _MSG["login_failed"]
    return _MSG["failure"]


def rows_to_quotes(rows: list[dict[str, Any]], columns: dict[str, dict[str, str]]) -> list[QuoteOut]:
    """Typed quotes from the extracted cell texts (``type`` per column)."""
    quotes: list[QuoteOut] = []
    for row in rows:
        typed: dict[str, Any] = {}
        for field_name, col in columns.items():
            text = row.get(field_name)
            kind = col.get("type", "text")
            if kind == "uf":
                typed[field_name] = parse_uf(text)
            elif kind == "clp":
                typed[field_name] = parse_clp(text)
            elif kind == "list":
                typed[field_name] = parse_list(text)
            else:
                typed[field_name] = (text or None) and text[:255]
        quotes.append(
            QuoteOut(
                plan_name=typed.get("plan_name"),
                premium_uf=typed.get("premium_uf"),
                premium_clp=typed.get("premium_clp"),
                deductible=typed.get("deductible"),
                coverages=typed.get("coverages"),
                raw={k: (v[:500] if isinstance(v, str) else v) for k, v in row.items()},
            )
        )
    return quotes


__all__ = ["StepRunner", "parse_clp", "parse_list", "parse_uf", "render_template", "rows_to_quotes"]
