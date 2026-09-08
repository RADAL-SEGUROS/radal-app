/**
 * The @-mention popover (agent spec §8.2).
 *
 * Two data sources, merged into grouped sections:
 *  1. Zero-query state (instant, from cache): `useNavigator()` — the groups
 *     list and the 5 recent cases.
 *  2. Typed query >= 2 chars: `GET /search?q=` (debounced 200 ms,
 *     `keepPreviousData`), sections Grupos / Clientes / Cotizaciones /
 *     Propuestas. Assets are omitted — not a supported ref type.
 *
 * The popover is presentation-only (`role="listbox"`); the composer owns the
 * keyboard (↑/↓ wrap, Enter/Tab select, Esc closes) and points
 * `aria-activedescendant` at the active option id.
 */
import * as React from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import api from "@/lib/api";
import { useNavigator } from "@/api/navigator";
import { cn } from "@/lib/utils";
import { AgentChip, makeChip, RefIcon, RefEntityType } from "@/components/agent/refs";

export interface MentionOption {
  id: string;
  chip: AgentChip;
  /** i18n key under `agent:mention.*`. */
  section: "groups" | "recent" | "clients" | "quotes" | "proposals";
}

interface RawSearchHit {
  id: number;
  label: string;
  sublabel?: string | null;
  url: string;
}

interface RawSearchResults {
  groups?: RawSearchHit[];
  clients?: RawSearchHit[];
  quotes?: RawSearchHit[];
  proposals?: RawSearchHit[];
}

function useDebounced<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

function searchOptions(
  data: RawSearchResults | undefined,
  section: keyof RawSearchResults,
  entityType: RefEntityType,
): MentionOption[] {
  const hits = data?.[section] ?? [];
  return hits.map((hit) => ({
    id: `mention-${entityType}-${hit.id}`,
    section,
    chip: makeChip(entityType, hit.id, hit.label, hit.sublabel ?? undefined),
  }));
}

/**
 * The flattened, ordered option list for a mention query (`null` = closed).
 * Exposed as a hook so the composer can drive keyboard selection over the
 * exact list the popover renders.
 */
export function useMentionOptions(query: string | null): {
  options: MentionOption[];
  isSearching: boolean;
} {
  const open = query !== null;
  const navigator = useNavigator(open);
  const debounced = useDebounced(query ?? "", 200);
  const term = (open ? debounced : "").trim();
  const typed = term.length >= 2;

  const search = useQuery({
    queryKey: ["agent-mention-search", term] as const,
    enabled: open && typed,
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const { data } = await api.get<RawSearchResults>("/search", { params: { q: term } });
      return data;
    },
  });

  return React.useMemo(() => {
    if (!open) return { options: [], isSearching: false };
    if (typed) {
      const data = search.data;
      return {
        options: [
          ...searchOptions(data, "groups", "account_group"),
          ...searchOptions(data, "clients", "client"),
          ...searchOptions(data, "quotes", "quote_request"),
          ...searchOptions(data, "proposals", "proposal"),
        ],
        isSearching: search.isFetching,
      };
    }
    const nav = navigator.data;
    const lowered = term.toLowerCase();
    const groups: MentionOption[] = (nav?.groups ?? [])
      .filter((g) => !lowered || g.name.toLowerCase().includes(lowered))
      .map((g) => ({
        id: `mention-account_group-${g.id}`,
        section: "groups" as const,
        chip: makeChip("account_group", g.id, g.name, g.latest_period_label ?? undefined),
      }));
    const recent: MentionOption[] = (nav?.recent ?? [])
      .filter(
        (c) =>
          !lowered ||
          c.title.toLowerCase().includes(lowered) ||
          (c.reference ?? "").toLowerCase().includes(lowered),
      )
      .map((c) => ({
        id: `mention-case_file-${c.case_file_id}`,
        section: "recent" as const,
        chip: makeChip(
          "case_file",
          c.case_file_id,
          c.reference ?? c.title,
          c.reference ? c.title : (c.line_name ?? undefined),
        ),
      }));
    return { options: [...groups, ...recent], isSearching: false };
  }, [open, typed, term, search.data, search.isFetching, navigator.data]);
}

const SECTION_ORDER: MentionOption["section"][] = [
  "groups",
  "recent",
  "clients",
  "quotes",
  "proposals",
];

export function MentionPopover({
  query,
  options,
  isSearching,
  activeId,
  onSelect,
  listboxId,
}: {
  query: string;
  options: MentionOption[];
  isSearching: boolean;
  activeId: string | null;
  onSelect: (option: MentionOption) => void;
  listboxId: string;
}) {
  const { t } = useTranslation("agent");

  const sections = SECTION_ORDER.map((section) => ({
    section,
    items: options.filter((o) => o.section === section),
  })).filter((s) => s.items.length > 0);

  return (
    <div
      id={listboxId}
      role="listbox"
      aria-label={t("mention.label")}
      className="absolute inset-x-0 bottom-full z-30 mb-2 max-h-[320px] overflow-y-auto rounded-lg bg-bg-surface p-1.5 shadow-overlay"
    >
      {sections.length === 0 ? (
        <p className="px-2.5 py-3 text-caption text-text-muted">
          {isSearching ? t("mention.searching") : t("mention.empty", { query })}
        </p>
      ) : (
        sections.map(({ section, items }) => (
          <div key={section} role="presentation">
            <p className="px-2.5 pb-1 pt-2 font-mono text-mono-sm uppercase text-text-muted">
              {t(`mention.${section}`)}
            </p>
            {items.map((option) => (
              <button
                key={option.id}
                id={option.id}
                type="button"
                role="option"
                aria-selected={option.id === activeId}
                // The textarea keeps focus; selection happens on mousedown so
                // the click never blurs the composer first.
                onMouseDown={(e) => {
                  e.preventDefault();
                  onSelect(option);
                }}
                className={cn(
                  "flex w-full items-center gap-2 rounded-sm px-2.5 py-1.5 text-left text-body transition-[background-color,color] duration-150 ease-out",
                  option.id === activeId
                    ? "bg-brand-soft text-brand-deep"
                    : "text-text-secondary hover:bg-[color-mix(in_srgb,var(--ink)_5%,transparent)]",
                )}
              >
                <RefIcon entityType={option.chip.ref.entity_type} />
                <span className="min-w-0 flex-1 truncate">{option.chip.label}</span>
                {option.chip.sublabel ? (
                  <span className="max-w-[45%] truncate text-caption text-text-muted">
                    {option.chip.sublabel}
                  </span>
                ) : null}
              </button>
            ))}
          </div>
        ))
      )}
      <p className="border-t border-line px-2.5 pb-1 pt-1.5 text-caption text-text-muted">
        {t("mention.hint")}
      </p>
    </div>
  );
}
