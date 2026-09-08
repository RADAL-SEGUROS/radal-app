import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

const config: Config = {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Signal raw tokens (legacy names kept — they alias to the primitives
        // in index.css, so older files restyle without edits).
        ink: {
          DEFAULT: "var(--ink)",
          2: "var(--ink-2)",
          3: "var(--ink-3)",
        },
        paper: {
          DEFAULT: "var(--paper)",
          2: "var(--paper-2)",
        },
        bone: "var(--bone)",
        sidebar: "var(--sidebar)",
        line: "var(--line)",
        // Hover borders / emphasized rules: `border-line-strong`.
        "line-strong": "var(--line-strong)",
        muted: {
          DEFAULT: "var(--bg-recessed)",
          foreground: "var(--text-muted)",
        },
        teal: {
          DEFAULT: "var(--teal)",
          deep: "var(--teal-deep)",
          soft: "var(--teal-soft)",
        },
        blue: {
          DEFAULT: "var(--blue)",
          deep: "var(--blue-deep)",
        },
        lime: {
          DEFAULT: "var(--lime)",
          deep: "var(--lime-deep)",
        },
        amber: {
          DEFAULT: "var(--amber)",
          deep: "var(--amber-deep)",
        },
        red: {
          DEFAULT: "var(--red)",
          deep: "var(--red-deep)",
        },

        // Brand roles. `brand-ring` / `brand-line` are pre-mixed tints so
        // components never need color-mix() inside a className:
        //   focus ring → `ring-brand-ring`, tinted hover border → `border-brand-line`.
        brand: {
          DEFAULT: "var(--brand)",
          deep: "var(--brand-deep)",
          soft: "var(--brand-soft)",
          ring: "color-mix(in srgb, var(--brand) 15%, transparent)",
          line: "color-mix(in srgb, var(--brand) 30%, transparent)",
        },
        cta: {
          DEFAULT: "var(--cta)",
          hover: "var(--cta-hover)",
          foreground: "var(--cta-tx)",
        },

        // Status trios: `bg-pos-soft` / `text-pos-text` / `border-pos-line`
        // (same shape for warn and neg). Sweep agents: use THESE, not
        // color-mix() or -[var(--…)] arbitrary values.
        pos: {
          DEFAULT: "var(--pos)",
          text: "var(--pos-text)",
          soft: "var(--pos-soft)",
          line: "color-mix(in srgb, var(--pos) 30%, transparent)",
        },
        warn: {
          DEFAULT: "var(--warn)",
          text: "var(--warn-text)",
          soft: "var(--warn-soft)",
          line: "color-mix(in srgb, var(--warn) 30%, transparent)",
        },
        neg: {
          DEFAULT: "var(--neg)",
          text: "var(--neg-text)",
          soft: "var(--neg-soft)",
          line: "color-mix(in srgb, var(--neg) 30%, transparent)",
        },

        // Semantic signals
        signal: {
          success: "var(--signal-success)",
          action: "var(--signal-action)",
          brand: "var(--signal-brand)",
          warn: "var(--signal-warn)",
          "warn-deep": "var(--signal-warn-deep)",
          danger: "var(--signal-danger)",
          "danger-deep": "var(--signal-danger-deep)",
          muted: "var(--signal-muted)",
        },

        // Surface / text roles (theme-aware)
        "bg-app": "var(--bg-app)",
        "bg-surface": "var(--bg-surface)",
        "bg-recessed": "var(--bg-recessed)",
        "bg-sidebar": "var(--bg-sidebar)",
        "text-primary": "var(--text-primary)",
        "text-secondary": "var(--text-secondary)",
        "text-tertiary": "var(--text-tertiary)",
        "text-muted": "var(--text-muted)",
        border: "var(--border)",

        // shadcn-style semantic aliases
        background: "var(--bg-app)",
        foreground: "var(--text-primary)",
        card: "var(--bg-surface)",
        "card-foreground": "var(--text-primary)",
        popover: "var(--bg-surface)",
        "popover-foreground": "var(--text-primary)",
        primary: "var(--cta)",
        "primary-foreground": "var(--cta-tx)",
        secondary: "var(--bg-recessed)",
        "secondary-foreground": "var(--text-primary)",
        accent: "var(--teal-soft)",
        "accent-foreground": "var(--teal-deep)",
        destructive: "var(--signal-danger)",
        "destructive-foreground": "#FFFFFC",
        input: "var(--border)",
        ring: "var(--focus-ring)",
      },
      fontFamily: {
        // Inter everywhere; mono only for genuine code content;
        // Space Grotesk ONLY in the "Radal." wordmark lockup.
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        ui: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        display: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ["ui-monospace", "SF Mono", "Menlo", "monospace"],
        wordmark: ["Space Grotesk", "system-ui", "sans-serif"],
      },
      fontSize: {
        display: ["28px", { lineHeight: "1.15", fontWeight: "600", letterSpacing: "-0.02em" }],
        h1: ["22px", { lineHeight: "1.2", fontWeight: "600", letterSpacing: "-0.015em" }],
        h2: ["17px", { lineHeight: "1.3", fontWeight: "600", letterSpacing: "-0.01em" }],
        h3: ["14.5px", { lineHeight: "1.35", fontWeight: "600" }],
        kpi: ["26px", { lineHeight: "1.1", fontWeight: "600", letterSpacing: "-0.02em" }],
        body: ["14px", { lineHeight: "1.55", fontWeight: "400" }],
        label: ["13px", { lineHeight: "1.4", fontWeight: "500" }],
        caption: ["12px", { lineHeight: "1.4", fontWeight: "400" }],
        // Deprecated — kept ONLY until the sweep removes their consumers.
        // Nothing new uses these.
        mono: ["12px", { lineHeight: "1.4", fontWeight: "500" }],
        "mono-sm": ["9.5px", { lineHeight: "1.4", fontWeight: "500", letterSpacing: "0.07em" }],
      },
      borderRadius: {
        // The concentric ladder: outer = inner + padding.
        card: "12px", // var(--r-card)
        lg: "10px", // var(--r-well)
        md: "9px", // var(--r-seg)
        sm: "8px", // var(--r-ctl)
      },
      boxShadow: {
        elev: "var(--elev)",
        "elev-hover": "var(--elev-hover)",
        overlay: "var(--elev-overlay)",
        // legacy names kept, re-pointed:
        card: "var(--elev)",
        sm: "var(--elev)",
        lift: "var(--elev-hover)",
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
        enter: {
          from: { opacity: "0", transform: "translateY(12px)" },
          to: { opacity: "1", transform: "none" },
        },
        dropIn: {
          from: { opacity: "0", transform: "translateY(-6px) scale(0.985)" },
          to: { opacity: "1", transform: "none" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        enter: "enter 400ms cubic-bezier(0.2, 0, 0, 1) both",
        // legacy name, new motion:
        fadeUp: "enter 400ms cubic-bezier(0.2, 0, 0, 1) both",
        dropIn: "dropIn 0.18s ease both",
      },
    },
  },
  plugins: [animate],
};

export default config;
