/**
 * Server feature flags — `GET /meta/features`.
 *
 * A feature that is not ready (portal-search RPA needs each insurer's own
 * portal user + password before it can apply filters) is announced by the
 * SERVER together with its reason, so every entry point renders the same
 * visibly-disabled "pronto" state carrying the server's own words.
 *
 * Tolerant by design: an older backend without the endpoint, or a payload
 * missing the flag, reads as `enabled: false` with `reason: null` — the caller
 * then shows its local fallback reason. A feature is only ever ON when the
 * server says so explicitly.
 */
import { useQuery } from "@tanstack/react-query";
import api from "@/lib/api";

export interface FeatureFlag {
  enabled: boolean;
  reason: string | null;
}

export interface FeaturesResponse {
  portal_search?: Partial<FeatureFlag> | null;
  [key: string]: Partial<FeatureFlag> | null | undefined;
}

/** The feature keys the UI reads. Add a key here when a new flag lands. */
export type FeatureKey = "portal_search";

export const featuresKey = ["meta", "features"] as const;

export function useFeatures() {
  return useQuery({
    queryKey: featuresKey,
    staleTime: 5 * 60 * 1000,
    retry: false,
    queryFn: async () => (await api.get<FeaturesResponse>("/meta/features")).data,
  });
}

export interface FeatureState extends FeatureFlag {
  isLoading: boolean;
}

/** One flag, normalised: `enabled` is true only on an explicit server `true`. */
export function useFeature(key: FeatureKey): FeatureState {
  const { data, isLoading } = useFeatures();
  const flag = data?.[key] ?? null;
  const reason = typeof flag?.reason === "string" && flag.reason.trim() ? flag.reason : null;
  return { enabled: flag?.enabled === true, reason, isLoading };
}
