import i18n from "i18next";
import { initReactI18next } from "react-i18next";

/**
 * Namespaces registered with i18next.
 *
 * Rule: `es` is complete and authoritative; `en` mirrors the same keys.
 * No hardcoded UI copy in components — everything goes through t().
 *
 * The bundles are collected with `import.meta.glob` instead of one static
 * import per file. Two reasons, both practical:
 *   - a namespace owned by another pass (`postsale`) registers itself the
 *     moment its JSON lands, without this file — which that pass may not edit —
 *     needing a change;
 *   - a missing file is a missing namespace, not a broken build.
 */
export const NAMESPACES = [
  "common",
  "auth",
  "dashboard",
  "clients",
  "placements",
  "inspections",
  "settings",
  "quotes",
  "proposals",
  "insurers",
  "offerings",
  "cases",
  "leads",
  "documents",
  "packs",
  "postsale",
  // v3 groups & accounts: the group / vigencia / ramo tree and its flows.
  "accounts",
  // v4/v5: the /analytics dashboard + consolidated entity tables.
  "analytics",
  // v6: the antecedentes expediente (process → review → register → view/PDF).
  "antecedentes",
  // v8: the incremental comparison expedient + the public insured-decision page.
  "comparison",
  "publicOffering",
  // v8: the outbound broker propuesta (mint from comparison → ratify → send).
  "propuesta",
] as const;

export type Namespace = (typeof NAMESPACES)[number];

export const LANG_KEY = "radal.lang";

function getInitialLang(): "es" | "en" {
  if (typeof window === "undefined") return "es";
  const stored = localStorage.getItem(LANG_KEY);
  return stored === "en" ? "en" : "es";
}

type Bundle = Record<string, unknown>;

const esModules = import.meta.glob<{ default: Bundle }>("../locales/es/*.json", {
  eager: true,
});
const enModules = import.meta.glob<{ default: Bundle }>("../locales/en/*.json", {
  eager: true,
});

function collect(modules: Record<string, { default: Bundle }>): Record<string, Bundle> {
  const out: Record<string, Bundle> = {};
  for (const [path, mod] of Object.entries(modules)) {
    const name = path.split("/").pop()?.replace(/\.json$/, "");
    if (name) out[name] = mod.default;
  }
  return out;
}

const resources = {
  es: collect(esModules),
  en: collect(enModules),
};

i18n.use(initReactI18next).init({
  resources,
  lng: getInitialLang(),
  fallbackLng: "es",
  defaultNS: "common",
  ns: NAMESPACES as unknown as string[],
  interpolation: { escapeValue: false },
  returnNull: false,
});

i18n.on("languageChanged", (lng) => {
  try {
    localStorage.setItem(LANG_KEY, lng);
    document.documentElement.setAttribute("lang", lng);
  } catch {
    /* ignore */
  }
});

export default i18n;
