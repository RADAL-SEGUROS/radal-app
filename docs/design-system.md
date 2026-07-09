# Radal Design System — "Aqua Spectrum"

Canonical visual spec for implementers of the Radal **corredora** web app. This
document is the source of truth for color tokens, typography, layout ratios,
component usage, logo handling, and the color logic for the coverage analyzer
and días-restantes indicators.

UI copy is **Spanish (Chile)**. Money is expressed in **UF**. All strings go
through `t()` (see `docs/i18n.md`).

---

## 1. Color tokens

Aqua Spectrum is built on a warm-neutral **ink/paper/bone** ground with a
**teal** brand accent, a **blue** action accent, and a **lime** affirmative
accent. Neutrals carry the interface; the three accents each own exactly one
job.

### 1.1 Token table

| Token          | Role                                  | Light value | Dark value | Notes |
|----------------|---------------------------------------|-------------|------------|-------|
| `--ink`        | Primary text / darkest surface        | `#0B1418`   | `#0B1418`  | Near-black teal-tinted. Text on light; base surface on dark. |
| `--ink-2`      | Secondary text / raised dark surface  | `#16242B`   | `#16242B`  | Card/panel surface in dark theme. |
| `--ink-3`      | Slate — muted text / lines on dark    | `#324049`   | `#324049`  | Muted/secondary text, dark-theme borders. |
| `--paper`      | App background (light)                | `#EEF2EF`   | `#0B1418`  | Warm off-white ground. Dark theme maps to ink. |
| `--paper-2`    | Recessed / hover background (light)   | `#E2E8E4`   | `#16242B`  | Table zebra, hover fills, wells. |
| `--bone`       | Card / elevated surface (light)       | `#FFFFFB`   | `#16242B`  | Cards, dialogs, popovers sit on bone. |
| `--teal`       | PRIMARY brand / active nav            | `#14B8A6`   | `#14B8A6`  | Active nav item, brand marks, focus ring. |
| `--teal-deep`  | Pine — teal hover/pressed             | `#0E7C70`   | `#0E7C70`  | Hover on teal elements. |
| `--teal-soft`  | Mist — teal tint/backgrounds          | `#B8EBE5`   | `#0E7C70`  | Soft teal chips/badges; dark uses pine. |
| `--blue`       | ACTION — primary CTAs, links          | `#2563EB`   | `#2563EB`  | Primary buttons, hyperlinks. |
| `--blue-deep`  | Blue hover/pressed                    | `#1A47B8`   | `#1A47B8`  | Hover on blue buttons/links. |
| `--lime`       | SUCCESS / affirmative                 | `#84CC16`   | `#84CC16`  | Positive confirmation, óptima coverage. |
| `--line`       | Borders / dividers (light)            | `#CFD8D2`   | `#324049`  | Hairlines, table rules, card borders. |

### 1.2 Semantic aliases (derive, do not hardcode)

Use these named signals in components rather than reaching for raw accents:

| Semantic token     | Maps to      | Meaning                                   |
|--------------------|--------------|-------------------------------------------|
| `--signal-success` | `--lime`     | Positive / óptima / affirmative           |
| `--signal-action`  | `--blue`     | Primary action, links                     |
| `--signal-brand`   | `--teal`     | Active nav, brand, focus                  |
| `--signal-warn`    | `#D97706`    | Ámbar — attention / near expiry           |
| `--signal-danger`  | `#DC2626`    | Rojo — critical / overdue / <=30D / <=4D  |
| `--signal-muted`   | `--ink-3`    | Gris — neutral, "no urgency"              |

---

## 2. Typography

Three font roles, each with a fixed job. Never mix roles.

| Role      | Family          | Used for                                                        |
|-----------|-----------------|-----------------------------------------------------------------|
| Display   | **Space Grotesk** | Headings (h1–h4), the `Radal.` wordmark, KPI headline numbers. |
| UI / body | **Inter Tight**   | Buttons, nav, labels, paragraphs, table cell text, form fields. |
| Mono      | **IBM Plex Mono** | IDs (POL-2024-0072, REN-001), codes, RUT, UF metadata labels, tabular figures. |

### 2.1 Google Fonts load

