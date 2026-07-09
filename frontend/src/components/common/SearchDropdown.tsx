import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Search, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import api from "@/lib/api";
import { cn } from "@/lib/utils";

export interface SearchHit {
  id: number;
  url: string;
  label: string;
  sub?: string;
}

export interface SearchResults {
  clientes: SearchHit[];
  polizas: SearchHit[];
  activos: SearchHit[];
}

function normalize(data: {
  clientes?: Array<Record<string, unknown>>;
  polizas?: Array<Record<string, unknown>>;
  activos?: Array<Record<string, unknown>>;
}): SearchResults {
  return {
    clientes: (data.clientes ?? []).map((c) => ({
      id: Number(c.id),
      url: String(c.url ?? `/clientes/${c.id}`),
      label: String(c.nombre ?? ""),
    })),
    polizas: (data.polizas ?? []).map((p) => ({
      id: Number(p.id),
      url: String(p.url ?? `/polizas/${p.id}`),
      label: String(p.numero_poliza ?? ""),
      sub: p.cliente ? String(p.cliente) : undefined,
    })),
    activos: (data.activos ?? []).map((a) => ({
      id: Number(a.id),
      url: String(a.url ?? `/clientes`),
      label: String(a.nombre ?? ""),
      sub: a.cliente ? String(a.cliente) : undefined,
    })),
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
  const [term, setTerm] = React.useState("");
  const [open, setOpen] = React.useState(false);
  const containerRef = React.useRef<HTMLDivElement>(null);
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
    { key: "clientes", hits: data?.clientes ?? [] },
    { key: "polizas", hits: data?.polizas ?? [] },
    { key: "activos", hits: data?.activos ?? [] },
  ];
  const hasResults = groups.some((g) => g.hits.length > 0);
  const showPanel = open && debounced.trim().length >= 2;

  return (
    <div ref={containerRef} className={cn("relative w-full", className)}>
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
        <input
          value={term}
          onChange={(e) => {
            setTerm(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          placeholder={placeholder ?? t("search.placeholder")}
          className="h-9 w-full rounded-md border border-line bg-bg-surface pl-9 pr-9 text-body text-text-primary placeholder:text-text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        {isFetching && showPanel ? (
          <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-text-muted" />
        ) : null}
      </div>

      {showPanel ? (
        <div className="absolute z-50 mt-1 max-h-96 w-full overflow-auto rounded-md border border-line bg-bg-surface p-1 shadow-lg">
          {!hasResults && !isFetching ? (
            <div className="px-3 py-6 text-center text-caption text-text-muted">
              {t("search.empty")}
            </div>
          ) : (
            groups
              .filter((g) => g.hits.length > 0)
              .map((group) => (
                <div key={group.key} className="py-1">
                  <div className="px-2 py-1 font-mono text-mono-sm uppercase tracking-wide text-text-muted">
                    {t(`search.categories.${group.key}`)}
                  </div>
                  {group.hits.map((hit) => (
                    <button
                      key={`${group.key}-${hit.id}`}
                      type="button"
                      onClick={() => go(hit.url)}
                      className="flex w-full flex-col items-start rounded-sm px-2 py-1.5 text-left transition-colors hover:bg-bg-recessed"
                    >
                      <span className="text-body text-text-primary">
                        {hit.label}
                      </span>
                      {hit.sub ? (
                        <span className="text-caption text-text-muted">
                          {hit.sub}
                        </span>
                      ) : null}
                    </button>
                  ))}
                </div>
              ))
          )}
        </div>
      ) : null}
    </div>
  );
}
