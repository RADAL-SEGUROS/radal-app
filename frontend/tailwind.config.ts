import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

const config: Config = {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Aqua Spectrum raw tokens
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
        line: "var(--line)",
        teal: {
          DEFAULT: "var(--teal)",
          deep: "var(--teal-deep)",
          soft: "var(--teal-soft)",
        },
        blue: {
          DEFAULT: "var(--blue)",
          deep: "var(--blue-deep)",
        },
        lime: "var(--lime)",

        // Semantic signals
        signal: {
          success: "var(--signal-success)",
          action: "var(--signal-action)",
          brand: "var(--signal-brand)",
          warn: "var(--signal-warn)",
          danger: "var(--signal-danger)",
          muted: "var(--signal-muted)",
        },

        // Surface / text roles (theme-aware)
        "bg-app": "var(--bg-app)",
        "bg-surface": "var(--bg-surface)",
        "bg-recessed": "var(--bg-recessed)",
        "text-primary": "var(--text-primary)",
        "text-secondary": "var(--text-secondary)",
        "text-muted": "var(--text-muted)",
        border: "var(--border)",

        // shadcn-style semantic aliases
        background: "var(--bg-app)",
        foreground: "var(--text-primary)",
        card: "var(--bg-surface)",
        "card-foreground": "var(--text-primary)",
        popover: "var(--bg-surface)",
        "popover-foreground": "var(--text-primary)",
        primary: "var(--blue)",
        "primary-foreground": "#FFFFFB",
        secondary: "var(--bg-recessed)",
        "secondary-foreground": "var(--text-primary)",
        muted: "var(--bg-recessed)",
        "muted-foreground": "var(--text-muted)",
        accent: "var(--teal-soft)",
        "accent-foreground": "var(--teal-deep)",
        destructive: "var(--signal-danger)",
        "destructive-foreground": "#FFFFFB",
        input: "var(--border)",
        ring: "var(--focus-ring)",
      },
      fontFamily: {
        display: ["Space Grotesk", "system-ui", "sans-serif"],
        ui: ["Inter Tight", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
        sans: ["Inter Tight", "system-ui", "sans-serif"],
      },
      fontSize: {
        display: ["32px", { lineHeight: "38px", fontWeight: "600" }],
        h1: ["28px", { lineHeight: "34px", fontWeight: "600" }],
        h2: ["22px", { lineHeight: "28px", fontWeight: "600" }],
        h3: ["18px", { lineHeight: "24px", fontWeight: "500" }],
        kpi: ["34px", { lineHeight: "38px", fontWeight: "600" }],
        body: ["15px", { lineHeight: "22px", fontWeight: "400" }],
        label: ["13px", { lineHeight: "18px", fontWeight: "500" }],
        caption: ["12px", { lineHeight: "16px", fontWeight: "400" }],
        mono: ["13px", { lineHeight: "18px", fontWeight: "500" }],
        "mono-sm": ["11px", { lineHeight: "15px", fontWeight: "500" }],
      },
      borderRadius: {
        card: "12px",
        lg: "12px",
        md: "8px",
        sm: "6px",
      },
      boxShadow: {
        card: "var(--shadow-card)",
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
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
      },
    },
  },
  plugins: [animate],
};

export default config;