Add to `index.html` `<head>` (or import in `index.css`):

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=Inter+Tight:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
```

### 2.2 Type scale

Modular scale (~1.25), Inter Tight for body, Space Grotesk for display sizes.

| Token        | Size / line-height | Weight | Font          | Use                          |
|--------------|--------------------|--------|---------------|------------------------------|
| `text-display` | 32 / 38          | 600    | Space Grotesk | Page title / greeting        |
| `text-h1`    | 28 / 34            | 600    | Space Grotesk | Section / detail title       |
| `text-h2`    | 22 / 28            | 600    | Space Grotesk | Card group heading           |
| `text-h3`    | 18 / 24            | 500    | Space Grotesk | Card title, accordion header |
| `text-kpi`   | 34 / 38            | 600    | Space Grotesk | KPI headline number          |
| `text-body`  | 15 / 22            | 400    | Inter Tight   | Default body / cell text     |
| `text-label` | 13 / 18            | 500    | Inter Tight   | Form labels, nav items       |
| `text-caption` | 12 / 16          | 400    | Inter Tight   | Helper / secondary caption   |
| `text-mono`  | 13 / 18            | 500    | IBM Plex Mono | IDs, RUT, UF codes           |
| `text-mono-sm` | 11 / 15          | 500    | IBM Plex Mono | Small metadata / badge codes |

---

## 3. Composition rules

### 3.1 The 60 / 30 / 10 rule
- **60% neutral** — paper/bone/ink surfaces and line borders form the bulk.
- **30% support** — secondary text (ink-2/ink-3), muted fills (paper-2),
  soft-teal chips.
- **10% accent** — the three signal colors (blue/teal/lime) plus warn/danger.
  Accents are punctuation, not paint.

### 3.2 One signal color per zone
Any single visual zone (a card, a toolbar, a table header) carries **at most one
accent signal**. If a card has a blue primary button, its status badge does not
also shout in a second accent — demote it to a neutral or soft-teal tint.

### 3.3 Accent ownership (do not cross-wire)
- **Blue** drives **primary action** — the main CTA button and hyperlinks. Only
  one blue primary per view.
- **Teal** marks **active state & brand** — the current nav item, the logo, the
  focus ring. Never use teal for a button that submits a form.
- **Lime** **confirms positive outcomes** — óptima coverage, "validada",
  success toasts. Never use lime for a neutral count.
- **Activity rows** use small **role-colored dots** (see §6.4), not full-row
  color fills.

---

## 4. Component usage

### 4.1 Buttons (cva variants)
| Variant     | Fill                | Text        | Hover           | Use                          |
|-------------|---------------------|-------------|-----------------|------------------------------|
| `primary`   | `--blue`            | white       | `--blue-deep`   | Main CTA (one per view)      |
| `secondary` | transparent + `--line` border | `--ink` | `--paper-2` fill | Secondary action         |
| `ghost`     | transparent         | `--ink-2`   | `--paper-2`     | Toolbar / low-emphasis       |
| `success`   | `--lime`            | `--ink`     | darken 8%       | Affirmative confirm (rare)   |
| `destructive` | `--signal-danger` | white       | darken 8%       | Delete / cancel expediente   |

Focus ring on every variant: `2px --teal` at `2px` offset.

### 4.2 Sidebar nav
- Inactive item: `--ink-2` text, transparent fill.
- **Active** item: `--teal-soft` fill (light) / pine fill (dark), `--teal` left
  indicator bar (3px), `--teal` icon + text.
- Hover: `--paper-2` fill.
- Section labels ("Gestión", "Comercial y operaciones"): `text-mono-sm`,
  `--ink-3`, uppercase-tracked.
- **Disabled items** (Pipeline, Facturación, Reportes): `--ink-3` at 45% opacity,
  `cursor-not-allowed`, no hover, tooltip "Próximamente".
- Bottom of sidebar: collaborator name (`text-label`) + cargo (`text-caption`,
  `--ink-3`) for traceability.

### 4.3 Cards
- Surface `--bone`, `1px --line` border, radius `12px`, padding `20px`.
- Title `text-h3`. KPI number `text-kpi`. Optional trend/label in
  `text-mono-sm`.
- Shadow: subtle only — `0 1px 2px rgba(11,20,24,.06)`. Dark theme: no shadow,
  rely on `--ink-2` surface contrast.

### 4.4 Badges
- Neutral status: `--paper-2` fill, `--ink-2` text, `text-mono-sm`.
- Brand/active: `--teal-soft` fill, `--teal-deep` text.
- Success: `--lime` tint fill, `--ink` text.
- Warn: ámbar tint fill, `--signal-warn` text.
- Danger: rojo tint fill, `--signal-danger` text.
- Estado enums (vigente, cotizando, liquidado, etc.) map to these five families;
  keep **one** badge accent per row.

### 4.5 Tables
- Header row: `--paper-2` background, `text-label` `--ink-3`, `1px --line`
  bottom border.
- Zebra rows: even rows `--paper-2` at ~40%.
- Row hover: `--paper-2` full.
- ID / RUT / UF columns render in `text-mono` (right-align numeric UF).
- Row-level status uses a single badge (§4.4). No full-row color fills except
  neutral hover.

---

## 5. Logo usage & files

- **Wordmark** is `Radal.` — always with the **terminal period**, set in Space
  Grotesk 600.
- **Dark theme** → **white or teal** mark on ink surface.
- **Light theme** → **ink or teal** mark on paper/bone surface.
- Teal mark is the preferred brand-forward option on either theme when a colored
  logo is wanted; white/ink are the monochrome fallbacks.

### 5.1 Files
Copy from source
`/Users/bgg/Documents/radal/marketing/brand book radal/assets/`
into `frontend/public/brand/`:

| File                    | Use                                    |
|-------------------------|----------------------------------------|
| `radal-mark-white.svg`  | Dark surfaces (sidebar dark, login dark) |
| `radal-mark-ink.svg`    | Light surfaces (sidebar light)         |
| `radal-mark-teal.svg`   | Brand-forward accent on either theme   |

Reference in code as `/brand/radal-mark-<variant>.svg`. Select variant by active
theme.

---

## 6. Color logic (business rules)

### 6.1 Coverage analyzer (`cobertura_pct` on pólizas / renovaciones)
Compares `suma_asegurada` against declared/replacement value → a coverage %.

| Condition            | Label            | `tipo_cobertura`  | Color            |
|----------------------|------------------|-------------------|------------------|
| `cobertura_pct < 95` | Infracobertura   | `infravalorada`   | `--signal-danger` (rojo) |
| `95 <= pct <= 105`   | Cobertura óptima | `optima`          | `--lime` (green) |
| `cobertura_pct > 105`| Sobrecobertura   | `sobrevalorada`   | `--signal-warn` (ámbar) |

Render as a badge + a thin bar; only the óptima band uses the lime affirmative.

### 6.2 Días-restantes — Renovaciones
Driven by `fecha_vencimiento` vs today.

| Condition                    | Color              |
|------------------------------|--------------------|
| `dias_restantes <= 60`       | ámbar (`--signal-warn`) |
| `dias_restantes > 60`        | gris (`--signal-muted`) |
| KPI **"Por vencer 30D"**     | rojo (`--signal-danger`) — count of `<= 30` days |

### 6.3 Días-restantes — Cotizaciones
Driven by `fecha_vence` vs today.

| Condition                    | Color              |
|------------------------------|--------------------|
| `dias_restantes <= 4`        | rojo (`--signal-danger`) |
| `dias_restantes > 4`         | ámbar (`--signal-warn`)  |
| KPI **"Por vencer L7D"**     | count of `< 7` days |

### 6.4 Activity role dots
Recent-activity rows carry a small dot colored by the actor's role:
- corredora roles → `--teal`
- aseguradora roles → `--blue`
- asegurado roles → `--lime`
Dot only; row text stays neutral.

---

## 7. `index.css` — paste-ready

```css
:root {
  /* Neutrals */
  --ink: #0B1418;
  --ink-2: #16242B;
  --ink-3: #324049;
  --paper: #EEF2EF;
  --paper-2: #E2E8E4;
  --bone: #FFFFFB;
  --line: #CFD8D2;

  /* Accents */
  --teal: #14B8A6;
  --teal-deep: #0E7C70;
  --teal-soft: #B8EBE5;
  --blue: #2563EB;
  --blue-deep: #1A47B8;
  --lime: #84CC16;

  /* Semantic signals */
  --signal-success: var(--lime);
  --signal-action: var(--blue);
  --signal-brand: var(--teal);
  --signal-warn: #D97706;
  --signal-danger: #DC2626;
  --signal-muted: var(--ink-3);

  /* Surface / text roles (light default) */
  --bg-app: var(--paper);
  --bg-surface: var(--bone);
  --bg-recessed: var(--paper-2);
  --text-primary: var(--ink);
  --text-secondary: var(--ink-2);
  --text-muted: var(--ink-3);
  --border: var(--line);
  --focus-ring: var(--teal);

  /* Fonts */
  --font-display: "Space Grotesk", system-ui, sans-serif;
  --font-ui: "Inter Tight", system-ui, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, monospace;

  --radius-card: 12px;
  --shadow-card: 0 1px 2px rgba(11, 20, 24, 0.06);
}

/* Dark theme override — Ink surfaces, white/teal logo */
:root[data-theme="dark"],
.dark {
  --bg-app: var(--ink);
  --bg-surface: var(--ink-2);
  --bg-recessed: var(--ink-2);
  --text-primary: #FFFFFB;
  --text-secondary: #C7D2CD;
  --text-muted: #7C8B92;
  --border: var(--ink-3);
  --teal-soft: var(--teal-deep);
  --focus-ring: var(--teal);
  --shadow-card: none;
}

html, body {
  background: var(--bg-app);
  color: var(--text-primary);
  font-family: var(--font-ui);
}
h1, h2, h3, h4, .wordmark { font-family: var(--font-display); }
.mono, code, .tabular { font-family: var(--font-mono); }
```

Wire the Tailwind theme to these CSS variables (e.g.
`colors: { ink: "var(--ink)", teal: "var(--teal)", ... }`) so utilities and cva
variants inherit the tokens.
