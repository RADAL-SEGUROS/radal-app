/**
 * /style-lab — three candidate visual directions for the Radal restart.
 *
 * The current Aqua Spectrum EXECUTION is deprecated (Sep 2026): the team wants
 * a state-of-the-art look — Vercel / OpenAI / Supabase class — grown from the
 * official brand (RADAL Brand Guidelines: the mark is one continuous stroke,
 * the shared record, ringed by nodes, the roles; "Every role. One record.").
 *
 * Craft doctrine applied throughout, from the `interfaces` skill set
 * (jakubkrehel/skills — better-ui, better-typography, better-colors,
 * better-layout), with its EXACT values, not approximations:
 *
 *   - elevation is layered transparent shadow, never a solid border: light
 *     surfaces use the 3-layer recipe (1px ring + lift + ambient), dark
 *     surfaces a single white ring `oklch(1 0 0 / .08)`; borders remain only
 *     where they mean structure (dividers, table rules, the rail edge);
 *   - concentric radii on every visibly nested pair: outer = inner + padding
 *     (segmented control 8 = 6 + 2, card 14 = 10 + 4 on its inner wells);
 *   - press feedback `scale(0.96)`, 150ms ease-out, transitions name their
 *     exact properties; the standard curve is cubic-bezier(0.2, 0, 0, 1);
 *   - staged entrance on direction switch: semantic chunks staggered 100ms,
 *     opacity + 12px translateY + 4px blur, keyframes (one-shot), and
 *     remounted via `key={dir}` so a palette swap never smears through
 *     lingering transitions;
 *   - type: headings ~1.1 line-height with balance, prose 1.5–1.6 with
 *     `text-wrap: pretty`, tabular numerals on data, positive tracking on
 *     small caps, weights never under 400 below 18px;
 *   - the Órbita gradient interpolates `in oklch`, so teal→blue stays vivid
 *     instead of greying at the midpoint;
 *   - one hue, one meaning per direction: teal = brand/active, blue = the one
 *     filled action per view, lime = positive, red = risk.
 *
 * This page is a LAB, not product UI: self-contained (no i18n, no auth, no app
 * components), one markup re-skinned purely by tokens — which is the thesis
 * for the real restyle. Data is the real demo corpus. Controls inside frames
 * are specimens; the only live control is the direction switcher (keys 1/2/3).
 */
import * as React from "react";

/* ────────────────────────────────────────────────────────────────────────── */
/* The three directions                                                       */
/* ────────────────────────────────────────────────────────────────────────── */

type DirKey = "registro" | "porcelana" | "orbita";

const DIRS: Record<
  DirKey,
  {
    name: string;
    school: string;
    thesis: string;
    type: string;
    palette: { hex: string; name: string }[];
  }
> = {
  registro: {
    name: "Registro",
    school: "Escuela Vercel · consola oscura",
    thesis:
      "El registro compartido como instrumento de precisión. Tinta casi negra, aros de luz en lugar de bordes, y el color aparece solo cuando significa algo. Denso, rápido, tabular: la app se siente como una terminal que aprendió modales.",
    type: "Geist · Geist Mono",
    palette: [
      { hex: "#0A0C0D", name: "Fondo" },
      { hex: "#101314", name: "Panel" },
      { hex: "#EDEFEE", name: "Texto" },
      { hex: "#2DD4BF", name: "Teal señal" },
      { hex: "#5B8DEF", name: "Azul acción" },
      { hex: "#A3E635", name: "Positivo" },
    ],
  },
  porcelana: {
    name: "Porcelana",
    school: "Escuela OpenAI · claridad editorial",
    thesis:
      "La calma como autoridad. Porcelana tibia, texto tinta, botones negros y aire — mucho aire. La sombra reemplaza al borde, el pino queda como hilo conductor. Para el corredor esto se lee como: aquí no se pierde nada.",
    type: "Instrument Sans · DM Mono",
    palette: [
      { hex: "#F7F7F3", name: "Fondo" },
      { hex: "#FFFFFF", name: "Panel" },
      { hex: "#191C1B", name: "Texto / CTA" },
      { hex: "#0E7C70", name: "Pino señal" },
      { hex: "#4D7C0F", name: "Positivo" },
      { hex: "#B91C1C", name: "Riesgo" },
    ],
  },
  orbita: {
    name: "Órbita",
    school: "Escuela Linear/Supabase · espectro nativo de IA",
    thesis:
      "El motivo oficial —nodos en órbita, lavado teal→azul— convertido en el único gesto expresivo. Vidrio oscuro, un gradiente que marca lo activo y viaja por oklch para no apagarse en el medio. La identidad de una plataforma donde la IA no es un botón: es el tejido.",
    type: "Sora · Inter · JetBrains Mono",
    palette: [
      { hex: "#0B0F14", name: "Fondo" },
      { hex: "#121820", name: "Panel" },
      { hex: "#F2F5F4", name: "Texto" },
      { hex: "#14B8A6", name: "Teal" },
      { hex: "#3B82F6", name: "Azul" },
      { hex: "#A3E635", name: "Positivo" },
    ],
  },
};

const ORDER: DirKey[] = ["registro", "porcelana", "orbita"];

/* ────────────────────────────────────────────────────────────────────────── */
/* Scoped stylesheet                                                          */
/* ────────────────────────────────────────────────────────────────────────── */

