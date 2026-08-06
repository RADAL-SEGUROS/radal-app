/**
 * Checklist JSON <-> UI model.
 *
 * The checklist is versioned JSON (docs/v2-data-modeling-decisions.md §7), so
 * its SHAPE is code-facing English while the prose inside is whatever the
 * inspector wrote (Spanish). The canonical shape written by the fixture import
 * is:
 *
 *   { version, sections: [{ name, items: [{ item, result, note? }] }],
 *     summary: { ok, observations, critical } }
 *
 * The parser is deliberately forgiving — it also accepts the raw Spanish shape
 * (`secciones`/`nombre`/`resultado`/`nota`) and a few common aliases — because a
 * report may have been written against an older template. Anything it cannot
 * recognise is reported as such rather than silently dropped.
 */

export const RESULT_TOKENS = [
  "ok",
  "observation",
  "critical",
  "not_applicable",
] as const;

export type ResultToken = (typeof RESULT_TOKENS)[number] | "unknown";

export interface ChecklistItem {
  /** Stable local key so React keeps inputs focused while editing. */
  uid: string;
  text: string;
  result: ResultToken;
  note: string;
}

export interface ChecklistSection {
  uid: string;
  name: string;
  items: ChecklistItem[];
}

export interface ParsedChecklist {
  version: number | null;
  sections: ChecklistSection[];
  /** False when the JSON held no recognisable section array. */
  recognized: boolean;
}

const RESULT_ALIASES: Record<string, ResultToken> = {
  ok: "ok",
  conforme: "ok",
  cumple: "ok",
  pass: "ok",
  observation: "observation",
  observacion: "observation",
  observación: "observation",
  warning: "observation",
  critical: "critical",
  critico: "critical",
  crítico: "critical",
  fail: "critical",
  not_applicable: "not_applicable",
  no_aplica: "not_applicable",
  na: "not_applicable",
  "n/a": "not_applicable",
};

let uidSeq = 0;
export function nextUid(prefix: string): string {
  uidSeq += 1;
  return `${prefix}-${uidSeq}`;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function firstString(source: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number") return String(value);
  }
  return "";
}

export function normalizeResult(raw: unknown): ResultToken {
  if (typeof raw !== "string") return "unknown";
  const token = raw.trim().toLowerCase().replace(/\s+/g, "_");
  return RESULT_ALIASES[token] ?? "unknown";
}

export function parseChecklist(raw: Record<string, unknown> | null): ParsedChecklist {
  if (!raw) return { version: null, sections: [], recognized: true };

  const sectionsRaw = raw.sections ?? raw.secciones;
  const version =
    typeof raw.version === "number"
      ? raw.version
      : typeof raw.version === "string" && raw.version.trim() !== ""
        ? Number(raw.version)
        : null;

  if (!Array.isArray(sectionsRaw)) {
    return {
      version: Number.isNaN(version as number) ? null : version,
      sections: [],
      recognized: false,
    };
  }

  const sections: ChecklistSection[] = sectionsRaw.map((entry) => {
    const section = asRecord(entry) ?? {};
    const itemsRaw = section.items ?? section.puntos;
    const items: ChecklistItem[] = Array.isArray(itemsRaw)
      ? itemsRaw.map((itemEntry) => {
          const item = asRecord(itemEntry) ?? {};
          return {
            uid: nextUid("item"),
            text: firstString(item, ["item", "text", "name", "descripcion", "descripción"]),
            result: normalizeResult(item.result ?? item.resultado ?? item.status),
            note: firstString(item, ["note", "nota", "comment", "comentario"]),
          };
        })
      : [];
    return {
      uid: nextUid("section"),
      name: firstString(section, ["name", "nombre", "title", "titulo", "título"]),
      items,
    };
  });

  return {
    version: Number.isNaN(version as number) ? null : version,
    sections,
    recognized: true,
  };
}

export interface ChecklistCounts {
  ok: number;
  observation: number;
  critical: number;
  not_applicable: number;
  unknown: number;
  total: number;
}

export function countResults(sections: ChecklistSection[]): ChecklistCounts {
  const counts: ChecklistCounts = {
    ok: 0,
    observation: 0,
    critical: 0,
    not_applicable: 0,
    unknown: 0,
    total: 0,
  };
  for (const section of sections) {
    for (const item of section.items) {
      counts[item.result] += 1;
      counts.total += 1;
    }
  }
  return counts;
}

/** Back to the canonical wire shape, with the summary recomputed. */
export function serializeChecklist(
  sections: ChecklistSection[],
  version: number | null,
): Record<string, unknown> {
  const counts = countResults(sections);
  return {
    version: version ?? 1,
    sections: sections.map((section) => ({
      name: section.name,
      items: section.items.map((item) => {
        const entry: Record<string, unknown> = {
          item: item.text,
          result: item.result === "unknown" ? null : item.result,
        };
        if (item.note.trim()) entry.note = item.note.trim();
        return entry;
      }),
    })),
    summary: {
      ok: counts.ok,
      observations: counts.observation,
      critical: counts.critical,
    },
  };
}
