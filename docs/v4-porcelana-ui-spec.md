# Radal v4 — Porcelana UI: implementable spec

> **Status: authoritative build spec** for the app-wide Porcelana restyle, the single
> context-switching sidebar, the Journey visualization, and the `/analytics` page
> (standing decisions 1–4 and 6). The agent overhaul (decision 5) is specified elsewhere;
> the only thing this spec does to it is the **nav change** (one "Agente" entry, §3.2/§5).
>
> Sources of truth, in order:
> 1. The Porcelana token block in `frontend/src/pages/style-lab/index.tsx`
>    (`.mk[data-dir="porcelana"]`) — every value below is copied from it, not approximated.
> 2. The craft doctrine at `/tmp/jk-skills/skills` (better-ui + surfaces, better-typography,
>    better-colors + palette-structure, better-layout) — apply its EXACT values.
> 3. `CLAUDE.md` rules 3 (no dead buttons), 4 (i18n: English keys, Spanish values, `en`
>    mirrors `es`), and the server-permission law (`GET /auth/permissions` drives the nav).
>
> Non-negotiables restated once so no package relitigates them:
> - **One ink-filled primary action per view.** Everything else is elevated-neutral or ghost.
> - **Elevation by layered shadow; borders only for structure** (dividers, table rules, the
>   rail edge, form inputs).
> - **Concentric radii**: control 8 / segmented 10 / well 12 / card 16; outer = inner + padding.
> - **Press `scale(0.96)`, 150 ms ease-out, transitions name exact properties**; staged
>   entrances stagger 100 ms with `cubic-bezier(0.2, 0, 0, 1)`.
> - **Light is primary; the dark toggle never breaks** (Porcelana-dark defined in §1.2).
> - **Server reasons stay authoritative**: a disabled control always carries the server's
>   reason; nothing is hidden without a path to it.

---

## 1. (a) The Porcelana token system

### 1.1 Strategy: the restyle flows through variables

The codebase references colors almost exclusively through CSS custom properties —
`var(--teal)`, `var(--ink)`, `color-mix(in srgb, var(--blue) …)` — and through Tailwind
classes that resolve to those variables (`bg-bg-surface`, `text-text-primary`,
`border-line`, `text-teal-deep`, …). **F1 changes the values behind the existing names and
adds the new Porcelana roles**; 38+ files restyle without being touched. Only the component
primitives listed in §1.7 need edits, because their *shape* (radius, shadow-vs-border,
press feedback) changes, not just their color.

Two accent hues collapse into one, deliberately: Porcelana has **no blue**. Pine
`#0E7C70` is the single brand/active/link hue; the CTA is **ink**. `--blue` and `--teal`
both become aliases of `--brand` so legacy `action`-toned badges and `brand`-toned badges
converge on pine (one hue, one meaning — better-colors). Positive/warn/danger keep their
own hues, moved to the Porcelana values.

### 1.2 `index.css` — the full replacement token sheet

Replace the current `:root` and dark blocks with the following. Names kept from the old
sheet are marked `(kept)`; everything unmarked is new. `--topbar-h` stays.

```css
:root {
  /* Layout constant (kept) — the sticky topbar's exact height. */
  --topbar-h: 63px;

  /* ── Porcelana neutrals: ONE warm, green-leaning grey family (kept names) ── */
  --paper:   #F7F7F3;   /* ground */
  --paper-2: #F1F1EB;   /* recessed: wells, zebra, segmented track */
  --bone:    #FFFFFF;   /* panel: cards, table wrap, popovers */
  --sidebar: #F7F7F3;   /* the rail sits on the GROUND, not on panel (style-lab .mk-side) */
  --line:    #E7E8E2;   /* hairline — STRUCTURE ONLY: dividers, table rules, rail edge, inputs */

  --ink:   #191C1B;     /* primary text; also the CTA fill */
  --ink-2: #2E3331;     /* strong secondary text; also the CTA hover */
  --ink-3: #55605D;     /* secondary text */
  --muted: #899290;     /* muted text, placeholders, inert icons */

  /* ── Brand: pine, the single accent hue ── */
  --brand:      #0E7C70;
  --brand-deep: #0A5A51;                    /* pine hover / high-contrast pine text */
  --brand-soft: rgba(14, 124, 112, 0.09);   /* active-nav fill, brand chips */

  /* ── CTA: ink buttons ── */
  --cta:       #191C1B;
  --cta-hover: #2E3331;
  --cta-tx:    #F7F7F3;

  /* ── Status ── */
  --pos:      #4D7C0F;  --pos-text:  #3F6212;   /* text on a pos tint (style-lab override) */
  --warn:     #B45309;  --warn-text: #92400E;
  --neg:      #B91C1C;  --neg-text:  #991B1B;

  /* ── Legacy accent names → aliases (do NOT delete; ~40 files read them) ── */
  --teal:       var(--brand);        /* (kept) */
  --teal-deep:  var(--brand-deep);   /* (kept) */
  --teal-soft:  var(--brand-soft);   /* (kept) — was a solid #B8EBE5, now the 9% tint */
  --blue:       var(--brand);        /* (kept) — blue is retired; action = brand */
  --blue-deep:  var(--brand-deep);   /* (kept) */
  --lime:       var(--pos);          /* (kept) */
  --lime-deep:  var(--pos-text);     /* (kept) */
  --amber:      var(--warn);         /* (kept) */
  --amber-deep: var(--warn-text);    /* (kept) */
  --red:        var(--neg);          /* (kept) */
  --red-deep:   var(--neg-text);     /* (kept) */

  /* ── Elevation (better-ui/surfaces, exact 3-layer recipe) ── */
  --elev:
    0px 0px 0px 1px oklch(0 0 0 / 0.06),
    0px 1px 2px -1px oklch(0 0 0 / 0.06),
    0px 2px 4px 0px oklch(0 0 0 / 0.04);
  --elev-hover:
    0px 0px 0px 1px oklch(0 0 0 / 0.08),
    0px 1px 2px -1px oklch(0 0 0 / 0.08),
    0px 2px 4px 0px oklch(0 0 0 / 0.06);
  /* Overlay surfaces (dropdowns, dialogs, the ⌘K panel) get one longer layer on top: */
  --elev-overlay:
    0px 0px 0px 1px oklch(0 0 0 / 0.06),
    0px 4px 8px -2px oklch(0 0 0 / 0.08),
    0px 12px 32px -8px oklch(0 0 0 / 0.10);

  /* Legacy shadow names → aliases (kept) */
  --shadow-sm:   var(--elev);
  --shadow:      var(--elev-hover);
  --shadow-card: var(--elev);

  /* ── Semantic signals (kept names) ── */
  --signal-success:     var(--pos);
  --signal-action:      var(--brand);   /* action = brand now */
  --signal-brand:       var(--brand);
  --signal-warn:        var(--warn);
  --signal-warn-deep:   var(--warn-text);
  --signal-danger:      var(--neg);
  --signal-danger-deep: var(--neg-text);
  --signal-muted:       var(--muted);

  /* ── Surface / text roles (kept names — the seam Tailwind reads) ── */
  --bg-app:         var(--paper);
  --bg-surface:     var(--bone);
  --bg-recessed:    var(--paper-2);
  --bg-sidebar:     var(--sidebar);
  --text-primary:   var(--ink);
  --text-secondary: var(--ink-2);
  --text-tertiary:  var(--ink-3);
  --text-muted:     var(--muted);
  --border:         var(--line);
  --focus-ring:     var(--brand);

  /* ── Fonts ── */
  --font-display:  "Instrument Sans", system-ui, sans-serif;
  --font-ui:       "Instrument Sans", system-ui, sans-serif;
  --font-mono:     "DM Mono", ui-monospace, monospace;
  --font-wordmark: "Space Grotesk", system-ui, sans-serif;  /* ONLY the "Radal." lockup */

  /* ── Concentric radii ── */
  --r-ctl:  8px;    /* buttons, inputs, nav rows, segmented items */
  --r-seg:  10px;   /* segmented control track = 8 + 2px padding */
  --r-well: 12px;   /* inner wells inside a 16px card with 4px inset */
  --r-card: 16px;
  --radius-card: var(--r-card);  /* (kept) */

  color-scheme: light;
}

/* ── Porcelana-dark: the same warm-grey family inverted onto ink surfaces.
      Single white-ring elevation (layered depth shadows are invisible on dark).
      Light is primary; this exists so the toggle never breaks. ── */
:root[data-theme="dark"],
.dark {
  --paper:   #101312;
  --paper-2: #1C201E;
  --bone:    #161A18;
  --sidebar: #101312;
  --line:    #262B28;

  --ink:   #F2F3EF;
  --ink-2: #C8CDC9;
  --ink-3: #9AA29E;
  --muted: #6F7773;

  --brand:      #2E9E90;                    /* pine lifted along its own hue */
  --brand-deep: #4FB8AA;                    /* hover/text pine on dark is LIGHTER */
  --brand-soft: rgba(46, 158, 144, 0.14);

  --cta:       #F2F3EF;                     /* the ink button inverts to porcelain */
  --cta-hover: #FFFFFF;
  --cta-tx:    #101312;

  --pos:  #8FBB53;  --pos-text:  #A5CC6E;
  --warn: #D9924C;  --warn-text: #E5AC74;
  --neg:  #E5736C;  --neg-text:  #EF938D;

  --elev:         0 0 0 1px oklch(1 0 0 / 0.08);
  --elev-hover:   0 0 0 1px oklch(1 0 0 / 0.13);
  --elev-overlay: 0 0 0 1px oklch(1 0 0 / 0.10), 0px 12px 32px -8px oklch(0 0 0 / 0.5);

  color-scheme: dark;
}
```

