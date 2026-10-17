# v12 — frontend contract

Stack: React 18 + Vite + TS + Tailwind (Signal tokens: `paper`, `bone`, `ink`, `brand`, `line`…), shadcn/radix primitives in
`src/components/ui`, TanStack Query (keys in `src/api/keys.ts`), react-hook-form + zod, i18next (`es` authoritative, `en` mirror;
`node scripts/check-locales.mjs` enforces parity and that every namespace is registered in `src/i18n/index.ts::NAMESPACES`).
Rules: no dead buttons (wired, or visibly disabled with a "pronto" chip + server reason); nav filtered by `GET /auth/permissions`
(`can(perms, module, action)`), never hardcoded roles; **no dynamic i18n keys** (use explicit maps `{state: "collections:state.overdue"}`
typed as const); the axios `paramsSerializer` in `lib/api.ts` already repeats array params — don't bypass `api`.
Public pages use their own no-auth axios instance with the OAC body hash (copy the pattern of `src/api/public.ts`).

## 1. Phase-1 scaffold (one agent, before lanes)
- `lib/permissions.ts`: add modules `Mail`, `Integrations`, `Platform` to `MODULES`.
- `i18n/index.ts`: register namespaces `policies`, `collections`, `notifications`, `mail`, `share`, `platform`, `rpa`, `catalog`
  and create `locales/{es,en}/<ns>.json` with a minimal `{ "title": "…" }` each (lanes fill them).
- `App.tsx` routes (pages created as minimal placeholders that lanes replace; keep the `deferredRoute`/glob mechanism or
  static lazy imports — must not break build):
  - `/` → `Navigate to="/policies"` (**policies are the home**; groups stay at `/groups`)
  - `/policies` (list, main view) · `/policies/new` (manual entry) · `/policies/:policyId` (existing detail)
  - `/collections` (Cobranza list) · `/collections/evidence` (Respuestas por confirmar) · `/collections/inquiries` ·
    `/collections/:planId` (existing plan detail — keep)
  - `/portal-search` (RPA) · `/settings` (existing; F3 adds tabs) · `/notifications` (full list, mobile-friendly)
  - `/platform` (maintainer home) · `/platform/insurers` · `/platform/provider-accounts` · `/platform/events` · `/platform/jobs`
    — rendered only for `user_type === "platform"` (permission `Platform.View`), outside broker assumptions
  - public, outside the shell: `/s/:token` (share view)
- Global rail (`SidebarGlobal.tsx`) order: **Pólizas** (`/policies`, module Policies) · **Cobranza** (`/collections`, module
  Collections, count chip = overdue installments from `/collections/summary`) · ASEGURADOS section (existing groups block) ·
  Portafolio · Analítica · Agente · Cotizar en portales (`/portal-search`, Integrations) · Compañías · Configuración ·
  Mantenedor (`/platform`, Platform — platform users only).
- `Topbar.tsx`: mount point `<NotificationBell />` imported from `@/components/notifications/NotificationBell` (F1 implements;
  phase 1 ships a no-op placeholder component file so the build passes).
- Responsive: the shell already has a `lg` breakpoint drawer (Sheet). Verify at 375 px: topbar fits, drawer opens, pages have
  no horizontal overflow except inside tables wrapped in `overflow-x-auto`.

