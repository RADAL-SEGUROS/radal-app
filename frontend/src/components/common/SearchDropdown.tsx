import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Search, Loader2, Users, Building, FileText, Send } from "lucide-react";
import { useTranslation } from "react-i18next";
import api from "@/lib/api";
import { dropIn } from "@/components/common/motion";
import { cn } from "@/lib/utils";

export interface SearchHit {
  id: number;
  url: string;
  label: string;
  sub?: string;
}

/** Mirrors the backend `SearchResults` schema (GET /search). */
export interface SearchResults {
  clients: SearchHit[];
  assets: SearchHit[];
  quotes: SearchHit[];
  proposals: SearchHit[];
}

const CATEGORY_ICON: Record<keyof SearchResults, React.ReactNode> = {
  clients: <Users className="h-[15px] w-[15px]" strokeWidth={1.75} />,
  assets: <Building className="h-[15px] w-[15px]" strokeWidth={1.75} />,
  quotes: <FileText className="h-[15px] w-[15px]" strokeWidth={1.75} />,
  proposals: <Send className="h-[15px] w-[15px]" strokeWidth={1.75} />,
};

type RawHit = { id: number; label: string; sublabel?: string | null; url: string };

/** The backend already returns `url`; we only map `sublabel` -> `sub`. */
function normalize(data: Partial<Record<keyof SearchResults, RawHit[]>>): SearchResults {
  const map = (hits: RawHit[] | undefined): SearchHit[] =>
    (hits ?? []).map((h) => ({
      id: Number(h.id),
      url: String(h.url),
      label: String(h.label ?? ""),
      sub: h.sublabel ?? undefined,
    }));
  return {
    clients: map(data.clients),
    assets: map(data.assets),
    quotes: map(data.quotes),
    proposals: map(data.proposals),
  };
}

function useDebounced<T>(value: T, delay = 250): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

interface SearchDropdownProps {
  className?: string;
  placeholder?: string;
}

export function SearchDropdown({ className, placeholder }: SearchDropdownProps) {
  const { t } = useTranslation("common");
  const navigate = useNavigate();
  const reduce = useReducedMotion();
  const [term, setTerm] = React.useState("");
  const [open, setOpen] = React.useState(false);
  const containerRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const debounced = useDebounced(term, 250);

  React.useEffect(() => {
    function onClick(e: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  // ⌘K / Ctrl-K focuses the search.
  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const { data, isFetching } = useQuery({
    queryKey: ["search", debounced],
    enabled: debounced.trim().length >= 2,
    queryFn: async () => {
      const res = await api.get("/search", { params: { q: debounced } });
      return normalize(res.data);
    },
  });

  const go = (url: string) => {
    setOpen(false);
    setTerm("");
    navigate(url);
  };

  const groups: Array<{ key: keyof SearchResults; hits: SearchHit[] }> = [
    { key: "clients", hits: data?.clients ?? [] },
    { key: "assets", hits: data?.assets ?? [] },
    { key: "quotes", hits: data?.quotes ?? [] },
    { key: "proposals", hits: data?.proposals ?? [] },
  ];
  const hasResults = groups.some((g) => g.hits.length > 0);
  const showPanel = open && debounced.trim().length >= 2;

  return (
    <div ref={containerRef} className={cn("relative w-full", className)}>
      <div className="relative flex items-center">
        <Search
          className="pointer-events-none absolute left-[13px] h-[18px] w-[18px] text-text-muted"
          strokeWidth={1.75}
        />
        <input
          ref={inputRef}
          value={term}
          onChange={(e) => {
            setTerm(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          placeholder={placeholder ?? t("search.placeholder")}
          aria-label={placeholder ?? t("search.placeholder")}
          className="h-[41px] w-full rounded-[11px] border border-line bg-bg-surface pl-10 pr-11 text-body text-text-primary outline-none transition-[border-color,box-shadow] duration-150 placeholder:text-text-muted focus-visible:border-teal focus-visible:shadow-[0_0_0_3px_color-mix(in_srgb,var(--teal)_18%,transparent)]"
        />
        {isFetching && showPanel ? (
          <Loader2 className="absolute right-[11px] h-4 w-4 animate-spin text-text-muted" />
        ) : (
          <kbd className="pointer-events-none absolute right-[11px] rounded-md border border-line bg-[color-mix(in_srgb,var(--ink)_5%,transparent)] px-1.5 py-0.5 font-mono text-[11px] text-text-muted">
            ⌘K
          </kbd>
        )}
      </div>

      <AnimatePresence>
        {showPanel ? (
          <motion.div
            variants={reduce ? undefined : dropIn}
            initial={reduce ? undefined : "hidden"}
            animate={reduce ? undefined : "show"}
            exit={reduce ? undefined : "exit"}
            className="absolute left-0 right-0 top-[49px] z-50 max-h-[64vh] overflow-y-auto rounded-[14px] border border-line bg-bg-surface p-[7px] shadow-lift"
          >
            {!hasResults && !isFetching ? (
              <div className="px-3 py-[18px] text-center text-body text-text-muted">
                {t("search.empty")}
              </div>
            ) : (
              groups
                .filter((g) => g.hits.length > 0)
                .map((group) => (
                  <div key={group.key}>
                    <div className="px-2.5 pb-1 pt-2.5 text-[10.5px] font-semibold uppercase tracking-[0.09em] text-text-muted">
                      {t(`search.categories.${group.key}`)}
                    </div>
                    {group.hits.map((hit) => (
                      <button
                        key={`${group.key}-${hit.id}`}
                        type="button"
                        onClick={() => go(hit.url)}
                        className="flex w-full items-center gap-[11px] rounded-[9px] px-2.5 py-2 text-left transition-colors hover:bg-[color-mix(in_srgb,var(--ink)_5%,transparent)]"
                      >
                        <span className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-lg bg-[color-mix(in_srgb,var(--teal)_12%,transparent)] text-teal-deep">
                          {CATEGORY_ICON[group.key]}
                        </span>
                        <span className="min-w-0">
                          <span className="block truncate text-label font-medium text-text-primary">
                            {hit.label}
                          </span>
                          {hit.sub ? (
                            <span className="block truncate text-caption text-text-muted">
                              {hit.sub}
                            </span>
                          ) : null}
                        </span>
                      </button>
                    ))}
                  </div>
                ))
            )}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
