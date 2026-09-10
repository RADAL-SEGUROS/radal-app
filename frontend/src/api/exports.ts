/**
 * The export catalog — what each entity can actually filter, group and emit.
 *
 * `GET /exports/entities` is the server's own description of itself: per
 * entity, the date column `date_from`/`date_to` bites on, the money column, the
 * `group_by` dimensions it can answer, and the exportable columns.
 *
 * The UI reads it instead of hardcoding a list, and the reason is concrete:
 * the dimensions are NOT uniform. `case_files` groups by `stage` but has no
 * insurer; `proposals` groups by `insurer` but has no stage. A fixed picker
 * would therefore offer a broker a dimension that answers 422 for the chart
 * right next to it — a dead control by another name (rule 3). Driving it from
 * the catalog means adding a dimension server-side lights it up here with no
 * frontend change.
 */
import { useQuery } from "@tanstack/react-query";
import api from "@/lib/api";
import type { ExportCatalog, ExportEntityInfo } from "@/api/types";

export function useExportCatalog(enabled = true) {
  return useQuery({
    queryKey: ["exports", "catalog"] as const,
    enabled,
    // The catalog is a property of the deployment, not of the data.
    staleTime: 30 * 60_000,
    queryFn: async () => {
      const { data } = await api.get<ExportCatalog>("/exports/entities");
      return data;
    },
  });
}

/** One entity's row of the catalog, or `undefined` while it loads. */
export function useExportEntity(
  entity: string,
  enabled = true,
): ExportEntityInfo | undefined {
  const catalog = useExportCatalog(enabled);
  return catalog.data?.entities.find((row) => row.entity === entity);
}

/**
 * The dimensions at least one of `entities` can group by, in catalog order.
 *
 * A union, not an intersection: an intersection would hide `stage` (which only
 * case_files answers) and `insurer` (only proposals), leaving the broker with
 * just "grupo" and "mes". Each chart then asks for the dimension only when its
 * OWN entity supports it — see `supports` below.
 */
export function unionDimensions(
  catalog: ExportCatalog | undefined,
  entities: string[],
): string[] {
  const seen: string[] = [];
  for (const row of catalog?.entities ?? []) {
    if (!entities.includes(row.entity)) continue;
    for (const dimension of row.group_by) {
      if (!seen.includes(dimension)) seen.push(dimension);
    }
  }
  return seen;
}

/** Whether `entity` can answer `dimension` — the guard against a 422. */
export function supports(
  catalog: ExportCatalog | undefined,
  entity: string,
  dimension: string | null | undefined,
): boolean {
  if (!dimension) return false;
  const row = catalog?.entities.find((item) => item.entity === entity);
  return Boolean(row?.group_by.includes(dimension));
}
