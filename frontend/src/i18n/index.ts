import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import esCommon from "@/locales/es/common.json";
import esAuth from "@/locales/es/auth.json";
import esDashboard from "@/locales/es/dashboard.json";
import esClientes from "@/locales/es/clientes.json";
import esPolizas from "@/locales/es/polizas.json";
import esRenovaciones from "@/locales/es/renovaciones.json";
import esCotizaciones from "@/locales/es/cotizaciones.json";
import esSiniestros from "@/locales/es/siniestros.json";
import esInspecciones from "@/locales/es/inspecciones.json";

import enCommon from "@/locales/en/common.json";
import enAuth from "@/locales/en/auth.json";
import enDashboard from "@/locales/en/dashboard.json";
import enClientes from "@/locales/en/clientes.json";
import enPolizas from "@/locales/en/polizas.json";
import enRenovaciones from "@/locales/en/renovaciones.json";
import enCotizaciones from "@/locales/en/cotizaciones.json";
import enSiniestros from "@/locales/en/siniestros.json";
import enInspecciones from "@/locales/en/inspecciones.json";

export const NAMESPACES = [
  "common",
  "auth",
  "dashboard",
  "clientes",
  "polizas",
  "renovaciones",
  "cotizaciones",
  "siniestros",
  "inspecciones",
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
    clientes: esClientes,
    polizas: esPolizas,
    renovaciones: esRenovaciones,
    cotizaciones: esCotizaciones,
    siniestros: esSiniestros,
    inspecciones: esInspecciones,
  },
  en: {
    common: enCommon,
    auth: enAuth,
    dashboard: enDashboard,
    clientes: enClientes,
    polizas: enPolizas,
    renovaciones: enRenovaciones,
    cotizaciones: enCotizaciones,
    siniestros: enSiniestros,
    inspecciones: enInspecciones,
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