Notes:
- The role blocks (`--bg-app` … `--focus-ring`, signals, legacy aliases, shadow aliases)
  do **not** need restating in the dark block — they point at primitives that the dark
  block already overrides. Delete the old dark-mode role restatements.
- `color-mix(in srgb, var(--teal) 14%, transparent)` expressions all over the codebase now
  produce pine tints automatically. No sweep needed in F1; F2/F3/F4 clean up the files
  they own anyway.
- `--pos-text`/`--warn-text`/`--neg-text` exist because the style-lab explicitly darkens
  badge TEXT on tints in Porcelana (`.mk[data-dir="porcelana"] .mk-badge.ok { color:#3F6212 }`).
  The `*-deep` aliases point at them, so `text-lime-deep` etc. keep working.

### 1.3 Old-token → new-token mapping table

| Old token / class | Now resolves to | Visual result |
|---|---|---|
| `--paper` / `bg-bg-app` | `#F7F7F3` | warm porcelain ground |
| `--paper-2` / `bg-bg-recessed` | `#F1F1EB` | recessed wells |
| `--bone` / `bg-bg-surface`, `bg-card` | `#FFFFFF` | white panels |
| `--sidebar` / `bg-bg-sidebar` | `#F7F7F3` | rail on ground |
| `--line` / `border-line`, `border` | `#E7E8E2` | hairline |
| `--ink` / `text-text-primary`, `foreground` | `#191C1B` | ink text |
| `--ink-2` / `text-text-secondary` | `#2E3331` | strong secondary |
| `--ink-3` / `text-text-tertiary` | `#55605D` | secondary |
| `--muted` / `text-text-muted` | `#899290` | muted |
| `--teal`, `--blue`, `--signal-action`, `--signal-brand`, `primary` | `var(--brand)` = `#0E7C70` | **pine** — one accent |
| `--teal-deep`, `--blue-deep`, `accent-foreground` | `#0A5A51` | pine deep |
| `--teal-soft`, `accent` | `rgba(14,124,112,.09)` | pine 9% tint |
| `--lime` / `--signal-success` | `#4D7C0F` | positive |
| `--lime-deep` | `#3F6212` | positive text on tint |
| `--amber` / `--signal-warn` | `#B45309` | warn |
| `--amber-deep` | `#92400E` | warn text on tint |
| `--red` / `--signal-danger`, `destructive` | `#B91C1C` | danger |
| `--red-deep` | `#991B1B` | danger text on tint |
| `--shadow-sm`, `--shadow-card` / `shadow-card`, `shadow-sm` | `var(--elev)` | 3-layer ring+lift+ambient |
| `--shadow` / `shadow-lift` | `var(--elev-hover)` | hover elevation |
| `--focus-ring` / `ring` | `var(--brand)` | pine focus ring |
| `--font-display`, `font-display` | Instrument Sans | headings |
| `--font-ui`, `font-ui`, `font-sans` | Instrument Sans | UI/body |
| `--font-mono`, `font-mono` | DM Mono | data/labels |
| *(new)* `--font-wordmark` | Space Grotesk | "Radal." lockup only |
| `primary-foreground` (tailwind) | change to `var(--cta-tx)` | text on ink CTA |

**One Tailwind mapping changes meaning and must be edited in `tailwind.config.ts`**:
`primary: "var(--blue)"` → `primary: "var(--cta)"` and
`"primary-foreground": "#FFFFFC"` → `"var(--cta-tx)"`. Everything else keeps its
variable reference.

### 1.4 `tailwind.config.ts` changes (F1)

