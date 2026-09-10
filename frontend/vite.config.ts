import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Absolute origin baked into the og:/twitter: tags in index.html. WhatsApp will
// not resolve a relative og:image, so the tags need a real origin at build time.
// CI can override per environment with VITE_SITE_URL; dev is the only deployed
// environment today, so it is the default.
const SITE_URL = (process.env.VITE_SITE_URL || "https://dev.radalseguros.cl").replace(/\/+$/, "");

function siteUrl(): Plugin {
  return {
    name: "radal-site-url",
    transformIndexHtml: {
      order: "pre",
      handler: (html) => html.replaceAll("%SITE_URL%", SITE_URL),
    },
  };
}

export default defineConfig({
  plugins: [react(), siteUrl()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5500,
  },
  preview: {
    port: 5500,
  },
});
