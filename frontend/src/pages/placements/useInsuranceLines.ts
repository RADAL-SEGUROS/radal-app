import * as React from "react";
import { usePlacements } from "@/api/placements";

export interface InsuranceLineOption {
  id: number;
  name: string;
}

/**
 * The insurance lines this broker can open a placement on.
 *
 * There is NO `/insurance-lines` endpoint in this pass (see `src/api/index.ts`),
 * so the options are derived from the lines already present on the broker's own
 * placements — real ids from a real endpoint, never invented.
 *
 * Consequence, surfaced honestly in the UI: a broker with zero placements gets
 * an empty list, and the "new placement" form renders its submit DISABLED with
 * an explanation instead of posting an id that does not exist.
 */
export function useInsuranceLines() {
  const query = usePlacements({ page_size: 200 });

  const lines = React.useMemo<InsuranceLineOption[]>(() => {
    const byId = new Map<number, string>();
    for (const placement of query.data?.items ?? []) {
      const line = placement.insurance_line;
      if (line && !byId.has(line.id)) {
        byId.set(line.id, line.name ?? `#${line.id}`);
      }
    }
    return [...byId.entries()]
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => a.name.localeCompare(b.name, "es"));
  }, [query.data]);

  return { lines, isLoading: query.isLoading, isError: query.isError };
}
