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
        sidebar: "var(--sidebar)",
        line: "var(--line)",
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
        primary: "var(--blue)",
        "primary-foreground": "#FFFFFC",
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
        display: ["Space Grotesk", "system-ui", "sans-serif"],
        ui: ["Inter Tight", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
        sans: ["Inter Tight", "system-ui", "sans-serif"],
      },
      fontSize: {
        display: ["28px", { lineHeight: "1.08", fontWeight: "500", letterSpacing: "-0.02em" }],
        h1: ["24px", { lineHeight: "1.12", fontWeight: "500", letterSpacing: "-0.02em" }],
        h2: ["20px", { lineHeight: "1.2", fontWeight: "500", letterSpacing: "-0.01em" }],
        h3: ["15.5px", { lineHeight: "1.3", fontWeight: "500" }],
        kpi: ["33px", { lineHeight: "1", fontWeight: "500", letterSpacing: "-0.02em" }],
        body: ["14px", { lineHeight: "1.5", fontWeight: "400" }],
        label: ["13.5px", { lineHeight: "1.4", fontWeight: "500" }],
        caption: ["12px", { lineHeight: "1.4", fontWeight: "400" }],
        mono: ["12.5px", { lineHeight: "1.4", fontWeight: "500" }],
        "mono-sm": ["11px", { lineHeight: "1.4", fontWeight: "500" }],
      },
      borderRadius: {
        card: "16px",
        lg: "12px",
        md: "10px",
        sm: "8px",
      },
      boxShadow: {
        card: "var(--shadow-card)",
        sm: "var(--shadow-sm)",
        lift: "var(--shadow)",
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
        fadeUp: {
          from: { opacity: "0", transform: "translateY(10px)" },
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
        fadeUp: "fadeUp 0.5s cubic-bezier(0.22,0.72,0.24,1) both",
        dropIn: "dropIn 0.18s ease both",
      },
    },
  },
  plugins: [animate],
};

export default config;
