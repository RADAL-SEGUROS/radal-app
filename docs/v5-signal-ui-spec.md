# Radal Signal — the v5 UI system (binding spec)

Status: **active direction** (2026-09-06). Supersedes the Porcelana execution values
(`docs/design-system.md` was already historical; Porcelana's ink-CTA/mono-chip execution is now
also deprecated). The discipline is borrowed from the user's "Signal" system in the Nirvana app:
**token system consumed only through Tailwind utility classes · hairline borders · restraint (one
accent per surface) · inline over boxed · real shadcn quality · purposeful framer-motion ·
Vercel/Linear/Supabase sleekness**. Light theme is primary; dark must keep working
(`darkMode: "class"`, ThemeProvider sets both `.dark` and `data-theme`).

## Principles (priority order)

1. **Restraint over decoration.** Color appears on the single most important cue per view — the
   primary button, the active tab, one status dot. Everything else is neutral ink + hairline.
2. **Hairline borders do structure; shadows only whisper depth.** Every card, popover, table
   wrapper and input carries `border border-line`; shadows are soft and secondary (never a fake
   1px ring inside a box-shadow).
3. **Inline over boxed.** Prefer `divide-y divide-line` rows over nested bordered cards. No
   card-in-card.
4. **Normal type.** Inter everywhere. **Mono is retired from UI chrome** — no mono badges, table
   headers, KPI labels, eyebrows, or ID chips. Numbers/RUTs/IDs use Inter `tabular-nums`.
   Space Grotesk survives ONLY in the "Radal." wordmark lockup (`.wordmark`).
5. **Keep information + behavior.** Restyling never removes data, props, hooks, i18n keys,
   handlers, permission gates, or the no-dead-buttons pattern (DisabledHint / "pronto").

## Fonts

`index.html` loads exactly: `Inter:wght@400..700` (variable) and `Space Grotesk:wght@500;600`.
DM Mono and Instrument Sans are removed. Tokens:

```css
--font-ui: "Inter", system-ui, -apple-system, sans-serif;
--font-display: var(--font-ui);
--font-mono: ui-monospace, "SF Mono", Menlo, monospace; /* genuine code content only */
--font-wordmark: "Space Grotesk", system-ui, sans-serif; /* ONLY .wordmark */
```

Body gets `font-feature-settings: "cv11"` (single-story a stays default; cv11 = open digits look);
`.cell-num` / `.tabular` become Inter + `font-variant-numeric: tabular-nums` (no mono).

## Tokens — exact values (keep every existing var NAME; only values change)

Light (`:root`):

```css
--paper: #fafaf9;        /* app ground */
--paper-2: #f4f4f2;      /* recessed wells, zebra, segmented track */
--bone: #ffffff;         /* panels/cards */
--sidebar: #fafaf9;
--line: #e8e8e5;         /* THE hairline */
--line-strong: #d9d9d5;  /* NEW — hover borders, emphasized rules */

--ink: #17191a;  --ink-2: #41464a;  --ink-3: #6b7075;  --muted: #9ba0a5;

--brand: #0e7c70;  --brand-deep: #0b655b;  --brand-soft: rgba(14,124,112,.08);

--cta: var(--brand);  --cta-hover: var(--brand-deep);  --cta-tx: #ffffff;   /* accent primary — never ink */

--pos: #16a34a;  --pos-text: #15803d;   --pos-soft: rgba(22,163,74,.10);
--warn: #d97706; --warn-text: #b45309;  --warn-soft: rgba(217,119,6,.10);
--neg: #dc2626;  --neg-text: #b91c1c;   --neg-soft: rgba(220,38,38,.08);

--elev: 0 1px 2px rgba(16,17,17,.04);
--elev-hover: 0 2px 6px rgba(16,17,17,.06);
--elev-overlay: 0 8px 24px -6px rgba(16,17,17,.14), 0 2px 8px rgba(16,17,17,.06);

--r-ctl: 8px;  --r-seg: 9px;  --r-well: 10px;  --r-card: 12px;
```

Dark (`:root[data-theme="dark"], .dark`):

