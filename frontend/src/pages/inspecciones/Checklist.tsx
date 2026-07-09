import { useTranslation } from "react-i18next";
import { Check, Minus, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Checklist as ChecklistData, ChecklistItem } from "./types";

interface NormalizedGroup {
  categoria: string | null;
  items: ChecklistItem[];
}

/** Coerce the various checklist JSON shapes into categorized groups. */
function normalize(checklist: ChecklistData): NormalizedGroup[] {
  if (!checklist) return [];

  let items: ChecklistItem[] = [];

  if (Array.isArray(checklist)) {
    // Either a flat item list or a list of { categoria, items }.
    const grouped = checklist.filter(
      (x): x is { categoria: string; items: ChecklistItem[] } =>
        typeof x === "object" &&
        x !== null &&
        "items" in x &&
        Array.isArray((x as { items?: unknown }).items),
    );
    if (grouped.length > 0) {
      return grouped.map((g) => ({
        categoria: g.categoria ?? null,
        items: g.items ?? [],
      }));
    }
    items = checklist as ChecklistItem[];
  } else if ("categorias" in checklist && Array.isArray(checklist.categorias)) {
    return checklist.categorias.map((g) => ({
      categoria: g.categoria ?? null,
      items: g.items ?? [],
    }));
  } else if ("items" in checklist && Array.isArray(checklist.items)) {
    items = checklist.items;
  }

  // Group flat items by their categoria field.
  const map = new Map<string | null, ChecklistItem[]>();
  for (const it of items) {
    const key = it.categoria ?? null;
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(it);
  }
  return Array.from(map.entries()).map(([categoria, its]) => ({
    categoria,
    items: its,
  }));
}

type Answer = "si" | "no" | "na" | "texto" | null;

function resolveAnswer(item: ChecklistItem): { kind: Answer; text?: string } {
  const raw = item.valor ?? item.respuesta ?? null;
  if (typeof raw === "boolean") return { kind: raw ? "si" : "no" };
  if (raw == null || raw === "") return { kind: null };
  const norm = String(raw).trim().toLowerCase();
  if (["si", "sí", "yes", "true"].includes(norm)) return { kind: "si" };
  if (["no", "false"].includes(norm)) return { kind: "no" };
  if (["na", "n.a.", "n/a", "no aplica"].includes(norm)) return { kind: "na" };
  return { kind: "texto", text: String(raw) };
}

function AnswerPill({ item }: { item: ChecklistItem }) {
  const { t } = useTranslation("inspecciones");
  const { kind, text } = resolveAnswer(item);

  if (kind === "si") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-lime/15 px-2 py-0.5 text-mono-sm font-mono text-lime">
        <Check className="h-3 w-3" />
        {t("checklist.si")}
      </span>
    );
  }
  if (kind === "no") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-signal-danger/15 px-2 py-0.5 text-mono-sm font-mono text-signal-danger">
        <X className="h-3 w-3" />
        {t("checklist.no")}
      </span>
    );
  }
  if (kind === "na") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-bg-recessed px-2 py-0.5 text-mono-sm font-mono text-text-muted">
        <Minus className="h-3 w-3" />
        {t("checklist.na")}
      </span>
    );
  }
  if (kind === "texto") {
    return <span className="text-body text-text-primary">{text}</span>;
  }
  return <span className="text-caption text-text-muted">{t("checklist.sinRespuesta")}</span>;
}

export function Checklist({ checklist }: { checklist: ChecklistData }) {
  const { t } = useTranslation("inspecciones");
  const groups = normalize(checklist);

  const total = groups.reduce((n, g) => n + g.items.length, 0);
  if (total === 0) {
    return (
      <p className="text-body text-text-muted">{t("detail.checklist.empty")}</p>
    );
  }

  return (
    <div className="space-y-6">
      {groups.map((group, gi) => (
        <div key={group.categoria ?? gi}>
          {group.categoria ? (
            <p className="mb-2 font-mono text-mono-sm uppercase tracking-wide text-text-muted">
              {group.categoria}
            </p>
          ) : null}
          <ul className="divide-y divide-line rounded-md border border-line">
            {group.items.map((item, ii) => (
              <li
                key={item.id ?? ii}
                className={cn(
                  "flex items-start justify-between gap-4 px-4 py-3",
                )}
              >
                <div className="min-w-0 space-y-0.5">
                  <p className="text-body text-text-primary">
                    {item.label ?? item.pregunta ?? "—"}
                  </p>
                  {item.nota ? (
                    <p className="text-caption text-text-muted">{item.nota}</p>
                  ) : null}
                </div>
                <div className="shrink-0 pt-0.5 text-right">
                  <AnswerPill item={item} />
                </div>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