```ts
// colors (only the deltas):
primary: "var(--cta)",
"primary-foreground": "var(--cta-tx)",
// add:
brand: { DEFAULT: "var(--brand)", deep: "var(--brand-deep)", soft: "var(--brand-soft)" },
cta:   { DEFAULT: "var(--cta)", hover: "var(--cta-hover)", foreground: "var(--cta-tx)" },

fontFamily: {
  display:  ["Instrument Sans", "system-ui", "sans-serif"],
  ui:       ["Instrument Sans", "system-ui", "sans-serif"],
  sans:     ["Instrument Sans", "system-ui", "sans-serif"],
  mono:     ["DM Mono", "ui-monospace", "monospace"],
  wordmark: ["Space Grotesk", "system-ui", "sans-serif"],
},

fontSize: {   // Instrument Sans runs wider than Inter Tight; scale trimmed one notch
  display: ["30px",   { lineHeight: "1.1",  fontWeight: "600", letterSpacing: "-0.015em" }],
  h1:      ["24px",   { lineHeight: "1.1",  fontWeight: "600", letterSpacing: "-0.015em" }],
  h2:      ["19px",   { lineHeight: "1.2",  fontWeight: "600", letterSpacing: "-0.01em"  }],
  h3:      ["15px",   { lineHeight: "1.3",  fontWeight: "600" }],
  kpi:     ["25px",   { lineHeight: "1.05", fontWeight: "600", letterSpacing: "-0.02em"  }],
  body:    ["13.5px", { lineHeight: "1.5",  fontWeight: "400" }],
  label:   ["12.5px", { lineHeight: "1.4",  fontWeight: "500" }],
  caption: ["11.5px", { lineHeight: "1.4",  fontWeight: "400" }],
  mono:    ["12px",   { lineHeight: "1.4",  fontWeight: "500" }],
  "mono-sm": ["9.5px",{ lineHeight: "1.4",  fontWeight: "500", letterSpacing: "0.07em" }],
},

borderRadius: {  // the concentric ladder — values unchanged, now doctrine-backed
  card: "16px",  // var(--r-card)
  lg:   "12px",  // var(--r-well)
  md:   "10px",  // var(--r-seg)
  sm:   "8px",   // var(--r-ctl)
},

boxShadow: {
  elev:      "var(--elev)",
  "elev-hover": "var(--elev-hover)",
  overlay:   "var(--elev-overlay)",
  // legacy names kept, re-pointed:
  card: "var(--elev)", sm: "var(--elev)", lift: "var(--elev-hover)",
},
```