const CSS = /* css */ `
@import url('https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&family=Instrument+Sans:wght@400;500;600;700&family=DM+Mono:wght@400;500&family=Sora:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');

/* ── Lab chrome (theme-independent, deliberately neutral) ─────────────── */
.slab { min-height: 100vh; background: #0A0C0D; color: #EDEFEE;
  -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }
.slab * { box-sizing: border-box; margin: 0; }
.slab ::selection { background: rgba(45,212,191,.28); }
.slab :focus-visible { outline: 2px solid #2DD4BF; outline-offset: 2px; border-radius: 4px; }

.slab-chrome {
  position: sticky; top: 0; z-index: 40;
  display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
  padding: 13px 28px; border-bottom: 1px solid #1F2527;
  background: rgba(10,12,13,.85); backdrop-filter: blur(14px);
}
.slab-chrome .wordmark {
  display: flex; align-items: center; gap: 10px;
  font-family: Geist, system-ui, sans-serif; font-weight: 600; font-size: 16px;
  letter-spacing: -.02em; color: #EDEFEE;
}
.slab-chrome .wordmark img { height: 22px; width: 22px; }
.slab-chrome .lab-tag {
  font-family: 'Geist Mono', monospace; font-size: 10.5px; letter-spacing: .12em;
  text-transform: uppercase; color: #6E7A77;
  box-shadow: 0 0 0 1px oklch(1 0 0 / 0.08); border-radius: 999px; padding: 4px 11px;
  white-space: nowrap;
}
.slab-tabs { display: flex; gap: 6px; margin-left: auto; }
.slab-tab {
  font-family: Geist, system-ui, sans-serif; font-size: 13px; font-weight: 500;
  color: #9BA6A3; background: none; border: 0; border-radius: 8px;
  box-shadow: 0 0 0 1px oklch(1 0 0 / 0.08);
  padding: 7px 14px; cursor: pointer; display: flex; align-items: baseline; gap: 8px;
  transition-property: color, background-color, box-shadow, scale;
  transition-duration: 150ms; transition-timing-function: ease-out;
}
.slab-tab kbd {
  font-family: 'Geist Mono', monospace; font-size: 10px; color: #6E7A77;
  box-shadow: 0 0 0 1px oklch(1 0 0 / 0.1); border-radius: 4px; padding: 0 4px;
  transition-property: color; transition-duration: 150ms;
}
.slab-tab:hover { color: #EDEFEE; box-shadow: 0 0 0 1px oklch(1 0 0 / 0.16); }
.slab-tab:active { scale: 0.96; }
.slab-tab[aria-pressed="true"] { color: #0A0C0D; background: #EDEFEE; box-shadow: 0 0 0 1px #EDEFEE; }
.slab-tab[aria-pressed="true"] kbd { color: #52605C; box-shadow: 0 0 0 1px oklch(0 0 0 / 0.25); }

.slab-intro { padding: 34px 28px 6px; max-width: 1240px; margin: 0 auto; }
.slab-intro h1 {
  font-family: Geist, system-ui, sans-serif; font-size: 30px; font-weight: 600;
  letter-spacing: -.025em; line-height: 1.1; text-wrap: balance;
}
.slab-intro .school {
  font-family: 'Geist Mono', monospace; font-size: 11px; letter-spacing: .12em;
  text-transform: uppercase; color: #2DD4BF; margin-bottom: 10px;
}
.slab-intro p { color: #9BA6A3; max-width: 74ch; margin-top: 10px; line-height: 1.6;
  font-family: Geist, sans-serif; font-size: 14.5px; text-wrap: pretty; }
.slab-meta { display: flex; flex-wrap: wrap; gap: 18px; align-items: center; margin-top: 16px; }
.slab-chips { display: flex; gap: 6px; flex-wrap: wrap; }
.slab-chip { display: flex; align-items: center; gap: 7px; font-family: 'Geist Mono', monospace; font-size: 10.5px; color: #9BA6A3; }
.slab-chip i { width: 16px; height: 16px; border-radius: 5px; box-shadow: inset 0 0 0 1px oklch(1 0 0 / 0.14); display: inline-block; }
.slab-type { font-family: 'Geist Mono', monospace; font-size: 11px; color: #6E7A77; letter-spacing: .04em; }

.slab-section { max-width: 1240px; margin: 26px auto 0; padding: 0 28px; }
.slab-section > h2 {
  font-family: 'Geist Mono', monospace; font-size: 11px; letter-spacing: .13em;
  text-transform: uppercase; color: #6E7A77; margin-bottom: 12px;
}
.slab-frame { border-radius: 16px; overflow: hidden; box-shadow: 0 0 0 1px oklch(1 0 0 / 0.08); }
.slab-foot { max-width: 1240px; margin: 40px auto 0; padding: 0 28px 60px; color: #6E7A77;
  font-family: Geist, sans-serif; font-size: 13px; line-height: 1.6; text-wrap: pretty; }
.slab-foot b { color: #9BA6A3; font-weight: 500; }

/* ── Staged entrance: semantic chunks, 100ms apart, one-shot keyframes ── */
@media (prefers-reduced-motion: no-preference) {
  .slab-stage > * { animation: slab-enter 400ms cubic-bezier(0.2, 0, 0, 1) both; }
  .slab-stage > :nth-child(1) { animation-delay: 0ms; }
  .slab-stage > :nth-child(2) { animation-delay: 100ms; }
  .slab-stage > :nth-child(3) { animation-delay: 200ms; }
  .slab-stage > :nth-child(4) { animation-delay: 300ms; }
  .slab-stage > :nth-child(5) { animation-delay: 400ms; }
  @keyframes slab-enter {
    from { opacity: 0; transform: translateY(12px); filter: blur(4px); }
    to   { opacity: 1; transform: none; filter: blur(0px); }
  }
}

/* ── The skinnable mock: everything below reads only var(--…) ─────────── */
.mk {
  /* neutrals: one cool, teal-leaning grey family, held across the ramp */
  --bg: #0A0C0D; --panel: #101314; --panel-2: #151A1B; --line: #1F2527;
  --tx: #EDEFEE; --tx-2: #9BA6A3; --tx-3: #69756F;
  --brand: #2DD4BF; --brand-soft: rgba(45,212,191,.12);
  --cta: #5B8DEF; --cta-hover: #6E9AF2; --cta-tx: #0A0C0D;
  --pos: #A3E635; --warn: #FBBF24; --neg: #F87171;
  --f: Geist, system-ui, sans-serif; --fd: Geist, system-ui, sans-serif;
  --fm: 'Geist Mono', ui-monospace, monospace;
  /* concentric pairs: control 6 inside segmented 8 (pad 2); well 10 inside card 14 (pad 4) */
  --r-ctl: 6px; --r-seg: 8px; --r-well: 10px; --r-card: 14px;
  /* elevation = shadow; structure = border */
  --elev: 0 0 0 1px oklch(1 0 0 / 0.08);
  --elev-hover: 0 0 0 1px oklch(1 0 0 / 0.13);
  --pad: 14px; --active-grad: none;
  font-family: var(--f); color: var(--tx); background: var(--bg);
  font-size: 13px; line-height: 1.5;
}
.mk ::selection { background: color-mix(in srgb, var(--brand) 30%, transparent); }
.mk[data-dir="porcelana"] {
  /* neutrals: one warm, green-leaning grey family */
  --bg: #F7F7F3; --panel: #FFFFFF; --panel-2: #F1F1EB; --line: #E7E8E2;
  --tx: #191C1B; --tx-2: #55605D; --tx-3: #899290;
  --brand: #0E7C70; --brand-soft: rgba(14,124,112,.09);
  --cta: #191C1B; --cta-hover: #2E3331; --cta-tx: #F7F7F3;
  --pos: #4D7C0F; --warn: #B45309; --neg: #B91C1C;
  --f: 'Instrument Sans', system-ui, sans-serif; --fd: 'Instrument Sans', system-ui, sans-serif;
  --fm: 'DM Mono', ui-monospace, monospace;
  --r-ctl: 8px; --r-seg: 10px; --r-well: 12px; --r-card: 16px;
  --elev:
    0px 0px 0px 1px oklch(0 0 0 / 0.06),
    0px 1px 2px -1px oklch(0 0 0 / 0.06),
    0px 2px 4px 0px oklch(0 0 0 / 0.04);
  --elev-hover:
    0px 0px 0px 1px oklch(0 0 0 / 0.08),
    0px 1px 2px -1px oklch(0 0 0 / 0.08),
    0px 2px 4px 0px oklch(0 0 0 / 0.06);
  --pad: 18px;
}
.mk[data-dir="orbita"] {
  /* neutrals: one cool, blue-leaning grey family */
  --bg: #0B0F14; --panel: #121820; --panel-2: #171E27; --line: #232C36;
  --tx: #F2F5F4; --tx-2: #98A4AC; --tx-3: #64707A;
  --brand: #2DD4BF; --brand-soft: rgba(20,184,166,.13);
  --cta: #3B82F6; --cta-hover: #5493FA; --cta-tx: #F2F5F4;
  --pos: #A3E635; --warn: #FBBF24; --neg: #F87171;
  --f: Inter, system-ui, sans-serif; --fd: Sora, system-ui, sans-serif;
  --fm: 'JetBrains Mono', ui-monospace, monospace;
  --r-ctl: 7px; --r-seg: 9px; --r-well: 10px; --r-card: 14px;
  --elev: 0 0 0 1px oklch(1 0 0 / 0.07);
  --elev-hover: 0 0 0 1px oklch(1 0 0 / 0.12), 0 8px 28px -16px rgba(20,184,166,.35);
  --pad: 16px;
  /* oklch keeps teal→blue vivid through the midpoint; sRGB would grey out */
  --active-grad: linear-gradient(135deg in oklch, #14B8A6, #3B82F6);
}

/* shell */
.mk-shell { display: grid; grid-template-columns: 244px minmax(0,1fr); min-height: 620px; background: var(--bg); }
@media (max-width: 860px) { .mk-shell { grid-template-columns: 1fr; } .mk-side { display: none; } }
.mk-side { border-right: 1px solid var(--line); padding: 16px 10px 12px; display: flex; flex-direction: column; gap: 1px; background: var(--panel); }
.mk[data-dir="porcelana"] .mk-side { background: var(--bg); }
.mk[data-dir="orbita"] .mk-side { background: rgba(255,255,255,.015); }
.mk-lock { display: flex; align-items: center; gap: 9px; padding: 2px 10px 12px; }
.mk-lock img { width: 20px; height: 20px; }
.mk-lock b { font-family: var(--fd); font-weight: 600; font-size: 15.5px; letter-spacing: -.02em; line-height: 1.1; }
.mk-tenant {
  margin: 0 8px 14px; display: flex; align-items: center; gap: 7px;
  font-family: var(--fm); font-size: 9.5px; letter-spacing: .1em; text-transform: uppercase;
  color: var(--tx-3); box-shadow: var(--elev); border-radius: 999px; padding: 5px 11px;
  white-space: nowrap;
}
.mk-tenant i { width: 6px; height: 6px; border-radius: 99px; background: var(--brand); flex: none; }
.mk-cap {
  font-family: var(--fm); font-size: 9.5px; letter-spacing: .13em; text-transform: uppercase;
  color: var(--tx-3); padding: 14px 10px 6px;
}
.mk-nav {
  display: flex; align-items: center; gap: 9px; padding: 6.5px 10px; min-height: 30px;
  border-radius: var(--r-ctl); color: var(--tx-2); font-size: 12.5px; font-weight: 500;
  position: relative;
  transition-property: background-color, color; transition-duration: 150ms;
  transition-timing-function: ease-out;
}
.mk-nav:hover { background: color-mix(in srgb, var(--tx) 5%, transparent); color: var(--tx); }
.mk-nav svg { width: 15px; height: 15px; flex: none; opacity: .8; }
.mk-nav .n { margin-left: auto; font-family: var(--fm); font-size: 10px; color: var(--tx-3);
  font-variant-numeric: tabular-nums; }
.mk-nav.on { color: var(--tx); background: var(--brand-soft); }
.mk[data-dir="registro"] .mk-nav.on { box-shadow: inset 2px 0 0 var(--brand); border-radius: 0 var(--r-ctl) var(--r-ctl) 0; }
.mk[data-dir="porcelana"] .mk-nav.on { background: #FFFFFF; box-shadow: var(--elev); color: var(--brand); }
.mk[data-dir="orbita"] .mk-nav.on { background: rgba(255,255,255,.045); }
.mk[data-dir="orbita"] .mk-nav.on::before {
  content: ""; position: absolute; left: 0; top: 6px; bottom: 6px; width: 2px;
  border-radius: 2px; background: var(--active-grad);
}
.mk-sub { padding-left: 22px; display: flex; flex-direction: column; gap: 1px; }
.mk-sub .mk-nav { font-size: 12px; padding: 5px 10px; min-height: 26px; }
.mk-side .grow { flex: 1; }
.mk-user { display: flex; gap: 9px; align-items: center; border-top: 1px solid var(--line); padding: 12px 10px 2px; }
.mk-user i {
  width: 28px; height: 28px; border-radius: 8px; flex: none;
  background: var(--brand-soft); color: var(--brand);
  display: grid; place-items: center; font-family: var(--fm); font-size: 10.5px; font-weight: 500;
}
.mk[data-dir="orbita"] .mk-user i { background: var(--active-grad); color: #0B0F14; }
.mk-user p { font-size: 12px; font-weight: 500; line-height: 1.3; }
.mk-user span { display: block; font-size: 10.5px; color: var(--tx-3); }

.mk-main { display: flex; flex-direction: column; min-width: 0; }
.mk-top { display: flex; align-items: center; gap: 14px; padding: 11px 22px; border-bottom: 1px solid var(--line); }
.mk-crumb { font-family: var(--fm); font-size: 10.5px; letter-spacing: .06em; color: var(--tx-3); display: flex; gap: 8px; white-space: nowrap; }
.mk-crumb b { color: var(--tx); font-weight: 500; }
.mk-k {
  margin-left: auto; display: flex; align-items: center; gap: 8px;
  box-shadow: var(--elev); border-radius: var(--r-ctl); padding: 6px 6px 6px 11px;
  color: var(--tx-3); font-size: 12px; min-width: 220px; background: var(--panel);
  transition-property: box-shadow; transition-duration: 150ms; transition-timing-function: ease-out;
}
.mk-k:hover { box-shadow: var(--elev-hover); }
.mk[data-dir="porcelana"] .mk-k { background: #FFFFFF; }
.mk-k svg { width: 14px; height: 14px; flex: none; }
.mk-k kbd { margin-left: auto; font-family: var(--fm); font-size: 9.5px;
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--tx) 14%, transparent);
  border-radius: 4px; padding: 2px 6px; }

.mk-body { padding: 22px; display: flex; flex-direction: column; gap: 18px; min-width: 0; }
.mk-h1 { font-family: var(--fd); font-size: 22px; font-weight: 600; letter-spacing: -.02em;
  line-height: 1.1; text-wrap: balance; }
.mk[data-dir="porcelana"] .mk-h1 { font-size: 24px; letter-spacing: -.015em; }
.mk-h1row { display: flex; align-items: flex-start; gap: 14px; flex-wrap: wrap; }
.mk-h1row .sp { flex: 1; }
.mk-ruts { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
.mk-rut { font-family: var(--fm); font-size: 10.5px; color: var(--tx-2);
  box-shadow: inset 0 0 0 1px var(--line); border-radius: 5px; padding: 2.5px 7px;
  background: var(--panel); white-space: nowrap; }

/* buttons — press scale 0.96, exact transition properties */
.mk-btn {
  font-family: var(--f); font-size: 12.5px; font-weight: 500; border-radius: var(--r-ctl);
  padding: 7px 13px; border: 0; cursor: default; line-height: 1.35;
  display: inline-flex; align-items: center; gap: 7px; white-space: nowrap;
  transition-property: background-color, color, box-shadow, scale;
  transition-duration: 150ms; transition-timing-function: ease-out;
}
.mk-btn:active { scale: 0.96; }
.mk-btn.pri { background: var(--cta); color: var(--cta-tx); }
.mk-btn.pri:hover { background: var(--cta-hover); }
.mk[data-dir="orbita"] .mk-btn.pri { background: var(--active-grad); }
.mk[data-dir="orbita"] .mk-btn.pri:hover { box-shadow: 0 6px 20px -10px rgba(20,184,166,.6); }
.mk-btn.sec { box-shadow: var(--elev); color: var(--tx-2); background: var(--panel); }
.mk-btn.sec:hover { box-shadow: var(--elev-hover); color: var(--tx); }
.mk-btn.gho { color: var(--tx-2); background: transparent; }
.mk-btn.gho:hover { background: color-mix(in srgb, var(--tx) 6%, transparent); color: var(--tx); }
.mk-btn.dan { background: color-mix(in srgb, var(--neg) 14%, transparent); color: var(--neg); }
.mk-btn.dan:hover { background: color-mix(in srgb, var(--neg) 20%, transparent); }

/* badges — small caps, wide tracking, one hue = one meaning */
.mk-badge { font-family: var(--fm); font-size: 9.5px; letter-spacing: .07em;
  text-transform: uppercase; border-radius: 5px; padding: 2.5px 7px; font-weight: 500;
  white-space: nowrap; }
.mk-badge.ok { background: color-mix(in srgb, var(--pos) 16%, transparent); color: var(--pos); }
.mk-badge.info { background: var(--brand-soft); color: var(--brand); }
.mk-badge.warn { background: color-mix(in srgb, var(--warn) 16%, transparent); color: var(--warn); }
.mk-badge.neg { background: color-mix(in srgb, var(--neg) 14%, transparent); color: var(--neg); }
.mk-badge.mut { background: var(--panel-2); color: var(--tx-3); }
.mk[data-dir="porcelana"] .mk-badge.ok { color: #3F6212; }
.mk[data-dir="porcelana"] .mk-badge.warn { color: #92400E; }
.mk[data-dir="porcelana"] .mk-badge.neg { color: #991B1B; }

/* cards — elevation by shadow; hover lifts; inner wells concentric */
.mk-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 14px; }
.mk-card {
  border-radius: var(--r-card); background: var(--panel);
  padding: var(--pad); box-shadow: var(--elev);
  display: flex; flex-direction: column; gap: 10px;
  transition-property: box-shadow; transition-duration: 150ms; transition-timing-function: ease-out;
}
.mk-card:hover { box-shadow: var(--elev-hover); }
.mk-card .head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.mk-card .head b { font-family: var(--fd); font-size: 15px; font-weight: 600;
  letter-spacing: -.01em; line-height: 1.2; }
.mk-card .head .dates { font-family: var(--fm); font-size: 10px; color: var(--tx-3); }
.mk-card .kv { display: flex; justify-content: space-between; gap: 12px; font-size: 12.5px; color: var(--tx-2); }
.mk-card .kv b { font-family: var(--fm); font-weight: 500; color: var(--tx); font-variant-numeric: tabular-nums; }
.mk-card .foot { display: flex; gap: 8px; margin-top: 2px; flex-wrap: wrap; }
.mk[data-dir="orbita"] .mk-card.hot {
  background:
    linear-gradient(var(--panel), var(--panel)) padding-box,
    linear-gradient(135deg in oklch, rgba(20,184,166,.55), rgba(59,130,246,.55)) border-box;
  border: 1px solid transparent; box-shadow: none;
}
.mk[data-dir="orbita"] .mk-card.hot:hover { box-shadow: 0 10px 34px -18px rgba(20,184,166,.4); }
.mk[data-dir="registro"] .mk-card.hot { box-shadow: 0 0 0 1px color-mix(in srgb, var(--brand) 45%, transparent); }
.mk[data-dir="porcelana"] .mk-card.hot { box-shadow:
  0 0 0 1px color-mix(in srgb, var(--brand) 40%, transparent),
  0px 1px 2px -1px oklch(0 0 0 / 0.06), 0px 2px 4px 0px oklch(0 0 0 / 0.04); }

/* KPI */
.mk-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; }
.mk-kpi { border-radius: var(--r-card); background: var(--panel); padding: var(--pad);
  box-shadow: var(--elev);
  transition-property: box-shadow; transition-duration: 150ms; transition-timing-function: ease-out; }
.mk-kpi:hover { box-shadow: var(--elev-hover); }
.mk-kpi .lbl { font-family: var(--fm); font-size: 9.5px; letter-spacing: .11em; text-transform: uppercase; color: var(--tx-3); }
.mk-kpi .val { font-family: var(--fd); font-size: 25px; font-weight: 600; letter-spacing: -.02em;
  line-height: 1.05; margin-top: 7px; font-variant-numeric: tabular-nums; }
.mk-kpi .sub { font-size: 11.5px; color: var(--tx-2); margin-top: 4px; }
.mk-kpi .sub.up { color: var(--pos); } .mk-kpi .sub.down { color: var(--neg); }
.mk-spark { margin-top: 10px; height: 28px; width: 100%; }
.mk-spark polyline { fill: none; stroke: var(--brand); stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
.mk-spark .fill { fill: var(--brand-soft); stroke: none; }
.mk-spark circle { fill: var(--brand); }
.mk[data-dir="orbita"] .mk-spark polyline { stroke: url(#slab-grad); }
.mk[data-dir="orbita"] .mk-spark circle { fill: #3B82F6; }

/* toolbar + table */
.mk-tools { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
/* concentric: outer r-seg = inner r-ctl + 2px padding */
.mk-view { display: flex; box-shadow: var(--elev); border-radius: var(--r-seg); padding: 2px; background: var(--panel); }
.mk-view span { font-size: 12px; padding: 5px 12px; color: var(--tx-3); border-radius: var(--r-ctl);
  transition-property: color, background-color; transition-duration: 150ms; transition-timing-function: ease-out; }
.mk-view span:hover { color: var(--tx); }
.mk-view span.on { background: var(--brand-soft); color: var(--brand); font-weight: 500; }
.mk[data-dir="porcelana"] .mk-view { background: var(--panel-2); box-shadow: none; }
.mk[data-dir="porcelana"] .mk-view span.on { background: #FFFFFF; box-shadow: var(--elev); color: var(--tx); }
.mk-filter { font-size: 11.5px; color: var(--tx-2); border: 1px dashed var(--line); border-radius: 999px; padding: 4.5px 12px; white-space: nowrap; }
.mk-tablewrap { border-radius: var(--r-card); overflow-x: auto; background: var(--panel); box-shadow: var(--elev); }
.mk-table { border-collapse: collapse; width: 100%; min-width: 760px; font-size: 12.5px; }
.mk-table th {
  text-align: left; font-family: var(--fm); font-size: 9.5px; letter-spacing: .1em;
  text-transform: uppercase; color: var(--tx-3); font-weight: 500;
  padding: 9px 14px; border-bottom: 1px solid var(--line); background: var(--panel-2);
  white-space: nowrap;
}
.mk-table td { padding: 10.5px 14px; border-bottom: 1px solid var(--line); color: var(--tx-2); white-space: nowrap;
  transition-property: background-color; transition-duration: 150ms; transition-timing-function: ease-out; }
.mk-table tr:last-child td { border-bottom: 0; }
.mk-table tbody tr:hover td { background: color-mix(in srgb, var(--tx) 3%, transparent); }
.mk-table td.g { color: var(--tx); font-weight: 500; }
.mk-table td.g i { display: inline-block; width: 6px; height: 6px; border-radius: 99px;
  background: var(--brand); margin-right: 9px; vertical-align: 1px; }
.mk-table td.num { font-family: var(--fm); font-variant-numeric: tabular-nums; text-align: right; color: var(--tx); }
.mk-table tr.sel td { background: var(--brand-soft); }

/* AI strip */
.mk-ai { display: grid; grid-template-columns: minmax(230px, 300px) minmax(0,1fr);
  border-radius: var(--r-card); overflow: hidden; background: var(--panel); box-shadow: var(--elev); }
@media (max-width: 760px) { .mk-ai { grid-template-columns: 1fr; } }
.mk-ai .doc { border-right: 1px solid var(--line); padding: var(--pad); background: var(--panel-2); display: flex; flex-direction: column; gap: 9px; }
@media (max-width: 760px) { .mk-ai .doc { border-right: 0; border-bottom: 1px solid var(--line); } }
.mk-ai .doc .file { display: flex; align-items: center; gap: 9px; font-size: 12.5px; font-weight: 500; color: var(--tx); }
.mk-ai .doc .file i { width: 28px; height: 28px; border-radius: var(--r-ctl); background: var(--brand-soft); color: var(--brand); display: grid; place-items: center; font-family: var(--fm); font-size: 9px; flex: none; }
.mk-ai .doc p { font-size: 11.5px; color: var(--tx-3); line-height: 1.55; text-wrap: pretty; }
.mk-ai .fields { padding: var(--pad); display: flex; flex-direction: column; gap: 9px; }
.mk-ai .frow { display: grid; grid-template-columns: 150px minmax(0,1fr) minmax(48px, 76px); gap: 12px; align-items: center; font-size: 12px; }
.mk-ai .frow .k { color: var(--tx-3); font-family: var(--fm); font-size: 10px; letter-spacing: .08em; }
.mk-ai .frow .v { font-family: var(--fm); color: var(--tx); font-variant-numeric: tabular-nums; }
.mk-ai .conf { height: 3px; border-radius: 2px; background: var(--panel-2); overflow: hidden; }
.mk[data-dir="porcelana"] .mk-ai .conf { background: var(--line); }
.mk-ai .conf i { display: block; height: 100%; border-radius: 2px; background: var(--brand); }
.mk[data-dir="orbita"] .mk-ai .conf i { background: var(--active-grad); }
.mk-ai .actions { display: flex; gap: 8px; margin-top: 6px; align-items: center; flex-wrap: wrap; }
.mk-ai .actions .note { font-family: var(--fm); font-size: 10px; color: var(--tx-3); margin-left: auto; }

/* specimens */
.mk-spec { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }
.mk-spec .box { border-radius: var(--r-card); background: var(--panel); padding: var(--pad);
  box-shadow: var(--elev); display: flex; flex-direction: column; gap: 11px; }
.mk-spec .box > span { font-family: var(--fm); font-size: 9.5px; letter-spacing: .11em; text-transform: uppercase; color: var(--tx-3); }
.mk-spec .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.mk-input { box-shadow: inset 0 0 0 1px var(--line); background: var(--bg);
  border-radius: var(--r-ctl); padding: 7px 11px; font-size: 12.5px; color: var(--tx-3);
  display: flex; justify-content: space-between; gap: 10px;
  transition-property: box-shadow; transition-duration: 150ms; transition-timing-function: ease-out; }
.mk-input:hover { box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--brand) 45%, var(--line)); }
.mk-input em { font-style: normal; color: var(--tx-3); font-family: var(--fm); font-size: 10px; align-self: center; }
.mk-type .t1 { font-family: var(--fd); font-size: 21px; font-weight: 600; letter-spacing: -.02em; line-height: 1.15; text-wrap: balance; }
.mk-type .t2 { font-size: 13px; color: var(--tx-2); margin-top: 6px; line-height: 1.55; text-wrap: pretty; }
.mk-type .t3 { font-family: var(--fm); font-size: 10.5px; color: var(--tx-3); margin-top: 8px; letter-spacing: .05em; }

/* orbit motif — only where the direction calls for it */
.mk-orbit { position: relative; overflow: hidden; }
.mk-orbit::after {
  content: ""; position: absolute; right: -70px; top: -70px; width: 260px; height: 260px;
  border-radius: 50%; pointer-events: none; display: none;
  background: radial-gradient(closest-side, color-mix(in srgb, var(--brand) 15%, transparent), transparent 70%);
}
.mk[data-dir="orbita"] .mk-orbit::after { display: block; }
.mk[data-dir="registro"] .mk-side {
  background-image: radial-gradient(color-mix(in srgb, var(--tx) 5%, transparent) 1px, transparent 1px);
  background-size: 22px 22px; background-position: 0 100%;
}
`;