```css
--paper: #0e0f10;  --paper-2: #17181a;  --bone: #141517;  --sidebar: #0e0f10;
--line: #26282b;   --line-strong: #333538;
--ink: #f2f3f4;  --ink-2: #c3c6c9;  --ink-3: #8f949a;  --muted: #63686e;
--brand: #1fa08f;  --brand-deep: #3cb8a6;  --brand-soft: rgba(31,160,143,.14);
--cta: #1fa08f;  --cta-hover: #3cb8a6;  --cta-tx: #ffffff;
--pos: #34c77b;  --pos-text: #5bd694;  --pos-soft: rgba(52,199,123,.14);
--warn: #e5a54c; --warn-text: #eebb75; --warn-soft: rgba(229,165,76,.14);
--neg: #ef6f67;  --neg-text: #f4938d; --neg-soft: rgba(239,111,103,.14);
--elev: 0 1px 2px rgba(0,0,0,.35);
--elev-hover: 0 2px 6px rgba(0,0,0,.45);
--elev-overlay: 0 12px 32px -8px rgba(0,0,0,.6), 0 2px 8px rgba(0,0,0,.4);
```

Keep: `--topbar-h`, all legacy aliases (`--teal/--blue/--lime/--amber/--red` families → they keep
pointing at brand/status), the `--signal-*`, surface/text role vars, `color-scheme` lines.
Add the three `*-soft` status vars and `--line-strong` to tailwind.config (`line-strong`,
`signal.success-soft` etc. or `pos-soft`-style names — pick one and use it consistently).

## Type scale (tailwind `fontSize`, replaces the Instrument scale)

```
display 28px / 1.15 / 600 / -0.02em      h1 22px / 1.2 / 600 / -0.015em
h2 17px / 1.3 / 600 / -0.01em            h3 14.5px / 1.35 / 600
kpi 26px / 1.1 / 600 / -0.02em           body 14px / 1.55 / 400
label 13px / 1.4 / 500                   caption 12px / 1.4 / 400
```

Delete `mono` and `mono-sm` sizes only after the sweep removes their consumers; until then they
may remain but nothing new uses them. Headings use `font-semibold tracking-tight` — no
`font-display` distinction anymore (it aliases to Inter).

## Component doctrine (src/components/ui)

- **Button** — variants: `primary` = `bg-cta text-cta-foreground hover:bg-cta-hover shadow-elev`
  (pine, white text — NEVER ink); `secondary` = `bg-bone border border-line hover:bg-paper-2
  hover:border-line-strong`; `outline` = transparent + `border border-line` (a REAL outline,
  distinct from secondary); `ghost`; `destructive` = `bg-[--neg-soft] text-[--neg-text]
  border border-transparent hover:border-[--neg]/30` (quiet, red only on the destructive control);
  `link`. Keep `teal`→ alias of a new `accent-soft` (brand-soft tint + brand-deep text). Sizes
  h-9/h-8/h-10/icon unchanged. Press `active:scale-[0.98]`, 150ms named-property transitions.
- **Badge** — the highest-leverage fix. Base: `inline-flex items-center gap-1.5 rounded-md
  px-2 py-0.5 text-[12px] font-medium leading-5` — **normal case, Inter, no mono**. Variants keep
  their NAMES (neutral, muted, brand, action, success, warn, danger, outline) with: neutral =
  `bg-paper-2 text-ink-2`; brand/action = `bg-brand-soft text-brand-deep`; success/warn/danger =
  `*-soft` bg + `*-text` color; outline = `border border-line text-ink-3 bg-transparent`. Add an
  optional `dot` boolean prop rendering a 6px `rounded-full` dot of the variant's strong color —
  status meaning at a glance.
- **Table / DataTable** — headers: `text-[12px] font-medium text-ink-3 normal-case` (NO mono, NO
  uppercase), `border-b border-line`, transparent bg (drop bg-recessed head). Rows `border-b
  border-line last:border-0`, hover `bg-paper-2/60`. Wrapper: `rounded-[var(--r-card)] border
  border-line bg-bone` (border replaces shadow-elev card). Numeric cells right-aligned
  `tabular-nums`. DataTable additionally gains optional controlled pagination props
  (`pageIndex/pageSize/total/onPageChange`) rendering a quiet footer pager — additive, callers
  unaffected.
- **Tabs** — add a variant API: `variant="underline"` (default for page/section tabs): list =
  `flex gap-4 border-b border-line bg-transparent p-0 h-auto`; trigger = `px-1 pb-2.5 pt-1
  text-label text-ink-3 border-b-2 border-transparent -mb-px rounded-none` with active
  `text-ink border-brand`. `variant="segmented"` keeps today's recessed-track style (view
  toggles). Existing call sites keep working (segmented stays the default only if changing the
  default breaks layouts — prefer flipping the big page tabs to underline during the sweep).
