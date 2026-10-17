# v12 — portal search RPA (lane B6 backend, F6 frontend)

Goal: **showcase** quoting in an insurer portal with Playwright, starting with **HDI Seguros** only. We do NOT have HDI
credentials, flows or page maps yet (the team will send them). So build a robust FRAMEWORK + an HDI connector driven by a
selector/step map that is easy to fix when the portal changes, and prove the engine end-to-end against a LOCAL fake portal.

## 1. Framework — `backend/app/services/rpa/`
- `base.py`: `PortalConnector` protocol `{key, name, version, supported_subtypes, requires_credentials, run(ctx) -> PortalResult}`;
  `RunContext{page, input, credentials, log_step(name, ok, detail), screenshot(name)}`; `PortalResult{quotes: list[QuoteOut]}`.
- `errors.py`: exception classes mapping 1:1 to `PortalErrorKind` (`CredentialsMissing`, `LoginFailed`, `LayoutChanged`
  (selector not found / unexpected page), `PortalTimeout`, `CaptchaDetected`, `NoResults`, `PortalUnavailable`); anything else →
  `unexpected`.
- `engine.py`: `execute_run(db, run, *, connector, browser_factory)` — launches Chromium (reuse `playwright`, already a backend
  dependency; `settings.RPA_HEADLESS`), per-step timeouts, captures a screenshot on failure into
  `document(category=portal_screenshot, entity_type=portal_search)`, writes `steps` (sanitised: never credentials, never full HTML),
  maps exceptions to `error_kind`, persists `portal_quote` rows, sets status/timestamps, and on failure:
  * notifies the requesting user (`portal_search_failed`, fyi) with `params.error_kind`;
  * notifies the RADAL team: email to `settings.RADAL_OPS_EMAIL` through the mail service (B4's `services/mail` send helper —
    if not available at build time, call a phase-1 stub `services/mail/ops.py::notify_ops(db, subject, body)` (as built) that records a
    `mail_message` and sends only when `MAIL_ENABLED`), and a log line at ERROR with connector key+version+error_kind;
  * `layout_changed` (≥2 consecutive failures of the same connector in 24 h) flips the portal to `maintenance` in
    `/portal-searches/portals` (in-memory/DB flag via the last runs) so users see "en mantención" instead of retrying blindly.
- `registry.py`: `CONNECTORS = {"hdi": HdiConnector()}`; `get_connector(key)`.
- `selectors/hdi.json` (data): login URL, field selectors, the step list (`goto`, `fill`, `click`, `select`, `wait_for`,
  `extract_table`) — the connector interprets it, so a portal change is usually a JSON edit, not code.

## 2. HDI connector — `services/rpa/connectors/hdi.py`
Supports subtypes `VEH-01` (Vehículos livianos) initially. Input schema (validated): `{plate, brand, model, year, use, commune,
insured_rut, start_date}`. Without credentials → `CredentialsMissing` (no browser launched). Base URL from settings
`RPA_HDI_BASE_URL` (blank ⇒ portal reported `maintenance` with reason "Conector en configuración").

## 3. Execution
`POST /portal-searches` creates `queued`; job `rpa.run` executes queued runs (oldest first, max 3 per invocation). In dev,
`RPA_INLINE=true` executes in a FastAPI `BackgroundTasks`. Lambda note: Playwright already ships in the backend image for PDFs.

## 4. User-facing explanation (frontend i18n, by `error_kind`)
`credentials_missing` → "Configura tus credenciales de HDI en Configuración → Integraciones." · `login_failed` → "HDI rechazó
el usuario o la clave." · `layout_changed` → "El portal de HDI cambió y el equipo Radal ya fue avisado. Mientras lo
actualizamos, cotiza directo en el portal o sube la cotización en PDF." · `timeout`/`portal_unavailable` → "El portal no
respondió; reintenta en unos minutos." · `captcha` → "HDI pidió verificación humana; completa la cotización en el portal." ·
`no_results` → "HDI no entregó planes para estos datos." Always offer: Reintentar · Subir cotización PDF (comparador) ·
Otros portales (list, disabled "pronto" for those not built).

## 5. Tests (hermetic)
`tests/fixtures/rpa/fake_hdi/*.html` — a tiny static portal (login form → quote form → results table) served with `file://` or a
local `http.server` thread on an ephemeral port; `RPA_HDI_BASE_URL` points there in the test. Cases: success (quotes parsed),
login failed, layout changed (missing selector), credentials missing, timeout; screenshots stored; notifications created; ops
mail recorded (MAIL_ENABLED false ⇒ status skipped). Skip browser tests cleanly (`pytest.importorskip` + launch check) if
Chromium is unavailable — but it IS installed on this machine (PDF engine), so they must run here.