/* ────────────────────────────────────────────────────────────────────────── */
/* Tiny inline icons — 1.5px stroke to match the 400–500 text beside them    */
/* ────────────────────────────────────────────────────────────────────────── */

const I = {
  folder: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />
    </svg>
  ),
  chart: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M4 20V10M10 20V4M16 20v-7M21 20H3" />
    </svg>
  ),
  scan: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M7 12h10" />
    </svg>
  ),
  compare: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M8 4v16M16 4v16M4 8l4-4 4 4M12 16l4 4 4-4" />
    </svg>
  ),
  building: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M5 21V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16M9 8h1M14 8h1M9 12h1M14 12h1M9 16h1M14 16h1M3 21h18" />
    </svg>
  ),
  search: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  ),
};

/* ────────────────────────────────────────────────────────────────────────── */
/* The mock views — one markup, skinned by the wrapper's data-dir            */
/* ────────────────────────────────────────────────────────────────────────── */

function MockShell({ dir }: { dir: DirKey }) {
  const mark =
    dir === "porcelana" ? "/brand/radal-mark-ink.svg" : "/brand/radal-mark-white.svg";
  return (
    <div className="mk" data-dir={dir}>
      <div className="mk-shell">
        <aside className="mk-side">
          <div className="mk-lock">
            <img src={mark} alt="" />
            <b>Radal.</b>
          </div>
          <div className="mk-tenant"><i />Fuenzalida SR</div>

          <div className="mk-cap">Grupos</div>
          <div className="mk-nav on">{I.folder}<span>Viña Indómita</span><span className="n">2</span></div>
          <div className="mk-sub">
            <div className="mk-nav on"><span>2026–2027</span><span className="n">1</span></div>
            <div className="mk-nav"><span>2025–2026</span><span className="n">1</span></div>
          </div>
          <div className="mk-nav">{I.folder}<span>La Favorita</span><span className="n">2</span></div>
          <div className="mk-nav">{I.folder}<span>Coccolino</span><span className="n">2</span></div>

          <div className="mk-cap">Visión general</div>
          <div className="mk-nav">{I.chart}<span>Analítica</span></div>
          <div className="mk-nav">{I.building}<span>Compañías y apetito</span></div>

          <div className="mk-cap">Asistentes IA</div>
          <div className="mk-nav">{I.scan}<span>Lector documental</span></div>
          <div className="mk-nav">{I.compare}<span>Comparador</span></div>

          <div className="grow" />
          <div className="mk-user">
            <i>U11</i>
            <div>
              <p>Usuaria Once</p>
              <span>Ejecutiva comercial</span>
            </div>
          </div>
        </aside>

        <div className="mk-main">
          <div className="mk-top">
            <div className="mk-crumb">
              <span>Grupos</span><span>›</span><b>Viña Indómita</b>
            </div>
            <div className="mk-k">{I.search}<span>Buscar en el registro…</span><kbd>⌘K</kbd></div>
          </div>

          <div className="mk-body mk-orbit">
            <div className="mk-h1row">
              <div>
                <h3 className="mk-h1">Grupo Viña Indómita</h3>
                <div className="mk-ruts">
                  <span className="mk-rut">99.568.600-7 · Viña Indómita SpA</span>
                  <span className="mk-rut">96.688.830-K · Viña Santa Alicia SpA</span>
                </div>
              </div>
              <div className="sp" />
              <span className="mk-btn sec">Descargar expediente</span>
              <span className="mk-btn pri">Nueva cuenta</span>
            </div>

            <div className="mk-cards">
              <div className="mk-card hot">
                <div className="head">
                  <b>Incendio y Sismo · 2026–2027</b>
                  <span className="dates">15 oct 2026 – 15 oct 2027</span>
                </div>
                <div className="kv"><span>Etapa</span><span><span className="mk-badge info">Comparación</span></span></div>
                <div className="kv"><span>Propuestas confirmadas</span><b>3</b></div>
                <div className="kv"><span>Mejor prima</span><b>UF 1.120,13</b></div>
                <div className="foot">
                  <span className="mk-badge mut">EXP-2026-0001</span>
                  <span className="mk-badge ok">11 documentos</span>
                </div>
              </div>
              <div className="mk-card">
                <div className="head">
                  <b>Incendio y Sismo · 2025–2026</b>
                  <span className="dates">15 oct 2025 – 15 oct 2026</span>
                </div>
                <div className="kv"><span>Póliza</span><b>0020119904 · Southbridge</b></div>
                <div className="kv"><span>Prima total</span><b>UF 1.181,28</b></div>
                <div className="kv">
                  <span>Post-venta</span>
                  <span style={{ display: "flex", gap: 6 }}>
                    <span className="mk-badge mut">2 endosos</span>
                    <span className="mk-badge neg">Cobranza 67d</span>
                  </span>
                </div>
                <div className="foot">
                  <span className="mk-badge warn">Histórico</span>
                  <span className="mk-badge ok">Siniestro liquidado</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const TABLE_ROWS = [
  ["Viña Indómita", "Incendio y Sismo", "2026–2027", "Comparación", "—", "info", "Abierta"],
  ["La Favorita", "Incendio y Sismo", "2028–2029", "Renovación", "—", "info", "Abierta"],
  ["La Favorita", "Incendio y Sismo", "2027–2028", "Vigente", "423,03", "ok", "Ganada"],
  ["Viña Indómita", "Incendio y Sismo", "2025–2026", "Vigente", "1.181,28", "ok", "Ganada"],
  ["Coccolino", "Incendio y Sismo", "2026–2027", "Propuesta emitida", "—", "info", "Abierta"],
  ["Coccolino", "Vehículos (flota)", "2026–2027", "Antecedentes", "—", "mut", "Abierta"],
  ["JO Pastelería", "Resp. Civil", "2026–2027", "En el mercado", "—", "mut", "Abierta"],
] as const;

function MockAnalytics({ dir }: { dir: DirKey }) {
  return (
    <div className="mk" data-dir={dir}>
      <div className="mk-body mk-orbit" style={{ padding: 22 }}>
        <svg width="0" height="0" aria-hidden>
          <defs>
            <linearGradient id="slab-grad" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stopColor="#14B8A6" />
              <stop offset="1" stopColor="#3B82F6" />
            </linearGradient>
          </defs>
        </svg>
        <div className="mk-h1row">
          <div>
            <h3 className="mk-h1">Analítica de cartera</h3>
            <div className="mk-ruts"><span className="mk-rut">7 grupos · 8 cuentas · 2 pólizas vigentes</span></div>
          </div>
          <div className="sp" />
          <div className="mk-view">
            <span className="on">Tabla</span><span>Tablero</span><span>Cronología</span>
          </div>
        </div>

        <div className="mk-kpis">
          <div className="mk-kpi">
            <div className="lbl">Prima cartera</div>
            <div className="val">UF 1.604,31</div>
            <div className="sub up">+UF 38,64 en endosos</div>
            <svg className="mk-spark" viewBox="0 0 120 28" preserveAspectRatio="none">
              <polygon className="fill" points="0,22 18,20 36,21 54,14 72,15 90,9 114,6 114,28 0,28" />
              <polyline points="0,22 18,20 36,21 54,14 72,15 90,9 114,6" />
              <circle cx="114" cy="6" r="2.4" />
            </svg>
          </div>
          <div className="mk-kpi">
            <div className="lbl">Cuentas abiertas</div>
            <div className="val">6</div>
            <div className="sub">2 esperando al mercado</div>
          </div>
          <div className="mk-kpi">
            <div className="lbl">Renovaciones 90 días</div>
            <div className="val">1</div>
            <div className="sub">La Favorita · 05 ene 2028</div>
          </div>
          <div className="mk-kpi">
            <div className="lbl">Cobranza vencida</div>
            <div className="val">CUP-008</div>
            <div className="sub down">67 días · riesgo art. 528</div>
          </div>
        </div>

        <div className="mk-tools">
          <span className="mk-filter">Estado: abiertas + ganadas</span>
          <span className="mk-filter">Ramo: todos</span>
          <span className="mk-filter">+ filtro</span>
        </div>

        <div className="mk-tablewrap">
          <table className="mk-table">
            <thead>
              <tr>
                <th>Grupo</th><th>Ramo</th><th>Vigencia</th><th>Etapa</th>
                <th style={{ textAlign: "right" }}>Prima UF</th><th>Estado</th>
              </tr>
            </thead>
            <tbody>
              {TABLE_ROWS.map(([g, ramo, vig, etapa, prima, tone, estado], i) => (
                <tr key={g + vig + ramo} className={i === 3 ? "sel" : undefined}>
                  <td className="g"><i />{g}</td>
                  <td>{ramo}</td>
                  <td style={{ fontFamily: "var(--fm)", fontSize: 11 }}>{vig}</td>
                  <td><span className={`mk-badge ${tone}`}>{etapa}</span></td>
                  <td className="num">{prima}</td>
                  <td>{estado}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function MockAI({ dir }: { dir: DirKey }) {
  return (
    <div className="mk" data-dir={dir}>
      <div className="mk-body" style={{ padding: 22 }}>
        <div className="mk-ai">
          <div className="doc">
            <div className="file"><i>PDF</i>03 Cotización HDI — La Favorita.pdf</div>
            <p>
              El lector propone; la persona confirma. Nada se escribe solo — cada
              lectura queda auditada con modelo, versión y confianza.
            </p>
            <span className="mk-badge info" style={{ alignSelf: "flex-start" }}>Sugerido por IA</span>
          </div>
          <div className="fields">
            <div className="frow">
              <span className="k">PRIMA AFECTA</span><span className="v">UF 826,45</span>
              <span className="conf"><i style={{ width: "96%" }} /></span>
            </div>
            <div className="frow">
              <span className="k">PRIMA EXENTA</span><span className="v">UF 117,36</span>
              <span className="conf"><i style={{ width: "92%" }} /></span>
            </div>
            <div className="frow">
              <span className="k">IVA (19% AFECTA)</span><span className="v">UF 157,03</span>
              <span className="conf"><i style={{ width: "98%" }} /></span>
            </div>
            <div className="frow">
              <span className="k">PRIMA TOTAL</span><span className="v">UF 1.100,84</span>
              <span className="conf"><i style={{ width: "97%" }} /></span>
            </div>
            <div className="actions">
              <span className="mk-btn pri">Confirmar y guardar</span>
              <span className="mk-btn gho">Corregir</span>
              <span className="note">confianza media 96% · llama-3.3-70b</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function MockSpecimens({ dir }: { dir: DirKey }) {
  const d = DIRS[dir];
  return (
    <div className="mk" data-dir={dir}>
      <div className="mk-body" style={{ padding: 22 }}>
        <div className="mk-spec">
          <div className="box">
            <span>Acciones</span>
            <div className="row">
              <span className="mk-btn pri">Nueva cuenta</span>
              <span className="mk-btn sec">Descargar</span>
              <span className="mk-btn gho">Cancelar</span>
              <span className="mk-btn dan">Cerrar carpeta</span>
            </div>
            <div className="mk-input"><span>Buscar RUT, póliza, grupo…</span><em>⌘K</em></div>
          </div>
          <div className="box">
            <span>Estados</span>
            <div className="row">
              <span className="mk-badge ok">Vigente</span>
              <span className="mk-badge info">Comparación</span>
              <span className="mk-badge warn">Histórico</span>
              <span className="mk-badge neg">Morosa · 67d</span>
              <span className="mk-badge mut">Borrador</span>
            </div>
            <div className="row">
              <span className="mk-rut">EXP-2026-0005</span>
              <span className="mk-rut">15-04-0091883</span>
              <span className="mk-rut">UF 9.110,00</span>
            </div>
          </div>
          <div className="box mk-type">
            <span>Tipografía · {d.type}</span>
            <div>
              <div className="t1">Cada rol. Un registro.</div>
              <div className="t2">
                La prima total es la suma de la neta y el IVA; el IVA grava solo
                la prima afecta — el sismo está exento.
              </div>
              <div className="t3">POLIZA 0020119904 · 15 OCT 2025 · UF 1.181,28</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────── */
/* Page                                                                       */
/* ────────────────────────────────────────────────────────────────────────── */

export default function StyleLabPage() {
  const [dir, setDir] = React.useState<DirKey>("registro");

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const idx = ["1", "2", "3"].indexOf(e.key);
      if (idx >= 0) setDir(ORDER[idx]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  React.useEffect(() => {
    document.title = "Radal · Laboratorio de estilo";
  }, []);

  const d = DIRS[dir];

  return (
    <div className="slab">
      <style>{CSS}</style>

      <header className="slab-chrome">
        <div className="wordmark">
          <img src="/brand/radal-mark-white.svg" alt="" />
          Radal.
        </div>
        <span className="lab-tag">Laboratorio de estilo · 3 direcciones</span>
        <nav className="slab-tabs" aria-label="Dirección visual">
          {ORDER.map((key, i) => (
            <button
              key={key}
              type="button"
              className="slab-tab"
              aria-pressed={dir === key}
              onClick={() => setDir(key)}
            >
              {DIRS[key].name}
              <kbd>{i + 1}</kbd>
            </button>
          ))}
        </nav>
      </header>

      {/* key={dir}: a palette swap remounts the stage, so (1) no transition
          smears across the whole page mid-swap, and (2) the staged entrance
          replays — an infrequent, meaningful moment, per the doctrine. */}
      <div className="slab-stage" key={dir}>
        <section className="slab-intro">
          <div className="school">{d.school}</div>
          <h1>{d.name}</h1>
          <p>{d.thesis}</p>
          <div className="slab-meta">
            <div className="slab-chips">
              {d.palette.map((c) => (
                <span key={c.hex} className="slab-chip">
                  <i style={{ background: c.hex }} />
                  {c.hex}
                </span>
              ))}
            </div>
            <span className="slab-type">{d.type}</span>
          </div>
        </section>

        <section className="slab-section">
          <h2>01 · El shell — grupos primero</h2>
          <div className="slab-frame"><MockShell dir={dir} /></div>
        </section>

        <section className="slab-section">
          <h2>02 · Analítica — la vista general, con opciones de tabla</h2>
          <div className="slab-frame"><MockAnalytics dir={dir} /></div>
        </section>

        <section className="slab-section">
          <h2>03 · El momento IA — sugerir, confirmar, auditar</h2>
          <div className="slab-frame"><MockAI dir={dir} /></div>
        </section>

        <section className="slab-section">
          <h2>04 · Piezas — botones, estados, tipografía</h2>
          <div className="slab-frame"><MockSpecimens dir={dir} /></div>
        </section>
      </div>

      <footer className="slab-foot">
        <b>Cómo decidir:</b> las tres direcciones nacen de la misma marca — el trazo
        continuo (el registro) y los nodos (los roles) — y comparten exactamente el
        mismo HTML: solo cambian los tokens. Elegida una, esa hoja de tokens se
        convierte en el nuevo sistema y se aplica sobre la app real por partes,
        empezando por el shell y la analítica. Ningún dato de esta página es
        inventado: es el corpus demo tal como está cargado.
      </footer>
    </div>
  );
}
