import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { execSync } from "node:child_process";

// Absolute origin baked into the og:/twitter: tags in index.html. WhatsApp will
// not resolve a relative og:image, so the tags need a real origin at build time.
// CI can override per environment with VITE_SITE_URL; dev is the only deployed
// environment today, so it is the default.
const SITE_URL = (process.env.VITE_SITE_URL || "https://dev.radalseguros.cl").replace(/\/+$/, "");

/**
 * The build stamp the UI shows and every bug report quotes.
 *
 * The image is built from `./frontend` with no `.git` in the context, so the
 * commit CANNOT be read from the repo at build time — CI passes it in as
 * `VITE_APP_VERSION` (see `.github/workflows/deploy-frontend-dev.yml` and the
 * Dockerfile ARG). Locally we fall back to reading git directly, and to "dev"
 * when even that is unavailable, so a `npm run dev` never fails over a stamp.
 *
 * Why it exists: the team files issues against a deployed app, and until now
 * nothing on screen said WHICH deploy. A report that cannot be tied to a build
 * is a report you re-investigate from scratch.
 */
function buildVersion(): string {
  if (process.env.VITE_APP_VERSION) {
    return process.env.VITE_APP_VERSION.slice(0, 7);
  }
  try {
    return execSync("git rev-parse --short=7 HEAD", {
      stdio: ["ignore", "pipe", "ignore"],
    })
      .toString()
      .trim();
  } catch {
    return "dev";
  }
}

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
  define: {
    __APP_VERSION__: JSON.stringify(buildVersion()),
    __APP_BUILD_TIME__: JSON.stringify(new Date().toISOString()),
    // `local` unless CI says otherwise — an issue that names the wrong
    // environment sends the reader to the wrong logs.
    __APP_ENV__: JSON.stringify(process.env.VITE_APP_ENV || "local"),
  },
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