- **Card** — `rounded-[var(--r-card)] border border-line bg-bone shadow-elev`; `interactive` →
  `hover:border-line-strong hover:shadow-elev-hover`. No card-in-card.
- **Input/Select/Textarea** — real `border border-line bg-bone` (inputs sit on white now),
  `focus-visible:border-brand focus-visible:ring-2 ring-brand/15`. Drop the inset-shadow hack.
- **Dialog/Dropdown/Popover/Sheet/Tooltip/Toast** — content gets `border border-line` +
  `shadow-overlay`. Tooltip stays inverted (bg-ink) — fine in both themes.
- **EmptyState** (promoted to components/common) — `py-12` centered: 40px icon tile
  (`rounded-[10px] bg-brand-soft text-brand-deep`, icon 20px), `text-h3` title, `text-caption
  text-ink-3 max-w-sm` description, single `primary` CTA. Teach the next step; never blank.
- **KpiCard** — label `text-caption font-medium text-ink-3` (sentence case), value
  `text-kpi font-semibold tabular-nums`, keep CountUp + tone-on-hint; icon tile brand-soft.
- **MonoChip → IdChip** — rename semantics: quiet identifier chip `rounded-md border border-line
  bg-bone px-1.5 py-0.5 text-[12px] text-ink-2 tabular-nums` (Inter). Keep the export name
  `MonoChip` as an alias so 46 importers compile; new code uses plain
  `<span class="tabular-nums text-ink-3">` where a chip adds nothing.
- **New primitives to add** (shadcn shape, consuming these tokens): `separator`, `scroll-area`,
  `popover` (radix dep already installed), `collapsible`, `switch`, `checkbox`, `textarea`,
  `progress`, `breadcrumb`, `command` (cmdk), `resizable` (react-resizable-panels).

## Motion

Keep `components/common/motion.tsx` (FadeUp/Stagger/CountUp) as the single entrance idiom: one
gentle stagger per page (`y: 8→0`, opacity, 0.4s), hovers are 150ms CSS, press is scale-0.98.
Nothing bouncy; respect `useReducedMotion` (already wired). Drop the `blur(4px)` from the enter
keyframe (smearing text reads cheap); plain fade+rise.

## Sweep rules (before → after)

- `font-mono` / `text-mono` / `text-mono-sm` in UI chrome → remove; use `text-caption` /
  `text-[12px] font-medium` / `tabular-nums` as appropriate. 52 files.
- `uppercase tracking-*` eyebrows/labels → sentence case `text-caption font-medium text-ink-3`.
- Legacy hue utilities `bg-/text-/border-teal|blue|lime|amber|red*` → semantic equivalents
  (`brand`, `signal-*`, `pos/warn/neg` mappings). 62 files.
- Arbitrary `color-mix(...)` and `-[var(--…)]` classNames → nearest token utility (`bg-brand-soft`,
  `border-line`, the new `*-soft` utilities). 49 + 15 files.
- One-off radii (`rounded-[11px]`, `rounded-[14px]`) → the ladder (`rounded-sm/md/lg/card`).
- `shadow-elev` used as a card ring → `border border-line shadow-elev` (border does the ring).
- Gradient div "charts" → keep for now unless the view is being rebuilt; new charts use recharts
  via the dataviz skill.

## Trap: tailwind-merge and the custom type scale

`cn()` runs `twMerge`, which cannot tell a custom font size (`text-caption`) from a custom colour
(`text-cta-foreground`) — both are `text-*` — and silently drops one of them. Unfixed, this made
every `size="sm"` primary button render a pine label on a pine fill (invisible) and stripped the
font size from every element that also set a colour. `src/lib/utils.ts` therefore builds `cn` with
`extendTailwindMerge` and an explicit `font-size` group listing the scale. **Any name added to
`fontSize` in `tailwind.config.ts` must be added to `FONT_SIZES` in `src/lib/utils.ts`**, or that
size will start vanishing at random call sites.

## Verification (every implementation agent runs before returning)

`cd frontend && npx tsc -b --noEmit` clean; `npm run check:locales` clean when locales touched;
final gate additionally `npm run build`. Definition of done greps (target ~zero in touched files):
`font-mono|text-mono` outside genuinely-code content, `bg-cta` outside button.tsx,
`uppercase` in badges/table headers, `color-mix(` in classNames.
