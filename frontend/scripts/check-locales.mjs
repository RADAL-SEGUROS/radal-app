#!/usr/bin/env node
/**
 * Locale parity check — `npm run check:locales`.
 *
 * The house rule (CLAUDE.md rule 4) is: `es` is authoritative and complete,
 * `en` mirrors exactly the same key set. Nothing else enforces it — TypeScript
 * cannot see inside a JSON bundle, and `vite build` happily ships a namespace
 * whose English half is missing half its keys. The failure only shows up at
 * runtime, as a raw key printed on screen.
 *
 * This script fails (exit 1) when, for any namespace:
 *   - a bundle exists in one locale and not the other;
 *   - the flattened key sets differ;
 *   - a leaf is an object on one side and a string on the other (a shape drift
 *     that a naive key diff would miss);
 *   - a namespace listed in `src/i18n/index.ts::NAMESPACES` has no `es` bundle,
 *     or an `es` bundle is not registered there.
 *
 * It reads no dependencies: plain node, no build step.
 */

import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");
const LOCALES = join(ROOT, "src", "locales");
const I18N_INDEX = join(ROOT, "src", "i18n", "index.ts");
const LANGS = ["es", "en"];
const BASE = "es";

/** Flatten a bundle into `path -> "leaf" | "object"`. */
function flatten(node, prefix, out) {
  for (const [key, value] of Object.entries(node)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      out.set(path, "object");
      flatten(value, path, out);
    } else {
      out.set(path, "leaf");
    }
  }
  return out;
}

function readBundle(lang, ns) {
  const file = join(LOCALES, lang, `${ns}.json`);
  if (!existsSync(file)) return null;
  try {
    return JSON.parse(readFileSync(file, "utf8"));
  } catch (error) {
    return { __parseError: String(error && error.message ? error.message : error) };
  }
}

function namespacesOnDisk(lang) {
  const dir = join(LOCALES, lang);
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((name) => name.endsWith(".json"))
    .map((name) => name.replace(/\.json$/, ""))
    .sort();
}

/** The NAMESPACES tuple in src/i18n/index.ts, read without importing TS. */
function registeredNamespaces() {
  if (!existsSync(I18N_INDEX)) return null;
  const source = readFileSync(I18N_INDEX, "utf8");
  const match = source.match(/export const NAMESPACES\s*=\s*\[([\s\S]*?)\]\s*as const;/);
  if (!match) return null;
  return [...match[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

const problems = [];
const fail = (message) => problems.push(message);

const byLang = Object.fromEntries(LANGS.map((lang) => [lang, namespacesOnDisk(lang)]));
const allNamespaces = [...new Set(LANGS.flatMap((lang) => byLang[lang]))].sort();

if (allNamespaces.length === 0) {
  fail(`no locale bundles found under ${LOCALES}`);
}

let comparedKeys = 0;

for (const ns of allNamespaces) {
  const bundles = {};
  for (const lang of LANGS) {
    const bundle = readBundle(lang, ns);
    if (bundle === null) {
      fail(`${ns}: missing bundle src/locales/${lang}/${ns}.json`);
      continue;
    }
    if (bundle.__parseError) {
      fail(`${ns}: src/locales/${lang}/${ns}.json is not valid JSON — ${bundle.__parseError}`);
      continue;
    }
    bundles[lang] = flatten(bundle, "", new Map());
  }
  if (LANGS.some((lang) => !bundles[lang])) continue;

  const base = bundles[BASE];
  comparedKeys += base.size;

  for (const lang of LANGS) {
    if (lang === BASE) continue;
    const other = bundles[lang];

    const missing = [...base.keys()].filter((key) => !other.has(key)).sort();
    const extra = [...other.keys()].filter((key) => !base.has(key)).sort();
    const shape = [...base.entries()]
      .filter(([key, kind]) => other.has(key) && other.get(key) !== kind)
      .map(([key]) => key)
      .sort();

    if (missing.length) {
      fail(`${ns}: ${missing.length} key(s) present in ${BASE} but missing in ${lang}:\n    ${missing.join("\n    ")}`);
    }
    if (extra.length) {
      fail(`${ns}: ${extra.length} key(s) present in ${lang} but missing in ${BASE}:\n    ${extra.join("\n    ")}`);
    }
    if (shape.length) {
      fail(`${ns}: ${shape.length} key(s) are an object in one locale and a value in the other:\n    ${shape.join("\n    ")}`);
    }
  }
}

const registered = registeredNamespaces();
if (registered === null) {
  fail(`could not read the NAMESPACES tuple from ${I18N_INDEX}`);
} else {
  for (const ns of registered) {
    if (!byLang[BASE].includes(ns)) {
      fail(`${ns}: registered in src/i18n/index.ts but has no src/locales/${BASE}/${ns}.json`);
    }
  }
  for (const ns of byLang[BASE]) {
    if (!registered.includes(ns)) {
      fail(`${ns}: src/locales/${BASE}/${ns}.json exists but is not listed in NAMESPACES (src/i18n/index.ts)`);
    }
  }
}

if (problems.length) {
  console.error("locale parity check FAILED\n");
  for (const problem of problems) console.error(`  - ${problem}`);
  console.error(
    `\n${problems.length} problem(s). ` +
      "`es` is authoritative; add the missing keys to the other locale (never delete to match).",
  );
  process.exit(1);
}

console.log(
  `locale parity OK — ${allNamespaces.length} namespaces, ${comparedKeys} keys per locale (${LANGS.join(" / ")}).`,
);
