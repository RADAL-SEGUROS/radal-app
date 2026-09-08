/**
 * The navigator — the data behind the MAIN sidebar rail (spec v3 §4.2).
 *
 * One request answers the whole rail: up to 8 groups ordered by
 * `latest_period_start` desc (each with up to 3 periods), the 5 most recently
 * updated visible account/renewal cases, how many clients sit in no group, and
 * `record_folders` — the SERVER-OWNED mapping from the team's ANTECEDENTES
 * leaves (MONTOS · SINIESTRALIDAD · INFORME · CUESTIONARIO · SLIP DE T&C) onto
 * `DocumentCategory`. **Never hardcode that mapping in a component**: read it
 * from `data.record_folders` so adding a category is a backend-only change.
 *
 * Everything here is already narrowed to what the caller may see — an inspector
 * gets fewer groups and fewer recent cases, and the counts shrink with them.
 * The UI therefore renders whatever arrives; it never filters again.
 *
 * Gate: `CaseFiles.View`.
 */
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import api from "@/lib/api";
import { qk } from "@/api/keys";
import type { NavigatorResponse, RecordFolder, RecordFolderKey } from "@/api/types";

/**
 * The rail is read on every page and changes rarely, so it is cached for five
 * minutes and kept on screen across refetches — the sidebar must never blink
 * back to a skeleton while the user is looking at it.
 */
export function useNavigator(enabled = true) {
  return useQuery({
    queryKey: qk.navigator.all,
    enabled,
    staleTime: 5 * 60 * 1000,
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const { data } = await api.get<NavigatorResponse>("/navigator");
      return data;
    },
  });
}

/**
 * `record_folders` as a lookup by key, for rendering an ANTECEDENTES row.
 * Returns `{}` while the navigator is still loading — callers render the five
 * `RECORD_FOLDER_KEYS` and simply show no category chips until it arrives.
 */
export function recordFolderMap(
  folders: RecordFolder[] | undefined,
): Partial<Record<RecordFolderKey, RecordFolder>> {
  const out: Partial<Record<RecordFolderKey, RecordFolder>> = {};
  for (const folder of folders ?? []) out[folder.key] = folder;
  return out;
}