## 2. Lanes
**F1 Policies (main view) + bell.** `/policies`: KPI cards from `/policies/summary` (click filters), filter bar (q, ramo/subtipo
from `/catalog/lines`, compañía, estado, cobranza, faltantes, Mías), table on ≥md and **card list on <md**. Columns: Asegurado ·
Ramo/Subtipo · Compañía · N° póliza · Vigencia · Prima (UF) · Cobranza (next due + level badge) · Faltantes chips · Estado.
Primary actions: "Subir póliza" (existing `PolicyUploadDialog`) and "Registrar a mano" (`/policies/new`, react-hook-form: insured
(client picker or RUT+name), compañía, ramo→subtipo cascade, vigencia, n° (optional), primas, contratante/pagador contacts, método
de pago). Detail: add blocks "Falta" (operability with reasons + Completar), "Partes" (edit parties), "Cobranza" summary + link,
"Compartir" (F4's `ShareDialog`), "Viaje" (entered/skipped stages, link to the group account as a viewer). Bell: popover with two
trays (Por hacer / Para saber), count from `/notifications/count` (poll 60 s), each item rendered from `kind` via an explicit
map + `params`, click → `link_path` + mark seen; `/notifications` full page.

**F2 Cobranza.** `/collections`: summary cards; subtabs Próximas · Vencidas · Al día · Saldadas; filters (compañía, ramo, método,
nivel, Mías); rows by urgency with: Asegurado · Póliza · Compañía · Cuota n/N · Vence · Monto (UF + CLP when available) · Pagado/
saldo · Nivel badge (native "En línea" / assisted "Asistida" / record "Registro" — text from i18n by level, tooltip = server
`level_reason`) · Estado + flags · última gestión · acciones. Row actions (side sheet): Registrar pago · Promesa de pago ·
PAC rechazado · Confirmar pago · Enviar recordatorio (preview → send; WhatsApp option disabled "pronto") · Consultar a la compañía
(inquiry) · Link de pago (native only; otherwise disabled with reason) · Compartir (ShareDialog) · Iniciar cobranza (plan).
`/collections/evidence`: inbox cards (source, insurer reply excerpt/report, suggested cuota, score + reasons) → Confirmar /
Otra cuota / Rechazar. `/collections/inquiries`: list with status, resend/cancel. Plan detail (`/collections/:planId`, existing):
add payments list with evidence level, notices sent, inquiries.

**F3 Settings + Platform.** Settings tabs added: **Correo** (Radal sender card (read-only) + custom SMTP/IMAP form with test
button; Google & Microsoft cards disabled with "pronto" chip and reason), **Cobranza** (cadence editor, tolerances, master
switch, default sender, auto inquiries, templates editor with variables help + preview + "Aprobar mensajes"), **Compañías y
cobro** (per insurer: level available, instructions merged, collections email; broker override form), **Integraciones** (link
card to `/portal-search?tab=credentials`). Platform console (`/platform/*`): insurer profiles table+form (onboarding status,
instructions JSON fields as a form, collections email, sender domains), provider accounts (create/edit, secrets write-only with
"guardado ✓" state, webhook URL copy, verify, rotate), channels matrix (insurer × channel status/store code/account), events
inbox (status filter, payload viewer, reprocess), jobs (last runs).

**F4 Share + public.** `components/share/ShareDialog.tsx` (props: entityType, entityId, defaultAudience) → create link, copy,
"Enviar por WhatsApp" (`https://wa.me/?text=` + encoded Spanish message with the URL), revoke list. Public `/s/:token`
(mobile-first, broker-branded header): insured view = policy summary, cuotas with state/amount, per-cuota action: native →
"Pagar" (POST checkout → redirect), assisted → insurer instructions (bank data with copy buttons, portal link) + "Ya pagué · subir
comprobante"; insurer view = policy + cuotas + "Informar estado" form per cuota (paid/unpaid/partial/cancelled, amount, date,
comment). Error states: revoked/expired/unknown → friendly Spanish page.

**F5 Catalogue + partial journey.** In `pages/groups/account-new.tsx`: replace free ramo creation ("Crear ramo" drawer + schema
creation) with a **ramo → subtipo cascade** from `/catalog/lines`; show suggested route chip; send `insurance_subtype_id`. Remove
any create/edit-ramo entry points in the UI (locked server-side too). Account page journey rail (`components/groups/Journey.tsx`
and `pages/groups/account.tsx`): render only entered stages solid; skipped stages as thin grey ticks labelled "omitida";
"Saltar a…" menu (skip-to targets) and "Activar etapa" on a skipped stage (disabled with reason when the case has a policy);
tabs for skipped stages hidden behind "Ver etapas omitidas". The group account is a **viewer**: emphasise what exists
(policies, files) over creation CTAs.

**F6 Agent + RPA.** Agent page: skills strip (from `/ai/skills`), active skills chips on the thread, suggested skill from
triggers as the user types; tool trace rows for new tools (Spanish labels map); pending action cards already exist — make sure new
write tools render (generic card fallback). `/portal-search`: portal cards (HDI available/maintenance + "pronto" for others),
credentials panel (write-only), search form per subtype (VEH-01 fields), runs list with status, steps timeline, quotes table,
error explanation by `error_kind` (explicit map) with actions Reintentar · Subir cotización PDF · Otros portales.

## 3. Done = `npx tsc -b --noEmit` clean · `node scripts/check-locales.mjs` OK · `npm run build` OK · no `t(\`…${x}\`)` dynamic
keys introduced (grep) · mobile 375 px sane for the lane's pages.
