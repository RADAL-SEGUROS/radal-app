/**
 * The closed ramo → subtipo catalogue (v12). Global, read-only for brokers:
 * ramos can no longer be created by a corredora (server returns 403).
 *
 * Frozen shared module (v12 orchestration): lanes import from here, nobody edits it.
 */
import { useQuery } from "@tanstack/react-query";
import api from "@/lib/api";
import { qk } from "@/api/keys";

export type SuggestedEntry = "market" | "portal";
export type PlacementRoute = "traditional" | "digital" | "manual";

export interface CatalogSubtype {
  id: number;
  code: string;
  name: string;
  suggested_entry: SuggestedEntry;
  suggested_route: PlacementRoute;
  cmf_codes: string[] | null;
}

export interface CatalogLine {
  id: number;
  code: string | null;
  name: string;
  sort_order: number;
  subtypes: CatalogSubtype[];
}

export function useCatalogLines() {
  return useQuery({
    queryKey: qk.catalog.lines,
    queryFn: async () => (await api.get<CatalogLine[]>("/catalog/lines")).data,
    staleTime: 60 * 60 * 1000,
  });
}

/** Find a subtype (and its ramo) by id in an already-loaded catalogue. */
export function findSubtype(
  lines: CatalogLine[] | undefined,
  subtypeId: number | null | undefined,
): { line: CatalogLine; subtype: CatalogSubtype } | null {
  if (!lines || subtypeId == null) return null;
  for (const line of lines) {
    const subtype = line.subtypes.find((s) => s.id === subtypeId);
    if (subtype) return { line, subtype };
  }
  return null;
}