Also add the staged-entrance keyframes (replacing `fadeUp`'s curve):

```ts
keyframes: {
  enter: {
    from: { opacity: "0", transform: "translateY(12px)", filter: "blur(4px)" },
    to:   { opacity: "1", transform: "none",             filter: "blur(0px)" },
  },
  // keep accordion-down/up and dropIn as-is
},
animation: {
  enter: "enter 400ms cubic-bezier(0.2, 0, 0, 1) both",
  fadeUp: "enter 400ms cubic-bezier(0.2, 0, 0, 1) both",  // legacy name, new motion
},
```

### 1.5 `index.html` font links (F1)

Replace the Google Fonts link with:

```html
<link
  href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=DM+Mono:wght@400;500&family=Space+Grotesk:wght@500;600&display=swap"
  rel="stylesheet"
/>
```

Space Grotesk keeps only the two weights the wordmark uses. Inter Tight and IBM Plex Mono
are removed.

### 1.6 Global CSS behaviours (F1, in `index.css` after the tokens)

```css
::selection { background: color-mix(in srgb, var(--brand) 28%, transparent); }

:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}

.wordmark { font-family: var(--font-wordmark); }   /* h1–h4 keep var(--font-display) */

img { outline: 1px solid oklch(0 0 0 / 0.1); outline-offset: -1px; }
.dark img, :root[data-theme="dark"] img { outline-color: oklch(1 0 0 / 0.1); }
```

Two behaviours to fix while in the file:

1. **Remove `transition: background 0.3s ease, color 0.3s ease` from `body`** and every
   `transition-colors duration-300` sprinkled on shell chrome. Theme switching instead
   uses the better-ui suppression recipe: `ThemeProvider.toggleTheme` injects a
   `<style>*,*::before,*::after{transition:none!important}</style>`, flips the class,
   forces a reflow (`document.documentElement.offsetHeight`), and removes the style on the
   next frame. The flip snaps instead of smearing.
2. `@media (prefers-reduced-motion: reduce)` block stays as-is.

Motion primitives (`components/common/motion.tsx`, F1): `FadeUp`/`Stagger` change to the
doctrine values — 400 ms, `cubic-bezier(0.2, 0, 0, 1)`, `translateY(12px)` + 4 px blur,
100 ms stagger between semantic chunks (not per-row: cap the stagger at 5 chunks). `EASE`
export becomes `[0.2, 0, 0, 1]`. `CountUp` and `dropIn` (menus, 150 ms) stay.

### 1.7 Component-level changes (F1 owns every file in this subsection)

**`ui/button.tsx`** — the CTA becomes ink; press scale replaces the translate lift:

```
base:  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-sm
        text-label font-medium
        transition-[background-color,color,box-shadow,scale] duration-150 ease-out
        active:scale-[0.96]
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
        focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-app)]
        disabled:pointer-events-none disabled:opacity-50
        [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0"
variants:
  primary:     "bg-cta text-cta-foreground hover:bg-cta-hover"          // THE ink button
  secondary:   "bg-bg-surface text-text-secondary shadow-elev hover:shadow-elev-hover hover:text-text-primary"
  outline:     = secondary (alias; border removed — elevation IS the border)
  ghost:       "bg-transparent text-text-tertiary hover:bg-[color-mix(in_srgb,var(--ink)_6%,transparent)] hover:text-text-primary"
  teal:        "bg-brand-soft text-brand-deep hover:bg-[color-mix(in_srgb,var(--brand)_16%,transparent)]"  // brand-tinted, rare
  success:     "bg-[color-mix(in_srgb,var(--pos)_16%,transparent)] text-[var(--pos-text)] hover:bg-[color-mix(in_srgb,var(--pos)_22%,transparent)]"
  destructive: "bg-[color-mix(in_srgb,var(--neg)_14%,transparent)] text-[var(--neg-text)] hover:bg-[color-mix(in_srgb,var(--neg)_20%,transparent)]"
  link:        "text-brand underline-offset-4 hover:text-brand-deep hover:underline"
sizes: default "h-9 px-4" · sm "h-8 px-3 text-caption" · lg "h-10 px-5" · icon "h-9 w-9"
```

All variant names survive so no call site breaks. Every `hover:-translate-y-px` is gone —
hover feedback is `--elev-hover` or a fill change, press feedback is the scale. No dead
`brightness` filters.

**`ui/badge.tsx` + `StatusBadge`/`toneFor` in `pages/proposals/shared.tsx`** — badges
become the DM Mono small-caps chips of the style-lab (`.mk-badge`):

```
base: "inline-flex items-center whitespace-nowrap rounded-[5px] px-[7px] py-[2.5px]
       font-mono text-mono-sm font-medium uppercase"     // 9.5px, tracking .07em from fontSize token
variants (names kept):
  neutral/muted: "bg-bg-recessed text-text-muted"
  brand:         "bg-brand-soft text-brand-deep"
  action:        = brand (alias — blue is retired)
  success:       "bg-[color-mix(in_srgb,var(--pos)_16%,transparent)] text-[var(--pos-text)]"
  warn:          "bg-[color-mix(in_srgb,var(--warn)_16%,transparent)] text-[var(--warn-text)]"
  danger:        "bg-[color-mix(in_srgb,var(--neg)_14%,transparent)] text-[var(--neg-text)]"
  outline:       "shadow-[inset_0_0_0_1px_var(--line)] text-text-secondary"
```

No border, no pill radius (999 → 5px), no `text-transform` in copy — the transform is CSS
(better-typography). `StatusBadge`, `OriginBadge`, `ConfidenceBadge`, `DueBadge`, `MonoChip`
in `proposals/shared.tsx` inherit automatically; F1 only retunes `MonoChip` (already mono)
to `rounded-[5px] shadow-[inset_0_0_0_1px_var(--line)] bg-bg-surface` per `.mk-rut`.

**`ui/card.tsx`** — `border border-line … shadow-card` →
`shadow-elev` with **no border**; `interactive` → `hover:shadow-elev-hover` only (no
translate, no teal border); `transition-[box-shadow] duration-150 ease-out`. Padding stays
the caller's; when a card nests a well, the well uses `rounded-lg` (12) inside the card's
`rounded-card` (16) with 4 px inset — concentric.

**`components/common/DataTable.tsx`** — wrapper `border border-line … shadow-card` →
`shadow-elev`, no border (table RULES inside keep their borders — structure). In
`ui/table.tsx`: header cells `bg-bg-recessed font-mono text-mono-sm uppercase
text-text-muted` (the `.mk-table th` recipe); row hover
`hover:bg-[color-mix(in_srgb,var(--ink)_3%,transparent)]`; body cells
`transition-[background-color] duration-150`. Add a documented cell utility class
`.cell-num` (`font-mono tabular-nums text-right text-text-primary`) in `index.css` for
numeric columns — callers adopt it opportunistically.

**`components/common/KpiCard.tsx`** — the `.mk-kpi` recipe: label FIRST as
`font-mono text-mono-sm uppercase text-text-muted`; value `text-kpi font-display
tabular-nums` (25 px); hint `text-caption text-text-secondary` with `text-[var(--pos-text)]`
/ `text-[var(--neg-text)]` up/down tones. The icon chip becomes optional and brand-soft
only (`bg-brand-soft text-brand-deep`); drop the six-tone chip map (tone now colors the
hint, not a chip). Keep `CountUp`.

**`ui/input.tsx`** — inset ring instead of border, ground fill (`.mk-input`):
`shadow-[inset_0_0_0_1px_var(--line)] bg-bg-app rounded-sm h-9 px-3 text-body`,
`hover:shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--brand)_45%,var(--line))]`,
`focus-visible:shadow-[inset_0_0_0_1px_var(--brand)] focus-visible:ring-2 focus-visible:ring-[color-mix(in_srgb,var(--brand)_25%,transparent)]`,
`transition-[box-shadow] duration-150 ease-out`. Mobile: `text-base sm:text-body` so iOS
never zooms (16 px floor). `ui/select.tsx` trigger matches.

**`ui/tabs.tsx`** — the segmented control, concentric (`.mk-view`):
`TabsList`: `rounded-md bg-bg-recessed p-[2px] h-9` (track 10 = item 8 + 2).
`TabsTrigger`: `rounded-sm px-3 text-label text-text-muted
transition-[color,background-color,box-shadow] duration-150 ease-out
data-[state=active]:bg-bg-surface data-[state=active]:text-text-primary
data-[state=active]:shadow-elev`.

**`ui/dropdown-menu.tsx` / `ui/dialog.tsx` / `ui/sheet.tsx`** — content surfaces:
`shadow-overlay`, no border, `rounded-lg` (12) for menus, `rounded-card` (16) for dialogs.
Menus keep `dropIn` (150 ms).

**`pages/proposals/shared.tsx`** (F1 owns the whole file for this pass): besides badges —
`Section` drops its border for `shadow-elev`; `SoonButton`/`DisabledHint` unchanged in
behaviour; `EmptyState` gets `text-wrap: pretty` on its hint (empty states teach —
decision 6 — so every `EmptyState` call keeps `title` + `hint` + one action).

**Topbar/AppShell icon buttons** (owned by F2, values specified here): the 38 px bordered
squares become `shadow-elev bg-bg-surface rounded-sm` with `hover:shadow-elev-hover
hover:text-text-primary active:scale-[0.96]`; the avatar button loses the teal→blue
gradient for `bg-brand-soft text-brand-deep font-mono` (the `.mk-user i` recipe).

---

## 2. (b) The single context-switching sidebar

### 2.1 Structure and mode detection

**One rail, 264 px** (`w-[264px]`), background `--bg-sidebar` (= ground), right hairline
`border-r border-line` (structure — stays a border). The double sidebar is gone:
`GroupShell` no longer renders an `<aside>`.

```
components/layout/Sidebar.tsx        — the shell: brand row, tenant chip, MODE SWITCH, user card
components/layout/SidebarGlobal.tsx  — global-mode content (new file, extracted)
components/groups/GroupSidebar.tsx   — REUSED as the group-mode content (adapted, §2.3)
```

Mode detection in `Sidebar.tsx` (it sits outside the route tree, so `useParams` is not
available):

```ts
const m = pathname.match(/^\/groups\/(\d+)(\/|$)/);
const groupId = m ? Number(m[1]) : null;   // "/groups" and "/groups/new" stay GLOBAL
```

`groupId !== null` → group mode. The `Sidebar` itself never remounts (it lives in
`AppShell`, above the router's page swaps), and `GroupSidebar` is keyed by `groupId` only
— navigating *within* a group changes props, not identity, so the tree keeps its scroll,
its expanded map (persisted under `radal.tree.expanded`) and its query cache. **This is
the no-remount guarantee that used to live in `GroupShell`; it moves up one level and must
be stated in the Sidebar's header comment.**

Mode switch animation: cross-fade + 8 px slide, 150 ms ease-out, `transition-property:
opacity, transform` — never a staged entrance (this is a high-frequency interaction).

### 2.2 Global mode — exact section content, top to bottom

| # | Section | Content | Gate |
|---|---|---|---|
| 0 | Brand | mark + `Radal.` wordmark (`font-wordmark`) | — |
| 1 | Tenant chip | §2.4 | — |
| 2 | **GRUPOS** | ≤8 groups from `useNavigator()` with `open_count` chips (no nested vigencias in global mode — the vigencia level lives in group mode now, which keeps the global rail short); then `Ver todos los grupos` → `/groups`; `+ Nuevo grupo` → `/groups/new` | `Groups:View`; create row `Groups:Create` |
| 3 | **Analítica** → `/analytics` | single entry, `BarChart3` icon | `Dashboard:View` (tabs re-gate individually, §4) |
| 4 | **Agente** → `/agent` | single entry, `Bot` icon — replaces "Lector documental" + "Comparador" | `Dashboard:View` |
| 5 | **Compañías** → `/insurers` | `Building2` icon | `Insurers:View` |
| 6 | **Más** (collapsed, persisted under the existing `navKeys.section("more")`) | Inicio `/dashboard` · Leads `/leads` · Expedientes `/cases` · Clientes `/clients` · Colocaciones `/placements` · Cotizaciones `/quotes` · Propuestas `/proposals` · Pólizas `/policies` · Inspecciones `/inspections` · Siniestros `/claims` · Ofertas `/offerings` | each row its own module `View` |
| 7 | **Administración** | Configuración `/settings` | `isBrokerAdmin()` |
| 8 | User card | initials block `bg-brand-soft text-brand-deep font-mono`, name + role | — |

Rules carried over verbatim: while the permission matrix is in flight render only
Dashboard-gated anchors (no popping); the disabled "pronto" rows (Finanzas, Reportes)
move INTO the collapsed Más section, still greyed with the chip — visible, never dead.
`/ai/reader` and `/ai/comparator` leave the nav but their routes stay in `App.tsx`
(deep links keep working; the reader is reachable from documents — decision 5).

Row anatomy (Porcelana active state, from `.mk[data-dir="porcelana"] .mk-nav.on`):

```
row:    flex items-center gap-[9px] rounded-sm px-3 min-h-[32px] text-label
        text-text-tertiary transition-[background-color,color,box-shadow] duration-150 ease-out
hover:  bg-[color-mix(in_srgb,var(--ink)_5%,transparent)] text-text-primary
ACTIVE: bg-bg-surface shadow-elev text-brand font-medium     ← white card + pine text,
                                                                NOT a tint fill
count:  ml-auto font-mono text-[10px] tabular-nums text-text-muted
icons:  15–17px, strokeWidth 1.5 (matches 400–500 text — better-ui)
section label: font-mono text-mono-sm uppercase text-text-muted px-3 pt-4 pb-1.5
```

### 2.3 Group mode — content and tree density rules

Top to bottom:

1. **Back affordance**: `← Grupos` — a ghost row (`ArrowLeft` 15 px + label,
   `text-caption text-text-muted hover:text-text-primary`) navigating to `/groups`.
2. **Group header** (from `useGroupTree`): group name (`font-display text-[15px]
   font-semibold`, links to `/groups/:id`), RUT chips (`MonoChip` per client, full legal
   name on the tooltip), archived badge when applicable.
3. **Quick actions**: `Descargar expediente` (existing `DownloadArchiveButton`, secondary)
   and **`Nueva cuenta`** — the rail's ONE primary ink button, full width
   (`Groups`/`CaseFiles:Create`-gated, disabled-with-reason otherwise). The help popover
   stays beside the download button.
4. **THE TREE** — `GroupSidebar`'s existing tree, unchanged in information architecture:
   vigencias → ramos → antecedentes / cotizaciones / propuestas / pólizas →
   endoso / cobranza / siniestros → renovación. All existing behaviours survive:
   first-mount auto-expand of the latest vigencia + the URL's branch, persisted expansion,
   historic chips, period locks, server-reasoned renewal rows.
5. **Pinned bottom** (above the user card, below a hairline): **Analítica** and
   **Agente** rows — the two global anchors the broker still needs mid-group. Same row
   style as global mode.

Density rules for the 264 px rail (the tree previously had 272 px):

- Rows `text-[12.5px]`, `py-[5px]`, indent **12 px per level** (was 13 — change the
  constant in `TreeNode.indentStyle`), gutter 8 px. Max visible depth is 4 (policy
  children); nothing changes structurally.
- Captions (full dates with months) keep their own wrapped line — never truncate a
  vigencia's months (the team reads vigencias by month).
- Active tree row uses the SAME white-card active treatment as nav rows (§2.2), replacing
  the teal-tint `ROW_ACTIVE` in `TreeNode.tsx` and `Sidebar` row constants.
- Count chips: `font-mono text-[10px] tabular-nums`; `0` renders greyed, `null` renders
  nothing (existing contract).
- Progressive disclosure over dense trees (decision 6): collapsed by default below the
  vigencia level except the branch the URL implies — already the behaviour; keep it.

### 2.4 The tenant chip (redesigned)

The current bordered teal pill is replaced by the style-lab `.mk-tenant` recipe:

```
mx-2 mt-3 mb-4 inline-flex items-center gap-[7px] self-start
rounded-full px-[11px] py-[5px] shadow-elev
font-mono text-[9.5px] uppercase tracking-[0.1em] text-text-muted whitespace-nowrap
· leading 6px dot: h-1.5 w-1.5 rounded-full bg-brand (no glow ring)
```

Elevation ring instead of a border + tint; muted mono type instead of shouting semibold.
In group mode the chip stays (the tenant is the constant; the group header sits below it).

### 2.5 Mobile (below `lg`)

The single sidebar becomes the existing `Sheet` behind the topbar hamburger — unchanged
mechanism in `AppShell`. Because the `Sidebar` is now mode-aware, **the sheet shows the
group tree when the user is inside a group** — so `GroupShell`'s own sheet + trigger row
is deleted (one sheet, one trigger, one implementation). Sheet width 286 px, closes on
navigation via the existing location-watch in `AppShell`.

### 2.6 What happens to the files

| File | Fate |
|---|---|
| `components/layout/Sidebar.tsx` | Rewritten: shell + mode switch; global content extracted to `SidebarGlobal.tsx` |
| `components/layout/SidebarGlobal.tsx` | NEW — the §2.2 content |
| `components/groups/GroupSidebar.tsx` | KEPT — becomes the group-mode content; gains the back row, pinned Analítica/Agente rows; loses nothing else |
| `components/groups/GroupShell.tsx` | KEPT as the layout route: 404 guard + the `max-w-[1240px]` page column. **Its `<aside>`, its `Sheet`, its mobile trigger row are deleted.** The `--topbar-h` sticky comment moves to Sidebar. |
| `components/groups/TreeNode.tsx` | Row-style + indent retune only (§2.3) — owned by F2 |
| `components/layout/AppShell.tsx` | Sidebar stays `sticky top-0 h-screen`; sheet unchanged; icon-button restyle §1.7 |
| `components/layout/Topbar.tsx` | Restyle only (search field per `.mk-k`: panel bg, `shadow-elev`, `⌘K` kbd chip); no structural change |
| `lib/treeState.ts` | Unchanged (both nav + tree expansion stores keep working) |

---

## 3. (c) The Journey — one visualization, brand-tied

### 3.1 Component

**`Journey`** at **`frontend/src/components/common/Journey.tsx`** (new file; owned by F3).

```ts
export interface JourneyProps {
  kind: CaseFileKind;                       // account | renewal | endorsement | collection | claim
  stage: CaseStage;
  /** From GET /case-files/{id}/transitions — [] when not fetched (compact cards). */
  transitions?: CaseTransitionOption[];
  variant: "hero" | "compact";
  /** Gate + mutation wiring, hero only (same contract JourneyStrip had). */
  canTransition?: boolean;
  isPending?: boolean;
  pendingStage?: CaseStage | null;
  onTransition?: (stage: CaseStage) => void;
  className?: string;
}
```

The component is presentation only: **every enabled/disabled decision comes from
`transitions`** (server reasons authoritative), never from a rule reimplemented here.
`CASE_STAGE_FLOW` from `api/types.ts` supplies the micro-stage order; the macro-phase
table below lives as a constant **inside `Journey.tsx`** (not in `types.ts`, to keep file
ownership clean).

### 3.2 Macro-phase grouping — all 29 `CaseStage` values

The broker thinks in six macro-phases pre-sale plus one short rail per post-sale kind.
Every stage maps to exactly one phase; the phase is what the stroke draws, the stage is
what the current-node label says.

**`kind: account` (and the account portion of `renewal`)** — 6 phases / 14 stages:

| Macro-phase (i18n key `journey.phases.*`) | Stages (in rail order) |
|---|---|
| `antecedentes` — "Antecedentes" | `lead`, `intake`, `pre_underwriting` |
| `mercado` — "Mercado" | `technical_basis`, `market_submission`, `quotes_received` |
| `comparacion` — "Comparación" | `comparison`, `insured_decision` |
| `propuesta` — "Propuesta" | `proposal_issued`, `ratified` |
| `poliza` — "Póliza" | `policy_issued`, `mirror_validation` |
| `vigente` — "Vigente" | `active` |

**`kind: renewal`** — prepends one phase, then re-enters the account phases:
`renovacion` — "Renovación" | `renewal_review` → then the 6 account phases above.

**Post-sale rails** (each stage IS its own node — the rails are already macro-grained):

| Kind | Phase label | Stages |
|---|---|---|
| `endorsement` | "Endoso" | `endorsement_requested`, `endorsement_proposed`, `endorsement_issued`, `endorsement_applied` |
| `collection` | "Cobranza" | `collection_scheduled`, `collection_in_progress`, `collection_overdue`, `collection_settled` — plus `collection_suspended` drawn as a **branch node** below the line between `in_progress` and `settled` (it is a side state, not a step; when current, the whole rail renders in warn tone) |
| `claim` | "Siniestro" | `claim_reported`, `claim_adjusting`, `claim_preliminary`, `claim_final`, `claim_settled` |

`closed` (the 29th value) is terminal-from-anywhere and is **not a node**: when
`stage === "closed"` the stroke renders fully muted with a `Cerrado` chip at the end; in
the hero variant the close affordance is a ghost button right of the rail (exactly the
role it has in `JourneyStrip` today, driven by the `closed` option's `allowed`/`reason`).

Overdue/suspended tone rule: `collection_overdue` and `collection_suspended` render their
current node in `--warn`/`--neg` respectively — the only stages that recolor the node
(status is meaning; one hue, one meaning).

### 3.3 Rendering — the continuous stroke ringed by nodes

Deliberate brand tie-in: the Radal mark is one continuous stroke (the shared record)
ringed by nodes (the roles). The Journey is the same idea horizontally.

One `<svg>` spanning the component width (`preserveAspectRatio="none"` for the line
layer; nodes positioned in an HTML overlay so text never distorts):

- **The stroke**: a single horizontal path, `stroke-width: 2.5`, `stroke-linecap: round`,
  y-centered on the node row. Two layers: the full path in `var(--line)` (the road), and
  the completed portion in `var(--brand)` (`#0E7C70`), drawn 0 → current node.
  On mount (hero only) the pine segment animates `stroke-dashoffset` → 0 over 400 ms
  `cubic-bezier(0.2, 0, 0, 1)`, one-shot keyframe; disabled under
  `prefers-reduced-motion`.
- **Nodes** (one per macro-phase; per stage on post-sale rails):
  - *done*: 10 px circle filled `var(--brand)`. No check glyph — a filled pine dot IS
    "done"; glyphs at this size read as noise.
  - *current*: 14 px node — pine fill with a 3 px ring of `var(--brand-soft)` **plus** a
    white gap ring (`0 0 0 2px var(--bg-surface), 0 0 0 5px var(--brand-soft)`), pulsing
    once on mount (scale 0.25→1 with 4px→0 blur — the icon-transition values).
  - *upcoming*: 8 px hollow node — `var(--bg-surface)` fill, `shadow-[0_0_0_1.5px_var(--line)]`.
- **Labels**: each node's phase label beneath it, `text-caption`; done = `text-text-muted`,
  current = `text-text-primary font-medium`, upcoming = `text-text-muted opacity-70`.
  `white-space: nowrap`; the row scrolls horizontally inside its own container below `md`
  (the page never scrolls sideways).
- **The current block (hero only)**, left-aligned under the rail:
  - Stage name: the *micro*-stage (`cases:stages.${stage}`) in `text-h3` — the phase says
    where, the stage says exactly what.
  - **"Qué sigue"**: one line derived from `transitions` — the first option in rail order
    with `allowed: true` renders as
    `Qué sigue: {t(`journey.next.${to_stage}`)}` next to an ink **`Avanzar a {stage}`**
    button (THE primary action of the account page — nothing else on that page is ink).
    When no option is allowed, the line shows the blocking reason of the next rail stage
    verbatim from the server (`option.reason`), muted with an `Info` icon — the user knows
    what they are working on at a glance, and what unblocks the next step.
  - Every *other* offered-but-disallowed step is reachable via an `Otras etapas ▾`
    ghost dropdown listing all options; disallowed items render disabled with
    `option.reason` as their tooltip (server reason, always).
- **Hero micro-detail**: within the current macro-phase, the phase's micro-stages render
  as small ticks on the stroke segment (3 px), so "Mercado · En el mercado (2/3)" is
  legible without adding nodes everywhere.

**`variant: "compact"`** (for cards): height ~28 px, stroke 2 px, nodes 6/10/6 px, labels
hidden except the current phase's, rendered inline right of the rail as
`{phase} · {stage}` in `text-caption`. No buttons, no transitions fetch (`transitions`
omitted), not interactive beyond the card's own link. Purpose: at-a-glance progress on
every account/ramo card.

### 3.4 States (summary table)

| State | Source | Rendering |
|---|---|---|
| done | index < current in `CASE_STAGE_FLOW[kind]` | pine-filled node, pine stroke segment |
| current | `stage` | large ringed pine node, label emphasized, stage name + qué-sigue below (hero) |
| next-allowed | option with `allowed: true` | ink `Avanzar` button (hero); compact: none |
| blocked | option with `allowed: false` | node stays upcoming; reason as tooltip AND as the qué-sigue line when it is the immediate next |
| unreachable | stage in rail with no option | hollow node, `journey.notReachable` tooltip |
| closed | `stage === "closed"` | whole rail muted + `Cerrado` chip |
| suspended/overdue | collection side states | warn/danger node tone |
| no permission | `canTransition === false` | rail fully rendered, all actions disabled with `journey.noPermission` |

### 3.5 Mount points — what replaces `JourneyStrip` where

| Surface | Variant | Wiring |
|---|---|---|
| `components/cases/CaseDetailBody.tsx` (the account page body used by `/groups/:id/accounts/:caseId` and `/cases/:caseId`) | **hero** | exactly the props `JourneyStrip` receives today (`useCaseFileTransitions`, `useTransitionCaseFile`, `CaseFiles:Submit` gate). The strip's `Card` wrapper is replaced by a plain section — the Journey is the page's hero, not a card among cards. |
| `pages/groups/overview.tsx` — each ramo/account card in the Vigencias view | **compact** | `kind` + `stage` from the tree payload; no fetch |
| `pages/groups/period.tsx` — per-ramo rows | **compact** | same |
| `pages/groups/policy.tsx` — each post-sale child case row (endoso/cobranza/siniestro) | **compact** | `kind` + `stage` from `usePolicyCaseFiles` |

`components/common/JourneyStrip.tsx` is **deleted** in the same change that swaps
`CaseDetailBody` (its only consumer). The pills-with-arrows presentation does not survive
anywhere. `StageBadge` (`pages/groups/shared.tsx`) stays for table cells where a rail
would be noise.

---

## 4. (d) `/analytics` — where the general lists live

### 4.1 Route and page

- Route: `analytics` inside `StandardPage` (added in `App.tsx`, F4).
- Files: `pages/analytics/index.tsx` (header, KPI row, tab shell) plus one file per tab
  under `pages/analytics/` (`accounts.tsx`, `quotes.tsx`, `proposals.tsx`, `policies.tsx`,
  `postsale.tsx`). The active tab persists in the URL (`?tab=`), so a filtered view is a
  shareable link.
- Header: `PageHeader` "Analítica de cartera" + a KPI row from `useCaseFilesSummary()`
  (open accounts, by-stage counts, overdue) — reusing `KpiCard`.

### 4.2 Tabs, data sources, gates

Tabs use the §1.7 segmented control. **A tab whose module lacks `View` does not render**
(the sidebar entry itself is Dashboard-gated; per-tab gating handles narrow roles like
`broker_inspector`, who sees only what their matrix allows).

| Tab (i18n `analytics:tabs.*`) | Hook / endpoint | Gate | Default columns |
|---|---|---|---|
| `accounts` — Cuentas | `useCaseFiles({ kind: ["account","renewal"] })` + `useCaseFilesSummary()` | `CaseFiles:View` | grupo/cliente · ramo · vigencia · etapa (`StageBadge`) · estado · prima (when won) · ref |
| `quotes` — Cotizaciones | `useQuotes()` | `Quotes:View` | cliente · objeto · declarado UF · enviada · vence (`DueBadge`) · estado · propuestas |
| `proposals` — Propuestas | `useProposals()` | `Proposals:View` | aseguradora (`OriginBadge`) · cotización · prima total UF · tasa · confirmada (`ConfidenceBadge`) · estado |
| `policies` — Pólizas | `usePolicies()` | `Policies:View` | nº póliza · cliente · aseguradora · vigencia · prima total UF · estado |
| `postsale` — Post-venta | segmented sub-toggle (Endosos `useEndorsements()` / Cobranzas `useCollectionPlans()` / Siniestros `useClaims()`) | `Endorsements` / `Collections` / `Claims` `:View` per sub-view; the tab renders if ANY of the three is granted, and the sub-toggle hides the others | per sub-view; deltas signed for endosos, `gross_amount_uf` ledger totals for cobranzas |

Every table is the existing `DataTable`; every row navigates to the detail route that
already exists (`/groups/...` when the row carries a group context via the case file,
otherwise the flat route — `/quotes/:id`, `/proposals/:id`, `/policies/:id`,
`/claims/:id`, `/endorsements/:id`, `/collections/:id`). Money cells use `uf()` from
`proposals/shared.tsx` with `.cell-num`.

### 4.3 Filters and the view-mode toggle

- **Filter chips** above each table (dashed-outline pill per `.mk-filter`:
  `rounded-full border border-dashed border-line px-3 py-1 text-caption
  text-text-secondary`). Each chip opens a small popover (Select) and only offers filters
  the endpoint honours server-side: Cuentas → `stage[]`, `status[]`, `insurance_line_id`;
  Cotizaciones → `status`, `priority`; Propuestas → `status`, `origin`, `is_confirmed`,
  `insurer_id`; Pólizas → `status[]`, `insurer_id`; Post-venta → `status[]`,
  `policy_id`. A filter with no server parameter is **not rendered** (no client-side
  pretend-filtering). Active chips render solid brand-soft; `+ filtro` adds one.
- **View-mode toggle** (segmented `Tabla | Tablero`) on the **Cuentas** tab only, where a
  board is meaningful: Tablero renders columns per macro-phase (§3.2 — six columns +
  post-sale swim-count footer) with compact cards (title · ramo chip · `Journey compact`).
  Data: the same `useCaseFiles` page grouped client-side by the stage→phase map; the
  toggle persists in `?view=`. Other tabs are table-only in this pass (no dead toggle).

### 4.4 What leaves the sidebar, what remains

- **Leaves**: the COMERCIAL section (Cotizaciones, Propuestas), Cartera (Pólizas), and the
  two AI entries (replaced by Agente). Replaced by the single **Analítica** entry.
- **Remains under Más** (§2.2): Inicio, Leads, Expedientes, Clientes, Colocaciones,
  Cotizaciones, Propuestas, Pólizas, Inspecciones, Siniestros, Ofertas — every old route
  keeps working and stays one click away; deep links unaffected. Nothing is hidden
  without a path (rule 3).

---

## 5. (e) Work packages — ordered, exclusive file ownership

Order: **F1 → F6 → (F2 ∥ F3 ∥ F4)**. F1 lands the tokens (no new i18n keys — pure
restyle). F6 lands every new key in both locales next, so F2/F3/F4 never touch a locale
file. F2, F3, F4 then build in parallel with zero shared files.

Acceptance for every package: `npx tsc --noEmit && npm run build` clean; the dynamic-key
check (§6 note) clicked through on screen; dark toggle exercised on every screen touched.

### F1 — Tokens & primitives (the Porcelana skin)

Owns exclusively:

```
frontend/index.html
frontend/src/index.css
frontend/tailwind.config.ts
frontend/src/providers/ThemeProvider.tsx        (theme-switch transition suppression)
frontend/src/components/ui/button.tsx
frontend/src/components/ui/badge.tsx
frontend/src/components/ui/card.tsx
frontend/src/components/ui/input.tsx
frontend/src/components/ui/select.tsx
frontend/src/components/ui/tabs.tsx
frontend/src/components/ui/table.tsx
frontend/src/components/ui/dialog.tsx
frontend/src/components/ui/dropdown-menu.tsx
frontend/src/components/ui/sheet.tsx
frontend/src/components/ui/accordion.tsx  avatar.tsx  label.tsx  skeleton.tsx  sonner.tsx  tooltip.tsx
frontend/src/components/common/DataTable.tsx
frontend/src/components/common/KpiCard.tsx
frontend/src/components/common/PageHeader.tsx
frontend/src/components/common/motion.tsx
frontend/src/pages/proposals/shared.tsx          (badges/formatters — 38 consumers)
```

Deliverable: the whole app renders Porcelana (light + dark) through the variable seam
with primitives reshaped per §1.7. F1 does NOT touch layout components, group files,
pages, `App.tsx`, or locales.

### F6 — i18n keys (lands second; owns ALL locale files)

Owns exclusively: `frontend/src/locales/es/**` and `frontend/src/locales/en/**`, plus
`frontend/src/i18n/index.ts` **only if** the glob does not auto-register `analytics.json`
(the README says it does — verify; if it does, F6 touches no `.ts` at all).

Every new key group, `es` authoritative, `en` mirrored 1:1:

**`common.json → nav.*`** (F2 consumes):
`nav.analytics` ("Analítica") · `nav.agent` rename check (exists: "Agente IA" → keep or
shorten to "Agente"; decide here, once) · `nav.backToGroups` ("← Grupos" label, "Grupos")
· `nav.sections.pinned` ("Accesos") · removal-safe: `nav.reader`/`nav.comparator` KEPT
(still used by `/ai/*` pages' titles).

**`cases.json → journey.*`** (F3 consumes; `journey.noPermission`, `journey.blocked`,
`journey.notReachable` already exist — keep):
- `journey.phases.antecedentes|mercado|comparacion|propuesta|poliza|vigente|renovacion|endoso|cobranza|siniestro`
- `journey.whatNext` ("Qué sigue") · `journey.advanceTo` ("Avanzar a {{stage}}") ·
  `journey.otherStages` ("Otras etapas") · `journey.closedChip` ("Cerrado") ·
  `journey.progress` ("{{done}}/{{total}}")
- `journey.next.*` — one line per TARGET stage, all 29 + `closed` (e.g.
  `journey.next.market_submission`: "enviar la carpeta al mercado";
  `journey.next.comparison`: "construir el comparativo con las propuestas confirmadas").
  These are the qué-sigue hints; the server's *blocking* reasons are shown verbatim and
  need no keys.

**`analytics.json`** (new namespace; F4 consumes):
- `title`, `subtitle`
- `tabs.accounts|quotes|proposals|policies|postsale`
- `views.table|board` · `postsale.endorsements|collections|claims`
- `kpis.openAccounts|inMarket|renewals90|overdueCollections`
- `filters.state|line|status|priority|origin|confirmed|insurer|add|clear`
- `columns.*` — every column header per tab (group, client, line, period, stage, status,
  premium, reference, object, declared, sentAt, dueAt, proposalsCount, insurer, quote,
  totalPremium, rate, confirmed, policyNumber, deltas, ledgerTotal, claimNumber, ruling…)
- `empty.accounts|quotes|proposals|policies|postsale` + one `emptyHint.*` each (empty
  states teach: each hint says what creates the first row and where)
- `board.postsaleFooter` ("{{count}} casos de post-venta")

**`accounts.json`** (F2 consumes): `nav.pinnedAnalytics` not needed (reuses
`common:nav.analytics`); add `tree.backToGroups` if the back row lives in the accounts
namespace instead — F6 decides ONE home for it and documents it in the PR.

⚠️ Dynamic-key reminder for every package: `t(\`journey.phases.${phase}\`)`,
`t(\`analytics:columns.${col}\`)` and `t(\`journey.next.${stage}\`)` pass `tsc` and build
while missing — F6 must land the COMPLETE sets above, and F2/F3/F4 must click every
screen. The 29-member `CaseStage` and the phase map are the highest-risk sets.

### F2 — Shell & the single sidebar

Owns exclusively:

```
frontend/src/components/layout/AppShell.tsx
frontend/src/components/layout/Sidebar.tsx
frontend/src/components/layout/SidebarGlobal.tsx      (new)
frontend/src/components/layout/Topbar.tsx
frontend/src/components/groups/GroupShell.tsx         (aside/sheet removal)
frontend/src/components/groups/GroupSidebar.tsx       (group-mode adaptation)
frontend/src/components/groups/TreeNode.tsx           (row style + 12px indent)
frontend/src/components/common/SearchDropdown.tsx     (topbar field restyle)
frontend/src/lib/treeState.ts                         (only if a new section key is needed)
```

### F3 — Journey & group pages

Owns exclusively:

```
frontend/src/components/common/Journey.tsx            (new)
frontend/src/components/common/JourneyStrip.tsx       (DELETED here)
frontend/src/components/cases/CaseDetailBody.tsx
frontend/src/pages/groups/overview.tsx
frontend/src/pages/groups/period.tsx
frontend/src/pages/groups/policy.tsx
frontend/src/pages/groups/account.tsx
frontend/src/pages/groups/shared.tsx
```

### F4 — Analytics & routes

Owns exclusively:

```
frontend/src/App.tsx                                   (adds the /analytics route; nothing else)
frontend/src/pages/analytics/index.tsx                 (new)
frontend/src/pages/analytics/accounts.tsx  quotes.tsx  proposals.tsx  policies.tsx  postsale.tsx  (new)
```

F4 adds **no API files** — every tab reads existing hooks. If a query-key or list-param
gap appears, F4 raises it; it does not edit `api/*`.

### Contested files — resolved assignments

| File | Wanted by | Assigned to | Why |
|---|---|---|---|
| `pages/proposals/shared.tsx` | F1 (badges) / F3, F4 (consumers) | **F1** | badge/format primitives live here; consumers only import |
| `components/groups/GroupSidebar.tsx` | F2 (sidebar content) / F3 (group pages) | **F2** | it is now nav chrome; F3 touches pages only |
| `components/groups/TreeNode.tsx` | F1 (style) / F2 (density) | **F2** | one owner for the whole rail look |
| `components/groups/GroupShell.tsx` | F2 (aside removal) / F3 (page frame) | **F2** | F3's pages render inside its Outlet untouched |
| `App.tsx` | F2 (nav targets) / F4 (route) | **F4** | nav rows are plain links; only F4 adds a route |
| `components/cases/CaseDetailBody.tsx` | F1 (restyle) / F3 (Journey swap) | **F3** | the swap dominates; F3 applies token classes while there |
| `components/common/KpiCard.tsx` | F1 (restyle) / F4 (analytics KPIs) | **F1** | F4 consumes the finished component |
| `components/common/DataTable.tsx` | F1 / F4 | **F1** | same |
| `components/common/motion.tsx` | F1 / F3 (journey animation) | **F1** | Journey keeps its stroke animation local in `Journey.tsx` |
| `locales/**` | everyone | **F6** | single owner; complete inventories above |
| `lib/permissions.ts` | none | untouched | no module changes in this pass |
| `lib/api.ts` | none | **FROZEN** | OAC contract — never part of any package |
| `pages/style-lab/index.tsx` | none | untouched | stays as the design record |

---

## 6. Decision-6 checklist (applies to every package's review)

- Fewer choices per screen: one ink button per view (`Nueva cuenta` in the group rail,
  `Avanzar` on the account page, the create action on a list page — never two).
- The working context always visible: tenant chip + group header + tree in group mode;
  breadcrumbs unchanged.
- Progressive disclosure: Más collapsed, tree branches collapsed below the active path,
  board view opt-in.
- Empty states teach: every `EmptyState` carries `title` + `hint` naming the action that
  creates the first row + that action's button (gated, disabled-with-reason).
- Server-driven permissions and no dead buttons remain law: nothing in this spec renders
  a control the server would 403, and nothing disappears without a reason or a path.
