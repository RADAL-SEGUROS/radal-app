import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import esCommon from "@/locales/es/common.json";
import esAuth from "@/locales/es/auth.json";
import esDashboard from "@/locales/es/dashboard.json";
import esClients from "@/locales/es/clients.json";
import esPlacements from "@/locales/es/placements.json";
import esInspections from "@/locales/es/inspections.json";
import esSettings from "@/locales/es/settings.json";
import esQuotes from "@/locales/es/quotes.json";
import esProposals from "@/locales/es/proposals.json";
import esInsurers from "@/locales/es/insurers.json";
import esOfferings from "@/locales/es/offerings.json";

import enCommon from "@/locales/en/common.json";
import enAuth from "@/locales/en/auth.json";
import enDashboard from "@/locales/en/dashboard.json";
import enClients from "@/locales/en/clients.json";
import enPlacements from "@/locales/en/placements.json";
import enInspections from "@/locales/en/inspections.json";
import enSettings from "@/locales/en/settings.json";
import enQuotes from "@/locales/en/quotes.json";
import enProposals from "@/locales/en/proposals.json";
import enInsurers from "@/locales/en/insurers.json";
import enOfferings from "@/locales/en/offerings.json";

/**
 * Namespaces registered with i18next. Domain namespaces are added back here by
 * the module passes, one per module (clients, assets, placements, quotes,
 * proposals, inspections, insurers, offerings, documents, settings).
 *
 * Rule: `es` is complete and authoritative; `en` mirrors the same keys.
 * No hardcoded UI copy in components — everything goes through t().
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
] as const;

export type Namespace = (typeof NAMESPACES)[number];

export const LANG_KEY = "radal.lang";

function getInitialLang(): "es" | "en" {
  if (typeof window === "undefined") return "es";
  const stored = localStorage.getItem(LANG_KEY);
  return stored === "en" ? "en" : "es";
}

const resources = {
  es: {
    common: esCommon,
    auth: esAuth,
    dashboard: esDashboard,
    clients: esClients,
    placements: esPlacements,
    inspections: esInspections,
    settings: esSettings,
    quotes: esQuotes,
    proposals: esProposals,
    insurers: esInsurers,
    offerings: esOfferings,
  },
  en: {
    common: enCommon,
    auth: enAuth,
    dashboard: enDashboard,
    clients: enClients,
    placements: enPlacements,
    inspections: enInspections,
    settings: enSettings,
    quotes: enQuotes,
    proposals: enProposals,
    insurers: enInsurers,
    offerings: enOfferings,
  },
} as const;

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
