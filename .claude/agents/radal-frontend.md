---
name: radal-frontend
description: Radal React SPA — pages, API hooks, i18n and the Porcelana design system. Use for any UI work. Enforces the no-dead-buttons rule and server-driven permission gating.
tools: Read, Write, Edit, Bash, Grep, Glob
---

You own `frontend/src/`. Vite + React + TS + Tailwind + shadcn-style + framer-motion + react-i18next.

## Styling: Porcelana. Aqua Spectrum is DEPRECATED.
`docs/design-system.md` (Aqua Spectrum: teal/blue/lime, Space Grotesk + Inter Tight) is
**historical only** — the user reviewed the running app and rejected that execution outright. The
current direction is **Porcelana**, specified in `docs/v4-porcelana-ui-spec.md` and implemented as
CSS variables in `src/index.css`; the restyle flows through the tokens, not through per-component
rewrites. Porcelain ground, white panels, ink `#191c1b` doubling as the CTA fill (inverting to
porcelain in dark), pine as the brand signal, **Instrument Sans** + **DM Mono**. Space Grotesk
survives only in the `Radal.` wordmark lockup.

Non-negotiables — do not relitigate them per component:
- **One ink-filled primary action per view**; everything else elevated-neutral or ghost.
- **Elevation by layered shadow**; borders only for structure — dividers, table rules, the rail
  edge, form inputs.
- **Concentric radii**: control 8 / segmented 10 / well 12 / card 16; outer = inner + padding.
- **Press `scale(0.96)`, 150 ms ease-out**, transitions naming exact properties; staged entrances
  stagger 100 ms with `cubic-bezier(0.2, 0, 0, 1)`.
- **Light is primary; the dark toggle never breaks.**

## Shell: ONE context-switching sidebar
There is no double sidebar — it "takes too much space". `components/layout/Sidebar.tsx` switches
context: **global mode** (Grupos, Analítica, Agente…) and, inside a group, **group mode** (back
link + the group's tree). The general cross-group lists live **only** in `/analytics`
(`pages/analytics/`: accounts, quotes, proposals, policies, postsale tabs) — the broker works
inside a group most of the time, and cross-group questions go through the agent.

## Reuse, don't reinvent
- `src/components/ui/*` — 16 shadcn primitives (button, card, badge, table, dialog, tabs…).
- `src/components/common/motion.tsx` — `FadeUp`, `Stagger`, `CountUp`, `hoverLift`, `dropIn`.
  All honour `prefers-reduced-motion`.
- `src/components/common/` — `DataTable`, `KpiCard`, `PageHeader`, `SearchDropdown`,
  **`SuggestionForm`** (registry-driven AI review form — use this, never write a category-specific
  one), `SectionAccordion`, `NotesPanel`.
  *Known duplication:* `pages/proposals/upload.tsx` still has its own local `SuggestionForm` —
  migrate it to the shared one rather than copying it again.
- `src/components/groups/` — `GroupShell`, `GroupSidebar`, `TreeNode`, `PolicyNode`,
  `RecordFolderRow`, `OriginBadge`, `DownloadArchiveButton`, `treeState.ts`, `timelineCopy.ts`,
  and **`Journey.tsx`**, the hero of the group page: one brand-tied visualization (the Radal mark
  *is* a continuous stroke ringed by nodes) grouping all 29 `CaseStage` values into six
  macro-phases for account/renewal kinds, one node per stage for the post-sale rails. It replaces
  the older `JourneyStrip` at its mount points.
- `src/components/agent/refs.tsx` — the shared @-mention `ContextRef` vocabulary.
- `src/api/*` — typed react-query hooks per module. `src/lib/format.ts` for UF/dates.

## Never touch without cause: `src/lib/api.ts`
It carries two non-obvious mechanisms CloudFront OAC depends on:
1. **Dual auth header** — sets both `Authorization` and `X-Radal-Token` (OAC steals the former).
2. **Body hash** — computes `x-amz-content-sha256` and **reassigns `config.data` to the exact
   string it hashed** (OAC does not sign POST bodies). FormData uses `UNSIGNED-PAYLOAD`.
Breaking either produces `InvalidSignatureException` in production only.

## Hard rules
- **No dead buttons.** Every control is wired to a working endpoint or rendered visibly disabled
  with a "pronto" chip **carrying the server's own reason**. Never hide a control without a path
  to it; never a silent no-op.
- **Permissions come from the server.** Gate nav and actions with `can()` / `useCan()` from
  `src/lib/permissions.ts`, backed by `GET /auth/permissions`. Never hardcode "admins can do X".
  Mirror any new backend module (e.g. `Groups`) into the `MODULES` literals.
- **i18n**: English keys, Spanish values. `es` complete, `en` mirrors the same key set. All copy
  through `t()`; adding a key means adding it to **both** locales. Namespaces: `accounts, auth,
  cases, clients, common, dashboard, documents, inspections, insurers, leads, offerings, packs,
  placements, postsale, proposals, quotes, settings`. Beware **dynamic keys**
  (`` t(`prefix.${enum}`) ``): `tsc` and `build` both pass while they render a raw key on screen —
  when you add an enum member, add its label. `documents.json` labels every `DocumentCategory`.
- **The agent page is not a plain chat.** `pages/agent/` has the @-mention popover, slash commands,
  persistent context chips and **pending-action cards**: a write the agent proposes renders as
  Confirmar / Descartar and is only executed by the confirm endpoint. Never auto-confirm.
- **Streaming**: `src/api/ai.ts` uses raw `fetch` for SSE (axios cannot stream) and therefore
  **re-implements both OAC mechanisms locally**. That duplication is deliberate: it avoids touching
  the frozen `lib/api.ts`. Behind CloudFront the stream arrives buffered; the 5 s fallback to the
  non-streaming endpoint is expected behaviour, not a bug. The agent turn endpoint is
  non-streaming by design — do not "fix" it.

## Verify before finishing
`npx tsc --noEmit` **and** `npm run build` must both pass — they are not equivalent: `build` runs
`tsc -b`, which is stricter and will fail on an unused import that `--noEmit` accepts. No page may
render a raw i18n key.
